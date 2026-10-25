"""
SINCOR2 MVP - Minimal Flask Application
Platform billing: SINC (subscriptions) + AXM (one-off intel). Legacy Stripe/PayPal gated off by default.
"""

import os
import re
import json
import time
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

from flask import Flask, render_template, request, jsonify, g, make_response, send_file, redirect, session, url_for
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from werkzeug.middleware.proxy_fix import ProxyFix
try:
    from authlib.integrations.flask_client import OAuth
    OAUTH_AVAILABLE = True
except ImportError:
    OAUTH_AVAILABLE = False
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

from sincor2.data_paths import data_dir, migrate_legacy_orders_db
from sincor2.pdf_loader import get_pdf_generator
from sincor2.email_sender import get_email_sender
from sincor2.shadow_monitor.runtime import build_shadow_runtime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('sincor2')


def _fiat_payments_unavailable() -> bool:
    return False


def _env_first(*keys: str, default: str = '') -> str:
    for key in keys:
        val = (os.environ.get(key) or '').strip()
        if val:
            return val
    return default


load_dotenv()

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
template_dir = os.path.join(project_root, 'templates')
static_dir = os.path.join(project_root, 'static')
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

# Agent-facing effects are proposal-only in this process. This registry does
# not convert legacy customer-facing billing/outreach routes; those callsites
# must be migrated separately before claiming app-wide side-effect isolation.
app.extensions['sincor_shadow_runtime'] = build_shadow_runtime()

if os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('TRUST_PROXY', '').lower() in ('1', 'true', 'yes'):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config['PREFERRED_URL_SCHEME'] = 'https'


@app.context_processor
def inject_social_links():
    from sincor2.social_links import SOCIAL_LINKS
    return {'social_links': SOCIAL_LINKS}


@app.context_processor
def inject_onchain_addresses():
    from sincor2.onchain.constants import AXIOM_TOKEN, SINC_TOKEN, TREASURY
    return {
        'sinc_token': SINC_TOKEN,
        'axiom_token': AXIOM_TOKEN,
        'treasury_address': TREASURY,
    }


@app.context_processor
def inject_auth_state():
    return {
        'is_admin': bool(session.get('is_admin')),
        'admin_username': session.get('admin_username') or '',
        'is_customer': bool(session.get('user_email')) and not session.get('is_admin'),
        'username': session.get('username') or session.get('admin_username') or '',
    }


# Runtime secret resolver (from #223): no committed fallbacks, fail closed in production.
from sincor2.runtime_secrets import resolve_flask_secret, resolve_jwt_secret

app.config['JWT_SECRET_KEY'] = resolve_jwt_secret()
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=8)
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024
app.config['SECRET_KEY'] = resolve_flask_secret()
from sincor2.cookie_security import is_production_https, secure_cookie_kwargs

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# C3 (P2 audit): Secure follows the effective production-HTTPS posture, not a
# single provider's environment label.
app.config['SESSION_COOKIE_SECURE'] = is_production_https()
logger.info('[COOKIE] SESSION_COOKIE_SECURE=%s (production_https=%s)',
            app.config['SESSION_COOKIE_SECURE'], is_production_https())
jwt = JWTManager(app)

try:
    from sincor2.a2a_bootstrap import register_a2a
    if register_a2a(app):
        logger.info('[A2A] discovery surfaces registered')
    else:
        logger.error('[A2A] registration failed')
except Exception as _a2a_exc:
    logger.error('[A2A] bootstrap error: %s', _a2a_exc)

try:
    from sincor2.a2a_inbound import register as register_a2a_inbound
    register_a2a_inbound(app)
    logger.info('[A2A] Inbound registered')
except Exception as _a2a_in_exc:
    logger.warning('[A2A] Inbound not registered: %s', _a2a_in_exc)

try:
    from sincor2.task_queue import register_flask_routes as _register_queue_routes
    _register_queue_routes(app)
except Exception as _q_exc:
    logger.warning('[QUEUE] poll routes not registered: %s', _q_exc)

# C2 (P2 audit): boot-time queue readiness gate. A configured-but-unreachable
# broker or a celery backend without shared job storage fails boot fast with
# a clear error instead of booting a half-working queue. Degraded (no-broker)
# mode is allowed but loudly logged — never silent.
try:
    from sincor2.task_queue import verify_queue_readiness
    _queue_report = verify_queue_readiness()
    if _queue_report.get("degraded"):
        logger.warning('[QUEUE] DEGRADED mode at boot: %s',
                       _queue_report.get("degraded_reason"))
    else:
        logger.info('[QUEUE] ready: backend=%s job_store=%s',
                    _queue_report.get("backend"), _queue_report.get("job_store"))
except Exception as _qgate_exc:
    logger.error('[QUEUE] readiness gate failed — refusing to boot: %s', _qgate_exc)
    raise

oauth = None
OAUTH_REDIRECT_BASE = _env_first('OAUTH_REDIRECT_BASE_URL', 'PUBLIC_BASE_URL', 'SITE_URL', default='').rstrip('/')
OAUTH_ERROR_MESSAGES = {
    'oauth_unavailable': 'Social login is temporarily unavailable. Please use email signup.',
    'oauth_failed': 'Social login failed. Please try again or use email signup.',
    'no_email': 'We could not get your email from that provider. Try another method.',
    'email_unverified': 'That provider account has an unverified email. Verify it with the provider, then try again.',
}


def _oauth_redirect_uri(endpoint: str) -> str:
    if OAUTH_REDIRECT_BASE:
        return f'{OAUTH_REDIRECT_BASE}{url_for(endpoint)}'
    return url_for(endpoint, _external=True)


def _oauth_provider_ready(name: str) -> bool:
    return bool(oauth and hasattr(oauth, name))


def _auth_cookie_response(email: str, redirect_url: str = '/dashboard'):
    session['user_email'] = email
    if not session.get('username'):
        session['username'] = (email or '').split('@')[0]
    access_token = create_access_token(identity=email)
    sep = '&' if '?' in redirect_url else '?'
    if 'email=' not in redirect_url:
        redirect_url = f'{redirect_url}{sep}email={email}'
    resp = make_response(redirect(redirect_url))
    resp.set_cookie(
        'access_token', access_token, max_age=28800,
        **secure_cookie_kwargs(),
    )
    return resp


def _session_email() -> str:
    email = (session.get('user_email') or '').strip()
    if email:
        return email
    token = request.cookies.get('access_token', '')
    if token:
        try:
            from flask_jwt_extended import decode_token
            email = (decode_token(token).get('sub') or '').strip()
            if email:
                return email
        except Exception:
            pass
    return (request.args.get('email') or '').strip()


def _customer_exists(email: str) -> bool:
    if not email:
        return False
    db = get_db()
    if db.execute('SELECT 1 FROM customer_profiles WHERE email=?', (email,)).fetchone():
        return True
    if db.execute('SELECT 1 FROM orders WHERE customer_email=? LIMIT 1', (email,)).fetchone():
        return True
    try:
        if db.execute('SELECT 1 FROM platform_subscriptions WHERE email=? LIMIT 1', (email,)).fetchone():
            return True
    except Exception:
        pass
    return False


def _identity_candidates(identifier: str) -> list:
    ident = (identifier or '').strip().lower()
    if not ident:
        return []
    out = [ident]
    if '@' in ident:
        local = ident.split('@', 1)[0].strip()
        if local and local not in out:
            out.append(local)
    return out


def _slug_username(raw: str) -> str:
    slug = re.sub(r'[^a-z0-9_]+', '', (raw or '').lower())
    return (slug or 'user')[:24]


def _is_admin_identity(identifier: str) -> bool:
    expected = (os.environ.get('ADMIN_USERNAME') or '').strip().lower()
    if not expected:
        return False
    return expected in _identity_candidates(identifier)


def _username_taken(db, username: str, except_email: str = '') -> bool:
    if not username:
        return True
    if _is_admin_identity(username):
        return True
    row = db.execute(
        'SELECT email FROM customer_profiles WHERE lower(username)=? LIMIT 1',
        (username.lower(),),
    ).fetchone()
    if not row:
        return False
    email = row['email'] if isinstance(row, sqlite3.Row) else row[0]
    if except_email and (email or '').lower() == except_email.lower():
        return False
    return True


def _allocate_username(db, seed: str, email: str) -> str:
    base = _slug_username(seed or (email or '').split('@')[0])
    if len(base) < 3:
        base = (base + 'user')[:8]
    candidate = base
    n = 1
    while _username_taken(db, candidate, except_email=email):
        n += 1
        suffix = str(n)
        candidate = f'{base[:24 - len(suffix)]}{suffix}'
        if n > 9999:
            candidate = 'user' + uuid.uuid4().hex[:8]
            break
    return candidate


def _ensure_customer_username_schema(db) -> None:
    cols = {row[1] for row in db.execute('PRAGMA table_info(customer_profiles)').fetchall()}
    if 'username' not in cols:
        db.execute('ALTER TABLE customer_profiles ADD COLUMN username TEXT')
        db.commit()
    missing = db.execute(
        "SELECT id, email, first_name FROM customer_profiles WHERE username IS NULL OR username = ''"
    ).fetchall()
    for row in missing:
        rid = row['id'] if isinstance(row, sqlite3.Row) else row[0]
        email = row['email'] if isinstance(row, sqlite3.Row) else row[1]
        first = row['first_name'] if isinstance(row, sqlite3.Row) else row[2]
        seed = (first or '').split()[0] if first else (email or '').split('@')[0]
        uname = _allocate_username(db, seed, email or '')
        db.execute('UPDATE customer_profiles SET username=? WHERE id=?', (uname, rid))
    if missing:
        db.commit()
    try:
        db.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_customer_profiles_username ON customer_profiles(username)'
        )
    except Exception:
        logger.debug('[DB] username unique index not applied')


def _resolve_customer(identifier: str):
    ident = (identifier or '').strip()
    if not ident:
        return None
    db = get_db()
    _ensure_customer_username_schema(db)
    if '@' in ident:
        email = ident.lower()
        if not _customer_exists(email):
            return None
        row = db.execute(
            'SELECT email, username FROM customer_profiles WHERE lower(email)=? LIMIT 1',
            (email,),
        ).fetchone()
        if not row:
            _upsert_lead(email, email.split('@')[0])
            row = db.execute(
                'SELECT email, username FROM customer_profiles WHERE lower(email)=? LIMIT 1',
                (email,),
            ).fetchone()
        username = ''
        if row:
            username = row['username'] if isinstance(row, sqlite3.Row) else row[1]
        return {'email': email, 'username': username or email.split('@')[0]}
    row = db.execute(
        'SELECT email, username FROM customer_profiles WHERE lower(username)=? LIMIT 1',
        (ident.lower(),),
    ).fetchone()
    if not row:
        return None
    email = row['email'] if isinstance(row, sqlite3.Row) else row[0]
    username = row['username'] if isinstance(row, sqlite3.Row) else row[1]
    return {'email': email, 'username': username}


def _upsert_lead(email: str, name: str) -> None:
    import secrets
    email = email.strip().lower()
    parts = (name or '').strip().split(None, 1)
    first = parts[0] if parts else email.split('@')[0]
    last = parts[1] if len(parts) > 1 else ''
    now = datetime.utcnow().isoformat()
    db = get_db()
    _ensure_customer_username_schema(db)
    existing = db.execute('SELECT profile_id FROM customer_profiles WHERE email=?', (email,)).fetchone()
    if existing:
        db.execute(
            'UPDATE customer_profiles SET first_name=?, last_name=?, updated_at=? WHERE email=?',
            (first, last, now, email),
        )
    else:
        profile_id = 'lead_' + secrets.token_urlsafe(12)
        uname = _allocate_username(db, first, email)
        db.execute(
            '''INSERT INTO customer_profiles
               (profile_id, email, first_name, last_name, username, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)''',
            (profile_id, email, first, last, uname, now, now),
        )
    row = db.execute('SELECT username FROM customer_profiles WHERE email=?', (email,)).fetchone()
    current = (row['username'] if row else '') or ''
    if not current:
        uname = _allocate_username(db, first, email)
        db.execute('UPDATE customer_profiles SET username=? WHERE email=?', (uname, email))
    db.commit()


def _oauth_finish_login(email: str, name: str, provider: str):
    session['user_name'] = name or email.split('@')[0]
    _upsert_lead(email, name or email.split('@')[0])
    logger.info('[OAUTH] %s login: %s', provider, email)
    return _auth_cookie_response(email)


def _google_userinfo(token: dict):
    user_info = token.get('userinfo')
    if user_info:
        return user_info
    if token.get('id_token') and hasattr(oauth.google, 'parse_id_token'):
        try:
            return oauth.google.parse_id_token(token)
        except Exception as exc:
            logger.warning('[OAUTH] Google id_token parse failed: %s', exc)
    resp = oauth.google.get('userinfo')
    return resp.json()


def _github_email_entries() -> list:
    """Raw /user/emails entries for the GitHub OAuth session (may be [])."""
    resp = oauth.github.get('user/emails')
    emails = resp.json() if resp.ok else []
    return emails if isinstance(emails, list) else []


def _github_primary_email(verified_only: bool = False, _entries: list | None = None) -> str:
    """Return the GitHub account's primary email.

    With verified_only=True, only an email the provider marks verified is
    returned (audit P1: never link an account on an unverified address).
    """
    emails = _entries if _entries is not None else _github_email_entries()
    if verified_only:
        for entry in emails:
            if entry.get('primary') and entry.get('verified'):
                return entry.get('email', '')
        for entry in emails:
            if entry.get('verified'):
                return entry.get('email', '')
        return ''
    for entry in emails:
        if entry.get('primary') and entry.get('verified'):
            return entry.get('email', '')
    for entry in emails:
        if entry.get('primary'):
            return entry.get('email', '')
    for entry in emails:
        if entry.get('verified'):
            return entry.get('email', '')
    return emails[0].get('email', '') if emails else ''


def _request_jwt_identity():
    """Resolve the JWT identity (sub) for this request.

    Accepts the standard Authorization: Bearer header and the httponly
    ``access_token`` cookie the app sets on login. Returns None when no
    usable token is present.
    """
    from flask_jwt_extended import decode_token
    auth = request.headers.get('Authorization', '')
    token = auth[7:] if auth.startswith('Bearer ') else ''
    if not token:
        token = request.cookies.get('access_token', '')
    if not token:
        return None
    try:
        return decode_token(token).get('sub')
    except Exception:
        return None


if OAUTH_AVAILABLE:
    oauth = OAuth(app)
    _gcid = _env_first('GOOGLE_CLIENT_ID', 'GOOGLE_OAUTH_CLIENT_ID')
    _gcs = _env_first('GOOGLE_CLIENT_SECRET', 'GOOGLE_OAUTH_CLIENT_SECRET')
    _ghid = _env_first('GITHUB_CLIENT_ID', 'GITHUB_OAUTH_CLIENT_ID')
    _ghs = _env_first('GITHUB_CLIENT_SECRET', 'GITHUB_OAUTH_CLIENT_SECRET')
    if _gcid and _gcs:
        oauth.register(
            'google',
            client_id=_gcid,
            client_secret=_gcs,
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile'},
        )
        logger.info('[OAUTH] Google OAuth registered')
    if _ghid and _ghs:
        oauth.register(
            'github',
            client_id=_ghid,
            client_secret=_ghs,
            access_token_url='https://github.com/login/oauth/access_token',
            authorize_url='https://github.com/login/oauth/authorize',
            api_base_url='https://api.github.com/',
            client_kwargs={'scope': 'read:user user:email'},
        )
        logger.info('[OAUTH] GitHub OAuth registered')

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["10000 per day", "1000 per hour"],
    # SINCOR_LIMITER_STORAGE_URI=redis://... shares the app-wide limiter
    # across gunicorn workers (G2.8). Default memory:// preserves dev/test
    # behavior. NOTE: when a Redis URI is configured but unreachable,
    # flask-limiter surfaces the storage error instead of silently
    # allowing — fail closed, matching the A2A fabric shared state.
    storage_uri=os.environ.get("SINCOR_LIMITER_STORAGE_URI", "memory://"),
)

# Shared credential-verifier throttling (audit P2-A3). Imported here so the
# mvp blueprints pick these up via _bind_mvp(); app.py imports them directly.
from sincor2.login_throttle import (  # noqa: E402
    credential_rate_limits,
    note_login_failure,
    note_login_success,
)

try:
    from sincor2.safety_locks import ProductionSafetyError, assert_production_safety
    assert_production_safety()
except ProductionSafetyError:
    # Fail-closed: a dangerous production config must crash startup, not
    # just log. Re-raise so the process does not serve traffic.
    raise
except Exception as e:
    logger.warning('[SAFETY] Lock check failed: %s', e)

try:
    from sincor2.platform_payments import (
        activate_subscription,
        create_checkout as platform_create_checkout,
        fiat_payments_enabled,
        get_subscription,
        init_platform_payments_db,
        list_plans as platform_list_plans,
        list_subscriptions,
        mark_checkout_fulfilled as platform_mark_checkout_fulfilled,
        verify_checkout as platform_verify_checkout,
    )
    init_platform_payments_db()
    PLATFORM_PAYMENTS_AVAILABLE = True
except Exception as e:
    logger.warning('[PAYMENTS] init failed: %s', e)
    PLATFORM_PAYMENTS_AVAILABLE = False
    fiat_payments_enabled = _fiat_payments_unavailable

STRIPE_AVAILABLE = False
stripe_processor = None
if fiat_payments_enabled():
    try:
        from sincor2.stripe_checkout import get_stripe_checkout
        from sincor2.stripe_routes import init_stripe_routes
        STRIPE_AVAILABLE = True
        stripe_processor = get_stripe_checkout()
        if stripe_processor and stripe_processor.enabled:
            init_stripe_routes(app, stripe_processor)
    except Exception as e:
        logger.warning('[STRIPE] %s', e)
        STRIPE_AVAILABLE = False

# x402/USDC pack rail (B2B skill packs) — always mounted; the rail is the
# human-payments rail, independent of the Stripe fiat path above.
# Mount outcomes are tracked in RAIL_MOUNT_STATUS and surfaced on /health
# (see _expose_rail_mount_status below): a failed mount must be loud —
# full traceback in the boot log AND visible in the health payload —
# never a one-line warning that leaves the rail silently absent.
RAIL_MOUNT_STATUS = {
    'rail_routes': False,
    'rail_delivery': False,
    'rail_pay': False,
}
try:
    from sincor2.rail_routes import init_rail_routes
    init_rail_routes(app)
    RAIL_MOUNT_STATUS['rail_routes'] = True
except Exception:
    logger.exception('[RAIL] init_rail_routes failed — /rail/checkout and '
                     '/rail/settle are NOT mounted')

# Token-gated digital-pack downloads (rail rework w4) — mounted next to
# the rail routes; this is the route the buyer email's download link
# points at.
try:
    from sincor2.rail_delivery import init_rail_delivery_routes
    init_rail_delivery_routes(app)
    RAIL_MOUNT_STATUS['rail_delivery'] = True
except Exception:
    logger.exception('[RAIL-DELIVERY] init_rail_delivery_routes failed — '
                     'token-gated /download/<id> is NOT mounted')

# Human payment pages: GET /rail/pay/<resource_id> + GET /rail/onramp.
# This blueprint was previously registered only in the legacy
# sincor2.app module, so the production app never served the pay pages
# that the checkout flow depends on.
try:
    from sincor2.blueprints.rail_pay import rail_pay_bp
    app.register_blueprint(rail_pay_bp)
    RAIL_MOUNT_STATUS['rail_pay'] = True
except Exception:
    logger.exception('[RAIL-PAY] rail_pay_bp failed to mount — '
                     '/rail/pay/<id> pages are NOT served')


@app.after_request
def _expose_rail_mount_status(response):
    """Attach the rail mount status to the /health payload.

    /health itself is served by sincor2.mvp_blueprints.health; this hook,
    registered where the rail mounts happen, adds a 'rail' section so an
    unmounted rail is visible in the health surface, not only in logs.
    """
    if request.path == '/health' and response.is_json:
        try:
            payload = json.loads(response.get_data(as_text=True))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            payload['rail'] = {
                'mounted': all(RAIL_MOUNT_STATUS.values()),
                **RAIL_MOUNT_STATUS,
            }
            response.set_data(json.dumps(payload))
    return response

pdf_guides_dir = str(data_dir() / 'files' / 'guides')
try:
    pdf_generator = get_pdf_generator(pdf_guides_dir)
except Exception as e:
    logger.warning('[PDF] %s', e)
    pdf_generator = None

try:
    email_sender = get_email_sender()
except Exception as e:
    logger.warning('[EMAIL] %s', e)
    email_sender = None

# One-scheduler consolidation (2026-10-11): exactly ONE BackgroundScheduler for
# the whole app, owned by the central registry in sincor2.scheduler. The 8
# standalone *_scheduler modules are job-function libraries now — they no
# longer create schedulers of their own. Canonical job IDs and defaults live
# in sincor2.scheduler.SCHEDULER_DEFAULTS.
try:
    from sincor2.scheduler import start_central_scheduler, stop_central_scheduler
    import atexit as _atexit_central
    central_scheduler = start_central_scheduler(app)
    if central_scheduler:
        _atexit_central.register(stop_central_scheduler)
        logger.info('[SCHEDULER] central scheduler started')
    else:
        logger.info('[SCHEDULER] central scheduler not started (no jobs enabled)')
except Exception as e:
    logger.warning('[SCHEDULER] %s', e)
    central_scheduler = None

try:
    from sincor2.x402_payments import init_x402_db
    init_x402_db()
    X402_AVAILABLE = True
except Exception as e:
    logger.warning('[X402] %s', e)
    X402_AVAILABLE = False

try:
    # Real /api/polyclaw/status view — registered here, ahead of the legacy
    # route defined later, so Flask's first-registered rule lets the real
    # implementation win. The trading loop itself now runs as the
    # "polyclaw_scan" job on the central scheduler above; this is route-only.
    # Live-mode CLOB warm-up stays async so it never blocks gunicorn boot
    # (dry-run default: no-op). Live/dry-run posture and private-key env
    # handling are untouched by the one-scheduler consolidation.
    from sincor2.polyclaw_scheduler import register_polyclaw_status_route, maybe_bootstrap_live_client
    register_polyclaw_status_route(app)
    maybe_bootstrap_live_client()
except Exception as e:
    logger.warning('[POLYCLAW] %s', e)


@app.before_request
def log_request():
    g.start_time = time.time()
    if request.headers.get('X-Forwarded-Proto', 'https') == 'http':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)
    if request.path not in ('/health', '/favicon.ico'):
        logger.info('%s %s', request.method, request.path)


@app.after_request
def apply_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    elapsed = time.time() - getattr(g, 'start_time', time.time())
    if request.path not in ('/health', '/favicon.ico'):
        logger.info('%s %s -> %s (%.3fs)', request.method, request.path, response.status_code, elapsed)
    return response


def validate_email(email):
    if not email or not isinstance(email, str):
        return None
    email = email.strip().lower()[:254]
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return email if re.match(pattern, email) else None


def validate_wallet(wallet):
    if not wallet or not isinstance(wallet, str):
        return None
    wallet = wallet.strip()
    return wallet if re.match(r'^0x[a-fA-F0-9]{40}$', wallet) else None


def sanitize_string(value, max_length=200):
    if not value or not isinstance(value, str):
        return ''
    value = value.strip()[:max_length]
    value = re.sub(r'[\x00-\x1f\x7f]', '', value)
    return value


DB_PATH = str(migrate_legacy_orders_db())


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        _ensure_customer_username_schema(g.db)
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute('''CREATE TABLE IF NOT EXISTS customer_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id TEXT UNIQUE NOT NULL,
        email TEXT NOT NULL,
        first_name TEXT,
        last_name TEXT,
        company_name TEXT,
        industry TEXT,
        team_size TEXT,
        primary_use_case TEXT,
        growth_challenge TEXT,
        revenue_target TEXT,
        consent_given INTEGER DEFAULT 0,
        consent_timestamp TEXT,
        ip_hash TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        username TEXT
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT UNIQUE NOT NULL,
        paypal_order_id TEXT,
        customer_email TEXT NOT NULL,
        product_name TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT DEFAULT 'USD',
        payment_status TEXT DEFAULT 'pending',
        delivery_status TEXT DEFAULT 'pending',
        delivery_url TEXT,
        order_type TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        metadata TEXT
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS stripe_webhook_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT,
        received_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'claimed'
    )''')
    db.commit()
    db.close()
    logger.info('[DB] ready at %s', DB_PATH)


# --- B3: Stripe webhook event idempotency --------------------------------------
# Every received Stripe event ID is persisted with a UNIQUE constraint BEFORE
# processing (insert-or-ignore claim). Duplicate deliveries are acknowledged
# without re-fulfillment, so fulfillment is idempotent keyed on event ID.
# Stripe is legacy/not-supported per founder decision D3 — this makes the
# webhook path safe IF the integration is ever enabled.
STRIPE_EVENT_CLAIM_TTL_SEC = 300  # reclaim window for crashed (claimed, never processed) events


def _ensure_stripe_event_schema(db) -> None:
    db.execute('''CREATE TABLE IF NOT EXISTS stripe_webhook_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT,
        received_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'claimed'
    )''')


def claim_stripe_webhook_event(event_id: str, event_type: str = "unknown") -> str:
    """Atomically claim a Stripe webhook event for processing.

    Insert-or-ignore on the PRIMARY KEY means exactly one concurrent delivery
    wins the claim. Returns:
      'claimed'             — first delivery; caller owns processing.
      'reclaimed'           — stale 'claimed' row from a crashed attempt;
                              caller owns processing.
      'duplicate_processed' — already fulfilled; ack without re-processing.
      'duplicate_inflight'  — claimed moments ago by a concurrent delivery;
                              ack without re-processing.
    """
    event_id = (event_id or "").strip()
    if not event_id:
        return "missing"
    db = get_db()
    db.execute("PRAGMA busy_timeout=5000")
    _ensure_stripe_event_schema(db)
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        "INSERT OR IGNORE INTO stripe_webhook_events "
        "(event_id, event_type, received_at, status) VALUES (?, ?, ?, 'claimed')",
        (event_id, event_type, now),
    )
    db.commit()
    if cur.rowcount == 1:
        return "claimed"
    row = db.execute(
        "SELECT status, received_at FROM stripe_webhook_events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    if row is None:
        return "claimed"  # defensive; cannot happen without a deleter
    if row["status"] == "processed":
        return "duplicate_processed"
    # Claimed but not processed: a concurrent delivery in flight, or a crashed
    # attempt. Reclaim only after the TTL so concurrent duplicates are dropped.
    try:
        claimed_at = datetime.fromisoformat(
            (row["received_at"] or "").replace("Z", "+00:00")
        )
        age_sec = (datetime.now(timezone.utc) - claimed_at).total_seconds()
    except (ValueError, TypeError):
        age_sec = 0
    if age_sec < STRIPE_EVENT_CLAIM_TTL_SEC:
        return "duplicate_inflight"
    cur = db.execute(
        "UPDATE stripe_webhook_events SET status='claimed', received_at=?, "
        "event_type=? WHERE event_id=? AND status='claimed'",
        (now, event_type, event_id),
    )
    db.commit()
    return "reclaimed" if cur.rowcount == 1 else "duplicate_inflight"


def mark_stripe_webhook_event_processed(event_id: str) -> None:
    """Mark a claimed Stripe event as processed (fulfilled)."""
    db = get_db()
    _ensure_stripe_event_schema(db)
    db.execute(
        "UPDATE stripe_webhook_events SET status='processed' WHERE event_id=?",
        ((event_id or "").strip(),),
    )
    db.commit()


def release_stripe_webhook_event_claim(event_id: str) -> None:
    """Release a 'claimed' event after a processing failure.

    The failed attempt is over, so no concurrent processing is possible;
    deleting the claim lets a redelivery claim and retry instead of being
    acked as a duplicate.
    """
    db = get_db()
    _ensure_stripe_event_schema(db)
    db.execute(
        "DELETE FROM stripe_webhook_events WHERE event_id=? AND status='claimed'",
        ((event_id or "").strip(),),
    )
    db.commit()


init_db()

_PAID_STATUSES = frozenset({'completed', 'paid', 'verified'})


def _row_to_dict(row):
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def _confirmed_paid_order(email: str):
    email = (email or '').strip()
    if not email:
        return None
    db = get_db()
    placeholders = ','.join('?' * len(_PAID_STATUSES))
    statuses = tuple(_PAID_STATUSES)
    try:
        row = db.execute(
            f"""SELECT * FROM orders
                WHERE customer_email=? AND lower(coalesce(payment_status,'')) IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1""",
            (email, *statuses),
        ).fetchone()
        if row:
            return _row_to_dict(row)
    except Exception as exc:
        logger.warning('[DASHBOARD] orders lookup failed: %s', exc)
    return None


PRODUCT_CATALOG = {
    'Starter': {'type': 'subscription', 'agents': 10, 'features': ['Scout', 'Synthesizer', 'Builder']},
    'Professional': {'type': 'subscription', 'agents': 25, 'features': ['All Starter features']},
    'Enterprise': {'type': 'subscription', 'agents': 42, 'features': ['All 42 AI Agents']},
    'Operator': {'type': 'internal', 'agents': 42, 'features': ['Full swarm']},
}


def _admin_username() -> str:
    return (os.environ.get('ADMIN_USERNAME') or '').strip()


def _admin_password() -> str:
    return os.environ.get('ADMIN_PASSWORD') or ''


ADMIN_USERNAME = _admin_username()
ADMIN_PASSWORD = _admin_password()


def _ct_eq(left: str, right: str) -> bool:
    import hmac
    a = (left or '').encode('utf-8')
    b = (right or '').encode('utf-8')
    if len(a) != len(b):
        hmac.compare_digest(a, a)
        return False
    return hmac.compare_digest(a, b)


def _admin_credentials_match(username: str, password: str) -> bool:
    expected_user = _admin_username()
    expected_pass = _admin_password()
    if not expected_user or not expected_pass:
        return False
    user_ok = any(_ct_eq(c, expected_user.lower()) for c in _identity_candidates(username))
    pass_ok = _ct_eq(password or '', expected_pass)
    return user_ok and pass_ok


def _safe_next_url(raw: str, default: str = '/admin') -> str:
    value = (raw or '').strip() or default
    if not value.startswith('/') or value.startswith('//'):
        return default
    return value


def _check_admin_token(req):
    from flask_jwt_extended import decode_token
    auth = req.headers.get('Authorization', '')
    token = ''
    if auth.startswith('Bearer '):
        token = auth[7:]
    elif getattr(req, 'cookies', None):
        token = req.cookies.get('access_token', '')
    if not token or not _admin_username():
        return False
    try:
        decoded = decode_token(token)
        return _ct_eq(str(decoded.get('sub') or '').lower(), _admin_username().lower())
    except Exception:
        return False


def _check_admin_key(req) -> bool:
    import hmac
    expected = _admin_password()
    if not expected:
        return False
    key = req.headers.get('X-Admin-Key', '')
    if not key:
        data = req.get_json(silent=True) or {}
        key = str(data.get('admin_key', ''))
    return bool(key) and hmac.compare_digest(str(key), str(expected))


def _is_admin_session() -> bool:
    if session.get('is_admin') and _ct_eq(str(session.get('admin_username') or '').lower(), _admin_username().lower()):
        return True
    return _check_admin_token(request)


def _require_admin(req):
    if _check_admin_token(req) or _check_admin_key(req) or (req is request and _is_admin_session()):
        return None
    return jsonify({'error': 'Unauthorized'}), 401


def _admin_cookie_response(username: str, redirect_url: str = '/admin'):
    identity = _admin_username() or username
    session['is_admin'] = True
    session['admin_username'] = identity
    session['username'] = identity
    session['user_email'] = identity
    access_token = create_access_token(identity=identity, additional_claims={'role': 'admin'})
    resp = make_response(redirect(_safe_next_url(redirect_url, '/admin')))
    resp.set_cookie(
        'access_token', access_token, max_age=28800,
        **secure_cookie_kwargs(),
    )
    return resp


# Unified console pages (dashboard overhaul): /agents, /tasks, /pool.
# Registered BEFORE the mvp blueprints on purpose: the wardrobe blueprint
# also defines an (orphan, unlinked) /agents index, and the user-facing
# console fleet page must win that URL. Wardrobe's /agents/<id> detail
# pages are unaffected (distinct route rule).
from sincor2.blueprints.console import console_bp as _console_bp

app.register_blueprint(_console_bp)

# DeFi product-arm catalog: /catalog + GET /api/defi/catalog (read-only).
from sincor2.blueprints.defi_catalog import defi_catalog_bp as _defi_catalog_bp

app.register_blueprint(_defi_catalog_bp)

# OBS-01 Agent Vitals (BETA): /obs/vitals + GET /api/obs/vitals. Read-only,
# customer-session gated, deliberately unlinked from all public nav/pricing/
# sitemaps (direct URL only) until the beta walkthrough passes. No money
# path, no auth changes, no ledger writes.
from sincor2.blueprints.vitals import vitals_bp as _vitals_bp

app.register_blueprint(_vitals_bp)

# /command-center page on production (GET only). Same login gate as the other
# operator surfaces on this app (see /dashboard, /operator). The full
# command-center API blueprint is deliberately NOT mounted here: its
# killswitch POST is unauthenticated upstream and must not be exposed on
# production until auth lands. The page renders; its live sections show
# their own "unavailable" states where the APIs are absent.
@app.get("/command-center")
def _console_command_center_page():
    if not (_is_admin_session() or session.get("user_email")):
        return redirect("/login?next=/command-center")
    return render_template("command_center.html", nav_active="command")

from sincor2.mvp_blueprints import register_mvp_blueprints
register_mvp_blueprints(app)


@app.errorhandler(404)
def not_found(error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found', 'status': 404}), 404
    return render_template('error.html', code=404, title='Page Not Found',
                           message="The page you're looking for doesn't exist or has been moved."), 404


@app.errorhandler(500)
def server_error(error):
    logger.error('[500] %s: %s', request.path, error)
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error', 'status': 500}), 500
    return render_template('error.html', code=500, title='Server Error',
                           message='Something went wrong on our end. Please try again later.'), 500


@app.errorhandler(413)
def request_too_large(error):
    return jsonify({'error': 'Request too large (max 1MB)'}), 413


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development' and not os.environ.get('RAILWAY_ENVIRONMENT')
    app.run(host='0.0.0.0', port=port, debug=debug)
