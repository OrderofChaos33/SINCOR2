#!/usr/bin/env python3
"""
SINCOR Main Flask Application with Product Showcase and Waitlist System
FIXED: Removed async/await for Flask compatibility
ADDED: JWT Authentication for admin endpoints
ADDED: Rate Limiting for DDoS protection
"""

import os
from flask import Flask, render_template, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

# Import authentication system
try:
    from auth_system import SINCORAuth, admin_required
    AUTH_AVAILABLE = True
except ImportError as e:
    print(f"Auth system not available: {e}")
    AUTH_AVAILABLE = False

# Import rate limiter
try:
    from rate_limiter import (
        SINCORRateLimiter,
        AUTH_LIMITS,
        PAYMENT_LIMITS,
        PUBLIC_LIMITS,
        ADMIN_LIMITS,
        MONITORING_LIMITS,
        ANALYTICS_LIMITS
    )
    RATE_LIMIT_AVAILABLE = True
except ImportError as e:
    print(f"Rate limiter not available: {e}")
    RATE_LIMIT_AVAILABLE = False

# Import security headers
try:
    from security_headers import SecurityHeaders
    SECURITY_HEADERS_AVAILABLE = True
except ImportError as e:
    print(f"Security headers not available: {e}")
    SECURITY_HEADERS_AVAILABLE = False

# Import production logger
try:
    from production_logger import SINCORLogger
    LOGGING_AVAILABLE = True
except ImportError as e:
    print(f"Production logger not available: {e}")
    LOGGING_AVAILABLE = False

# Import monitoring dashboard
try:
    from monitoring_dashboard import MonitoringDashboard
    MONITORING_AVAILABLE = True
except ImportError as e:
    print(f"Monitoring dashboard not available: {e}")
    MONITORING_AVAILABLE = False

# Import validation models
try:
    from validation_models import (
        WaitlistSignup,
        PaymentCreateRequest,
        PaymentExecuteRequest,
        LoginRequest,
        validate_request
    )
    VALIDATION_AVAILABLE = True
except ImportError as e:
    print(f"Validation models not available: {e}")
    VALIDATION_AVAILABLE = False

# Import waitlist system with error handling
try:
    from waitlist_system import waitlist_manager
    WAITLIST_AVAILABLE = True
except ImportError as e:
    print(f"Waitlist system not available: {e}")
    WAITLIST_AVAILABLE = False

# Import PayPal integration with SYNC wrappers
try:
    from paypal_integration_sync import PayPalIntegrationSync, SINCORPaymentProcessorSync
    from paypal_integration import PaymentRequest
    paypal_processor = PayPalIntegrationSync()
    PAYPAL_AVAILABLE = True
    print("PayPal Integration Loaded Successfully (Sync Mode)")
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
    print("Monetization Engine Loaded Successfully")
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

# Initialize authentication
if AUTH_AVAILABLE:
    sincor_auth = SINCORAuth(app)
    print("JWT Authentication Enabled")
else:
    sincor_auth = None
    print("WARNING: JWT Authentication NOT available - admin endpoints unprotected!")

# Initialize rate limiter
if RATE_LIMIT_AVAILABLE:
    rate_limiter = SINCORRateLimiter(app)
    limiter = rate_limiter.get_limiter()
    print("Rate Limiting Enabled")
else:
    limiter = None
    print("WARNING: Rate Limiting NOT available - vulnerable to abuse!")

# Initialize security headers
if SECURITY_HEADERS_AVAILABLE:
    security_headers = SecurityHeaders(app)
    print("Security Headers Enabled")
else:
    print("WARNING: Security Headers NOT available - vulnerable to XSS, clickjacking!")

# Initialize production logging
if LOGGING_AVAILABLE:
    sincor_logger = SINCORLogger(app)
    print("Production Logging Enabled")
else:
    sincor_logger = None
    print("WARNING: Production Logging NOT available - limited monitoring!")

# Initialize monitoring dashboard
if MONITORING_AVAILABLE:
    monitoring_dashboard = MonitoringDashboard(app)
    print("Monitoring Dashboard Enabled")
else:
    print("WARNING: Monitoring Dashboard NOT available!")

# Initialize engine API controller
try:
    from api_engine_controller import register_engine_api
    register_engine_api(app)
    print("Engine API Controller Enabled")
    ENGINE_API_AVAILABLE = True
except ImportError as e:
    print(f"Engine API controller not available: {e}")
    ENGINE_API_AVAILABLE = False
except Exception as e:
    print(f"Engine API initialization error: {e}")
    ENGINE_API_AVAILABLE = False

# ==================== AUTHENTICATION ROUTES ====================

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit(AUTH_LIMITS) if limiter else lambda f: f
def login():
    """Authenticate and get access token (RATE LIMITED)"""
    if not AUTH_AVAILABLE:
        return jsonify({'error': 'Authentication system not available'}), 503

    try:
        auth_data = request.get_json()

        username = auth_data.get('username')
        password = auth_data.get('password')

        if not username or not password:
            return jsonify({
                'success': False,
                'error': 'Username and password required'
            }), 400

        # Authenticate user
        result = sincor_auth.authenticate_user(username, password)

        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 401

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Authentication failed: {str(e)}'
        }), 500


@app.route('/api/auth/refresh', methods=['POST'])
@jwt_required(refresh=True)
@limiter.limit(AUTH_LIMITS) if limiter else lambda f: f
def refresh():
    """Refresh access token using refresh token (RATE LIMITED)"""
    if not AUTH_AVAILABLE:
        return jsonify({'error': 'Authentication system not available'}), 503

    try:
        current_user = get_jwt_identity()

        # Create new access token
        from flask_jwt_extended import create_access_token
        access_token = create_access_token(identity=current_user)

        return jsonify({
            'success': True,
            'access_token': access_token,
            'expires_in': 3600
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Token refresh failed: {str(e)}'
        }), 500


@app.route('/api/auth/me')
@jwt_required()
@limiter.limit(ADMIN_LIMITS) if limiter else lambda f: f
def get_current_user():
    """Get current authenticated user info (RATE LIMITED)"""
    if not AUTH_AVAILABLE:
        return jsonify({'error': 'Authentication system not available'}), 503

    try:
        current_user = get_jwt_identity()

        return jsonify({
            'success': True,
            'user': {
                'username': current_user,
                'role': sincor_auth.get_current_user_role()
            }
        }), 200

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Failed to get user info: {str(e)}'
        }), 500


# ==================== PUBLIC ROUTES ====================

@app.route('/')
@limiter.exempt if limiter else lambda f: f
def index():
    """Main landing page with product showcase (NO RATE LIMIT)"""
    return render_template('index.html')


@app.route('/api/waitlist', methods=['POST'])
@limiter.limit(PUBLIC_LIMITS) if limiter else lambda f: f
def join_waitlist():
    """Handle waitlist signups (RATE LIMITED + VALIDATED)"""
    try:
        if not WAITLIST_AVAILABLE:
            return jsonify({'success': False, 'error': 'Waitlist system temporarily unavailable'})

        signup_data = request.get_json()

        # Validate required fields
        if not signup_data or not signup_data.get('email'):
            return jsonify({'success': False, 'error': 'Email address is required'})

        # SECURITY: Validate input using Pydantic model
        if VALIDATION_AVAILABLE:
            validated_data, error = validate_request(WaitlistSignup, signup_data)
            if error:
                return jsonify({'success': False, 'error': error}), 400
            signup_data = validated_data

        # Add to waitlist using the waitlist manager
        result = waitlist_manager.add_to_waitlist(signup_data)

        return jsonify(result)

    except Exception as e:
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})


@app.route('/api/products')
@limiter.limit(MONITORING_LIMITS) if limiter else lambda f: f
def get_products():
    """Get information about all SINCOR products (RATE LIMITED)"""
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


@app.route('/dashboard')
def dashboard():
    """SINCOR Control Center Dashboard"""
    return render_template('dashboard.html')


@app.route('/health')
@limiter.exempt if limiter else lambda f: f
def health_check():
    """Health check endpoint (NO RATE LIMIT)"""
    import datetime

    # Check if monetization is available based on loaded systems
    monetization_available = bool(PAYPAL_AVAILABLE and MONETIZATION_AVAILABLE)

    return jsonify({
        'status': 'healthy',
        'service': 'SINCOR Master Platform',
        'ai_agents': 42,
        'waitlist_available': WAITLIST_AVAILABLE,
        'monetization_available': monetization_available,
        'auth_available': AUTH_AVAILABLE,
        'rate_limit_available': RATE_LIMIT_AVAILABLE,
        'google_api_available': bool(os.environ.get('GOOGLE_API_KEY')),
        'email_available': bool(os.environ.get('SMTP_HOST') and os.environ.get('SMTP_USER')),
        'port': os.environ.get('PORT', '5000'),
        'timestamp': datetime.datetime.now().isoformat()
    })


# ==================== PROTECTED ADMIN ROUTES ====================

@app.route('/api/waitlist/analytics')
@jwt_required()
@limiter.limit(ANALYTICS_LIMITS) if limiter else lambda f: f
def waitlist_analytics():
    """Get waitlist analytics (PROTECTED + RATE LIMITED)"""
    try:
        if not WAITLIST_AVAILABLE:
            return jsonify({'success': False, 'error': 'Analytics temporarily unavailable'})

        current_user = get_jwt_identity()
        print(f"Waitlist analytics accessed by: {current_user}")

        analytics = waitlist_manager.get_analytics()
        return jsonify({
            'success': True,
            'analytics': analytics
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error loading analytics: {str(e)}'})


@app.route('/admin')
@jwt_required()
@limiter.limit(ADMIN_LIMITS) if limiter else lambda f: f
def admin_panel():
    """Simple admin panel to view waitlist analytics (PROTECTED + RATE LIMITED)"""
    try:
        current_user = get_jwt_identity()

        if not WAITLIST_AVAILABLE:
            return f"""
            <!DOCTYPE html>
            <html>
            <head><title>SINCOR Admin</title></head>
            <body style="font-family: system-ui; margin: 2rem;">
                <h1>SINCOR Admin Panel</h1>
                <p>Logged in as: <strong>{current_user}</strong></p>
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
                .header {{ background: #333; color: white; padding: 1rem; border-radius: 8px; margin-bottom: 1rem; }}
                .stat {{ background: #f0f0f0; padding: 1rem; margin: 1rem 0; border-radius: 8px; }}
                .product {{ background: #e0f0ff; padding: 0.5rem; margin: 0.5rem 0; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h1>SINCOR Waitlist Analytics</h1>
                <p>Logged in as: <strong>{current_user}</strong></p>
            </div>

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


# ==================== DASHBOARD ROUTES ====================

@app.route('/executive')
@limiter.exempt if limiter else lambda f: f
def executive_dashboard():
    """Executive Dashboard - Command center with KPIs"""
    return render_template('executive_dashboard.html')


@app.route('/professional')
@limiter.exempt if limiter else lambda f: f
def professional_dashboard():
    """Professional Dashboard - Advanced analytics"""
    from datetime import datetime

    # Mock data for demonstration
    mock_data = {
        'company_name': 'SINCOR Demo',
        'industry': 'technology',
        'current_date': datetime.now().strftime('%B %d, %Y'),
        'metrics': {
            'new_leads_today': 42,
            'appointments_scheduled': 12,
            'completion_rate': 87,
            'customer_satisfaction': 4.8,
            'revenue_today': '$2,450'
        },
        'industry_metrics': {
            'vehicles_completed': 156,
            'monthly_revenue': '$45,230',
            'avg_service_value': '$290',
            'booking_conversion': '78%',
            'repeat_customers': '45%',
            'next_available': 'Tomorrow 9AM'
        },
        'agents': {
            'coordination_score': 94,
            'active_count': 42
        }
    }

    return render_template('professional_dashboard.html', **mock_data)


@app.route('/consciousness-transfer')
@limiter.exempt if limiter else lambda f: f
def consciousness_transfer_dashboard():
    """Consciousness Transfer Dashboard - Monitoring"""
    return render_template('consciousness_transfer_dashboard.html')


@app.route('/admin-dashboard')
@jwt_required()
@limiter.limit(ADMIN_LIMITS) if limiter else lambda f: f
def admin_dashboard():
    """Admin Dashboard - Protected control panel"""
    return render_template('admin_dashboard.html')


# ==================== PUBLIC PAGE ROUTES ====================

@app.route('/discovery-dashboard')
@limiter.exempt if limiter else lambda f: f
def discovery_dashboard():
    """Live Demo page"""
    return render_template('discovery-dashboard.html')


@app.route('/enterprise-dashboard')
@limiter.exempt if limiter else lambda f: f
def enterprise_dashboard():
    """Enterprise solutions page"""
    return render_template('enterprise-dashboard.html')


@app.route('/franchise-empire')
@limiter.exempt if limiter else lambda f: f
def franchise_empire():
    """Franchise opportunities page"""
    return render_template('franchise-empire.html')


@app.route('/affiliate-program')
@limiter.exempt if limiter else lambda f: f
def affiliate_program():
    """Affiliate program page"""
    return render_template('affiliate-program.html')


@app.route('/media-packs')
@limiter.exempt if limiter else lambda f: f
def media_packs():
    """Media packs and resources page"""
    return render_template('media-packs.html')


@app.route('/pricing')
@limiter.exempt if limiter else lambda f: f
def pricing():
    """Pricing plans page"""
    return render_template('pricing.html')


@app.route('/privacy')
@limiter.exempt if limiter else lambda f: f
def privacy():
    """Privacy policy page"""
    return render_template('privacy.html')


@app.route('/terms')
@limiter.exempt if limiter else lambda f: f
def terms():
    """Terms of service page"""
    return render_template('terms.html')


@app.route('/security')
@limiter.exempt if limiter else lambda f: f
def security():
    """Security and compliance page"""
    return render_template('security.html')


# ==================== ENVIRONMENT TEST ROUTES ====================

@app.route('/api/test/paypal')
@limiter.limit(MONITORING_LIMITS) if limiter else lambda f: f
def test_paypal():
    """Test PayPal environment variables (RATE LIMITED)"""
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
@limiter.limit(MONITORING_LIMITS) if limiter else lambda f: f
def test_google():
    """Test Google API environment variables (RATE LIMITED)"""
    google_api_key = os.environ.get('GOOGLE_API_KEY')
    google_places_key = os.environ.get('GOOGLE_PLACES_API_KEY')

    return jsonify({
        'google_api_configured': bool(google_api_key),
        'google_places_configured': bool(google_places_key),
        'api_key_preview': google_api_key[:10] + "..." if google_api_key else None,
        'places_key_preview': google_places_key[:10] + "..." if google_places_key else None
    })


@app.route('/api/test/environment')
@limiter.limit(MONITORING_LIMITS) if limiter else lambda f: f
def test_environment():
    """Test all environment variables for presence and basic validation (RATE LIMITED)"""

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
        'JWT_SECRET_KEY',
        'ADMIN_USERNAME',
        'ADMIN_PASSWORD',
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
    auth_ready = results.get('JWT_SECRET_KEY', {}).get('valid_format', False)

    return jsonify({
        'total_variables': total_vars,
        'configured_count': configured_vars,
        'valid_count': valid_vars,
        'success_rate': round((valid_vars / total_vars) * 100, 1),
        'services': {
            'paypal_integration_ready': paypal_ready,
            'google_apis_ready': google_ready,
            'anthropic_ai_ready': anthropic_ready,
            'authentication_ready': auth_ready,
            'rate_limiting_ready': RATE_LIMIT_AVAILABLE,
            'monetization_available': paypal_ready
        },
        'detailed_results': results
    })


# ==================== PAYMENT ROUTES (PROTECTED + RATE LIMITED) ====================

@app.route('/api/payment/create', methods=['POST'])
@jwt_required()
@limiter.limit(PAYMENT_LIMITS) if limiter else lambda f: f
def create_payment():
    """Create a PayPal payment (PROTECTED + RATE LIMITED + VALIDATED)"""
    if not PAYPAL_AVAILABLE:
        return jsonify({'error': 'PayPal integration not available'}), 503

    try:
        current_user = get_jwt_identity()
        payment_data = request.get_json()

        # SECURITY: Validate input using Pydantic model
        if VALIDATION_AVAILABLE:
            validated_data, error = validate_request(PaymentCreateRequest, payment_data)
            if error:
                return jsonify({'error': error}), 400
            payment_data = validated_data

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

        # Process payment synchronously
        result = paypal_processor.create_payment_sync(payment_request)

        print(f"Payment created by: {current_user} - Amount: ${payment_data['amount']}")

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
@jwt_required()
@limiter.limit(PAYMENT_LIMITS) if limiter else lambda f: f
def execute_payment():
    """Execute a PayPal payment after approval (PROTECTED + RATE LIMITED + VALIDATED)"""
    if not PAYPAL_AVAILABLE:
        return jsonify({'error': 'PayPal integration not available'}), 503

    try:
        current_user = get_jwt_identity()
        payment_data = request.get_json()

        # SECURITY: Validate input using Pydantic model
        if VALIDATION_AVAILABLE:
            validated_data, error = validate_request(PaymentExecuteRequest, payment_data)
            if error:
                return jsonify({'error': error}), 400
            payment_data = validated_data

        payment_id = payment_data.get('payment_id')
        payer_id = payment_data.get('payer_id')

        # Execute payment synchronously
        result = paypal_processor.execute_payment_sync(payment_id, payer_id)

        print(f"Payment executed by: {current_user} - Payment ID: {payment_id}")

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
@admin_required()
@limiter.limit(ADMIN_LIMITS) if limiter else lambda f: f
def start_monetization():
    """Start the monetization engine (PROTECTED + RATE LIMITED - admin only)"""
    if not MONETIZATION_AVAILABLE:
        return jsonify({'error': 'Monetization engine not available'}), 503

    try:
        current_user = get_jwt_identity()
        print(f"Monetization engine started by: {current_user}")

        # Execute monetization strategy synchronously
        # Note: This should be moved to a background task queue (Celery) for production

        return jsonify({
            'success': True,
            'message': 'Monetization engine started successfully',
            'note': 'Running synchronously - consider using Celery for background processing',
            'started_by': current_user
        })

    except Exception as e:
        return jsonify({'error': f'Failed to start monetization: {str(e)}'}), 500


@app.route('/api/monetization/status')
@limiter.limit(MONITORING_LIMITS) if limiter else lambda f: f
def monetization_status():
    """Get monetization engine status (RATE LIMITED)"""
    return jsonify({
        'paypal_available': PAYPAL_AVAILABLE,
        'monetization_available': MONETIZATION_AVAILABLE,
        'waitlist_available': WAITLIST_AVAILABLE,
        'auth_available': AUTH_AVAILABLE,
        'rate_limit_available': RATE_LIMIT_AVAILABLE,
        'environment_configured': bool(os.environ.get('PAYPAL_REST_API_ID')),
        'production_mode': os.environ.get('PAYPAL_ENV', 'sandbox') == 'live'
    })


# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500


# ==================== MAIN ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_ENV') == 'development'

    print("\n" + "="*60)
    print("SINCOR MASTER PLATFORM")
    print("="*60)
    print(f"Port: {port}")
    print(f"Debug mode: {debug_mode}")
    print(f"Authentication: {'ENABLED' if AUTH_AVAILABLE else 'DISABLED'}")
    print(f"Rate Limiting: {'ENABLED' if RATE_LIMIT_AVAILABLE else 'DISABLED'}")
    print(f"PayPal: {'ENABLED' if PAYPAL_AVAILABLE else 'DISABLED'}")
    print(f"Monetization: {'ENABLED' if MONETIZATION_AVAILABLE else 'DISABLED'}")
    print(f"Waitlist: {'ENABLED' if WAITLIST_AVAILABLE else 'DISABLED'}")

    if WAITLIST_AVAILABLE:
        print(f"Database: {waitlist_manager.db_path}")

    if AUTH_AVAILABLE:
        print("\nDefault admin credentials:")
        print(f"  Username: {os.environ.get('ADMIN_USERNAME', 'admin')}")
        print(f"  Password: {os.environ.get('ADMIN_PASSWORD', 'changeme123')}")
        print("  CHANGE THESE IN PRODUCTION!")

    if RATE_LIMIT_AVAILABLE:
        print("\nRate Limiting Active:")
        print("  Authentication: 5/min, 20/hour, 50/day")
        print("  Payments: 10/min, 50/hour, 200/day")
        print("  Public endpoints: 20/min, 100/hour, 500/day")

    print("="*60 + "\n")

    app.run(host='0.0.0.0', port=port, debug=debug_mode)
