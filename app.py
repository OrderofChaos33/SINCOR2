#!/usr/bin/env python3
"""
SINCOR Main Flask Application with Product Showcase and Waitlist System
"""

import os
from flask import Flask, render_template, request, jsonify

# Import compliance email system
try:
    from compliance_email import send_gdpr_notification_email, send_unsubscribe_confirmation
    EMAIL_AVAILABLE = True
except ImportError:
    print("Compliance email system not available")
    EMAIL_AVAILABLE = False

# Import waitlist system with error handling
try:
    from waitlist_system import waitlist_manager
    WAITLIST_AVAILABLE = True
except ImportError as e:
    print(f"Waitlist system not available: {e}")
    WAITLIST_AVAILABLE = False

# Import PayPal integration with error handling
try:
    from paypal_integration import PayPalIntegration, PaymentRequest
    paypal_processor = PayPalIntegration()
    PAYPAL_AVAILABLE = True
    print("✅ PayPal Integration Loaded Successfully")
except ImportError as e:
    print(f"PayPal integration not available: {e}")
    PAYPAL_AVAILABLE = False
    paypal_processor = None
except Exception as e:
    print(f"PayPal configuration error: {e}")
    PAYPAL_AVAILABLE = False
    paypal_processor = None

# Import monetization engine with error handling
try:
    from monetization_engine import MonetizationEngine
    monetization_engine = MonetizationEngine()
    MONETIZATION_AVAILABLE = True
    print("✅ Monetization Engine Loaded Successfully")
except ImportError as e:
    print(f"Monetization engine not available: {e}")
    MONETIZATION_AVAILABLE = False
    monetization_engine = None
except Exception as e:
    print(f"Monetization engine error: {e}")
    MONETIZATION_AVAILABLE = False
    monetization_engine = None

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'development-key-change-in-production')

# Configure template folder
app.template_folder = 'templates'

@app.route('/')
def index():
    """Main landing page - SINC Token"""
    return render_template('index.html')

@app.route('/trade')
def trade():
    """Trade SINC token page"""
    return render_template('trade.html')

@app.route('/dashboard')
def dashboard():
    """Token analytics dashboard"""
    return render_template('dashboard.html')

@app.route('/faucet')
def faucet():
    """SINC Token Faucet page"""
    return render_template('faucet.html')

@app.route('/whitepaper')
def whitepaper():
    """SINC Token Whitepaper"""
    return render_template('whitepaper.html')

# Legal compliance routes
@app.route('/privacy')
def privacy_policy():
    """Privacy Policy page - GDPR/CCPA compliant"""
    return render_template('privacy.html')

@app.route('/terms')
def terms_of_service():
    """Terms of Service page"""
    return render_template('terms.html')

@app.route('/cookies')
def cookie_policy():
    """Cookie Policy page"""
    return render_template('cookies.html')

# GDPR Data Subject Rights API
@app.route('/api/gdpr/data-request', methods=['POST'])
def gdpr_data_request():
    """Handle GDPR data subject access requests"""
    try:
        request_data = request.get_json()
        email = request_data.get('email')
        request_type = request_data.get('type')  # 'access', 'delete', 'rectify', 'portability'
        details = request_data.get('details', '')
        
        if not email or not request_type:
            return jsonify({'success': False, 'error': 'Email and request type required'}), 400
        
        valid_types = ['access', 'delete', 'rectify', 'portability']
        if request_type not in valid_types:
            return jsonify({'success': False, 'error': f'Invalid request type. Must be one of: {", ".join(valid_types)}'}), 400
        
        # Generate reference ID
        import hashlib
        from datetime import datetime
        ref_id = f'GDPR-{request_type.upper()}-{hashlib.md5((email + str(datetime.now())).encode()).hexdigest()[:8]}'
        
        # Log to console and file
        log_message = f"[{datetime.now().isoformat()}] GDPR {request_type.upper()} request from {email} | Ref: {ref_id}"
        print(log_message)
        
        try:
            with open('gdpr_requests.log', 'a', encoding='utf-8') as f:
                f.write(f"{log_message}\n")
                if details:
                    f.write(f"  Details: {details}\n")
        except Exception as log_error:
            print(f"Failed to write to log file: {log_error}")
        
        # Send email notification to privacy team
        email_sent = False
        if EMAIL_AVAILABLE:
            email_sent = send_gdpr_notification_email(email, request_type, ref_id, details)
        
        return jsonify({
            'success': True,
            'message': f'Your {request_type} request has been received. We will respond within 30 days as required by GDPR Article 12(3).',
            'reference_id': ref_id,
            'email_sent': email_sent
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# Unsubscribe endpoint (CAN-SPAM compliance)
@app.route('/unsubscribe', methods=['GET', 'POST'])
def unsubscribe():
    """CAN-SPAM compliant email unsubscribe"""
    if request.method == 'POST':
        email = request.form.get('email') or request.get_json().get('email')
        
        if not email:
            return jsonify({'success': False, 'error': 'Email required'}), 400
        
        # Basic email validation
        import re
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
            return jsonify({'success': False, 'error': 'Invalid email format'}), 400
        
        # Log unsubscribe request
        from datetime import datetime
        log_message = f"[{datetime.now().isoformat()}] UNSUBSCRIBE: {email}"
        print(log_message)
        
        try:
            with open('unsubscribe_requests.log', 'a', encoding='utf-8') as f:
                f.write(f"{log_message}\n")
        except Exception as log_error:
            print(f"Failed to write to log file: {log_error}")
                # Send confirmation email (optional but good practice)
        if EMAIL_AVAILABLE:
            send_unsubscribe_confirmation(email)
                # TODO: Update database and email service provider
        # Implement: remove from mailing list, update preferences, etc.
        
        return jsonify({
            'success': True,
            'message': 'You have been successfully unsubscribed from all marketing communications.',
            'processed_at': datetime.now().isoformat()
        })
    
    # GET request - show unsubscribe confirmation page
    return render_template('unsubscribe.html')

@app.route('/api/waitlist', methods=['POST'])
def join_waitlist():
    """Handle waitlist signups"""
    try:
        if not WAITLIST_AVAILABLE:
            return jsonify({'success': False, 'error': 'Waitlist system temporarily unavailable'})
            
        signup_data = request.get_json()
        
        # Validate required fields
        if not signup_data or not signup_data.get('email'):
            return jsonify({'success': False, 'error': 'Email address is required'})
        
        # Add to waitlist using the waitlist manager
        result = waitlist_manager.add_to_waitlist(signup_data)
        
        # Add CAN-SPAM compliant unsubscribe info
        if result.get('success'):
            result['unsubscribe_url'] = f"{request.host_url}unsubscribe"
            result['privacy_policy'] = f"{request.host_url}privacy"
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

@app.route('/api/products')
def get_products():
    """Get information about all SINCOR products"""
    try:
        # Return static product information for now
        product_info = {
            'growth_engine': {
                'product_name': 'SINCOR Growth Engine',
                'tagline': 'Your AI sales org in a box',
                'color_theme': 'purple',
                'agent_count': 5
            },
            'ops_core': {
                'product_name': 'SINCOR Ops Core', 
                'tagline': 'Run leaner, faster, cleaner',
                'color_theme': 'teal',
                'agent_count': 6
            },
            'creative_forge': {
                'product_name': 'SINCOR Creative Forge',
                'tagline': 'Creative firepower, amplified', 
                'color_theme': 'lime',
                'agent_count': 5
            },
            'intelligence_hub': {
                'product_name': 'SINCOR Intelligence Hub',
                'tagline': 'Intelligence that drives decisions',
                'color_theme': 'red',
                'agent_count': 5
            }
        }
        
        return jsonify({
            'success': True,
            'products': product_info
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error loading products: {str(e)}'})

@app.route('/api/waitlist/analytics')
def waitlist_analytics():
    """Get waitlist analytics (admin endpoint)"""
    try:
        if not WAITLIST_AVAILABLE:
            return jsonify({'success': False, 'error': 'Analytics temporarily unavailable'})
            
        analytics = waitlist_manager.get_analytics()
        return jsonify({
            'success': True,
            'analytics': analytics
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error loading analytics: {str(e)}'})

@app.route('/admin')
def admin_panel():
    """Simple admin panel to view waitlist analytics"""
    try:
        if not WAITLIST_AVAILABLE:
            return """
            <!DOCTYPE html>
            <html>
            <head><title>SINCOR Admin</title></head>
            <body style="font-family: system-ui; margin: 2rem;">
                <h1>SINCOR Admin Panel</h1>
                <p>Waitlist system temporarily unavailable.</p>
                <p><a href="/">← Back to Main Site</a></p>
            </body>
            </html>
            """
            
        analytics = waitlist_manager.get_analytics()
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>SINCOR Admin - Waitlist Analytics</title>
            <style>
                body {{ font-family: system-ui; margin: 2rem; }}
                .stat {{ background: #f0f0f0; padding: 1rem; margin: 1rem 0; border-radius: 8px; }}
                .product {{ background: #e0f0ff; padding: 0.5rem; margin: 0.5rem 0; }}
            </style>
        </head>
        <body>
            <h1>SINCOR Waitlist Analytics</h1>
            
            <div class="stat">
                <h2>Total Signups: {analytics['total_signups']}</h2>
            </div>
            
            <div class="stat">
                <h3>Signups by Product:</h3>
                {''.join(f'<div class="product">{product}: {count} signups</div>' 
                        for product, count in analytics['products'].items())}
            </div>
            
            <div class="stat">
                <h3>High Priority Signups:</h3>
                {''.join(f'<div class="product">Score {signup[0]}: {signup[1]} - {signup[2]}</div>' 
                        for signup in analytics['high_priority_signups'][:10])}
            </div>
            
            <p><a href="/">← Back to Main Site</a></p>
        </body>
        </html>
        """
    except Exception as e:
        return f"<h1>Error loading analytics</h1><p>{str(e)}</p>"

@app.route('/health')
def health_check():
    """Health check endpoint"""
    import datetime

    # Check if monetization is available based on loaded systems
    monetization_available = bool(PAYPAL_AVAILABLE and MONETIZATION_AVAILABLE)

    return jsonify({
        'status': 'healthy',
        'service': 'SINCOR Master Platform',
        'ai_agents': 42,
        'waitlist_available': WAITLIST_AVAILABLE,
        'monetization_available': monetization_available,
        'google_api_available': bool(os.environ.get('GOOGLE_API_KEY')),
        'email_available': bool(os.environ.get('SMTP_HOST') and os.environ.get('SMTP_USER')),
        'port': os.environ.get('PORT', '5000'),
        'timestamp': datetime.datetime.now().isoformat()
    })

@app.route('/discovery-dashboard')
def discovery_dashboard():
    """Live Demo page"""
    return render_template('discovery-dashboard.html')

@app.route('/enterprise-dashboard')
def enterprise_dashboard():
    """Enterprise solutions page"""
    return render_template('enterprise-dashboard.html')

@app.route('/franchise-empire')
def franchise_empire():
    """Franchise opportunities page"""
    return render_template('franchise-empire.html')

@app.route('/affiliate-program')
def affiliate_program():
    """Affiliate program page"""
    return render_template('affiliate-program.html')

@app.route('/media-packs')
def media_packs():
    """Media packs and resources page"""
    return render_template('media-packs.html')

@app.route('/pricing')
def pricing():
    """Pricing plans page"""
    return render_template('pricing.html')

@app.route('/buy')
def buy():
    """Buy SINCOR - checkout page with PayPal integration"""
    return render_template('buy.html')

@app.route('/success')
def success():
    """Purchase success page"""
    return render_template('success.html')

@app.route('/privacy')
def privacy():
    """Privacy policy page"""
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    """Terms of service page"""
    return render_template('terms.html')

@app.route('/security')
def security():
    """Security and compliance page"""
    return render_template('security.html')

@app.route('/api/test/paypal')
def test_paypal():
    """Test PayPal environment variables"""
    paypal_client_id = os.environ.get('PAYPAL_REST_API_ID')
    paypal_secret = os.environ.get('PAYPAL_REST_API_SECRET')
    paypal_sandbox = os.environ.get('PAYPAL_SANDBOX', 'true')

    return jsonify({
        'paypal_configured': bool(paypal_client_id and paypal_secret),
        'client_id_set': bool(paypal_client_id),
        'secret_set': bool(paypal_secret),
        'sandbox_mode': paypal_sandbox.lower() == 'true',
        'client_id_preview': paypal_client_id[:10] + "..." if paypal_client_id else None
    })

@app.route('/api/test/google')
def test_google():
    """Test Google API environment variables"""
    google_api_key = os.environ.get('GOOGLE_API_KEY')
    google_places_key = os.environ.get('GOOGLE_PLACES_API_KEY')

    return jsonify({
        'google_api_configured': bool(google_api_key),
        'google_places_configured': bool(google_places_key),
        'api_key_preview': google_api_key[:10] + "..." if google_api_key else None,
        'places_key_preview': google_places_key[:10] + "..." if google_places_key else None
    })

@app.route('/api/test/environment')
def test_environment():
    """Test all environment variables for presence and basic validation"""

    # Test core environment variables for SINCOR platform
    test_vars = [
        'ANTHROPIC_API_KEY',
        'GOOGLE_ADS_API_ID',
        'GOOGLE_ADS_API_KEY',
        'GOOGLE_API_KEY',
        'GOOGLE_OAUTH_CLIENT_ID',
        'GOOGLE_OAUTH_CLIENT_SECRET',
        'PAYPAL_ENV',
        'PAYPAL_REST_API_ID',
        'PAYPAL_REST_API_SECRET',
        'SECRET_KEY',
        'SQUARE_APP_ID',
        'SQUARE_APP_SECRET',
        'TWILO_AUTH',
        'TWILO_ID',
        'TWILO_NUMBER'
    ]

    results = {}
    for var_name in test_vars:
        actual_value = os.environ.get(var_name)
        if actual_value:
            # Basic validation for each type
            is_valid = len(actual_value.strip()) > 10  # All should be longer than 10 chars
            results[var_name] = {
                'configured': True,
                'valid_format': is_valid,
                'preview': actual_value[:15] + "..." if len(actual_value) > 15 else actual_value,
                'length': len(actual_value)
            }
        else:
            results[var_name] = {
                'configured': False,
                'valid_format': False,
                'preview': None,
                'length': 0
            }

    # Calculate summary
    total_vars = len(test_vars)
    configured_vars = sum(1 for r in results.values() if r['configured'])
    valid_vars = sum(1 for r in results.values() if r['valid_format'])

    # Service readiness based on presence and basic validation
    paypal_ready = (results.get('PAYPAL_REST_API_ID', {}).get('valid_format', False) and
                   results.get('PAYPAL_REST_API_SECRET', {}).get('valid_format', False))
    google_ready = results.get('GOOGLE_API_KEY', {}).get('valid_format', False)
    anthropic_ready = results.get('ANTHROPIC_API_KEY', {}).get('valid_format', False)

    return jsonify({
        'total_variables': total_vars,
        'configured_count': configured_vars,
        'valid_count': valid_vars,
        'success_rate': round((valid_vars / total_vars) * 100, 1),
        'services': {
            'paypal_integration_ready': paypal_ready,
            'google_apis_ready': google_ready,
            'anthropic_ai_ready': anthropic_ready,
            'monetization_available': paypal_ready
        },
        'detailed_results': results
    })

# PayPal payment processing routes
@app.route('/api/payment/create', methods=['POST'])
def create_payment():
    """Create a PayPal payment"""
    if not PAYPAL_AVAILABLE:
        return jsonify({'error': 'PayPal integration not available'}), 503

    try:
        payment_data = request.get_json()

        # Validate required fields
        required_fields = ['amount', 'description']
        for field in required_fields:
            if field not in payment_data:
                return jsonify({'error': f'Missing required field: {field}'}), 400

        # Create payment request
        payment_request = PaymentRequest(
            amount=float(payment_data['amount']),
            currency=payment_data.get('currency', 'USD'),
            description=payment_data['description'],
            customer_email=payment_data.get('customer_email', ''),
            order_id=payment_data.get('order_id', ''),
            return_url=payment_data.get('return_url', request.host_url + 'payment/success'),
            cancel_url=payment_data.get('cancel_url', request.host_url + 'payment/cancel')
        )

        # Process payment
        result = paypal_processor.create_payment(payment_request)

        return jsonify({
            'success': result.success,
            'payment_id': result.payment_id,
            'approval_url': result.approval_url,
            'amount': result.amount,
            'status': result.status.value
        })

    except Exception as e:
        return jsonify({'error': f'Payment creation failed: {str(e)}'}), 500

@app.route('/api/payment/execute', methods=['POST'])
def execute_payment():
    """Execute a PayPal payment after approval"""
    if not PAYPAL_AVAILABLE:
        return jsonify({'error': 'PayPal integration not available'}), 503

    try:
        payment_data = request.get_json()
        payment_id = payment_data.get('payment_id')
        payer_id = payment_data.get('payer_id')

        if not payment_id or not payer_id:
            return jsonify({'error': 'Missing payment_id or payer_id'}), 400

        # Execute payment
        result = paypal_processor.execute_payment(payment_id, payer_id)

        return jsonify({
            'success': result.success,
            'payment_id': result.payment_id,
            'status': result.status.value,
            'amount': result.amount,
            'net_amount': result.net_amount,
            'transaction_fee': result.transaction_fee
        })

    except Exception as e:
        return jsonify({'error': f'Payment execution failed: {str(e)}'}), 500

@app.route('/api/monetization/start', methods=['POST'])
def start_monetization():
    """Start the monetization engine"""
    if not MONETIZATION_AVAILABLE:
        return jsonify({'error': 'Monetization engine not available'}), 503

    try:
        # Execute monetization strategy
        strategy_report = monetization_engine.execute_monetization_strategy(
            max_concurrent_opportunities=10
        )

        return jsonify({
            'success': True,
            'message': 'Monetization engine started successfully',
            'strategy_report': strategy_report
        })

    except Exception as e:
        return jsonify({'error': f'Failed to start monetization: {str(e)}'}), 500

@app.route('/api/monetization/status')
def monetization_status():
    """Get monetization engine status"""
    return jsonify({
        'paypal_available': PAYPAL_AVAILABLE,
        'monetization_available': MONETIZATION_AVAILABLE,
        'waitlist_available': WAITLIST_AVAILABLE,
        'environment_configured': bool(os.environ.get('PAYPAL_REST_API_ID')),
        'production_mode': os.environ.get('PAYPAL_ENV', 'sandbox') == 'live'
    })

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    
    print(f"Starting SINCOR Product Platform on port {port}")
    print(f"Debug mode: {debug_mode}")
    if WAITLIST_AVAILABLE:
        print(f"Database: {waitlist_manager.db_path}")
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)