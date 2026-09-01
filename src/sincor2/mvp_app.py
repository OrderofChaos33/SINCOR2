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
from datetime import datetime, timedelta
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

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('sincor2')


def _fiat_payments_unavailable() -> bool:
    return False


def _env_first(*keys: str, default: str = '') -> str:
    """Return the first non-empty environment variable from *keys."""
    for key in keys:
        val = (os.environ.get(key) or '').strip()
        if val:
            return val
    return default


# Load environment variables
load_dotenv()

# Initialize Flask app
# Get the project root (up 2 directories from sincor2 to root)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
template_dir = os.path.join(project_root, 'templates')
static_dir = os.path.join(project_root, 'static')
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

# Railway / reverse-proxy: correct scheme + host for OAuth redirect URIs
if os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('TRUST_PROXY', '').lower() in ('1', 'true', 'yes'):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config['PREFERRED_URL_SCHEME'] = 'https'


@app.context_processor
def inject_social_links():
    from sincor2.social_links import SOCIAL_LINKS
    return {'social_links': SOCIAL_LINKS}


@app.context_processor
def inject_onchain_addresses():
    """Live SINC / AXM / treasury — templates must not hardcode retired contracts."""
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


# Configure JWT � MUST be set in Railway secrets for production
jwt_secret = os.environ.get('JWT_SECRET_KEY') or os.environ.get('JWT_SECRET')
if not jwt_secret:
    if os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('ENVIRONMENT') == 'production':
        logger.critical('[JWT] JWT_SECRET_KEY not set in production! Generating random secret (tokens won\'t survive restarts)')
    jwt_secret = os.urandom(32).hex()
app.config['JWT_SECRET_KEY'] = jwt_secret
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=8)  # Reduced from 24h
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max request size
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY') or jwt_secret  # For session/CSRF
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = bool(os.environ.get('RAILWAY_ENVIRONMENT'))
jwt = JWTManager(app)

# A2A JSON-RPC + discovery. Production gunicorn entry is this module, so the
# router must live here (app.py already registers it for the test client).
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
    logger.info('[A2A] Inbound register + marketplace registered on mvp_app')
except Exception as _a2a_in_exc:
    logger.warning('[A2A] Inbound not registered: %s', _a2a_in_exc)

try:
    from sincor2.task_queue import register_flask_routes as _register_queue_routes
    _register_queue_routes(app)
    logger.info('[QUEUE] /api/tasks poll routes registered')
except Exception as _q_exc:
    logger.warning('[QUEUE] poll routes not registered: %s', _q_exc)

# OAuth (Google + GitHub)
oauth = None
OAUTH_REDIRECT_BASE = _env_first('OAUTH_REDIRECT_BASE_URL', 'PUBLIC_BASE_URL', 'SITE_URL', default='').rstrip('/')
OAUTH_ERROR_MESSAGES = {
    'oauth_unavailable': 'Social login is temporarily unavailable. Please use email signup.',
    'oauth_failed': 'Social login failed. Please try again or use email signup.',
    'no_email': 'We could not get your email from that provider. Try another method.',
}


def _oauth_redirect_uri(endpoint: str) -> str:
    """Build absolute OAuth callback URL (Railway-safe)."""
    if OAUTH_REDIRECT_BASE:
        return f'{OAUTH_REDIRECT_BASE}{url_for(endpoint)}'
    return url_for(endpoint, _external=True)


def _oauth_provider_ready(name: str) -> bool:
    return bool(oauth and hasattr(oauth, name))


def _auth_cookie_response(email: str, redirect_url: str = '/dashboard'):
    """Set session + JWT cookie and redirect."""
    session['user_email'] = email
    if not session.get('username'):
        session['username'] = (email or '').split('@')[0]
    access_token = create_access_token(identity=email)
    sep = '&' if '?' in redirect_url else '?'
    if 'email=' not in redirect_url:
        redirect_url = f'{redirect_url}{sep}email={email}'
    resp = make_response(redirect(redirect_url))
    resp.set_cookie(
        'access_token', access_token, httponly=True,
        secure=bool(os.environ.get('RAILWAY_ENVIRONMENT')),
        samesite='Lax', max_age=28800,
    )
    return resp


def _session_email() -> str:
    """Resolve logged-in customer email from session, JWT cookie, or query."""
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
    """True if email has a profile, order, or platform subscription."""
    if not email:
        return False
    db = get_db()
    if db.execute('SELECT 1 FROM customer_profiles WHERE email=?', (email,)).fetchone():
        return True
    if db.execute(
        "SELECT 1 FROM orders WHERE customer_email=? LIMIT 1", (email,)
    ).fetchone():
        return True
    try:
        if db.execute(
            "SELECT 1 FROM platform_subscriptions WHERE email=? LIMIT 1", (email,)
        ).fetchone():
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
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_customer_profiles_username '
            'ON customer_profiles(username)'
        )
    except Exception:
        logger.debug('[DB] username unique index not applied')


def _resolve_customer(identifier: str):
    """Return {email, username} for a customer looked up by email or username."""
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
    """Persist signup lead into customer_profiles."""
    import secrets
    email = email.strip().lower()
    parts = (name or '').strip().split(None, 1)
    first = parts[0] if parts else email.split('@')[0]
    last = parts[1] if len(parts) > 1 else ''
    now = datetime.utcnow().isoformat()
    db = get_db()
    _ensure_customer_username_schema(db)
    existing = db.execute(
        'SELECT profile_id FROM customer_profiles WHERE email=?', (email,)
    ).fetchone()
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
    row = db.execute(
        'SELECT username FROM customer_profiles WHERE email=?', (email,)
    ).fetchone()
    current = (row['username'] if row else '') or ''
    if not current:
        uname = _allocate_username(db, first, email)
        db.execute(
            'UPDATE customer_profiles SET username=? WHERE email=?',
            (uname, email),
        )
    db.commit()


def _oauth_finish_login(email: str, name: str, provider: str):
    """Create session + JWT cookie after successful OAuth."""
    session['user_name'] = name or email.split('@')[0]
    _upsert_lead(email, name or email.split('@')[0])
    logger.info('[OAUTH] %s login: %s', provider, email)
    return _auth_cookie_response(email)


def _google_userinfo(token: dict):
    """Extract Google profile from token or userinfo endpoint."""
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


def _github_primary_email() -> str:
    """Fetch verified primary email when /user omits it."""
    resp = oauth.github.get('user/emails')
    emails = resp.json() if resp.ok else []
    if not isinstance(emails, list):
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
    else:
        logger.warning('[OAUTH] Google OAuth skipped — set GOOGLE_OAUTH_CLIENT_ID/SECRET')
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
    else:
        logger.warning('[OAUTH] GitHub OAuth skipped — set GITHUB_CLIENT_ID/SECRET')
elif not OAUTH_AVAILABLE:
    logger.warning('[OAUTH] authlib not installed — social login disabled')

# Rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["10000 per day", "1000 per hour"],
    storage_uri="memory://",
)

# Production kill-switches — no accidental on-chain writes or compliance egress
try:
    from sincor2.safety_locks import assert_production_safety
    assert_production_safety()
except Exception as e:
    logger.warning("[SAFETY] Lock check failed: %s", e)

# Platform payments (SINC + AXM) — default billing path
try:
    from sincor2.platform_payments import (
        activate_subscription,
        create_checkout as platform_create_checkout,
        fiat_payments_enabled,
        get_subscription,
        init_platform_payments_db,
        list_plans as platform_list_plans,
        list_subscriptions,
        verify_checkout as platform_verify_checkout,
    )
    init_platform_payments_db()
    PLATFORM_PAYMENTS_AVAILABLE = True
    logger.info("[PAYMENTS] SINC + AXM platform billing active (fiat=%s)", fiat_payments_enabled())
except Exception as e:
    logger.warning(f"[PAYMENTS] Platform payments init failed: {e}")
    PLATFORM_PAYMENTS_AVAILABLE = False
    fiat_payments_enabled = _fiat_payments_unavailable

# Legacy Stripe — only when LEGACY_FIAT_PAYMENTS_ENABLED=true
STRIPE_AVAILABLE = False
stripe_processor = None
if fiat_payments_enabled():
    try:
        from sincor2.stripe_checkout import get_stripe_checkout
        from sincor2.stripe_routes import init_stripe_routes
        STRIPE_AVAILABLE = True
    except (ImportError, ModuleNotFoundError) as e:
        logger.warning(f"[STRIPE] Import error: {e}")
        STRIPE_AVAILABLE = False

    if STRIPE_AVAILABLE:
        stripe_processor = get_stripe_checkout()
        if stripe_processor and stripe_processor.enabled:
            init_stripe_routes(app, stripe_processor)
            logger.info("[APP] Legacy Stripe routes registered")
        else:
            logger.warning("[APP] LEGACY_FIAT_PAYMENTS_ENABLED but Stripe not configured")
else:
    logger.info("[APP] Stripe/PayPal disabled — use /buy with SINC or AXM")
# PDF Generator initialization
pdf_guides_dir = str(data_dir() / 'files' / 'guides')
try:
    pdf_generator = get_pdf_generator(pdf_guides_dir)
    logger.info(f"[PDF] PDF generator initialized for: {pdf_guides_dir}")
except Exception as e:
    logger.warning(f"[PDF] PDF generator initialization failed: {e}")
    pdf_generator = None

# Email Sender initialization
try:
    email_sender = get_email_sender()
    logger.info(f"[EMAIL] Email sender initialized ({email_sender.mode} mode)")
except Exception as e:
    logger.warning(f"[EMAIL] Email sender initialization failed: {e}")
    email_sender = None

# Outreach Scheduler — autonomous lead gen (Yelp + Google Places + Resend)
try:
    from sincor2.outreach_scheduler import start_outreach_scheduler
    import atexit
    outreach_scheduler = start_outreach_scheduler(app)
    if outreach_scheduler:
        from sincor2.outreach_scheduler import stop_outreach_scheduler
        atexit.register(stop_outreach_scheduler)
except Exception as e:
    logger.warning(f"[OUTREACH] Scheduler init failed: {e}")
    outreach_scheduler = None

# Content Agent — autonomous blog/SEO publishing every 48h
try:
    from sincor2.content_scheduler import start_content_scheduler, stop_content_scheduler
    import atexit as _atexit
    content_scheduler = start_content_scheduler(app)
    if content_scheduler:
        _atexit.register(stop_content_scheduler)
        logger.info("[CONTENT] Content agent scheduler started")
except Exception as e:
    logger.warning(f"[CONTENT] Content scheduler init failed: {e}")
    content_scheduler = None

# Launch ops — content drafts → /launch/review (hook keeper is local Windows task)
try:
    from sincor2.launch_ops_scheduler import start_launch_ops_scheduler, stop_launch_ops_scheduler
    import atexit as _atexit_launch
    launch_ops_scheduler = start_launch_ops_scheduler(app)
    if launch_ops_scheduler:
        _atexit_launch.register(stop_launch_ops_scheduler)
        logger.info("[LAUNCH_OPS] Launch content scheduler started")
except Exception as e:
    logger.warning(f"[LAUNCH_OPS] Scheduler init failed: {e}")
    launch_ops_scheduler = None

# Daily email — ~5 min approval reminder for /launch/review
try:
    from sincor2.launch_review_notify import (
        start_review_reminder_scheduler,
        stop_review_reminder_scheduler,
    )
    import atexit as _atexit_review
    review_reminder_scheduler = start_review_reminder_scheduler(app)
    if review_reminder_scheduler:
        _atexit_review.register(stop_review_reminder_scheduler)
        logger.info("[REVIEW_REMINDER] Daily approval email scheduler started")
except Exception as e:
    logger.warning(f"[REVIEW_REMINDER] Scheduler init failed: {e}")
    review_reminder_scheduler = None

# Daily email — partner outreach due list for /launch/partners
try:
    from sincor2.partner_outreach_notify import (
        start_partner_reminder_scheduler,
        stop_partner_reminder_scheduler,
    )
    import atexit as _atexit_partner
    partner_reminder_scheduler = start_partner_reminder_scheduler(app)
    if partner_reminder_scheduler:
        _atexit_partner.register(stop_partner_reminder_scheduler)
        logger.info("[PARTNER_REMINDER] Daily partner outreach email scheduler started")
except Exception as e:
    logger.warning(f"[PARTNER_REMINDER] Scheduler init failed: {e}")
    partner_reminder_scheduler = None

# Daily ops — read-only chain/revenue/wallet monitoring
try:
    from sincor2.daily_ops_scheduler import start_daily_ops_scheduler, stop_daily_ops_scheduler
    import atexit as _atexit_daily
    daily_ops_scheduler = start_daily_ops_scheduler(app)
    if daily_ops_scheduler:
        _atexit_daily.register(stop_daily_ops_scheduler)
        logger.info("[DAILY_OPS] Daily ops scheduler started")
except (Exception, SystemExit) as e:
    logger.warning(f"[DAILY_OPS] Scheduler init failed: {e}")
    daily_ops_scheduler = None

# Compliance monitor — marketing/env audit
try:
    from sincor2.compliance_monitor import start_compliance_scheduler
    import atexit as _atexit_compliance
    compliance_scheduler = start_compliance_scheduler()
    if compliance_scheduler:
        def _stop_compliance():
            if compliance_scheduler.running:
                compliance_scheduler.shutdown(wait=False)
        _atexit_compliance.register(_stop_compliance)
        logger.info("[COMPLIANCE] Compliance monitor scheduler started")
except Exception as e:
    logger.warning(f"[COMPLIANCE] Scheduler init failed: {e}")
    compliance_scheduler = None

try:
    from sincor2.subscription_scheduler import start_subscription_scheduler, stop_subscription_scheduler
    import atexit as _atexit_sub
    subscription_scheduler = start_subscription_scheduler(app)
    if subscription_scheduler:
        _atexit_sub.register(stop_subscription_scheduler)
except Exception as e:
    logger.warning(f"[SUBSCRIPTION] Scheduler init failed: {e}")
    subscription_scheduler = None

try:
    from sincor2.x402_payments import init_x402_db
    init_x402_db()
    X402_AVAILABLE = True
except Exception as e:
    logger.warning(f"[X402] Init failed: {e}")
    X402_AVAILABLE = False

# Polyclaw Autonomous Trading Agent — scans Polymarket for arbitrage, executes 24/7
try:
    from sincor2.polyclaw_scheduler import start_polyclaw_scheduler, stop_polyclaw_scheduler
    import atexit as _atexit_poly
    polyclaw_scheduler = start_polyclaw_scheduler(app)
    if polyclaw_scheduler:
        _atexit_poly.register(stop_polyclaw_scheduler)
        logger.info("[POLYCLAW] Polyclaw trading agent scheduler started")
except Exception as e:
    logger.warning(f"[POLYCLAW] Polyclaw scheduler init failed: {e}")
    polyclaw_scheduler = None

# DeFi Execution Engine — Arbitrage + Liquidations + Flash Loans + HFQ
try:
    import threading
    defi_engine_script = Path(__file__).parent.parent.parent / ".." / ".openclaw" / "workspace" / "defi_execution_engine.py"
    if defi_engine_script.exists():
        logger.info("[DEFI] DeFi Execution Engine: INITIALIZING")
        # Run async to avoid blocking startup
        defi_thread = threading.Thread(
            target=lambda: __import__('subprocess').run(
                [__import__('sys').executable, str(defi_engine_script)],
                daemon=True
            ),
            daemon=True
        )
        defi_thread.start()
        logger.info("[DEFI] DeFi Execution Engine: LIVE (Arbitrage + Liquidations + Flash Loans + HFQ)")
except Exception as e:
    logger.warning(f"[DEFI] DeFi engine init failed: {e}")


# ============================================================================
# SECURITY MIDDLEWARE
# ============================================================================

@app.before_request
def log_request():
    """Log incoming requests, record start time, enforce HTTPS in production."""
    g.start_time = time.time()
    # Enforce HTTPS in production (Railway sets X-Forwarded-Proto)
    if request.headers.get('X-Forwarded-Proto', 'https') == 'http':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)
    if request.path not in ('/health', '/favicon.ico'):
        logger.info(f"{request.method} {request.path}")


@app.after_request
def apply_security_headers(response):
    """Apply security headers and log response timing."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['X-Permitted-Cross-Domain-Policies'] = 'none'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net https://js.stripe.com 'unsafe-inline'; "
        "style-src 'self' https://cdn.jsdelivr.net https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net; "
        "img-src 'self' https: data:; "
        "frame-src 'self' https://js.stripe.com; "
        "connect-src 'self' https://api.stripe.com"
    )

    # Log response timing
    elapsed = time.time() - getattr(g, 'start_time', time.time())
    if request.path not in ('/health', '/favicon.ico'):
        logger.info(f"{request.method} {request.path} ? {response.status_code} ({elapsed:.3f}s)")

    return response


# ============================================================================
# INPUT VALIDATION HELPERS
# ============================================================================

def validate_email(email):
    """Validate email format. Returns sanitized email or None."""
    if not email or not isinstance(email, str):
        return None
    email = email.strip().lower()[:254]  # RFC 5321 limit
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return email if re.match(pattern, email) else None


def validate_wallet(wallet):
    """Validate Ethereum wallet address."""
    if not wallet or not isinstance(wallet, str):
        return None
    wallet = wallet.strip()
    return wallet if re.match(r'^0x[a-fA-F0-9]{40}$', wallet) else None


def sanitize_string(value, max_length=200):
    """Sanitize user input string: strip, limit length, remove control chars."""
    if not value or not isinstance(value, str):
        return ''
    value = value.strip()[:max_length]
    value = re.sub(r'[\x00-\x1f\x7f]', '', value)  # Remove control characters
    return value

# ============================================================================
# DATABASE SETUP (SQLite for orders)
# ============================================================================

DB_PATH = str(migrate_legacy_orders_db())


def get_db():
    """Get database connection for current request."""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        _ensure_customer_username_schema(g.db)
    return g.db


@app.teardown_appcontext
def close_db(exception):
    """Close database connection at end of request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """Initialize the orders database."""
    db = sqlite3.connect(DB_PATH)
    # Customer profiles — encrypted at rest, GDPR/CCPA compliant
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
    db.commit()
    db.close()
    logger.info(f"[DB] Orders database ready at {DB_PATH}")


# Initialize DB on import
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
    """Latest order with a confirmed payment, or None. Never invents a row."""
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
    try:
        row = db.execute(
            """SELECT email, plan_id, status, created_at, tx_hash
               FROM platform_subscriptions
               WHERE email=? AND lower(coalesce(status,'')) IN ('active','paid','verified','completed')
               ORDER BY created_at DESC LIMIT 1""",
            (email,),
        ).fetchone()
        if row:
            rec = _row_to_dict(row)
            plan_id = (rec.get('plan_id') or 'starter').lower()
            try:
                from sincor2.platform_payments import PLATFORM_PLANS
                product = PLATFORM_PLANS.get(plan_id, {}).get('product_name') or plan_id.title()
            except Exception:
                product = plan_id.title()
            return {
                'order_id': rec.get('tx_hash') or rec.get('email'),
                'customer_email': rec.get('email'),
                'product_name': product,
                'payment_status': rec.get('status') or 'completed',
                'created_at': rec.get('created_at') or '',
            }
    except Exception as exc:
        logger.debug('[DASHBOARD] platform_subscriptions lookup skipped: %s', exc)
    return None


# Product catalog - maps product names to types and deliverables
PRODUCT_CATALOG = {
    'Starter': {
        'type': 'subscription', 'agents': 10,
        'features': ['Scout', 'Synthesizer', 'Builder', 'Basic lead gen', 'Email support']
    },
    'Professional': {
        'type': 'subscription', 'agents': 25,
        'features': ['All Starter features', 'Advanced lead gen', 'Content creation', 'Priority support', 'Custom workflows', '1-on-1 onboarding']
    },
    'Enterprise': {
        'type': 'subscription', 'agents': 42,
        'features': ['All 42 AI Agents', 'Dedicated success manager', '24/7 priority support', 'White-label options', 'Custom integrations']
    },
    'Operator': {
        'type': 'internal', 'agents': 42,
        'features': ['Full swarm', 'Command center', 'Training vault', 'Operator console']
    },
    'Business Intelligence Report': {
        'type': 'bi_report', 'pages': 20, 'delivery_days': 2,
        'sections': ['Executive Summary', 'Revenue Analysis', 'Growth Opportunities', 'Competitive Positioning', 'Recommendations']
    },
    'Competitive Analysis': {
        'type': 'bi_report', 'pages': 15, 'delivery_days': 2,
        'sections': ['SWOT Analysis', 'Pricing Strategy', 'Market Positioning', 'Gap Analysis', 'Recommendations']
    },
    '90-Day Growth Forecast': {
        'type': 'bi_report', 'pages': 25, 'delivery_days': 2,
        'sections': ['Revenue Projections', 'Growth Roadmap', 'Resource Plan', 'Risk Assessment', 'KPI Framework']
    },
    'Content Package - Micro': {'type': 'content', 'pieces': '1-5', 'delivery_days': 3},
    'Content Package - Standard': {'type': 'content', 'pieces': '10-20', 'delivery_days': 7},
    'Content Package - Professional': {'type': 'content', 'pieces': '30-50', 'delivery_days': 14},
    'Content Package - Enterprise': {'type': 'content', 'pieces': '100+', 'delivery_days': 21},
}


# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================

# Admin credentials — must be set via environment variables in production.
# Read live from the environment so Railway vars (and tests) are not frozen at import.


def _admin_username() -> str:
    return (os.environ.get('ADMIN_USERNAME') or '').strip()


def _admin_password() -> str:
    return os.environ.get('ADMIN_PASSWORD') or ''


ADMIN_USERNAME = _admin_username()
ADMIN_PASSWORD = _admin_password()

if not _admin_username() or not _admin_password():
    logger.warning('[AUTH] ADMIN_USERNAME or ADMIN_PASSWORD not set — admin login disabled')


def _ct_eq(left: str, right: str) -> bool:
    """Constant-time string compare that still returns False on length mismatch."""
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
    user_ok = any(
        _ct_eq(c, expected_user.lower()) for c in _identity_candidates(username)
    )
    pass_ok = _ct_eq(password or '', expected_pass)
    return user_ok and pass_ok


def _safe_next_url(raw: str, default: str = '/admin') -> str:
    value = (raw or '').strip() or default
    if not value.startswith('/') or value.startswith('//'):
        return default
    return value


def _check_admin_token(req):
    """Return True if the request carries a valid admin JWT (header or cookie)."""
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
    """Return True if request carries ADMIN_PASSWORD via header or JSON body."""
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
    """Return None if authorized, else (response, status_code)."""
    if _check_admin_token(req) or _check_admin_key(req) or (req is request and _is_admin_session()):
        return None
    return jsonify({'error': 'Unauthorized'}), 401


def _admin_cookie_response(username: str, redirect_url: str = '/admin'):
    """Set operator session + JWT cookie and redirect to the command console."""
    identity = _admin_username() or username
    session['is_admin'] = True
    session['admin_username'] = identity
    session['username'] = identity
    session['user_email'] = identity
    access_token = create_access_token(identity=identity, additional_claims={'role': 'admin'})
    resp = make_response(redirect(_safe_next_url(redirect_url, '/admin')))
    resp.set_cookie(
        'access_token', access_token, httponly=True,
        secure=bool(os.environ.get('RAILWAY_ENVIRONMENT')),
        samesite='Lax', max_age=28800,
    )
    return resp




# ============================================================================
# ROUTE BLUEPRINTS (extracted from this file)
# ============================================================================
from sincor2.mvp_blueprints import register_mvp_blueprints
register_mvp_blueprints(app)

# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors with a styled page."""
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found', 'status': 404}), 404
    return render_template('error.html', code=404, title='Page Not Found',
                           message="The page you're looking for doesn't exist or has been moved."), 404


@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    logger.error(f"[500] Internal server error on {request.path}: {error}")
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error', 'status': 500}), 500
    return render_template('error.html', code=500, title='Server Error',
                           message="Something went wrong on our end. Please try again later."), 500


@app.errorhandler(413)
def request_too_large(error):
    """Handle oversized request payloads."""
    return jsonify({'error': 'Request too large (max 1MB)'}), 413



# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Never run debug in production
    debug = os.environ.get('FLASK_ENV') == 'development' and not os.environ.get('RAILWAY_ENVIRONMENT')
    app.run(host='0.0.0.0', port=port, debug=debug)  # nosec B104 — required for Railway deployment
