#!/usr/bin/env python3
"""
SINCOR - Stripe Payment Integration
Complete checkout flow with real payment processing
"""

import os
import logging
from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'sincor-secret-key-change-in-production')

# Import Stripe integration
try:
    from src.sincor2.stripe_checkout import get_stripe_checkout
    from src.sincor2.stripe_routes import init_stripe_routes
    STRIPE_AVAILABLE = True
except (ImportError, ModuleNotFoundError) as e:
    logger.warning(f"[STRIPE] Import error: {e}")
    STRIPE_AVAILABLE = False

# Initialize Stripe
stripe_processor = None
if STRIPE_AVAILABLE:
    stripe_processor = get_stripe_checkout()
    if stripe_processor and stripe_processor.enabled:
        logger.info("[APP] Stripe integration initialized")
        init_stripe_routes(app, stripe_processor)
    else:
        logger.warning("[APP] Stripe not properly configured")

# ===== ROUTES =====

@app.route('/')
def home():
    """Home page"""
    return render_template('home.html')

@app.route('/buy')
def buy():
    """Buy page with high-converting Stripe checkout"""
    if STRIPE_AVAILABLE and stripe_processor and stripe_processor.enabled:
        return render_template('buy_converting.html')
    else:
        # Fallback to old buy page
        return render_template('buy_stripe.html')

@app.route('/pricing')
def pricing():
    """Pricing page"""
    return render_template('pricing.html')

@app.route('/payment/success')
def payment_success():
    """Payment success page"""
    session_id = request.args.get('session_id')

    # Get session details
    if session_id and stripe_processor:
        session = stripe_processor.get_session(session_id)
        if session:
            return render_template('payment_success.html',
                                 session=session,
                                 email=session.get('customer_email'),
                                 amount=session.get('amount_total', 0) / 100)

    return render_template('payment_success.html')

@app.route('/payment/cancel')
def payment_cancel():
    """Payment cancelled page"""
    return render_template('payment_cancel.html')

@app.route('/health')
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'stripe': {
            'available': STRIPE_AVAILABLE,
            'enabled': stripe_processor.enabled if stripe_processor else False,
            'mode': stripe_processor.mode if stripe_processor else 'unknown'
        }
    })

# ===== ERROR HANDLERS =====

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', error='Page not found', code=404), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"[APP] Server error: {str(e)}")
    return render_template('error.html', error='Server error', code=500), 500

# ===== START APP =====

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV', 'production') == 'development'

    logger.info(f"[APP] Starting SINCOR on port {port}")
    logger.info(f"[APP] Debug mode: {debug}")
    logger.info(f"[APP] Stripe available: {STRIPE_AVAILABLE}")

    app.run(host='0.0.0.0', port=port, debug=debug)
