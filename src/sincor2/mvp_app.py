"""
SINCOR2 MVP - Minimal Flask Application
A lean, deployable MVP with health checks, home page, buy flow, and Stripe payments.
Stripe checkout → order DB → asset delivery pipeline.
"""

import os
import re
import json
import time
import logging
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, request, jsonify, g, make_response, send_file, redirect
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
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

# Configure JWT — MUST be set in Railway secrets for production
jwt_secret = os.environ.get('JWT_SECRET_KEY')
if not jwt_secret:
    if os.environ.get('RAILWAY_ENVIRONMENT') or os.environ.get('ENVIRONMENT') == 'production':
        logger.critical('[JWT] JWT_SECRET_KEY not set in production! Generating random secret (tokens won\'t survive restarts)')
    jwt_secret = os.urandom(32).hex()
app.config['JWT_SECRET_KEY'] = jwt_secret
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=8)  # Reduced from 24h
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max request size
jwt = JWTManager(app)

# Rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# Stripe integration
try:
    from sincor2.stripe_checkout import get_stripe_checkout
    from sincor2.stripe_routes import stripe_bp, init_stripe_routes
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
        logger.warning("[APP] Stripe not properly configured — set STRIPE_API_KEY")

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
        logger.info(f"{request.method} {request.path} → {response.status_code} ({elapsed:.3f}s)")

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

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint for Railway and monitoring."""
    return jsonify({
        'status': 'healthy',
        'service': 'SINCOR2 MVP',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '1.0.0-mvp'
    }), 200


@app.route('/', methods=['GET'])
def home():
    """Home page."""
    return render_template('home.html')


# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================

# Admin credentials — must be set via environment variables in production
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    logger.warning('[AUTH] ADMIN_USERNAME or ADMIN_PASSWORD not set — admin login disabled')


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
@limiter.limit("5 per minute")
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

# Product pricing — server-side validation of amounts
PRODUCT_PRICES = {
    'Starter': 297,
    'Professional': 997,
    'Enterprise': 2997,
}


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
@limiter.limit("30 per minute")
def payment_webhook():
    """
    Receive Stripe webhook events for payment processing.
    Verifies webhook signature before processing.
    Stores order in DB and triggers product fulfillment/delivery.
    """
    if not stripe_processor or not stripe_processor.enabled:
        logger.error('[WEBHOOK] Stripe not configured — cannot process webhook')
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
    features = product_info.get('features', [])

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
            return f'<meta http-equiv="refresh" content="0; url=/thank-you/{row["order_id"]}" />'

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
        'UNSUBSCRIBE': f'mailto:support@getsincor.com?subject=Unsubscribe',
        'COMPANY_ADDRESS': '123 Innovation Drive, Tech City, TC 12345',
        **tier_flags
    }

    logger.info(f"[EMAIL] Rendering thank-you email for {order_id} | {tier_name} | {order_data.get('customer_email')}")

    return render_template('thank_you_purchase_email.html', **template_vars)


@app.route('/admin/training-vault')
def admin_training_vault():
    """
    Render the training vault dashboard for logged-in customers.
    Shows tier-specific guides, videos, industry guides, and onboarding progress.
    """
    # Get customer email from query params or JWT
    customer_email = request.args.get('email')
    if not customer_email:
        customer_email = request.args.get('customer_email')

    if not customer_email or not validate_email(customer_email):
        # Redirect to login if no valid email
        return render_template('error.html', code=401, title='Authentication Required',
                             message="Please log in to access your training vault."), 401

    # Fetch customer's orders from database
    db = get_db()
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
    features = product_info.get('features', [])

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
def download_guide(filename):
    """
    Serve training guide PDF files.
    Generates PDF on first request, caches for subsequent requests.
    """
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
@limiter.limit("10 per minute")
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
@limiter.limit("5 per minute")
def register_airdrop():
    """Register wallet for SIN token airdrop."""
    data = request.get_json(silent=True) or {}
    raw_wallet = data.get('wallet', '')
    tasks = data.get('tasks', {})

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
    """Customer dashboard after purchase."""
    return render_template('dashboard.html')


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


# ============================================================================
# WHITEPAPER & DOCUMENTATION
# ============================================================================

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

    eth_price = 2500
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
def crypto_verify_payment():
    """Verify crypto payment on blockchain and trigger fulfillment."""
    data = request.get_json() or {}
    payment_id = data.get('payment_id', '')
    tx_hash = data.get('tx_hash', '')
    email = data.get('email', '')
    product_name = data.get('product_name', 'Crypto Purchase')
    amount = data.get('amount', 0)

    if not payment_id or not tx_hash:
        return jsonify({'error': 'payment_id and tx_hash required'}), 400

    # Store crypto order in DB
    order_id = f"CRYPTO-ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    product_info = PRODUCT_CATALOG.get(product_name, {'type': 'generic'})
    order_type = product_info.get('type', 'generic')

    if email:
        db = get_db()
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

        # Trigger fulfillment
        trigger_fulfillment(order_id, email, product_name, amount, order_type, product_info)

    return jsonify({
        'status': 'verified',
        'payment_id': payment_id,
        'tx_hash': tx_hash,
        'order_id': order_id,
        'network': 'Base',
        'message': 'Payment confirmed. Fulfillment triggered.'
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
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Never run debug in production
    debug = os.environ.get('FLASK_ENV') == 'development' and not os.environ.get('RAILWAY_ENVIRONMENT')
    app.run(host='0.0.0.0', port=port, debug=debug)

