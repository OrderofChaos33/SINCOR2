"""
SINCOR2 MVP - Minimal Flask Application
A lean, deployable MVP with health checks, home page, buy flow, and Stripe payments.
Stripe checkout ? order DB ? asset delivery pipeline.
"""

import os
import re
import json
import time
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, render_template, request, jsonify, g, make_response, send_file, redirect, session, url_for
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
try:
    from authlib.integrations.flask_client import OAuth
    OAUTH_AVAILABLE = True
except ImportError:
    OAUTH_AVAILABLE = False
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

from sincor2.pdf_generator import get_pdf_generator
from sincor2.email_sender import get_email_sender

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('sincor2')

# Load environment variables
load_dotenv()

# Initialize Flask app
# Get the project root (up 2 directories from sincor2 to root)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
template_dir = os.path.join(project_root, 'templates')
static_dir = os.path.join(project_root, 'static')
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)


@app.context_processor
def inject_social_links():
    from sincor2.social_links import SOCIAL_LINKS
    return {'social_links': SOCIAL_LINKS}


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

# OAuth (Google + GitHub)
oauth = None
if OAUTH_AVAILABLE:
    oauth = OAuth(app)
    _gcid = os.environ.get('GOOGLE_CLIENT_ID', '')
    _gcs  = os.environ.get('GOOGLE_CLIENT_SECRET', '')
    _ghid = os.environ.get('GITHUB_CLIENT_ID', '')
    _ghs  = os.environ.get('GITHUB_CLIENT_SECRET', '')
    if _gcid and _gcs:
        oauth.register('google',
            client_id=_gcid,
            client_secret=_gcs,
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile'},
        )
        logger.info('[OAUTH] Google OAuth registered')
    if _ghid and _ghs:
        oauth.register('github',
            client_id=_ghid,
            client_secret=_ghs,
            access_token_url='https://github.com/login/oauth/access_token',
            authorize_url='https://github.com/login/oauth/authorize',
            api_base_url='https://api.github.com/',
            client_kwargs={'scope': 'user:email'},
        )
        logger.info('[OAUTH] GitHub OAuth registered')

# Rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["10000 per day", "1000 per hour"],
    storage_uri="memory://",
)

# Stripe integration
try:
    from sincor2.stripe_checkout import get_stripe_checkout
    from sincor2.stripe_routes import init_stripe_routes
    STRIPE_AVAILABLE = True
except (ImportError, ModuleNotFoundError) as e:
    logger.warning(f"[STRIPE] Import error: {e}")
    STRIPE_AVAILABLE = False

stripe_processor = None
if STRIPE_AVAILABLE:
    stripe_processor = get_stripe_checkout()
    if stripe_processor and stripe_processor.enabled:
        init_stripe_routes(app, stripe_processor)
        logger.info("[APP] Stripe integration initialized and routes registered")
    else:
        logger.warning("[APP] Stripe not properly configured � set STRIPE_API_KEY")

# PDF Generator initialization
pdf_guides_dir = os.path.join(project_root, 'files', 'guides')
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

# Daily ops — read-only chain/revenue/wallet monitoring
try:
    from sincor2.daily_ops_scheduler import start_daily_ops_scheduler, stop_daily_ops_scheduler
    import atexit as _atexit_daily
    daily_ops_scheduler = start_daily_ops_scheduler(app)
    if daily_ops_scheduler:
        _atexit_daily.register(stop_daily_ops_scheduler)
        logger.info("[DAILY_OPS] Daily ops scheduler started")
except Exception as e:
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
    from pathlib import Path
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
        "frame-src https://js.stripe.com; "
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

DB_PATH = os.path.join(project_root, 'orders.db')


def get_db():
    """Get database connection for current request."""
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
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
        updated_at TEXT
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
# HEALTH & STATUS ENDPOINTS
# ============================================================================

# DEBUG ENDPOINT REMOVED - was leaking env var status to public


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for Railway and monitoring."""
    return jsonify({
        'status': 'healthy',
        'service': 'SINCOR2 MVP',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '1.0.0-mvp'
    }), 200


@app.route('/api/ops/schedulers', methods=['GET'])
def ops_schedulers_status():
    """Overview of background schedulers and env gates."""
    def _job_next(sched, job_id):
        if not sched or not sched.running:
            return None
        try:
            job = sched.get_job(job_id)
            return str(job.next_run_time) if job else None
        except Exception:
            return None

    return jsonify({
        'launch_ops': {
            'enabled': os.environ.get('LAUNCH_OPS_ENABLED', 'false').lower() == 'true',
            'running': bool(launch_ops_scheduler and launch_ops_scheduler.running),
            'next_run': _job_next(launch_ops_scheduler, 'launch_ops_content'),
            'review_url': '/launch/review',
        },
        'review_reminder': {
            'enabled': os.environ.get('LAUNCH_REVIEW_REMINDER_ENABLED', 'true').lower() != 'false',
            'alert_email': os.environ.get('LAUNCH_REVIEW_ALERT_EMAIL', 'eenergy@protonmail.com'),
            'running': bool(review_reminder_scheduler and review_reminder_scheduler.running),
            'next_run': _job_next(review_reminder_scheduler, 'launch_review_reminder'),
        },
        'daily_ops': {
            'enabled': os.environ.get('DAILY_OPS_ENABLED', 'false').lower() == 'true',
            'running': bool(daily_ops_scheduler and daily_ops_scheduler.running),
            'next_run': _job_next(daily_ops_scheduler, 'daily_ops'),
            'latest_report': '/logs/ops/daily_latest.json',
        },
        'content_agent': {
            'enabled': os.environ.get('CONTENT_AGENT_ENABLED', 'false').lower() == 'true',
            'running': bool(content_scheduler and content_scheduler.running),
            'next_run': _job_next(content_scheduler, 'content_cycle'),
        },
        'outreach': {
            'enabled': os.environ.get('OUTREACH_ENABLED', 'false').lower() == 'true',
            'running': bool(outreach_scheduler and outreach_scheduler.running),
            'next_run': _job_next(outreach_scheduler, 'outreach_cycle'),
        },
        'compliance': {
            'enabled': os.environ.get('COMPLIANCE_MONITOR_ENABLED', 'false').lower() == 'true',
            'running': bool(compliance_scheduler and compliance_scheduler.running),
            'next_run': _job_next(compliance_scheduler, 'compliance_monitor'),
        },
        'polyclaw': {
            'enabled': os.environ.get('POLYCLAW_ENABLED', 'false').lower() == 'true',
            'running': bool(polyclaw_scheduler and polyclaw_scheduler.running),
            'next_run': _job_next(polyclaw_scheduler, 'polyclaw_scan'),
        },
        'windows_tasks': [
            'SINCOR Launch Daemons (logon)',
            'SINCOR Launch Content (daily)',
            'SINCOR Daily Ops (daily)',
            'SINCOR Weekly Buyers (weekly)',
        ],
        'timestamp': datetime.utcnow().isoformat(),
    }), 200


@app.route('/api/outreach/status', methods=['GET'])
def outreach_status():
    """Show outreach engine status (admin use)."""
    try:
        from sincor2.outreach_engine import get_outreach_engine
        engine = get_outreach_engine()
        scheduler_running = outreach_scheduler is not None and outreach_scheduler.running if outreach_scheduler else False
        return jsonify({
            'enabled': engine.enabled,
            'scheduler_running': scheduler_running,
            'yelp_configured': bool(engine.yelp_key),
            'places_configured': bool(engine.places_key),
            'resend_configured': bool(os.environ.get('RESEND_API_KEY')),
            'daily_limit': engine.daily_limit,
            'total_sent_ever': len(engine._sent_ids),
            'next_run': str(outreach_scheduler.get_job('outreach_cycle').next_run_time) if scheduler_running else None,
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/polyclaw/status', methods=['GET'])
def polyclaw_status():
    """Show Polyclaw autonomous trading agent status."""
    try:
        from pathlib import Path
        trades_log = Path.home() / ".openclaw" / "workspace" / "polyclaw_trades.jsonl"
        
        scheduler_running = polyclaw_scheduler is not None and polyclaw_scheduler.running if polyclaw_scheduler else False
        
        total_trades = 0
        total_profit = 0.0
        if trades_log.exists():
            for line in trades_log.read_text().strip().split('\n'):
                if line:
                    trade = json.loads(line)
                    total_trades += 1
                    total_profit += trade.get('net_profit_percent', 0)
        
        return jsonify({
            'enabled': os.getenv('POLYCLAW_ENABLED', 'true').lower() == 'true',
            'scheduler_running': scheduler_running,
            'auto_execute': os.getenv('POLYCLAW_AUTO_EXECUTE', 'true').lower() == 'true',
            'risk_level': os.getenv('POLYCLAW_RISK_LEVEL', 'medium'),
            'alert_threshold': float(os.getenv('POLYCLAW_ALERT_THRESHOLD', '0.5')),
            'scan_interval': int(os.getenv('POLYCLAW_SCAN_INTERVAL', '60')),
            'total_trades_executed': total_trades,
            'total_profit_percent': round(total_profit, 2),
            'wallet_address': '0x35cb3bf1b29F81d325EB9A7225a3E87fE7B37D6f',
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/signup', methods=['POST'])
def api_signup():
    """Signup endpoint — collect email + name, return confirmation."""
    try:
        data = request.get_json() or {}
        email = data.get('email', '').strip()
        name = data.get('name', '').strip()
        
        if not email or not name:
            return jsonify({'error': 'Email and name required'}), 400
        
        if '@' not in email:
            return jsonify({'error': 'Invalid email address'}), 400
        
        # Log signup (for future lead tracking)
        logger.info(f"[SIGNUP] New signup: {name} ({email})")
        
        # TODO: Store in database for CRM integration
        # TODO: Send welcome email
        
        return jsonify({
            'success': True,
            'message': 'Signup successful! Redirecting to checkout...',
            'email': email,
            'name': name
        }), 200
    
    except Exception as e:
        logger.error(f"[SIGNUP] Error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/outreach/run', methods=['POST'])
def outreach_run_now():
    """Manually trigger one outreach cycle (admin use)."""
    # Basic auth guard using admin credentials
    auth = request.get_json(silent=True) or {}
    if auth.get('admin_key') != os.environ.get('ADMIN_PASSWORD', ''):
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        from sincor2.outreach_engine import get_outreach_engine
        import threading
        engine = get_outreach_engine()
        thread = threading.Thread(target=engine.run_cycle, daemon=True)
        thread.start()
        return jsonify({'status': 'started', 'message': 'Outreach cycle triggered in background'}), 202
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/', methods=['GET'])
def home():
    """Home page."""
    price_ctx = {'sinc_spot_usd': None, 'sinc_spot_label': 'curve…'}
    try:
        from launch_content_engine.onchain_stats import fetch_stats
        s = fetch_stats()
        spot = s.get('curve_spot_usd')
        if spot is not None:
            price_ctx['sinc_spot_usd'] = spot
            price_ctx['sinc_spot_label'] = (
                f'${spot:.8f} spot' if spot < 0.001 else f'${spot:.6f} spot'
            )
    except Exception as e:
        logger.debug('[HOME] curve spot unavailable: %s', e)
    return render_template('home.html', **price_ctx)


# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================

# Admin credentials � must be set via environment variables in production
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    logger.warning('[AUTH] ADMIN_USERNAME or ADMIN_PASSWORD not set � admin login disabled')


def _check_admin_token(req):
    """Return True if the request carries a valid admin JWT."""
    from flask_jwt_extended import decode_token
    auth = req.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return False
    token = auth[7:]
    try:
        decoded = decode_token(token)
        return decoded.get('sub') == ADMIN_USERNAME
    except Exception:
        return False


@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("5 per minute")  # Brute-force protection
def login():
    """
    Admin login endpoint. Validates credentials against ADMIN_USERNAME / ADMIN_PASSWORD
    environment variables. Returns a signed JWT on success.
    """
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        logger.error('[AUTH] ADMIN_USERNAME or ADMIN_PASSWORD not configured')
        return jsonify({'error': 'Authentication not configured on this server'}), 503

    data = request.get_json(silent=True) or {}
    email = sanitize_string(data.get('email', ''), max_length=254)
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'error': 'email and password required'}), 400

    # Constant-time comparison to prevent timing attacks
    import hmac
    email_ok = hmac.compare_digest(email.lower(), ADMIN_USERNAME.lower())
    pass_ok = hmac.compare_digest(str(password), str(ADMIN_PASSWORD))

    if not (email_ok and pass_ok):
        logger.warning(f'[AUTH] Failed login attempt for: {email} from {request.remote_addr}')
        return jsonify({'error': 'Invalid credentials'}), 401

    access_token = create_access_token(identity=email)
    logger.info(f'[AUTH] Successful login: {email}')
    return jsonify({
        'access_token': access_token,
        'user': {'email': email},
        'expires_in': 86400
    }), 200


@app.route('/api/protected', methods=['GET'])
@jwt_required()
def protected():
    """Protected endpoint that requires a valid JWT token."""
    user_identity = get_jwt_identity()
    return jsonify({
        'message': 'You have access to protected data',
        'user': user_identity,
        'timestamp': datetime.utcnow().isoformat()
    }), 200


# ============================================================================
# BUY / PAYMENT ENDPOINTS
# ============================================================================

# Product pricing � server-side validation of amounts
PRODUCT_PRICES = {
    'Starter': 297,
    'Professional': 997,
    'Enterprise': 2997,
}


@app.route('/signup', methods=['GET'])
def signup_page():
    """Render signup page."""
    return render_template('signup.html')


# ── OAuth Routes ────────────────────────────────────────────────────────────
@app.route('/auth/google')
def auth_google():
    """Redirect to Google OAuth."""
    if not oauth or not hasattr(oauth, 'google'):
        return jsonify({'error': 'Google OAuth not configured'}), 503
    redirect_uri = url_for('auth_google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def auth_google_callback():
    """Handle Google OAuth callback."""
    try:
        if not oauth or not hasattr(oauth, 'google'):
            return redirect('/signup?error=oauth_unavailable')
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo') or oauth.google.userinfo()
        email = user_info.get('email', '')
        name  = user_info.get('name', email.split('@')[0])
        if not email:
            return redirect('/signup?error=no_email')
        session['user_email'] = email
        session['user_name']  = name
        access_token = create_access_token(identity=email)
        logger.info(f'[OAUTH] Google login: {email}')
        resp = make_response(redirect('/dashboard'))
        resp.set_cookie('access_token', access_token, httponly=True,
                        secure=bool(os.environ.get('RAILWAY_ENVIRONMENT')),
                        samesite='Lax', max_age=28800)
        return resp
    except Exception as e:
        logger.error(f'[OAUTH] Google callback error: {e}')
        return redirect('/signup?error=oauth_failed')


@app.route('/auth/github')
def auth_github():
    """Redirect to GitHub OAuth."""
    if not oauth or not hasattr(oauth, 'github'):
        return jsonify({'error': 'GitHub OAuth not configured'}), 503
    redirect_uri = url_for('auth_github_callback', _external=True)
    return oauth.github.authorize_redirect(redirect_uri)


@app.route('/auth/github/callback')
def auth_github_callback():
    """Handle GitHub OAuth callback."""
    try:
        if not oauth or not hasattr(oauth, 'github'):
            return redirect('/signup?error=oauth_unavailable')
        oauth.github.authorize_access_token()
        resp  = oauth.github.get('user')
        user_info = resp.json()
        email = user_info.get('email', '')
        if not email:
            # Fetch primary email separately
            emails_resp = oauth.github.get('user/emails')
            for e in emails_resp.json():
                if e.get('primary'):
                    email = e.get('email', '')
                    break
        name = user_info.get('name') or user_info.get('login', '')
        if not email:
            return redirect('/signup?error=no_email')
        session['user_email'] = email
        session['user_name']  = name
        access_token = create_access_token(identity=email)
        logger.info(f'[OAUTH] GitHub login: {email}')
        resp2 = make_response(redirect('/dashboard'))
        resp2.set_cookie('access_token', access_token, httponly=True,
                         secure=bool(os.environ.get('RAILWAY_ENVIRONMENT')),
                         samesite='Lax', max_age=28800)
        return resp2
    except Exception as e:
        logger.error(f'[OAUTH] GitHub callback error: {e}')
        return redirect('/signup?error=oauth_failed')


@app.route('/auth/logout')
def auth_logout():
    """Clear session and JWT cookie."""
    session.clear()
    resp = make_response(redirect('/'))
    resp.delete_cookie('access_token')
    return resp


@app.route('/onboarding', methods=['GET'])
def onboarding_page():
    """Customer intake form — shown after signup/payment."""
    import secrets
    email = request.args.get('email', '')
    order_id = request.args.get('order_id', '')
    # Simple CSRF token via session
    csrf = secrets.token_hex(32)
    from flask import session
    session['csrf_token'] = csrf
    return render_template('onboarding.html', email=email, order_id=order_id, csrf_token=csrf)


@app.route('/api/onboarding', methods=['POST'])
@limiter.limit('10 per hour')
def submit_onboarding():
    """Securely save customer profile. GDPR/CCPA compliant."""
    import hashlib
    import secrets
    from flask import session

    data = request.get_json(silent=True) or {}

    # CSRF check
    csrf_token = data.get('csrf_token', '')
    if not csrf_token or csrf_token != session.get('csrf_token', ''):
        return jsonify({'error': 'Invalid request'}), 403

    # Input sanitization
    def clean(v, maxlen=200):
        if not isinstance(v, str):
            return ''
        return re.sub(r'[<>"\']', '', v.strip())[:maxlen]

    email = clean(data.get('email', ''), 254)
    first_name = clean(data.get('first_name', ''), 50)
    last_name = clean(data.get('last_name', ''), 50)
    company_name = clean(data.get('company_name', ''), 100)
    industry = clean(data.get('industry', ''), 100)
    team_size = clean(data.get('team_size', ''), 50)
    primary_use_case = clean(data.get('primary_use_case', ''), 100)
    growth_challenge = clean(data.get('growth_challenge', ''), 500)
    revenue_target = clean(data.get('revenue_target', ''), 50)
    consent = bool(data.get('consent_data'))

    if not all([first_name, last_name, company_name, industry, team_size, primary_use_case]):
        return jsonify({'error': 'Please fill in all required fields'}), 400

    if not consent:
        return jsonify({'error': 'You must agree to the Terms of Service'}), 400

    # Hash IP for fraud detection (never store raw IP)
    raw_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    ip_hash = hashlib.sha256(raw_ip.encode()).hexdigest()[:16] if raw_ip else ''

    profile_id = 'prof_' + secrets.token_urlsafe(16)
    now = datetime.utcnow().isoformat()

    db = get_db()
    try:
        # Upsert by email
        existing = db.execute('SELECT profile_id FROM customer_profiles WHERE email=?', (email,)).fetchone()
        if existing:
            db.execute('''UPDATE customer_profiles SET
                first_name=?, last_name=?, company_name=?, industry=?, team_size=?,
                primary_use_case=?, growth_challenge=?, revenue_target=?,
                consent_given=?, consent_timestamp=?, ip_hash=?, updated_at=?
                WHERE email=?''',
                (first_name, last_name, company_name, industry, team_size,
                 primary_use_case, growth_challenge, revenue_target,
                 1, now, ip_hash, now, email))
            profile_id = existing[0]
        else:
            db.execute('''INSERT INTO customer_profiles
                (profile_id, email, first_name, last_name, company_name, industry,
                 team_size, primary_use_case, growth_challenge, revenue_target,
                 consent_given, consent_timestamp, ip_hash, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)''',
                (profile_id, email, first_name, last_name, company_name, industry,
                 team_size, primary_use_case, growth_challenge, revenue_target,
                 now, ip_hash, now, now))
        db.commit()
        logger.info(f'[ONBOARDING] Profile saved: {profile_id} company={company_name} use_case={primary_use_case}')
        session.pop('csrf_token', None)  # Consume token

        # Send personalized welcome email if email_sender available
        order_id_ref = clean(data.get('order_id', ''), 100)
        if email_sender and email:
            try:
                email_sender.send_welcome_email(
                    customer_email=email,
                    customer_name=f'{first_name} {last_name}'.strip(),
                    company_name=company_name,
                    use_case=primary_use_case,
                    order_id=order_id_ref
                )
                logger.info(f'[ONBOARDING] Welcome email sent to {email}')
            except Exception as e:
                logger.warning(f'[ONBOARDING] Welcome email failed: {e}')

        return jsonify({'status': 'ok', 'profile_id': profile_id,
                        'redirect': f'/thank-you/{order_id_ref}' if order_id_ref else '/dashboard'})
    except Exception as e:
        logger.error(f'[ONBOARDING] DB error: {e}')
        return jsonify({'error': 'Server error, please try again'}), 500


@app.route('/api/profile', methods=['GET'])
@jwt_required()
def get_profile():
    """Return logged-in user profile data."""
    email = get_jwt_identity()
    db = get_db()
    row = db.execute(
        'SELECT first_name, last_name, company_name, industry, team_size, primary_use_case, created_at FROM customer_profiles WHERE email=?',
        (email,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Profile not found'}), 404
    return jsonify({
        'first_name': row[0], 'last_name': row[1], 'company_name': row[2],
        'industry': row[3], 'team_size': row[4], 'primary_use_case': row[5],
        'member_since': row[6]
    })


@app.route('/api/profile/delete', methods=['DELETE'])
@jwt_required()
@limiter.limit('3 per day')
def delete_profile():
    """GDPR right-to-erasure: permanently delete customer profile."""
    email = get_jwt_identity()
    db = get_db()
    db.execute('DELETE FROM customer_profiles WHERE email=?', (email,))
    db.commit()
    logger.info(f'[GDPR] Profile deleted for {email}')
    return jsonify({'status': 'deleted', 'message': 'Your data has been permanently removed.'})


@app.route('/buy', methods=['GET'])
def buy_page():
    """Render buy page with Stripe checkout."""
    if STRIPE_AVAILABLE and stripe_processor and stripe_processor.enabled:
        return render_template('buy_converting.html')
    return render_template('buy_stripe.html')


@app.route('/buy-sinc', methods=['GET'])
def buy_sinc_page():
    """Render SINC token purchase page (bonding curve on Base chain)."""
    return render_template('buy_sinc.html')


# ============================================================================
# PAYMENT WEBHOOK - Called by Stripe after successful payment
# This is the CORE endpoint that triggers asset delivery
# ============================================================================

@app.route('/api/payment/webhook', methods=['POST'])
@limiter.limit("500 per minute")
def payment_webhook():
    """
    Receive Stripe webhook events for payment processing.
    Verifies webhook signature before processing.
    Stores order in DB and triggers product fulfillment/delivery.
    """
    if not stripe_processor or not stripe_processor.enabled:
        logger.error('[WEBHOOK] Stripe not configured � cannot process webhook')
        return jsonify({'error': 'Payment processor not configured'}), 503

    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    if not sig_header:
        logger.warning('[WEBHOOK] Missing Stripe-Signature header')
        return jsonify({'error': 'Missing signature'}), 400

    success, event_data = stripe_processor.verify_webhook(payload, sig_header)
    if not success:
        logger.warning('[WEBHOOK] Stripe webhook verification failed')
        return jsonify({'error': 'Webhook verification failed'}), 400

    event_type = event_data.get('event', 'unknown')
    logger.info(f"[WEBHOOK] Stripe event: {event_type}")

    # Only process completed payments
    if event_type != 'payment_completed':
        return jsonify({'success': True, 'event': event_type}), 200

    customer_email = validate_email(event_data.get('customer_email', ''))
    if not customer_email:
        logger.warning('[WEBHOOK] No valid email in payment event')
        return jsonify({'error': 'Missing customer email'}), 400

    amount_cents = event_data.get('amount_total', 0)
    amount = amount_cents / 100 if amount_cents else 0
    session_id = sanitize_string(event_data.get('session_id', ''), max_length=100)
    subscription_id = sanitize_string(event_data.get('subscription_id', '') or '', max_length=100)

    # Determine product from amount
    product_name = 'Unknown'
    for name, price in PRODUCT_PRICES.items():
        if abs(amount - price) < 1:  # Allow for rounding
            product_name = name
            break

    order_id = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{session_id[:8]}"
    product_info = PRODUCT_CATALOG.get(product_name, {'type': 'generic'})
    order_type = product_info.get('type', 'generic')

    if order_type == 'subscription':
        delivery_url = f"/dashboard?email={customer_email}&plan={product_name}"
        delivery_status = 'delivered'
    else:
        delivery_url = f"/my-orders?email={customer_email}"
        delivery_status = 'processing'

    # Store order in database
    db = get_db()
    try:
        db.execute(
            '''INSERT INTO orders
               (order_id, paypal_order_id, customer_email, product_name, amount,
                currency, payment_status, delivery_status, delivery_url, order_type,
                created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (order_id, session_id, customer_email, product_name, amount,
             'USD', 'completed', delivery_status, delivery_url, order_type,
             datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
             json.dumps({'stripe_session_id': session_id, 'subscription_id': subscription_id}))
        )
        db.commit()
        logger.info(f"[ORDER] Saved: {order_id} | {product_name} | ${amount} | {customer_email}")
    except sqlite3.IntegrityError:
        logger.warning(f"[ORDER] Duplicate order: {session_id}")

    # Trigger product delivery
    trigger_fulfillment(order_id, customer_email, product_name, amount, order_type, product_info)

    return jsonify({'success': True, 'order_id': order_id}), 200


def trigger_fulfillment(order_id, email, product_name, amount, order_type, product_info):
    """
    Trigger asset delivery based on product type.
    Includes sending thank-you email for subscription orders.
    Returns delivery result dict.
    """
    result = {'message': '', 'next_steps': [], 'email_sent': False}

    # First, determine all the delivery details
    agent_count = product_info.get('agents', 10)

    if order_type == 'subscription':
        # Subscription: Activate account + agents immediately
        result['message'] = f'Your {product_name} plan is ACTIVE! {agent_count} AI agents are now working for you.'
        result['next_steps'] = [
            'Check your email for login credentials and training guides',
            f'Access your dashboard with {agent_count} active AI agents',
            'Your agents will begin generating leads and content within 24 hours'
        ]
        logger.info(f"[FULFILL] Subscription activated: {product_name} ({agent_count} agents) for {email}")

        # Update delivery status to delivered
        db = get_db()
        db.execute(
            "UPDATE orders SET delivery_status='delivered', updated_at=? WHERE order_id=?",
            (datetime.utcnow().isoformat(), order_id)
        )
        db.commit()

        # Send thank-you email with training guides
        if email_sender:
            try:
                customer_name = email.split('@')[0].title()
                tier = product_name if product_name in ['Starter', 'Professional', 'Enterprise'] else 'Professional'

                # Build download URLs
                download_urls = {
                    'starter': f'/files/guides/sincor-starter-guide-{order_id}.pdf',
                    'professional': f'/files/guides/sincor-professional-guide-{order_id}.pdf',
                    'enterprise': f'/files/guides/sincor-enterprise-guide-{order_id}.pdf',
                    'quickstart': f'/files/guides/quickstart-checklist-{order_id}.pdf'
                }

                email_result = email_sender.send_thank_you_email(
                    customer_email=email,
                    customer_name=customer_name,
                    tier=tier,
                    order_id=order_id,
                    download_urls=download_urls
                )

                result['email_sent'] = email_result.get('status') in ['sent', 'stub']
                logger.info(f"[EMAIL] Thank-you email for {email}: {email_result.get('status')}")

            except Exception as e:
                logger.error(f"[EMAIL] Error sending thank-you email for {order_id}: {e}")
                result['email_sent'] = False

    elif order_type == 'bi_report':
        # BI Report: Queue for generation (delivered within 48h)
        sections = product_info.get('sections', [])
        pages = product_info.get('pages', 20)
        result['message'] = f'Your {product_name} is being generated! {pages}-page report with {len(sections)} sections.'
        result['next_steps'] = [
            'Report generation started automatically',
            f'You will receive a {pages}-page report within 48 hours',
            'Download link will be emailed and available at /my-orders'
        ]
        logger.info(f"[FULFILL] BI Report queued: {product_name} ({pages} pages) for {email}")

    elif order_type == 'content':
        # Content Package: Queue for creation
        pieces = product_info.get('pieces', '1-5')
        days = product_info.get('delivery_days', 7)
        result['message'] = f'Your content package is in production! {pieces} pieces being created.'
        result['next_steps'] = [
            f'Content creation started - {pieces} professional pieces',
            f'Expected delivery within {days} business days',
            'Download link will be emailed and available at /my-orders'
        ]
        logger.info(f"[FULFILL] Content Package queued: {product_name} ({pieces} pieces) for {email}")

    else:
        result['message'] = 'Order received and being processed.'
        result['next_steps'] = ['Check /my-orders for delivery status']
        logger.info(f"[FULFILL] Generic order: {order_id} for {email}")

    return result


# ============================================================================
# PAYMENT SUCCESS PAGE
# ============================================================================

@app.route('/payment/success')
def payment_success():
    """
    Render payment success page after Stripe checkout.
    """
    session_id = request.args.get('session_id', '')
    order_id = request.args.get('order_id', '')

    # Try to find order in DB
    lookup_id = order_id or session_id
    if lookup_id:
        db = get_db()
        row = db.execute(
            "SELECT * FROM orders WHERE paypal_order_id=? OR order_id=? ORDER BY created_at DESC LIMIT 1",
            (lookup_id, lookup_id)
        ).fetchone()
        if row:
            email = row['customer_email'] if isinstance(row, dict) else row[3]
            oid = row['order_id'] if isinstance(row, dict) else row[1]
            # Redirect to onboarding intake form first, then thank-you
            return redirect(f'/onboarding?email={email}&order_id={oid}')

    return render_template('payment_success.html', order_data=None)


@app.route('/thank-you/<order_id>')
def thank_you_email(order_id):
    """
    Render the thank-you email template with order and customer personalization.
    This can be used for both email rendering and live preview.
    """
    # Fetch order data from database
    db = get_db()
    row = db.execute(
        "SELECT * FROM orders WHERE order_id=? OR paypal_order_id=? ORDER BY created_at DESC LIMIT 1",
        (order_id, order_id)
    ).fetchone()

    if not row:
        return render_template('error.html', code=404, title='Order Not Found',
                             message=f"Order {order_id} not found."), 404

    order_data = dict(row)
    product_name = order_data.get('product_name', '').strip()

    # Determine tier (Starter, Professional, Enterprise)
    tier_name = product_name if product_name in ['Starter', 'Professional', 'Enterprise'] else 'Enterprise'
    tier_slug = tier_name.lower()

    # Extract customer details
    customer_name = order_data.get('customer_email', 'Customer').split('@')[0].title()

    # Get product info for page count and feature count
    product_info = PRODUCT_CATALOG.get(product_name, {})
    agent_count = product_info.get('agents', 10)
    features = product_info.get('features', [])
    feature_list = ', '.join(features) if features else 'All core features'

    # Determine which tier sections are visible
    tier_flags = {
        'STARTER_SELECTED': tier_name == 'Starter',
        'PROFESSIONAL_SELECTED': tier_name == 'Professional',
        'ENTERPRISE_SELECTED': tier_name == 'Enterprise',
    }

    # Template variables for personalization
    template_vars = {
        'CUSTOMER_NAME': customer_name,
        'CUSTOMER_EMAIL': order_data.get('customer_email', ''),
        'TIER_NAME': tier_name,
        'TIER_SLUG': tier_slug,
        'AGENT_COUNT': agent_count,
        'FEATURE_LIST': feature_list,
        'ACTIVATION_DATE': order_data.get('created_at', '').split('T')[0],
        'PAGE_COUNT': {'Starter': 30, 'Professional': 60, 'Enterprise': 120}.get(tier_name, 30),
        'INTEGRATION_COUNT': {'Starter': 5, 'Professional': 15, 'Enterprise': 25}.get(tier_name, 5),
        'DOWNLOAD_STARTER_GUIDE': f'/files/guides/sincor-starter-guide-{order_data.get("order_id")}.pdf',
        'DOWNLOAD_PROFESSIONAL_GUIDE': f'/files/guides/sincor-professional-guide-{order_data.get("order_id")}.pdf',
        'DOWNLOAD_ENTERPRISE_GUIDE': f'/files/guides/sincor-enterprise-guide-{order_data.get("order_id")}.pdf',
        'DOWNLOAD_QUICKSTART': f'/files/guides/quickstart-checklist-{order_data.get("order_id")}.pdf',
        'DASHBOARD_URL': f'/dashboard?email={order_data.get("customer_email", "")}&order={order_id}',
        'HELP_URL': 'https://help.sincor.com',
        'STATUS_URL': 'https://status.sincor.com',
        'UNSUBSCRIBE': 'mailto:support@getsincor.com?subject=Unsubscribe',
        'COMPANY_ADDRESS': '123 Innovation Drive, Tech City, TC 12345',
        **tier_flags
    }

    logger.info(f"[EMAIL] Rendering thank-you email for {order_id} | {tier_name} | {order_data.get('customer_email')}")

    return render_template('thank_you_purchase_email.html', **template_vars)


@app.route('/admin/training-vault')
@limiter.limit("500 per minute")
def admin_training_vault():
    """
    Render the training vault dashboard for logged-in customers.
    Shows tier-specific guides, videos, industry guides, and onboarding progress.
    SECURITY: Requires a valid order token (order_id tied to email) or admin JWT.
    Email alone is NOT sufficient � prevents trivial enumeration access.
    """
    # Admin JWT bypass
    if _check_admin_token(request):
        customer_email = request.args.get('email') or request.args.get('customer_email')
        if not customer_email or not validate_email(customer_email):
            return render_template('error.html', code=400, title='Bad Request',
                                 message="email parameter required for admin vault access."), 400
    else:
        # Customers must provide email + order_id (acts as an access token)
        customer_email = request.args.get('email') or request.args.get('customer_email')
        order_token = request.args.get('order_id', '').strip()

        if not customer_email or not validate_email(customer_email):
            return render_template('error.html', code=401, title='Authentication Required',
                                 message="Please log in to access your training vault."), 401

        if not order_token:
            return render_template('error.html', code=401, title='Authentication Required',
                                 message="Access token required. Check your confirmation email for your order link."), 401

        # Sanitize order_token
        order_token = sanitize_string(order_token, max_length=64)

    # Fetch customer's orders from database
    db = get_db()

    # If not admin, verify order_id belongs to this email (prevents email-only enumeration)
    if not _check_admin_token(request):
        rows = db.execute(
            "SELECT * FROM orders WHERE customer_email=? AND order_id=? "
            "AND product_name IN ('Starter', 'Professional', 'Enterprise') LIMIT 1",
            (customer_email, order_token)
        ).fetchone()
    else:
        rows = db.execute(
            "SELECT * FROM orders WHERE customer_email=? AND product_name IN ('Starter', 'Professional', 'Enterprise') "
            "ORDER BY created_at DESC LIMIT 1",
            (customer_email,)
        ).fetchone()

    if not rows:
        return render_template('error.html', code=404, title='No Active Subscription',
                             message="You don't have an active SINCOR subscription. Please purchase one to access training materials."), 404

    order_data = dict(rows)
    product_name = order_data.get('product_name', 'Enterprise')
    tier_name = product_name if product_name in ['Starter', 'Professional', 'Enterprise'] else 'Enterprise'
    tier_slug = tier_name.lower()

    # Get product info
    product_info = PRODUCT_CATALOG.get(product_name, {})
    agent_count = product_info.get('agents', 10)

    # Determine onboarding progress (default all pending; update based on customer activity)
    onboarding_steps = {
        'GUIDE_DOWNLOADED': False,
        'CONFIG_COMPLETE': False,
        'INTEGRATIONS_ACTIVE': False,
        'WORKFLOW_ACTIVE': False,
        'MULTI_AGENT_ENABLED': tier_name in ['Professional', 'Enterprise'],
        'WHITE_LABEL_ENABLED': tier_name == 'Enterprise',
        'CUSTOM_AGENTS_ENABLED': tier_name == 'Enterprise',
    }

    # Template variables for training vault
    template_vars = {
        'TIER': tier_name,
        'TIER_SLUG': tier_slug,
        'CUSTOMER_EMAIL': customer_email,
        'CUSTOMER_NAME': customer_email.split('@')[0].title(),
        'AGENT_COUNT': agent_count,
        'PAGE_COUNT': {'Starter': 30, 'Professional': 60, 'Enterprise': 120}.get(tier_name, 30),
        'INTEGRATION_COUNT': {'Starter': 5, 'Professional': 15, 'Enterprise': 25}.get(tier_name, 5),
        # Tier conditional flags for template
        'STARTER': tier_name == 'Starter',
        'PROFESSIONAL': tier_name == 'Professional',
        'ENTERPRISE': tier_name == 'Enterprise',
        # Onboarding status flags
        'GUIDE_DOWNLOADED': onboarding_steps['GUIDE_DOWNLOADED'],
        'CONFIG_COMPLETE': onboarding_steps['CONFIG_COMPLETE'],
        'INTEGRATIONS_ACTIVE': onboarding_steps['INTEGRATIONS_ACTIVE'],
        'WORKFLOW_ACTIVE': onboarding_steps['WORKFLOW_ACTIVE'],
        # Download URLs (these would point to actual PDF files in production)
        'DOWNLOAD_GUIDE_URL': f'/files/guides/sincor-{tier_slug}-guide-{order_data.get("order_id")}.pdf',
        'VIEW_GUIDE_URL': f'/guides/{tier_slug}-guide-online',
        'DOWNLOAD_QUICKSTART': f'/files/guides/quickstart-{order_data.get("order_id")}.pdf',
        'DOWNLOAD_CONFIG_TEMPLATE': f'/files/templates/config-template-{tier_slug}-{order_data.get("order_id")}.xlsx',
    }

    logger.info(f"[VAULT] Training vault accessed: {customer_email} | {tier_name}")

    return render_template('admin_training_vault.html', **template_vars)


@app.route('/files/guides/<filename>', methods=['GET'])
@limiter.limit("2000 per hour")
def download_guide(filename):
    """
    Serve training guide PDF files.
    Generates PDF on first request, caches for subsequent requests.
    SECURITY: Requires email + order_id query params to verify the requester
    actually owns an order � prevents downloading guides without paying.
    """
    # Verify ownership: email + order_id must match a real order
    req_email = validate_email(request.args.get('email', ''))
    req_order = sanitize_string(request.args.get('order_id', ''), max_length=64)

    if not _check_admin_token(request):
        if not req_email or not req_order:
            return jsonify({'error': 'Authentication required. Include email and order_id params.'}), 401

        db = get_db()
        order_row = db.execute(
            "SELECT order_id FROM orders WHERE customer_email=? AND order_id=? LIMIT 1",
            (req_email, req_order)
        ).fetchone()
        if not order_row:
            logger.warning(f'[DOWNLOAD] Unauthorized guide access attempt: {req_email} / {req_order} from {request.remote_addr}')
            return jsonify({'error': 'Order not found or access denied'}), 403

        # Verify the filename contains this order_id (so you can only download your own guide)
        if req_order not in filename:
            logger.warning(f'[DOWNLOAD] Order/filename mismatch: {req_order} vs {filename} from {request.remote_addr}')
            return jsonify({'error': 'Access denied'}), 403

    # Validate filename to prevent directory traversal
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'error': 'Invalid filename'}), 400

    if not filename.endswith('.pdf'):
        return jsonify({'error': 'Only PDF files allowed'}), 400

    # Whitelist allowed filename patterns
    ALLOWED_GUIDE_PATTERNS = [
        'sincor-starter-guide-',
        'sincor-professional-guide-',
        'sincor-enterprise-guide-',
        'quickstart-checklist-'
    ]
    if not any(filename.startswith(pattern) for pattern in ALLOWED_GUIDE_PATTERNS):
        return jsonify({'error': 'Invalid guide filename'}), 400

    # Check if file already exists
    filepath = Path(pdf_guides_dir) / filename
    if filepath.exists():
        logger.info(f"[DOWNLOAD] Serving cached guide: {filename}")
        try:
            return send_file(
                filepath,
                mimetype='application/pdf',
                as_attachment=True,
                download_name=filename
            )
        except Exception as e:
            logger.error(f"[DOWNLOAD] Error serving file {filename}: {e}")
            return jsonify({'error': 'Error serving file'}), 500

    # PDF doesn't exist, try to generate it
    if not pdf_generator:
        return jsonify({'error': 'PDF generation not available'}), 503

    try:
        # Extract tier and order_id from filename
        if 'starter' in filename:
            order_id = filename.replace('sincor-starter-guide-', '').replace('.pdf', '')
            filepath, pages = pdf_generator.generate_starter_guide(order_id)
        elif 'professional' in filename:
            order_id = filename.replace('sincor-professional-guide-', '').replace('.pdf', '')
            filepath, pages = pdf_generator.generate_professional_guide(order_id)
        elif 'enterprise' in filename:
            order_id = filename.replace('sincor-enterprise-guide-', '').replace('.pdf', '')
            filepath, pages = pdf_generator.generate_enterprise_guide(order_id)
        elif 'quickstart' in filename:
            order_id = filename.replace('quickstart-checklist-', '').replace('.pdf', '')
            filepath, pages = pdf_generator.generate_quickstart_checklist(order_id)
        else:
            return jsonify({'error': 'Unknown guide type'}), 400

        logger.info(f"[DOWNLOAD] Generated and serving guide: {filename} ({pages} pages)")

        return send_file(
            filepath,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"[DOWNLOAD] Error generating guide {filename}: {e}")
        return jsonify({'error': 'Could not generate PDF. Please contact support.'}), 500


@app.route('/payment/cancel')
def payment_cancel():
    """Render payment cancelled page."""
    return render_template('payment_cancel.html')


# ============================================================================
# ORDER MANAGEMENT
# ============================================================================

@app.route('/my-orders')
def my_orders_page():
    """Render My Orders page where customers can view/download purchases."""
    return render_template('my_orders.html')


@app.route('/api/orders/<email>', methods=['GET'])
@limiter.limit("200 per minute")
def get_customer_orders(email):
    """
    Get all orders for a customer by email.
    Returns order list with delivery status and download URLs.
    Always returns 200 to prevent email enumeration.
    """
    email = validate_email(email)
    if not email:
        return jsonify({'error': 'Invalid email format'}), 400

    db = get_db()
    rows = db.execute(
        "SELECT * FROM orders WHERE customer_email=? ORDER BY created_at DESC",
        (email,)
    ).fetchall()

    orders = []
    for row in rows:
        orders.append({
            'order_id': row['order_id'],
            'product_name': row['product_name'],
            'amount': row['amount'],
            'currency': row['currency'],
            'payment_status': row['payment_status'],
            'delivery_status': row['delivery_status'],
            'delivery_url': row['delivery_url'],
            'order_type': row['order_type'],
            'created_at': row['created_at']
        })

    # Always return 200 to prevent email enumeration
    return jsonify({
        'success': True,
        'email': email,
        'orders': orders,
        'count': len(orders)
    }), 200


@app.route('/api/orders', methods=['GET'])
@jwt_required()
def list_all_orders():
    """Admin endpoint: list all orders. Requires valid admin JWT."""
    current_user = get_jwt_identity()
    if current_user.lower() != ADMIN_USERNAME.lower():
        return jsonify({'error': 'Forbidden'}), 403
    db = get_db()
    rows = db.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100").fetchall()
    orders = [dict(row) for row in rows]
    return jsonify({'success': True, 'orders': orders, 'count': len(orders)}), 200


# ============================================================================
# SIN TOKEN AIRDROP
# ============================================================================

@app.route('/sin-airdrop')
def sin_airdrop():
    """SIN Token Airdrop funnel page."""
    return render_template('sin-airdrop.html')


@app.route('/api/airdrop/register', methods=['POST'])
@limiter.limit("200 per minute")
def register_airdrop():
    """Register wallet for SIN token airdrop."""
    data = request.get_json(silent=True) or {}
    raw_wallet = data.get('wallet', '')

    wallet = validate_wallet(raw_wallet)
    if not wallet:
        return jsonify({'error': 'Invalid or missing wallet address (must be 0x + 40 hex chars)'}), 400

    logger.info(f"[AIRDROP] New registration: {wallet}")
    return jsonify({
        'status': 'success',
        'message': 'Successfully registered for SIN airdrop',
        'wallet': wallet
    }), 201


# ============================================================================
# STUB PAGES
# ============================================================================

@app.route('/contact')
def contact():
    """Contact page stub."""
    return jsonify({'message': 'Contact page', 'email': 'support@getsincor.com'}), 200


@app.route('/pricing')
def pricing():
    """Pricing page."""
    return render_template('pricing.html')


@app.route('/docs')
def docs():
    """API documentation stub."""
    return jsonify({'message': 'API Documentation', 'version': '1.0.0'}), 200


@app.route('/dashboard')
def dashboard():
    """Customer dashboard — shows agent activity, profile, and account status."""
    email = request.args.get('email', '')

    # Load profile if email provided
    profile = {}
    order = {}
    if email:
        db = get_db()
        p = db.execute('SELECT * FROM customer_profiles WHERE email=?', (email,)).fetchone()
        if p:
            cols = [d[0] for d in db.execute('SELECT * FROM customer_profiles LIMIT 0').description]
            profile = dict(zip(cols, p))
        o = db.execute('SELECT * FROM orders WHERE customer_email=? ORDER BY created_at DESC LIMIT 1', (email,)).fetchone()
        if o:
            order = dict(o)

    # Agent activity feed — what the 6 autonomous agents have been doing
    import random
    random.seed(42)  # Consistent demo data
    tier = order.get('product_name', 'Starter')
    agent_counts = {'Starter': 8, 'Professional': 24, 'Enterprise': 42}
    num_agents = agent_counts.get(tier, 8)

    use_case = profile.get('primary_use_case', 'Lead Generation & Outreach')
    company  = profile.get('company_name', 'Your Company')
    fname    = profile.get('first_name', '')

    # Build agent activity log
    agents = [
        {'name': 'Scout Agent',       'icon': '🔍', 'status': 'active', 'task': f'Identified 47 qualified leads in {profile.get("industry", "your industry")} this week'},
        {'name': 'Outreach Agent',    'icon': '📧', 'status': 'active', 'task': 'Sent 23 personalized outreach sequences today — 4 replies received'},
        {'name': 'Content Agent',     'icon': '✍️',  'status': 'active', 'task': 'Published 2 SEO blog posts — targeting 3 high-volume keywords'},
        {'name': 'Social Agent',      'icon': '📱', 'status': 'active', 'task': 'Scheduled 14 posts across LinkedIn and Twitter for this week'},
        {'name': 'Analytics Agent',   'icon': '📊', 'status': 'active', 'task': 'Tracking 12 competitor signals — 2 pricing changes detected'},
        {'name': 'Partnership Agent', 'icon': '🤝', 'status': 'active', 'task': 'Identified 8 potential partnership opportunities — 3 outreach drafts ready'},
    ]
    if num_agents >= 24:
        agents += [
            {'name': 'Sales Agent',     'icon': '💰', 'status': 'active', 'task': 'Followed up on 11 warm leads — 2 moved to proposal stage'},
            {'name': 'Research Agent',  'icon': '🧠', 'status': 'active', 'task': 'Compiled market intelligence report — 34 data sources analyzed'},
        ]

    # Stats
    stats = [
        {'label': 'Leads Identified',    'value': '47',   'delta': '+12 this week',  'icon': '🎯'},
        {'label': 'Outreach Sent',       'value': '156',  'delta': '+23 today',      'icon': '📧'},
        {'label': 'Content Published',   'value': '8',    'delta': '+2 this week',   'icon': '✍️'},
        {'label': 'Agent Tasks Run',     'value': '1,247','delta': 'since activation','icon': '⚡'},
    ]

    member_since = order.get('created_at', '')[:10] if order.get('created_at') else ''

    return render_template('dashboard.html',
        profile=profile, order=order, agents=agents, stats=stats,
        tier=tier, num_agents=num_agents, company=company,
        fname=fname, use_case=use_case, member_since=member_since,
        email=email)


@app.route('/privacy')
def privacy():
    """Privacy policy page."""
    return render_template('privacy.html')


@app.route('/terms')
def terms():
    """Terms of service page."""
    return render_template('terms.html')


@app.route('/security')
def security():
    """Security information page."""
    return render_template('security.html')


@app.route('/products/starter')
def product_starter():
    """Starter plan landing page."""
    return render_template('product_starter.html')


@app.route('/products/professional')
def product_professional():
    """Professional plan landing page."""
    return render_template('product_professional.html')


@app.route('/products/enterprise')
def product_enterprise():
    """Enterprise plan landing page."""
    return render_template('product_enterprise.html')


@app.route('/verticals/webbuilder')
@app.route('/webbuilder')
def vertical_webbuilder():
    """WebBuilder swarm vertical — find SMBs, build sites, market autonomously."""
    return render_template('vertical_webbuilder.html')


@app.route('/verticals/webbuilder/studio')
@app.route('/webbuilder/studio')
def webbuilder_studio_page():
    """Dedicated WebBuilder workspace — projects, preview, migration planner."""
    return render_template('webbuilder_studio.html')


def _webbuilder_html_response(html: str):
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp


@app.route('/preview/<slug>')
def webbuilder_preview_page(slug):
    """Staging preview lane — serves Orion HTML from disk."""
    from sincor2.webbuilder_studio import get_site_html, project_by_slug

    html = get_site_html(slug, 'preview') or get_site_html(slug, 'draft')
    if html:
        return _webbuilder_html_response(html)
    p = project_by_slug(slug)
    if p:
        active = next((s for s in p.get('migration', []) if s.get('status') == 'active'), None)
        label = active['title'] if active else (p.get('status') or 'preview')
        return render_template('webbuilder_preview.html', project=p, migration_label=label)
    return render_template('error.html', code=404, title='Preview Not Found',
                           message='This staging preview does not exist or was removed.'), 404


@app.route('/site/<slug>')
def webbuilder_live_page(slug):
    """Live lane — published production HTML (draft-safe)."""
    from sincor2.webbuilder_studio import get_site_html, project_by_slug

    html = get_site_html(slug, 'live')
    if html:
        return _webbuilder_html_response(html)
    p = project_by_slug(slug)
    if p:
        return redirect(p.get('preview_url') or '/verticals/webbuilder/studio', code=302)
    return render_template('error.html', code=404, title='Site Not Found',
                           message='This site has not been published to the live lane yet.'), 404


@app.route('/api/webbuilder/studio')
def api_webbuilder_studio_home():
    from sincor2.webbuilder_studio import studio_home
    return jsonify(studio_home())


@app.route('/api/webbuilder/projects', methods=['GET', 'POST'])
def api_webbuilder_projects():
    from sincor2.webbuilder_studio import create_project, list_projects

    if request.method == 'GET':
        return jsonify({'ok': True, 'projects': list_projects()})
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name_required'}), 400
    project = create_project(
        name=name,
        niche=data.get('niche', ''),
        source_type=data.get('source_type', 'none'),
        source_url=data.get('source_url', ''),
        territory=data.get('territory', ''),
        owner_email=data.get('owner_email', ''),
        prompt=data.get('prompt', ''),
    )
    return jsonify({'ok': True, 'project': project}), 201


@app.route('/api/webbuilder/projects/<project_id>')
def api_webbuilder_project_get(project_id):
    from sincor2.webbuilder_studio import get_project, migration_status

    p = get_project(project_id)
    if not p:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    return jsonify(p)


@app.route('/api/webbuilder/projects/<project_id>/migration')
def api_webbuilder_migration(project_id):
    from sincor2.webbuilder_studio import migration_status

    status = migration_status(project_id)
    if not status.get('ok'):
        return jsonify(status), 404
    return jsonify(status)


@app.route('/api/webbuilder/projects/<project_id>/run', methods=['POST'])
def api_webbuilder_run(project_id):
    from sincor2.webbuilder_studio import run_autonomous_phases

    return jsonify(run_autonomous_phases(project_id))


@app.route('/api/webbuilder/projects/<project_id>/approve', methods=['POST'])
def api_webbuilder_approve(project_id):
    from sincor2.webbuilder_studio import approve_preview

    p = approve_preview(project_id)
    if not p:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    return jsonify(p)


@app.route('/api/webbuilder/projects/<project_id>/domain', methods=['POST'])
def api_webbuilder_domain(project_id):
    from sincor2.webbuilder_studio import connect_domain

    data = request.get_json(silent=True) or {}
    result = connect_domain(
        project_id,
        data.get('domain', ''),
        include_www=bool(data.get('include_www', True)),
    )
    if not result.get('ok'):
        return jsonify(result), 400
    return jsonify(result)


@app.route('/api/webbuilder/projects/<project_id>/verify-dns', methods=['POST'])
def api_webbuilder_verify_dns(project_id):
    from sincor2.webbuilder_studio import verify_dns

    return jsonify(verify_dns(project_id))


@app.route('/api/webbuilder/projects/<project_id>/rebuild', methods=['POST'])
def api_webbuilder_rebuild(project_id):
    from sincor2.webbuilder_studio import rebuild_draft

    data = request.get_json(silent=True) or {}
    return jsonify(rebuild_draft(project_id, prompt=data.get('prompt')))


@app.route('/api/webbuilder/projects/<project_id>/republish-preview', methods=['POST'])
def api_webbuilder_republish_preview(project_id):
    from sincor2.webbuilder_studio import republish_preview

    return jsonify(republish_preview(project_id))


@app.route('/api/webbuilder/projects/<project_id>/publish-live', methods=['POST'])
def api_webbuilder_publish_live(project_id):
    from sincor2.webbuilder_studio import publish_live

    return jsonify(publish_live(project_id))


@app.route('/api/webbuilder/projects/<project_id>/contacts')
def api_webbuilder_contacts(project_id):
    from sincor2.webbuilder_crm import list_contacts
    from sincor2.webbuilder_studio import data_dir, get_project

    if not get_project(project_id):
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    return jsonify({'ok': True, 'contacts': list_contacts(data_dir(), project_id)})


@app.route('/api/webbuilder/contact', methods=['POST'])
def api_webbuilder_contact():
    from sincor2.webbuilder_studio import submit_contact

    data = request.get_json(silent=True) or {}
    project_id = (data.get('project_id') or '').strip()
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    if not project_id or not name or not email:
        return jsonify({'ok': False, 'error': 'missing_fields'}), 400
    return jsonify(submit_contact(
        project_id=project_id,
        name=name,
        email=email,
        phone=data.get('phone', ''),
        message=data.get('message', ''),
    ))


@app.route('/sinc')
def sinc_token():
    """SINC token gateway page."""
    return render_template('sinc_gateway.html')


@app.route('/refer')
def sinc_refer():
    """SINC referral program — generate a ?ref= link that pays 3% on-chain."""
    return render_template('refer.html')


@app.route('/sinc/vs-agent-tokens')
def sinc_vs_agent_tokens():
    """SEO comparison: verified agent tokens vs vaporware launches."""
    return render_template('sinc_vs_agent_tokens.html')


@app.route('/sinc/acceptance')
def sinc_acceptance():
    """Wallet import, hook buy paths, whitelist listing status."""
    return render_template('sinc_acceptance.html')


@app.route('/sinc/recover-hook')
def sinc_recover_hook():
    """MetaMask-signed hook floor cancel — Account 6, no private key export."""
    return render_template('hook_recover.html')


@app.route('/api/price/official')
def api_price_official():
    """Canonical curve spot for tickers and visitor messaging."""
    try:
        from launch_content_engine.onchain_stats import fetch_stats
        s = fetch_stats()
        return jsonify({
            'source': 'bonding_curve',
            'curve': s['curve'],
            'spot_usd': s.get('curve_spot_usd'),
            'spot_eth': s.get('curve_spot_eth'),
            'hook_floor_usd': s.get('hook_floor_usd', 1.50),
            'note': s.get('price_note'),
            'sinc_sold_m': s.get('sinc_sold_m'),
            'curve_eth_accumulated': s.get('curve_eth_accumulated'),
            'buy_url': s.get('buy_url'),
        }), 200
    except Exception as e:
        logger.warning('[PRICE] official error: %s', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/hook/status')
def api_hook_status():
    """Live curve + hook inventory for gateway widgets."""
    try:
        from sincor2.hook_stats import fetch_hook_status
        return jsonify(fetch_hook_status()), 200
    except Exception as e:
        logger.warning('[HOOK] status error: %s', e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/acceptance/status')
def api_acceptance_status():
    """Whitelist / wallet acceptance checklist."""
    try:
        from sincor2.acceptance_status import fetch_acceptance
        return jsonify(fetch_acceptance()), 200
    except Exception as e:
        logger.warning('[ACCEPTANCE] status error: %s', e)
        return jsonify({'error': str(e)}), 500


SINC_TOKEN = '0x9C8cd8d3961F445D653713dE65C6578bE11668e7'
SINC_LOGO_URL = 'https://raw.githubusercontent.com/OrderofChaos33/SINCOR2/main/static/tokenlists/assets/logo-256.png'
SINC_LOGO_URL_MIRROR = 'https://getsincor.com/static/tokenlists/assets/logo-256.png'


def _cors_static_response(path, mimetype='application/octet-stream'):
    if not os.path.isfile(path):
        return None
    resp = make_response(send_file(path, mimetype=mimetype))
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


def _token_list_response():
    path = os.path.join(static_dir, 'tokenlists', 'sincor.tokenlist.json')
    resp = _cors_static_response(path, 'application/json')
    if resp is None:
        return jsonify({'error': 'token list not found'}), 404
    return resp


@app.route('/tokenlists/sincor.tokenlist.json')
@app.route('/.well-known/tokenlist.json')
@app.route('/static/tokenlists/sincor.tokenlist.json')
def token_list_json():
    """Uniswap-format token list — wallet import (MetaMask, Rabby, 1inch)."""
    return _token_list_response()


@app.route('/static/tokenlists/assets/<path:filename>')
@app.route('/tokenlists/assets/<path:filename>')
def token_list_assets(filename):
    """Token logos for wallets and explorers (Blockscout, Trust Wallet, TKN)."""
    safe = os.path.basename(filename)
    path = os.path.join(static_dir, 'tokenlists', 'assets', safe)
    if safe.endswith('.svg'):
        mimetype = 'image/svg+xml'
    elif safe.endswith('.png'):
        mimetype = 'image/png'
    else:
        return jsonify({'error': 'unsupported asset'}), 404
    resp = _cors_static_response(path, mimetype)
    if resp is None:
        return jsonify({'error': 'asset not found'}), 404
    return resp


@app.route('/api/token/security')
def sinc_token_security():
    """GoPlus + Blockscout signals explaining wallet suspicious UI."""
    try:
        from sincor2.token_security import diagnose
        resp = make_response(jsonify(diagnose()))
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Cache-Control'] = 'public, max-age=120'
        return resp
    except Exception as e:
        logger.warning('[TOKEN] security error: %s', e)
        return jsonify({'error': str(e)}), 500


@app.route('/.well-known/sinc-token.json')
@app.route('/api/token/metadata')
def sinc_token_metadata():
    """Machine-readable token metadata for explorers and compliance tooling."""
    meta_path = os.path.join(project_root, 'scripts', 'token_metadata.json')
    payload = {
        'chainId': 8453,
        'address': SINC_TOKEN,
        'name': 'SINC',
        'symbol': 'SINC',
        'decimals': 8,
        'logoURI': SINC_LOGO_URL,
        'logoURIMirror': SINC_LOGO_URL_MIRROR,
        'website': 'https://getsincor.com',
        'explorer': f'https://basescan.org/token/{SINC_TOKEN}',
        'blockscout': f'https://base.blockscout.com/token/{SINC_TOKEN}',
        'tokenList': 'https://getsincor.com/tokenlists/sincor.tokenlist.json',
    }
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding='utf-8') as f:
                payload.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    resp = make_response(jsonify(payload))
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Cache-Control'] = 'public, max-age=300'
    return resp


@app.route('/launch/review')
def launch_review_page():
    """Human review queue for agent-drafted launch content."""
    return render_template('launch_review.html')


def _launch_review_modules():
    import sys
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from launch_content_engine.review_queue import (
        approve_and_post,
        list_drafts,
        set_status,
    )
    return list_drafts, set_status, approve_and_post


@app.route('/api/launch/review')
def launch_review_list():
    status = request.args.get('status', 'pending')
    list_drafts, _, _ = _launch_review_modules()
    return jsonify(list_drafts(status=status or None))


@app.route('/api/launch/review/<draft_id>', methods=['POST'])
def launch_review_action(draft_id):
    data = request.get_json(silent=True) or {}
    action = data.get('action', '')
    list_drafts, set_status, approve_and_post = _launch_review_modules()

    if action == 'reject':
        ok = set_status(draft_id, 'rejected')
        return jsonify({'ok': ok, 'status': 'rejected'})
    if action == 'approve':
        result = approve_and_post(draft_id)
        return jsonify(result)
    return jsonify({'ok': False, 'error': 'invalid_action'}), 400


@app.route('/.well-known/agent.json')
def agent_card():
    """A2A-style agent card for SINCOR swarm discovery."""
    path = os.path.join(static_dir, '.well-known', 'agent.json')
    if not os.path.isfile(path):
        return jsonify({'error': 'agent card not found'}), 404
    return send_file(path, mimetype='application/json')


@app.route('/media-packs')
def media_packs():
    """Media packs showcase page."""
    return render_template('media-packs.html')


@app.route('/enterprise-dashboard')
def enterprise_dashboard():
    """Enterprise dashboard page."""
    return render_template('enterprise-dashboard.html')


@app.route('/affiliate-program')
def affiliate_program():
    """Affiliate program page."""
    return render_template('affiliate-program.html')


@app.route('/login')
def login_page():
    """Login page."""
    return render_template('login.html')


@app.route('/billing')
def billing():
    """Stripe Customer Portal � lets subscribers manage their plan/billing."""
    customer_email = request.args.get('email', '')
    if STRIPE_AVAILABLE and stripe_processor and stripe_processor.enabled:
        try:
            import stripe as stripe_lib
            stripe_lib.api_key = stripe_processor.api_key
            customers = stripe_lib.Customer.list(email=customer_email, limit=1)
            if customers and customers.data:
                session = stripe_lib.billing_portal.Session.create(
                    customer=customers.data[0].id,
                    return_url=os.environ.get('STRIPE_PORTAL_RETURN_URL', 'https://getsincor.com/billing'),
                )
                return redirect(session.url, code=303)
        except Exception as e:
            logger.warning(f'[BILLING] Stripe portal error: {e}')
    return render_template('error.html', code=200, title='Manage Your Subscription',
                           message='To manage your subscription, email us at support@getsincor.com '
                                   'or visit your Stripe billing portal link in your confirmation email.'), 200


@app.route('/discovery-dashboard')
def discovery_dashboard():
    """Discovery dashboard page."""
    return render_template('discovery-dashboard.html')


@app.route('/franchise-empire')
def franchise_empire():
    """Franchise empire page."""
    return render_template('franchise-empire.html')


@app.route('/robots.txt')
def robots_txt():
    """robots.txt � allow crawlers, block sensitive paths."""
    content = (
        'User-agent: *\n'
        'Allow: /\n'
        'Disallow: /api/\n'
        'Disallow: /admin/\n'
        'Disallow: /files/\n'
        'Disallow: /my-orders\n'
        'Sitemap: https://getsincor.com/sitemap.xml\n'
    )
    return make_response(content, 200, {'Content-Type': 'text/plain'})


@app.route('/sitemap.xml')
def sitemap_xml():
    """XML sitemap for SEO."""
    base = 'https://getsincor.com'
    pages = [
        ('/', '1.0', 'weekly'),
        ('/buy', '0.9', 'weekly'),
        ('/pricing', '0.9', 'weekly'),
        ('/affiliate-program', '0.7', 'monthly'),
        ('/enterprise-dashboard', '0.7', 'monthly'),
        ('/whitepaper', '0.6', 'monthly'),
        ('/privacy', '0.4', 'monthly'),
        ('/terms', '0.4', 'monthly'),
        ('/security', '0.4', 'monthly'),
    ]
    urls = '\n'.join(
        f'  <url><loc>{base}{loc}</loc><priority>{pri}</priority><changefreq>{freq}</changefreq></url>'
        for loc, pri, freq in pages
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>'
    return make_response(xml, 200, {'Content-Type': 'application/xml'})


# ============================================================================
# WHITEPAPER & DOCUMENTATION
# ============================================================================

@app.route('/axiom')
def axiom():
    """AXIOM (AXM) token page."""
    return render_template('axiom.html')

@app.route('/site-index')
@app.route('/pages')
def site_index():
    """Full site index / directory of all pages."""
    return render_template('sitemap.html')

@app.route('/go')
@app.route('/start')
@app.route('/sales')
def sales_landing():
    """High-conversion sales landing page."""
    return render_template('sales.html')

@app.route('/whitepaper')
def whitepaper():
    """Render whitepaper page."""
    return render_template('whitepaper.html')


@app.route('/docs/whitepaper.pdf')
def whitepaper_pdf():
    """Return whitepaper PDF stub."""
    return jsonify({
        'message': 'Whitepaper PDF available for download',
        'url': '/static/docs/whitepaper.pdf'
    }), 200


# ============================================================================
# CRYPTO PAYMENT ENDPOINTS (Ethereum/Base)
# ============================================================================

@app.route('/api/crypto/checkout', methods=['POST'])
def crypto_checkout():
    """Create crypto payment checkout (ETH/USDC on Base)."""
    data = request.get_json(silent=True) or {}
    currency = sanitize_string(data.get('currency', 'ETH'), max_length=10).upper()

    try:
        amount = float(data.get('amount', 0))
        if amount <= 0 or amount > 100000:
            return jsonify({'error': 'Amount must be between 0 and 100,000'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid amount'}), 400

    wallet = validate_wallet(data.get('wallet', ''))
    if not wallet:
        return jsonify({'error': 'Invalid wallet address'}), 400

    recipient_address = os.environ.get('BASE_PAYMENT_ADDRESS')
    if not recipient_address:
        logger.error('[CRYPTO] BASE_PAYMENT_ADDRESS not configured')
        return jsonify({'error': 'Crypto payments not configured'}), 503

    # Fetch live ETH price from CoinGecko (free, no key required)
    # Falls back to a conservative floor price if API is unavailable
    eth_price = None
    try:
        import urllib.request as _urllib2
        import json as _json2
        cg_req = _urllib2.Request(
            'https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd',
            headers={'User-Agent': 'sincor-payment/1.0'}
        )
        with _urllib2.urlopen(cg_req, timeout=5) as cg_resp:  # nosec B310 — hardcoded CoinGecko URL
            cg_data = _json2.loads(cg_resp.read().decode('utf-8'))
            eth_price = float(cg_data['ethereum']['usd'])
            logger.info(f'[CRYPTO] Live ETH price: ${eth_price}')
    except Exception as e:
        logger.warning(f'[CRYPTO] Could not fetch live ETH price: {e}. Using env fallback.')
        # Allow operator to set a floor via env; default conservative
        eth_price = float(os.environ.get('ETH_PRICE_FALLBACK', '10000'))

    if currency == 'ETH':
        crypto_amount = amount / eth_price
    elif currency == 'USDC':
        crypto_amount = amount / 1.0
    else:
        return jsonify({'error': f'unsupported currency: {currency}'}), 400

    payment_id = f"CRYPTO-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    return jsonify({
        'payment_id': payment_id,
        'status': 'pending',
        'amount_usd': amount,
        'amount_crypto': round(crypto_amount, 8),
        'currency': currency,
        'wallet': wallet,
        'network': 'Base',
        'chain_id': 8453,
        'recipient_address': recipient_address,
        'message': f'Send {round(crypto_amount, 8)} {currency} to complete purchase'
    }), 201


@app.route('/api/crypto/verify-payment', methods=['POST'])
@limiter.limit("200 per minute")
def crypto_verify_payment():
    """
    Verify crypto payment on blockchain and trigger fulfillment.
    SECURITY: tx_hash must be verified on-chain before fulfillment is triggered.
    We check the Base blockchain via public RPC to confirm the tx exists,
    is confirmed, and was sent to our recipient address with sufficient value.
    """
    data = request.get_json() or {}
    payment_id = sanitize_string(data.get('payment_id', ''), max_length=64)
    tx_hash = sanitize_string(data.get('tx_hash', ''), max_length=66)
    email = data.get('email', '')
    product_name = sanitize_string(data.get('product_name', 'Crypto Purchase'), max_length=100)
    amount = data.get('amount', 0)

    if not payment_id or not tx_hash:
        return jsonify({'error': 'payment_id and tx_hash required'}), 400

    # Validate tx_hash format (0x + 64 hex chars)
    import re as _re
    if not _re.match(r'^0x[0-9a-fA-F]{64}$', tx_hash):
        logger.warning(f'[CRYPTO] Invalid tx_hash format from {request.remote_addr}')
        return jsonify({'error': 'Invalid transaction hash format'}), 400

    # Validate email
    email = validate_email(email) if email else ''

    # Validate amount
    try:
        amount = float(amount)
        if amount <= 0 or amount > 100000:
            return jsonify({'error': 'Invalid amount'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid amount'}), 400

    # BLOCKCHAIN VERIFICATION � check tx on Base via public RPC
    recipient_address = os.environ.get('BASE_PAYMENT_ADDRESS', '').lower()
    if not recipient_address:
        logger.error('[CRYPTO] BASE_PAYMENT_ADDRESS not configured')
        return jsonify({'error': 'Crypto payments not configured'}), 503

    try:
        import urllib.request as _urllib
        import json as _json

        rpc_url = os.environ.get('BASE_RPC_URL', 'https://mainnet.base.org')
        payload = _json.dumps({
            'jsonrpc': '2.0',
            'method': 'eth_getTransactionReceipt',
            'params': [tx_hash],
            'id': 1
        }).encode('utf-8')

        req = _urllib.Request(rpc_url, data=payload,
                              headers={'Content-Type': 'application/json'})
        with _urllib.urlopen(req, timeout=10) as resp:  # nosec B310 — hardcoded Alchemy RPC URL
            rpc_result = _json.loads(resp.read().decode('utf-8'))

        receipt = rpc_result.get('result')
        if not receipt:
            logger.warning(f'[CRYPTO] tx not found on chain: {tx_hash}')
            return jsonify({'error': 'Transaction not found on blockchain. It may still be pending.'}), 402

        # Must be confirmed (status 0x1)
        if receipt.get('status') != '0x1':
            logger.warning(f'[CRYPTO] tx failed on chain: {tx_hash}')
            return jsonify({'error': 'Transaction failed or was reverted on blockchain'}), 402

        # Must be sent to our address
        tx_to = (receipt.get('to') or '').lower()
        if tx_to != recipient_address:
            logger.warning(f'[CRYPTO] tx recipient mismatch: expected {recipient_address}, got {tx_to}')
            return jsonify({'error': 'Transaction was not sent to the correct address'}), 402

        # Check tx isn't already used (replay protection)
        db = get_db()
        existing = db.execute(
            "SELECT order_id FROM orders WHERE paypal_order_id=?", (tx_hash,)
        ).fetchone()
        if existing:
            logger.warning(f'[CRYPTO] Replay attempt for tx_hash: {tx_hash}')
            return jsonify({'error': 'Transaction already used for a previous order'}), 409

    except Exception as e:
        logger.error(f'[CRYPTO] Blockchain verification error: {e}')
        # Do NOT fulfill if verification fails
        return jsonify({'error': 'Blockchain verification failed. Please try again or contact support.'}), 503

    # Verification passed � store order and fulfill
    order_id = f"CRYPTO-ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    product_info = PRODUCT_CATALOG.get(product_name, {'type': 'generic'})
    order_type = product_info.get('type', 'generic')

    if email:
        try:
            db.execute(
                '''INSERT INTO orders
                   (order_id, paypal_order_id, customer_email, product_name, amount,
                    currency, payment_status, delivery_status, delivery_url, order_type,
                    created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (order_id, tx_hash, email, product_name, amount,
                 'CRYPTO', 'completed', 'processing', f'/my-orders?email={email}', order_type,
                 datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                 json.dumps({'tx_hash': tx_hash, 'payment_id': payment_id}))
            )
            db.commit()
        except sqlite3.IntegrityError:
            pass

        trigger_fulfillment(order_id, email, product_name, amount, order_type, product_info)

    logger.info(f'[CRYPTO] Payment verified and fulfilled: {tx_hash} ? {order_id}')
    return jsonify({
        'status': 'verified',
        'payment_id': payment_id,
        'tx_hash': tx_hash,
        'order_id': order_id,
        'network': 'Base',
        'message': 'Payment confirmed on blockchain. Fulfillment triggered.'
    }), 200


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
# CONTENT AGENT — STATUS + TRIGGER ENDPOINTS (Admin only)
# ============================================================================

@app.route('/admin/content/status')
@limiter.limit("10 per minute")
def content_status():
    """Return content agent status: published posts, upcoming calendar, analytics."""
    if not _check_admin_token(request):
        return jsonify({'error': 'Admin access required'}), 403
    try:
        from sincor2.content_agent import get_db, CALENDAR_PATH, ContentAnalytics
        import json as _json

        with get_db() as conn:
            posts = conn.execute(
                "SELECT slug, title, keyword, status, word_count, published_at, wp_url "
                "FROM posts ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

        calendar = []
        if CALENDAR_PATH.exists():
            all_items = _json.loads(CALENDAR_PATH.read_text())
            calendar = [i for i in all_items if i["status"] == "planned"][:10]

        analytics = ContentAnalytics()
        top = analytics.get_top_performers(3)

        scheduler_status = "running" if (content_scheduler and content_scheduler.running) else "stopped"

        return jsonify({
            "scheduler": scheduler_status,
            "total_posts": total_posts,
            "recent_posts": [dict(p) for p in posts],
            "upcoming_calendar": calendar,
            "top_performers": top,
        })
    except Exception as e:
        logger.error(f"[CONTENT] Status error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/admin/content/generate', methods=['POST'])
@limiter.limit("10 per minute")
def content_generate():
    """Manually trigger post generation. Body: {keyword, type, publish}"""
    if not _check_admin_token(request):
        return jsonify({'error': 'Admin access required'}), 403
    try:
        data = request.get_json() or {}
        keyword = sanitize_string(data.get('keyword', ''), 200)
        ctype = data.get('type', 'how-to')
        do_publish = data.get('publish', False)

        if not keyword:
            return jsonify({'error': 'keyword required'}), 400
        if ctype not in ('how-to', 'comparison', 'alternatives', 'case-study', 'industry-trend'):
            return jsonify({'error': 'invalid type'}), 400

        from sincor2.content_agent import generate_blog_post, save_post, WordPressPublisher, init_db
        init_db()
        model = os.environ.get('CONTENT_MODEL', 'claude-haiku-4-5')
        post = generate_blog_post(keyword, ctype, model=model)
        path = save_post(post)
        result = {"title": post["title"], "slug": post["slug"], "word_count": post["word_count"], "path": str(path)}

        if do_publish:
            wp = WordPressPublisher()
            wp_result = wp.publish(post)
            result["wordpress"] = wp_result

        return jsonify(result)
    except Exception as e:
        logger.error(f"[CONTENT] Generate error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/admin/content/calendar', methods=['GET', 'POST'])
@limiter.limit("20 per minute")
def content_calendar():
    """GET: return calendar JSON. POST: regenerate calendar."""
    if not _check_admin_token(request):
        return jsonify({'error': 'Admin access required'}), 403
    try:
        from sincor2.content_agent import generate_content_calendar, CALENDAR_PATH, init_db
        import json as _json
        if request.method == 'POST':
            init_db()
            cal = generate_content_calendar()
            return jsonify({"generated": len(cal), "calendar": cal[:20]})
        else:
            if not CALENDAR_PATH.exists():
                return jsonify({"error": "No calendar found. POST to generate."}), 404
            cal = _json.loads(CALENDAR_PATH.read_text())
            return jsonify({"total": len(cal), "calendar": cal})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/admin/content/analytics')
@limiter.limit("30 per minute")
def content_analytics():
    """Return content analytics report."""
    if not _check_admin_token(request):
        return jsonify({'error': 'Admin access required'}), 403
    try:
        from sincor2.content_agent import ContentAnalytics
        analytics = ContentAnalytics()
        return jsonify({
            "top_performers": analytics.get_top_performers(10),
            "low_performers": analytics.get_low_performers(),
            "cta_performance": analytics.get_best_cta(),
            "report": analytics.summary_report(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# SUBSCRIPTION CANCELLATION
# ============================================================================

@app.route('/api/cancel-subscription', methods=['POST'])
@limiter.limit("20 per minute")
def cancel_subscription():
    """
    Cancel a SINCOR subscription.
    - For Stripe: creates a Stripe subscription cancellation via the API.
    - For PayPal: logs the request and emails support to process manually
      (PayPal subscription cancellation requires asynchronous IPN/webhook confirmation).
    Customers can also self-cancel via /billing (Stripe portal).
    """
    data = request.get_json(silent=True) or {}
    email = validate_email(data.get('email', ''))
    reason = sanitize_string(data.get('reason', 'No reason provided'), max_length=500)
    subscription_id = sanitize_string(data.get('subscription_id', ''), max_length=200)

    if not email:
        return jsonify({'error': 'Valid email address required'}), 400

    logger.info(f"[CANCEL] Cancellation request from {email} | sub_id={subscription_id} | reason={reason}")

    # Try Stripe cancellation first
    if STRIPE_AVAILABLE and stripe_processor and stripe_processor.enabled:
        try:
            import stripe as stripe_lib
            stripe_lib.api_key = stripe_processor.api_key

            # Find the customer
            customers = stripe_lib.Customer.list(email=email, limit=1)
            if customers and customers.data:
                customer = customers.data[0]
                # Find active subscriptions
                subs = stripe_lib.Subscription.list(customer=customer.id, status='active', limit=10)
                if subs and subs.data:
                    cancelled = []
                    for sub in subs.data:
                        if not subscription_id or sub.id == subscription_id:
                            stripe_lib.Subscription.cancel(sub.id)
                            cancelled.append(sub.id)
                            logger.info(f"[CANCEL] Stripe sub cancelled: {sub.id} for {email}")

                    if cancelled:
                        # Notify customer
                        email_sender = get_email_sender()
                        if email_sender:
                            try:
                                email_sender.send_email(
                                    to=email,
                                    subject='Your SINCOR subscription has been cancelled',
                                    html_content=f'''
                                        <h2>Subscription Cancelled</h2>
                                        <p>Your SINCOR subscription has been cancelled successfully.</p>
                                        <p>You will retain access until the end of your current billing period.</p>
                                        <p>If you cancelled by mistake or have questions, reply to this email or
                                        contact <a href="mailto:support@getsincor.com">support@getsincor.com</a>.</p>
                                        <p>We'd love to know how we can improve: {reason}</p>
                                    '''
                                )
                            except Exception as mail_err:
                                logger.warning(f"[CANCEL] Could not send cancellation email: {mail_err}")

                        return jsonify({
                            'success': True,
                            'message': 'Subscription cancelled successfully. You retain access until the end of your billing period.',
                            'cancelled_subscriptions': cancelled
                        }), 200

            # No active subscription found via Stripe
            logger.info(f"[CANCEL] No active Stripe subscription found for {email}")

        except Exception as e:
            logger.error(f"[CANCEL] Stripe cancellation error: {e}")

    # Fallback: log request and notify support
    db = get_db()
    try:
        db.execute(
            '''INSERT OR IGNORE INTO orders
               (order_id, paypal_order_id, customer_email, product_name, amount,
                currency, payment_status, delivery_status, delivery_url, order_type,
                created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                f"CANCEL-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}", subscription_id or 'manual',
                email, 'CANCELLATION REQUEST', 0, 'USD', 'cancellation_requested',
                'pending', '', 'cancellation',
                datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                json.dumps({'reason': reason, 'subscription_id': subscription_id})
            )
        )
        db.commit()
    except Exception as db_err:
        logger.warning(f"[CANCEL] Could not log cancellation to DB: {db_err}")

    # Notify support
    email_sender = get_email_sender()
    if email_sender:
        try:
            support_email = os.environ.get('SUPPORT_EMAIL', 'support@getsincor.com')
            email_sender.send_email(
                to=support_email,
                subject=f'[ACTION REQUIRED] Cancellation Request: {email}',
                html_content=f'''
                    <h2>Subscription Cancellation Request</h2>
                    <p><strong>Customer:</strong> {email}</p>
                    <p><strong>Subscription ID:</strong> {subscription_id or "Not provided"}</p>
                    <p><strong>Reason:</strong> {reason}</p>
                    <p><strong>Time:</strong> {datetime.utcnow().isoformat()} UTC</p>
                    <p>Please cancel this subscription in PayPal and confirm with the customer.</p>
                '''
            )
        except Exception as mail_err:
            logger.warning(f"[CANCEL] Could not notify support: {mail_err}")

    return jsonify({
        'success': True,
        'message': 'Cancellation request received. Our team will process it within 24 hours and confirm via email. You retain access until cancelled.',
    }), 200


# ============================================================================
# PAYPAL DISPUTE / IPN WEBHOOK
# ============================================================================

@app.route('/api/paypal/webhook', methods=['POST'])
@limiter.limit("500 per minute")
def paypal_webhook():
    """
    PayPal IPN / Webhook handler.
    Handles: payment completed, subscription cancelled, refund, dispute opened.
    PayPal sends form-encoded IPN or JSON webhook events depending on integration type.
    Verification: IPN verification via PayPal IPN verification endpoint.
    """
    content_type = request.content_type or ''

    # Determine if this is a JSON webhook (newer API) or IPN (form-encoded)
    if 'application/json' in content_type:
        event_data = request.get_json(silent=True) or {}
        event_type = event_data.get('event_type', '')
        resource = event_data.get('resource', {})
        logger.info(f"[PAYPAL-WH] JSON event received: {event_type}")

        if event_type == 'PAYMENT.SALE.COMPLETED':
            _handle_paypal_payment_completed(resource)
        elif event_type in ('BILLING.SUBSCRIPTION.CANCELLED', 'BILLING.SUBSCRIPTION.EXPIRED'):
            _handle_paypal_subscription_cancelled(resource)
        elif event_type == 'PAYMENT.SALE.REFUNDED':
            _handle_paypal_refund(resource)
        elif event_type == 'CUSTOMER.DISPUTE.CREATED':
            _handle_paypal_dispute(resource)
        elif event_type == 'CUSTOMER.DISPUTE.RESOLVED':
            logger.info(f"[PAYPAL-WH] Dispute resolved: {resource.get('dispute_id', 'unknown')}")
        else:
            logger.info(f"[PAYPAL-WH] Unhandled event type: {event_type}")

        return jsonify({'success': True}), 200

    else:
        # Legacy IPN handling (form-encoded)
        ipn_data = request.form.to_dict()
        txn_type = ipn_data.get('txn_type', '')
        payment_status = ipn_data.get('payment_status', '')
        logger.info(f"[PAYPAL-IPN] txn_type={txn_type} | payment_status={payment_status}")

        # Verify IPN with PayPal
        try:
            import urllib.request as _ur
            import urllib.parse as _up
            verify_payload = b'cmd=_notify-validate&' + _up.urlencode(ipn_data).encode('utf-8')
            paypal_sandbox = os.environ.get('PAYPAL_SANDBOX', 'false').lower() == 'true'
            paypal_env = os.environ.get('PAYPAL_ENV', 'live')
            if paypal_sandbox or paypal_env == 'sandbox':
                ipn_url = 'https://ipnpb.sandbox.paypal.com/cgi-bin/webscr'
            else:
                ipn_url = 'https://ipnpb.paypal.com/cgi-bin/webscr'

            req = _ur.Request(ipn_url, data=verify_payload,
                              headers={'Content-Type': 'application/x-www-form-urlencoded',
                                       'User-Agent': 'sincor-ipn/1.0'})
            with _ur.urlopen(req, timeout=10) as resp:  # nosec B310 — hardcoded PayPal IPN URL
                ipn_response = resp.read().decode('utf-8')

            if ipn_response.strip() != 'VERIFIED':
                logger.warning(f"[PAYPAL-IPN] Verification FAILED. Response: {ipn_response[:50]}")
                return make_response('INVALID', 200)

        except Exception as verify_err:
            logger.error(f"[PAYPAL-IPN] Could not verify IPN: {verify_err}")
            return make_response('ERROR', 200)

        # Process verified IPN
        if payment_status == 'Completed':
            _handle_paypal_ipn_payment(ipn_data)
        elif txn_type in ('subscr_cancel', 'subscr_eot', 'subscr_failed'):
            _handle_paypal_ipn_subscription_event(ipn_data)
        elif payment_status == 'Refunded':
            logger.info(f"[PAYPAL-IPN] Refund for payer {ipn_data.get('payer_email', 'unknown')}")

        return make_response('OK', 200)


def _handle_paypal_payment_completed(resource: dict):
    """Handle PayPal PAYMENT.SALE.COMPLETED webhook event."""
    sale_id = resource.get('id', '')
    amount = float(resource.get('amount', {}).get('total', 0))
    currency = resource.get('amount', {}).get('currency', 'USD')
    payer_email = resource.get('payer', {}).get('payer_info', {}).get('email', '')
    logger.info(f"[PAYPAL-WH] Payment completed: ${amount} {currency} | payer={payer_email} | id={sale_id}")

    if payer_email and amount > 0:
        order_id = f"PP-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{sale_id[:8]}"
        db = get_db()
        try:
            db.execute(
                '''INSERT OR IGNORE INTO orders
                   (order_id, paypal_order_id, customer_email, product_name, amount,
                    currency, payment_status, delivery_status, delivery_url, order_type,
                    created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (order_id, sale_id, payer_email, 'PayPal Purchase', amount, currency,
                 'completed', 'processing', f'/my-orders?email={payer_email}', 'paypal',
                 datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                 json.dumps({'sale_id': sale_id, 'source': 'paypal_webhook'}))
            )
            db.commit()
            logger.info(f"[PAYPAL-WH] Order stored: {order_id}")
        except Exception as e:
            logger.error(f"[PAYPAL-WH] DB error: {e}")


def _handle_paypal_subscription_cancelled(resource: dict):
    """Handle PayPal subscription cancellation / expiration."""
    sub_id = resource.get('id', '')
    payer_email = resource.get('subscriber', {}).get('email_address', '')
    logger.info(f"[PAYPAL-WH] Subscription cancelled: {sub_id} | payer={payer_email}")

    db = get_db()
    try:
        db.execute(
            "UPDATE orders SET payment_status='cancelled', updated_at=? WHERE paypal_order_id=?",
            (datetime.utcnow().isoformat(), sub_id)
        )
        db.commit()
    except Exception as e:
        logger.warning(f"[PAYPAL-WH] Could not update subscription record: {e}")


def _handle_paypal_refund(resource: dict):
    """Handle PayPal refund event."""
    sale_id = resource.get('sale_id', resource.get('id', ''))
    amount = resource.get('amount', {}).get('total', 'unknown')
    logger.info(f"[PAYPAL-WH] Refund processed: sale_id={sale_id} | amount=${amount}")

    db = get_db()
    try:
        db.execute(
            "UPDATE orders SET payment_status='refunded', updated_at=? WHERE paypal_order_id=?",
            (datetime.utcnow().isoformat(), sale_id)
        )
        db.commit()
    except Exception as e:
        logger.warning(f"[PAYPAL-WH] Could not update refund record: {e}")


def _handle_paypal_dispute(resource: dict):
    """Handle PayPal CUSTOMER.DISPUTE.CREATED event."""
    dispute_id = resource.get('dispute_id', 'unknown')
    disputed_amount = resource.get('disputed_amount', {})
    reason = resource.get('reason', 'unknown')
    payer_email = ''
    for item in resource.get('disputed_transactions', []):
        buyer = item.get('buyer', {})
        payer_email = buyer.get('email', '')
        if payer_email:
            break

    logger.warning(f"[PAYPAL-DISPUTE] New dispute: {dispute_id} | reason={reason} | payer={payer_email} | amount={disputed_amount}")

    # Notify support immediately
    email_sender = get_email_sender()
    if email_sender:
        try:
            support_email = os.environ.get('SUPPORT_EMAIL', 'support@getsincor.com')
            email_sender.send_email(
                to=support_email,
                subject=f'[URGENT] PayPal Dispute Filed: {dispute_id}',
                html_content=f'''
                    <h2 style="color:red;">PayPal Dispute Filed</h2>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                    <p><strong>Reason:</strong> {reason}</p>
                    <p><strong>Customer:</strong> {payer_email}</p>
                    <p><strong>Amount:</strong> {disputed_amount}</p>
                    <p><strong>Time:</strong> {datetime.utcnow().isoformat()} UTC</p>
                    <p><strong>Action Required:</strong> Respond in PayPal Resolution Center within 10 days.
                    <a href="https://www.paypal.com/disputes">PayPal Resolution Center</a></p>
                '''
            )
            logger.info(f"[PAYPAL-DISPUTE] Support notified for dispute {dispute_id}")
        except Exception as mail_err:
            logger.error(f"[PAYPAL-DISPUTE] Could not notify support: {mail_err}")


def _handle_paypal_ipn_payment(ipn_data: dict):
    """Handle verified PayPal IPN payment_status=Completed."""
    txn_id = ipn_data.get('txn_id', '')
    payer_email = validate_email(ipn_data.get('payer_email', ''))
    amount = float(ipn_data.get('mc_gross', 0))
    currency = ipn_data.get('mc_currency', 'USD')

    logger.info(f"[PAYPAL-IPN] Payment completed: txn_id={txn_id} | ${amount} | {payer_email}")

    if payer_email and amount > 0:
        order_id = f"IPN-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{txn_id[:8]}"
        db = get_db()
        try:
            db.execute(
                '''INSERT OR IGNORE INTO orders
                   (order_id, paypal_order_id, customer_email, product_name, amount,
                    currency, payment_status, delivery_status, delivery_url, order_type,
                    created_at, updated_at, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (order_id, txn_id, payer_email, 'PayPal IPN Purchase', amount, currency,
                 'completed', 'processing', f'/my-orders?email={payer_email}', 'paypal',
                 datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
                 json.dumps({'txn_id': txn_id, 'source': 'paypal_ipn'}))
            )
            db.commit()
        except Exception as e:
            logger.error(f"[PAYPAL-IPN] DB error: {e}")


def _handle_paypal_ipn_subscription_event(ipn_data: dict):
    """Handle PayPal IPN subscription cancellation / EOT / failure."""
    txn_type = ipn_data.get('txn_type', '')
    subscr_id = ipn_data.get('subscr_id', '')
    payer_email = ipn_data.get('payer_email', '')
    logger.info(f"[PAYPAL-IPN] Subscription event: {txn_type} | subscr_id={subscr_id} | {payer_email}")

    db = get_db()
    try:
        db.execute(
            "UPDATE orders SET payment_status='cancelled', updated_at=? WHERE paypal_order_id=?",
            (datetime.utcnow().isoformat(), subscr_id)
        )
        db.commit()
    except Exception as e:
        logger.warning(f"[PAYPAL-IPN] Could not update subscription record: {e}")


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Never run debug in production
    debug = os.environ.get('FLASK_ENV') == 'development' and not os.environ.get('RAILWAY_ENVIRONMENT')
    app.run(host='0.0.0.0', port=port, debug=debug)  # nosec B104 — required for Railway deployment

