"""
SINCOR2 MVP - Minimal Flask Application
A lean, deployable MVP with health checks, home page, buy flow, and basic auth.
"""

import os
import json
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, jsonify
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from dotenv import load_dotenv

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
jwt = JWTManager(app)

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
    return render_template('home_mvp.html', service_name='SINCOR2 MVP')


# ============================================================================
# AUTHENTICATION ENDPOINTS
# ============================================================================

@app.route('/api/auth/login', methods=['POST'])
def login():
    """
    Mock login endpoint.
    POST /api/auth/login with JSON: {"email": "user@example.com", "password": "demo"}
    Returns a JWT token.
    """
    data = request.get_json() or {}
    email = data.get('email', '')
    password = data.get('password', '')
    
    # Mock authentication (accept any email/password for demo)
    if not email or not password:
        return jsonify({'error': 'email and password required'}), 400
    
    # Create access token
    access_token = create_access_token(identity=email)
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

@app.route('/buy', methods=['GET'])
def buy_page():
    """Render buy page with product and checkout form."""
    return render_template('buy.html', 
                          product_name='SINCOR2 MVP Access',
                          product_price=29.99,
                          product_id='sincor2-mvp-001')


@app.route('/api/checkout', methods=['POST'])
def checkout():
    """
    Mock checkout endpoint.
    POST /api/checkout with JSON: {"email": "user@example.com", "amount": 29.99}
    Returns a mock payment order ID.
    """
    data = request.get_json() or {}
    email = data.get('email', '')
    amount = data.get('amount', 0)
    
    if not email or not amount:
        return jsonify({'error': 'email and amount required'}), 400
    
    # Mock order creation
    order_id = f"ORDER-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    return jsonify({
        'order_id': order_id,
        'status': 'pending',
        'email': email,
        'amount': amount,
        'currency': 'USD',
        'next_step': '/api/process-payment',
        'message': 'Order created. Ready for payment processing.'
    }), 201


@app.route('/api/process-payment', methods=['POST'])
def process_payment():
    """
    Mock payment processor.
    POST /api/process-payment with JSON: {"order_id": "ORDER-...", "payment_method": "card"}
    Returns success (90% of time) or failure (10% of time) for demo purposes.
    """
    data = request.get_json() or {}
    order_id = data.get('order_id', '')
    payment_method = data.get('payment_method', 'card')
    
    if not order_id:
        return jsonify({'error': 'order_id required'}), 400
    
    # Mock payment processor: 90% success, 10% failure
    import random
    success = random.random() > 0.1  # 90% success rate
    
    if success:
        return jsonify({
            'status': 'success',
            'order_id': order_id,
            'transaction_id': f"TXN-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'message': 'Payment processed successfully.',
            'next_step': '/api/order-confirmation'
        }), 200
    else:
        return jsonify({
            'status': 'failed',
            'order_id': order_id,
            'error': 'Payment processing failed. Please try again.',
            'error_code': 'PAYMENT_DECLINED'
        }), 402


@app.route('/api/order-confirmation/<order_id>', methods=['GET'])
def order_confirmation(order_id):
    """Retrieve order confirmation details."""
    return jsonify({
        'order_id': order_id,
        'status': 'confirmed',
        'created_at': datetime.utcnow().isoformat(),
        'message': f'Order {order_id} confirmed. Check your email for details.'
    }), 200


# ============================================================================
# ERROR HANDLERS
# ============================================================================


# ============================================================================
# STUB PAGES
# ============================================================================

@app.route('/contact')
def contact():
    """Contact page stub."""
    return jsonify({'message': 'Contact page', 'status': 'contact'}), 200


@app.route('/pricing')
def pricing():
    """Pricing page stub."""
    return jsonify({'message': 'Pricing page', 'plans': ['Basic', 'Pro', 'Enterprise']}), 200


@app.route('/docs')
def docs():
    """API documentation stub."""
    return jsonify({'message': 'API Documentation', 'version': '1.0.0'}), 200


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({'error': 'Not found', 'status': 404}), 404


@app.errorhandler(500)
def server_error(error):
    """Handle 500 errors."""
    return jsonify({'error': 'Internal server error', 'status': 500}), 500


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=port, debug=debug)
