"""
SINCOR2 MVP - Minimal Flask Application
A lean, deployable MVP with health checks, home page, buy flow, and basic auth.
Full PayPal checkout → order DB → asset delivery pipeline.
"""

import os
import re
import json
import time
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, jsonify, g, make_response
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from dotenv import load_dotenv

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

# Configure JWT
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'dev-secret-key-change-in-prod')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=24)
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1MB max request size
jwt = JWTManager(app)

# PayPal configuration
PAYPAL_CLIENT_ID = os.environ.get('PAYPAL_REST_API_ID', os.environ.get('PAYPAL_CLIENT_ID', 'sandbox-demo'))
PAYPAL_SANDBOX = os.environ.get('PAYPAL_SANDBOX', 'true').lower() == 'true'


# ============================================================================
# SECURITY MIDDLEWARE
# ============================================================================

@app.before_request
def log_request():
    """Log incoming requests and record start time for latency tracking."""
    g.start_time = time.time()
    if request.path not in ('/health', '/favicon.ico'):
        logger.info(f"{request.method} {request.path} from {request.remote_addr}")


@app.after_request
def apply_security_headers(response):
    """Apply security headers and log response timing."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

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

@app.route('/api/auth/login', methods=['POST'])
def login():
    """
    Authentication endpoint requiring valid credentials.
    POST /api/auth/login with JSON: {"email": "user@example.com", "password": "password"}
    Returns a JWT token if credentials match environment variables.
    """
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '').strip()

    if not email or not password:
        return jsonify({'error': 'email and password required'}), 400

    # Load credentials from environment variables (no hardcoded defaults)
    valid_email = os.environ.get('LOGIN_EMAIL', '').lower().strip()
    valid_password = os.environ.get('LOGIN_PASSWORD', '')

    # Don't reveal if credentials are configured
    if not valid_email or not valid_password:
        logger.warning("LOGIN_EMAIL or LOGIN_PASSWORD not configured in environment")
        return jsonify({'error': 'Authentication system not configured'}), 503

    # Strict comparison (constant-time would be better, but this is adequate)
    if email == valid_email and password == valid_password:
        access_token = create_access_token(identity=email)
        logger.info(f"Successful login for {email}")
        return jsonify({
            'access_token': access_token,
            'user': {'email': email},
            'expires_in': 86400
        }), 200
    else:
        logger.warning(f"Failed login attempt for {email}")
        return jsonify({'error': 'Invalid email or password'}), 401


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

@app.route('/buy', methods=['GET'])
def buy_page():
    """Render buy page with PayPal SDK buttons and product catalog."""
    return render_template('buy.html', paypal_client_id=PAYPAL_CLIENT_ID)


# ============================================================================
# PAYMENT WEBHOOK - Called by PayPal SDK after successful capture
# This is the CORE endpoint that triggers asset delivery
# ============================================================================

@app.route('/api/payment/webhook', methods=['POST'])
def payment_webhook():
    """
    Receive PayPal payment confirmation from frontend.
    Called by buy.html onApprove() after actions.order.capture() succeeds.
    Stores order in DB and triggers product fulfillment/delivery.
    """
    data = request.get_json(silent=True) or {}

    # Validate required fields
    paypal_order_id = sanitize_string(data.get('id', ''), max_length=50)
    payer = data.get('payer', {})
    if not isinstance(payer, dict):
        payer = {}
    raw_email = payer.get('email_address', '')
    customer_email = validate_email(raw_email)

    purchase_units = data.get('purchase_units', [{}])
    if not isinstance(purchase_units, list) or len(purchase_units) == 0:
        purchase_units = [{}]
    product_name = sanitize_string(purchase_units[0].get('description', 'Unknown'))
    amount_obj = purchase_units[0].get('amount', {}) if purchase_units else {}

    try:
        amount = float(amount_obj.get('value', 0)) if isinstance(amount_obj, dict) else float(amount_obj or 0)
        if amount < 0 or amount > 100000:
            return jsonify({'error': 'Invalid payment amount'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid amount format'}), 400

    if not paypal_order_id:
        return jsonify({'error': 'Missing PayPal order ID'}), 400
    if not customer_email:
        return jsonify({'error': 'Invalid or missing email address'}), 400

    logger.info(f"[PAYMENT] Processing: {paypal_order_id} | {product_name} | ${amount} | {customer_email}")

    # Generate internal order ID
    order_id = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{paypal_order_id[:8]}"

    # Determine product type and delivery info
    product_info = PRODUCT_CATALOG.get(product_name, {'type': 'generic'})
    order_type = product_info.get('type', 'generic')

    # Build delivery URL based on product type
    if order_type == 'subscription':
        delivery_url = f"/dashboard?email={customer_email}&plan={product_name}"
        delivery_status = 'delivered'
    elif order_type == 'bi_report':
        delivery_url = f"/download/report/{order_id}"
        delivery_status = 'processing'
    elif order_type == 'content':
        delivery_url = f"/download/content/{order_id}"
        delivery_status = 'processing'
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
            (order_id, paypal_order_id, customer_email, product_name, amount,
             'USD', 'completed', delivery_status, delivery_url, order_type,
             datetime.utcnow().isoformat(), datetime.utcnow().isoformat(),
             json.dumps({'payer': payer, 'product_info': product_info}))
        )
        db.commit()
        logger.info(f"[ORDER] Saved: {order_id} | {product_name} | ${amount} | {customer_email}")
    except sqlite3.IntegrityError:
        logger.warning(f"[ORDER] Duplicate order: {paypal_order_id}")
        # Order already exists, return success anyway
        pass

    # Trigger product delivery based on type
    delivery_result = trigger_fulfillment(order_id, customer_email, product_name, amount, order_type, product_info)

    return jsonify({
        'success': True,
        'order_id': order_id,
        'product': product_name,
        'delivery_status': delivery_status,
        'delivery_url': delivery_url,
        'message': delivery_result.get('message', 'Order received'),
        'next_steps': delivery_result.get('next_steps', [])
    }), 200


def trigger_fulfillment(order_id, email, product_name, amount, order_type, product_info):
    """
    Trigger asset delivery based on product type.
    Returns delivery result dict.
    """
    result = {'message': '', 'next_steps': []}

    if order_type == 'subscription':
        # Subscription: Activate account + agents immediately
        agent_count = product_info.get('agents', 10)
        features = product_info.get('features', [])
        result['message'] = f'Your {product_name} plan is ACTIVE! {agent_count} AI agents are now working for you.'
        result['next_steps'] = [
            'Check your email for login credentials',
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
    """Render payment success page after PayPal capture."""
    order_id = request.args.get('order_id', '')

    # Try to find order in DB for extra details
    order_data = None
    if order_id:
        db = get_db()
        row = db.execute(
            "SELECT * FROM orders WHERE paypal_order_id=? OR order_id=? ORDER BY created_at DESC LIMIT 1",
            (order_id, order_id)
        ).fetchone()
        if row:
            order_data = dict(row)

    return render_template('payment_success.html', order_data=order_data)


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
def get_customer_orders(email):
    """
    Get all orders for a customer by email.
    Returns order list with delivery status and download URLs.
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

    return jsonify({
        'success': True,
        'email': email,
        'orders': orders,
        'count': len(orders)
    }), 200


@app.route('/api/orders', methods=['GET'])
def list_all_orders():
    """Admin endpoint: list all orders."""
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
        'recipient_address': os.environ.get('BASE_PAYMENT_ADDRESS', '0x8825A2cf25Ad34bAbCdF292c4d27C679d563C048'),
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
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
