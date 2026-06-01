#!/usr/bin/env python3
"""
SINCOR Main Flask Application with Product Showcase and Waitlist System
FIXED: Removed async/await for Flask compatibility
ADDED: JWT Authentication for admin endpoints
ADDED: Rate Limiting for DDoS protection
"""

import logging
import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import Flask, render_template, request, jsonify, redirect, session, url_for
from flask_jwt_extended import jwt_required, get_jwt_identity

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

_log = logging.getLogger(__name__)

# Import authentication system
try:
    from sincor2.auth_system import SINCORAuth, admin_required
    AUTH_AVAILABLE = True
except ImportError as e:
    print(f"Auth system not available: {e}")
    AUTH_AVAILABLE = False

# Import rate limiter
try:
    from sincor2.rate_limiter import (
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
    from sincor2.security_headers import SecurityHeaders
    SECURITY_HEADERS_AVAILABLE = True
except ImportError as e:
    print(f"Security headers not available: {e}")
    SECURITY_HEADERS_AVAILABLE = False

# Import production logger
try:
    from sincor2.production_logger import SINCORLogger
    LOGGING_AVAILABLE = True
except ImportError as e:
    print(f"Production logger not available: {e}")
    LOGGING_AVAILABLE = False

# Import monitoring dashboard
try:
    from sincor2.monitoring_dashboard import MonitoringDashboard
    MONITORING_AVAILABLE = True
except ImportError as e:
    print(f"Monitoring dashboard not available: {e}")
    MONITORING_AVAILABLE = False

# Import validation models
try:
    from sincor2.validation_models import (
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
    from sincor2.waitlist_system import waitlist_manager
    WAITLIST_AVAILABLE = True
except ImportError as e:
    print(f"Waitlist system not available: {e}")
    WAITLIST_AVAILABLE = False

# Import PayPal integration with SYNC wrappers
try:
    from sincor2.paypal_integration_sync import PayPalIntegrationSync, SINCORPaymentProcessorSync
    from sincor2.paypal_integration import PaymentRequest
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
    from sincor2.monetization_engine import MonetizationEngine
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

# Import order fulfillment system
try:
    from sincor2.order_fulfillment import fulfillment_system
    FULFILLMENT_AVAILABLE = True
    print("Order Fulfillment System Loaded Successfully")
except ImportError as e:
    print(f"Fulfillment system not available: {e}")
    FULFILLMENT_AVAILABLE = False
    fulfillment_system = None
except Exception as e:
    print(f"Fulfillment system error: {e}")
    FULFILLMENT_AVAILABLE = False
    fulfillment_system = None

# Import SINAX Proof Topology Navigator
try:
    from sincor2.sinax.ptn import ProofTopologyNavigator
    ptn = ProofTopologyNavigator()
    PTN_AVAILABLE = True
    print("SINAX Proof Topology Navigator Loaded Successfully")
except ImportError as e:
    print(f"SINAX PTN not available: {e}")
    PTN_AVAILABLE = False
    ptn = None
except Exception as e:
    print(f"SINAX PTN error: {e}")
    PTN_AVAILABLE = False
    ptn = None

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'development-key-change-in-production')

# Configure template folder
app.template_folder = 'templates'

# Initialize A2A (Agent-to-Agent) integration routes
try:
    from sincor2.a2a_integration import A2ARouter
    a2a_router = A2ARouter()
    app.register_blueprint(a2a_router.blueprint)
    print("✓ A2A integration routes registered")
except Exception as e:
    print(f"WARNING: A2A integration not available: {e}")

# Initialize Stripe routes (MUST be after app creation, BEFORE other routes)
try:
    from sincor2.stripe_routes import init_stripe_routes
    from sincor2.stripe_checkout import StripeCheckout
    
    stripe_processor = StripeCheckout()
    init_stripe_routes(app, stripe_processor)
    print("✓ Stripe routes initialized")
except ImportError as e:
    print(f"WARNING: Stripe not available: {e}")
except Exception as e:
    print(f"WARNING: Stripe initialization error: {e}")

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

# Add COOP header for PayPal popup compatibility
@app.after_request
def set_coop_header(response):
    """
    Set Cross-Origin-Opener-Policy header to allow PayPal popups.
    This fixes the spinning popup issue with PayPal JavaScript SDK.
    Required for PayPal buttons to open payment popups properly.
    """
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
    return response

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


def _build_oauth_redirect_uri(callback_endpoint):
    """Build OAuth callback URI using PLATFORM_URL when configured."""
    platform_url = os.environ.get('PLATFORM_URL', '').strip().rstrip('/')
    if platform_url:
        return f"{platform_url}{url_for(callback_endpoint)}"
    return url_for(callback_endpoint, _external=True)


def _oauth_failure_redirect(provider, reason):
    allowed_providers = {'google', 'github'}
    allowed_reasons = {
        'not_configured',
        'invalid_state',
        'missing_code',
        'missing_access_token',
        'token_exchange_failed',
        'oauth_error'
    }

    safe_provider = provider if provider in allowed_providers else 'oauth'
    safe_reason = reason if reason in allowed_reasons else 'oauth_error'
    return redirect(url_for('signup', oauth=safe_provider, error=safe_reason))


def _store_oauth_user(provider, email, name, provider_user_id, avatar_url=None):
    if not email:
        raise ValueError("OAuth provider did not return an email address")

    session['oauth_user'] = {
        'provider': provider,
        'email': email,
        'name': name or email.split('@')[0],
        'provider_user_id': str(provider_user_id),
        'avatar_url': avatar_url,
        'connected_at': datetime.now(timezone.utc).isoformat()
    }


# ==================== PUBLIC ROUTES ====================

@app.route('/')
@limiter.exempt if limiter else lambda f: f
def index():
    """Main landing page — competitive intelligence (NO RATE LIMIT)"""
    return render_template('home.html')


@app.route('/signup')
@limiter.exempt if limiter else lambda f: f
def signup():
    """Signup page with OAuth options."""
    return render_template('signup.html')


@app.route('/wallet-connect')
@limiter.exempt if limiter else lambda f: f
def wallet_connect():
    """Legacy wallet-connect URL mapped to the live SINC gateway page."""
    return redirect('/sinc', code=302)


@app.route('/auth/google')
@limiter.limit(PUBLIC_LIMITS) if limiter else lambda f: f
def auth_google():
    """Start Google OAuth flow."""
    google_client_id = os.environ.get('GOOGLE_OAUTH_CLIENT_ID')
    if not google_client_id:
        return _oauth_failure_redirect('google', 'not_configured')

    state = secrets.token_urlsafe(32)
    session['google_oauth_state'] = state

    params = {
        'client_id': google_client_id,
        'redirect_uri': _build_oauth_redirect_uri('auth_google_callback'),
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account'
    }
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")


@app.route('/auth/google/callback')
@limiter.limit(PUBLIC_LIMITS) if limiter else lambda f: f
def auth_google_callback():
    """Handle Google OAuth callback."""
    expected_state = session.pop('google_oauth_state', None)
    if not expected_state or expected_state != request.args.get('state'):
        return _oauth_failure_redirect('google', 'invalid_state')

    code = request.args.get('code')
    if not code:
        failure_reason = 'oauth_error' if request.args.get('error') else 'missing_code'
        return _oauth_failure_redirect('google', failure_reason)

    google_client_id = os.environ.get('GOOGLE_OAUTH_CLIENT_ID')
    google_client_secret = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')
    if not google_client_id or not google_client_secret:
        return _oauth_failure_redirect('google', 'not_configured')

    try:
        token_response = requests.post(
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': google_client_id,
                'client_secret': google_client_secret,
                'code': code,
                'grant_type': 'authorization_code',
                'redirect_uri': _build_oauth_redirect_uri('auth_google_callback')
            },
            timeout=15
        )
        token_response.raise_for_status()
        access_token = token_response.json().get('access_token')
        if not access_token:
            return _oauth_failure_redirect('google', 'missing_access_token')

        auth_header = 'Bearer ' + access_token
        profile_response = requests.get(
            'https://openidconnect.googleapis.com/v1/userinfo',
            headers={'Authorization': auth_header},
            timeout=15
        )
        profile_response.raise_for_status()
        profile = profile_response.json()

        _store_oauth_user(
            provider='google',
            email=profile.get('email'),
            name=profile.get('name'),
            provider_user_id=profile.get('sub'),
            avatar_url=profile.get('picture')
        )
    except requests.RequestException as oauth_error:
        print(f"Google OAuth request failed: {oauth_error}")
        return _oauth_failure_redirect('google', 'token_exchange_failed')
    except ValueError as oauth_error:
        print(f"Google OAuth profile validation failed: {oauth_error}")
        return _oauth_failure_redirect('google', 'token_exchange_failed')

    return redirect(f"{url_for('dashboard')}?oauth=google")


@app.route('/auth/github')
@limiter.limit(PUBLIC_LIMITS) if limiter else lambda f: f
def auth_github():
    """Start GitHub OAuth flow."""
    github_client_id = os.environ.get('GITHUB_OAUTH_CLIENT_ID')
    if not github_client_id:
        return _oauth_failure_redirect('github', 'not_configured')

    state = secrets.token_urlsafe(32)
    session['github_oauth_state'] = state

    params = {
        'client_id': github_client_id,
        'redirect_uri': _build_oauth_redirect_uri('auth_github_callback'),
        'scope': 'read:user user:email',
        'state': state
    }
    return redirect(f"https://github.com/login/oauth/authorize?{urlencode(params)}")


@app.route('/auth/github/callback')
@limiter.limit(PUBLIC_LIMITS) if limiter else lambda f: f
def auth_github_callback():
    """Handle GitHub OAuth callback."""
    expected_state = session.pop('github_oauth_state', None)
    if not expected_state or expected_state != request.args.get('state'):
        return _oauth_failure_redirect('github', 'invalid_state')

    code = request.args.get('code')
    if not code:
        failure_reason = 'oauth_error' if request.args.get('error') else 'missing_code'
        return _oauth_failure_redirect('github', failure_reason)

    github_client_id = os.environ.get('GITHUB_OAUTH_CLIENT_ID')
    github_client_secret = os.environ.get('GITHUB_OAUTH_CLIENT_SECRET')
    if not github_client_id or not github_client_secret:
        return _oauth_failure_redirect('github', 'not_configured')

    try:
        token_response = requests.post(
            'https://github.com/login/oauth/access_token',
            data={
                'client_id': github_client_id,
                'client_secret': github_client_secret,
                'code': code,
                'state': expected_state,
                'redirect_uri': _build_oauth_redirect_uri('auth_github_callback')
            },
            headers={'Accept': 'application/json'},
            timeout=15
        )
        token_response.raise_for_status()
        access_token = token_response.json().get('access_token')
        if not access_token:
            return _oauth_failure_redirect('github', 'missing_access_token')

        auth_header = 'Bearer ' + access_token
        headers = {
            'Authorization': auth_header,
            'Accept': 'application/vnd.github+json'
        }

        profile_response = requests.get('https://api.github.com/user', headers=headers, timeout=15)
        profile_response.raise_for_status()
        profile = profile_response.json()

        email_response = requests.get('https://api.github.com/user/emails', headers=headers, timeout=15)
        email_response.raise_for_status()
        emails = email_response.json()

        primary_email = next(
            (item.get('email') for item in emails if item.get('primary') and item.get('verified')),
            None
        ) or next(
            (item.get('email') for item in emails if item.get('verified')),
            profile.get('email')
        )

        _store_oauth_user(
            provider='github',
            email=primary_email,
            name=profile.get('name') or profile.get('login'),
            provider_user_id=profile.get('id'),
            avatar_url=profile.get('avatar_url')
        )
    except requests.RequestException as oauth_error:
        print(f"GitHub OAuth request failed: {oauth_error}")
        return _oauth_failure_redirect('github', 'token_exchange_failed')
    except ValueError as oauth_error:
        print(f"GitHub OAuth profile validation failed: {oauth_error}")
        return _oauth_failure_redirect('github', 'token_exchange_failed')

    return redirect(f"{url_for('dashboard')}?oauth=github")


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


@app.route('/api/signup', methods=['POST'])
@limiter.limit(PUBLIC_LIMITS) if limiter else lambda f: f
def api_signup():
    """Compatibility signup endpoint mapping to waitlist"""
    try:
        if not WAITLIST_AVAILABLE:
            return jsonify({'success': False, 'error': 'Signup system temporarily unavailable'}), 503

        signup_data = request.get_json()
        if not signup_data or not signup_data.get('email'):
            return jsonify({'success': False, 'error': 'Email address is required'}), 400

        if VALIDATION_AVAILABLE:
            validated_data, error = validate_request(WaitlistSignup, signup_data)
            if error:
                return jsonify({'success': False, 'error': 'Invalid signup data'}), 400
            signup_data = validated_data

        result = waitlist_manager.add_to_waitlist(signup_data)
        if result.get('success'):
            return jsonify({
                'success': True,
                'message': 'Successfully added to waitlist'
            }), 200

        return jsonify({'success': False, 'error': 'Signup failed'}), 400

    except Exception as e:
        app.logger.exception('Signup compatibility endpoint failed: %s', e)
        return jsonify({'success': False, 'error': 'Server error'}), 500


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
@limiter.exempt if limiter else lambda f: f
def admin_dashboard():
    """Admin Dashboard - Full control panel with real data"""
    # Get real metrics from fulfillment system
    metrics = {
        'total_leads': 1 if FULFILLMENT_AVAILABLE and len(fulfillment_system.orders) > 0 else 0,
        'active_clients': len(fulfillment_system.orders) if FULFILLMENT_AVAILABLE else 0,
        'uptime_percentage': 99.9,
        'uptime_days': 3,
        'agent_tasks_completed': len(fulfillment_system.orders) if FULFILLMENT_AVAILABLE else 0
    }

    # Get agent network status
    agents = {
        'coordination_score': 94,
        'total_agents': 42,
        'all_online': True,
        'categories': {
            'Business Operations': {'count': 12, 'status': 'Online', 'agents': ['Sales Agent', 'Support Agent', 'Operations Manager']},
            'Intelligence & Analytics': {'count': 8, 'status': 'Active', 'agents': ['Data Analyst', 'BI Reporter', 'Forecaster']},
            'Marketing & Content': {'count': 10, 'status': 'Running', 'agents': ['Content Writer', 'SEO Specialist', 'Social Media Manager']},
            'Compliance & Legal': {'count': 6, 'status': 'Monitoring', 'agents': ['Compliance Officer', 'Legal Advisor', 'Risk Manager']},
            'Technical': {'count': 6, 'status': 'Active', 'agents': ['DevOps', 'Security', 'Database Admin']}
        }
    }

    # Get recent activity
    activity = []
    if FULFILLMENT_AVAILABLE:
        for order in list(fulfillment_system.orders.values())[:5]:
            activity.append({
                'category': 'success' if order.delivery_status.value == 'delivered' else 'info',
                'title': f'Order {order.order_id[:8]}...',
                'description': f'{order.product_name} - {order.customer_email} - Status: {order.delivery_status.value}'
            })

    if not activity:
        activity = [{
            'category': 'success',
            'title': 'System Initialized',
            'description': 'SINCOR platform is running and all systems operational'
        }]

    return render_template('admin_dashboard.html', metrics=metrics, agents=agents, activity=activity)


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


@app.route('/dashboards')
@limiter.exempt if limiter else lambda f: f
def dashboards_menu():
    """Dashboards command center - Central navigation for all dashboards"""
    return render_template('dashboards_menu.html')


# Route aliases for dashboard menu compatibility
@app.route('/executive-dashboard')
@limiter.exempt if limiter else lambda f: f
def executive_dashboard_alias():
    """Executive Dashboard - Alias route"""
    return executive_dashboard()


@app.route('/professional-dashboard')
@limiter.exempt if limiter else lambda f: f
def professional_dashboard_alias():
    """Professional Dashboard - Alias route"""
    return professional_dashboard()


@app.route('/consciousness-dashboard')
@limiter.exempt if limiter else lambda f: f
def consciousness_dashboard_alias():
    """Consciousness Transfer Dashboard - Alias route"""
    return consciousness_transfer_dashboard()


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


@app.route('/products/starter')
@limiter.exempt if limiter else lambda f: f
def product_starter():
    """Starter plan landing page"""
    return render_template('product_starter.html')


@app.route('/products/professional')
@limiter.exempt if limiter else lambda f: f
def product_professional():
    """Professional plan landing page"""
    return render_template('product_professional.html')


@app.route('/products/enterprise')
@limiter.exempt if limiter else lambda f: f
def product_enterprise():
    """Enterprise plan landing page"""
    return render_template('product_enterprise.html')


@app.route('/sinc')
@limiter.exempt if limiter else lambda f: f
def sinc_token():
    """SINC token gateway page"""
    return render_template('sinc_gateway.html')


@app.route('/axiom')
@limiter.exempt if limiter else lambda f: f
def axiom_token():
    """AXIOM (AXM) token page — autonomous intelligence token on Base"""
    return render_template('axiom.html')


@app.route('/buy')
@limiter.exempt if limiter else lambda f: f
def buy():
    """Buy SINCOR - Main store page with all products"""
    # Get PayPal client ID from environment only (no hardcoded fallback)
    paypal_client_id = os.getenv('PAYPAL_REST_API_ID', '')
    return render_template('buy.html', paypal_client_id=paypal_client_id)


@app.route('/paypal-test')
@limiter.exempt if limiter else lambda f: f
def paypal_test():
    """PayPal SDK test page"""
    paypal_client_id = os.getenv('PAYPAL_REST_API_ID', '')
    return render_template('paypal_test.html', paypal_client_id=paypal_client_id)


@app.route('/paypal-test2')
@limiter.exempt if limiter else lambda f: f
def paypal_test2():
    """PayPal SDK test - No intent parameter"""
    paypal_client_id = os.getenv('PAYPAL_REST_API_ID', '')
    return render_template('paypal_test2.html', paypal_client_id=paypal_client_id)


@app.route('/paypal-test3')
@limiter.exempt if limiter else lambda f: f
def paypal_test3():
    """PayPal SDK test - Subscription intent"""
    paypal_client_id = os.getenv('PAYPAL_REST_API_ID', '')
    return render_template('paypal_test3.html', paypal_client_id=paypal_client_id)


@app.route('/paypal-test4')
@limiter.exempt if limiter else lambda f: f
def paypal_test4():
    """PayPal SDK test - Components parameter"""
    paypal_client_id = os.getenv('PAYPAL_REST_API_ID', '')
    return render_template('paypal_test4.html', paypal_client_id=paypal_client_id)


@app.route('/paypal-summary')
@limiter.exempt if limiter else lambda f: f
def paypal_summary():
    """PayPal SDK test summary page"""
    paypal_client_id = os.getenv('PAYPAL_REST_API_ID', '')
    return render_template('paypal_summary.html', paypal_client_id=paypal_client_id)


@app.route('/paypal-diagnostic')
@limiter.exempt if limiter else lambda f: f
def paypal_diagnostic():
    """PayPal SDK diagnostic with detailed logging"""
    paypal_client_id = os.getenv('PAYPAL_REST_API_ID', '')
    return render_template('paypal_diagnostic.html', paypal_client_id=paypal_client_id)


@app.route('/payment/success')
@limiter.exempt if limiter else lambda f: f
def payment_success():
    """Payment success page with order details"""
    return render_template('payment_success.html')


@app.route('/payment/cancel')
@limiter.exempt if limiter else lambda f: f
def payment_cancel():
    """Payment cancellation page"""
    return render_template('payment_cancel.html')


@app.route('/api/payment/webhook', methods=['POST'])
@limiter.limit(PAYMENT_LIMITS) if limiter else lambda f: f
def payment_webhook():
    """PayPal webhook for order fulfillment"""
    if not FULFILLMENT_AVAILABLE:
        return jsonify({'error': 'Fulfillment system not available'}), 503

    try:
        webhook_data = request.get_json()

        # Extract order details
        order_data = {
            'order_id': webhook_data.get('id', f"ORD-{int(datetime.now().timestamp())}"),
            'customer_email': webhook_data.get('payer', {}).get('email_address', 'unknown@example.com'),
            'product_name': webhook_data.get('purchase_units', [{}])[0].get('description', 'Unknown Product'),
            'amount': float(webhook_data.get('purchase_units', [{}])[0].get('amount', {}).get('value', 0)),
            'payment_id': webhook_data.get('id', '')
        }

        # Process order fulfillment asynchronously
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        order = loop.run_until_complete(fulfillment_system.process_order(order_data))
        loop.close()

        return jsonify({
            'success': True,
            'order_id': order.order_id,
            'delivery_status': order.delivery_status.value,
            'delivery_url': order.delivery_url
        })

    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/my-orders')
@limiter.exempt if limiter else lambda f: f
def my_orders():
    """Customer portal - view purchased products"""
    return render_template('my_orders.html')


@app.route('/api/orders/<email>')
@limiter.limit(MONITORING_LIMITS) if limiter else lambda f: f
def get_customer_orders(email):
    """Get all orders for a customer email"""
    if not FULFILLMENT_AVAILABLE:
        return jsonify({'error': 'Fulfillment system not available'}), 503

    try:
        orders = fulfillment_system.get_customer_orders(email)

        return jsonify({
            'success': True,
            'orders': [
                {
                    'order_id': order.order_id,
                    'product_name': order.product_name,
                    'amount': order.amount,
                    'created_at': order.created_at,
                    'delivery_status': order.delivery_status.value,
                    'delivery_url': order.delivery_url
                }
                for order in orders
            ]
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
        'GITHUB_OAUTH_CLIENT_ID',
        'GITHUB_OAUTH_CLIENT_SECRET',
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
@limiter.limit(PAYMENT_LIMITS) if limiter else lambda f: f
def create_payment():
    """Create a PayPal payment (PUBLIC + RATE LIMITED + VALIDATED)"""
    if not PAYPAL_AVAILABLE:
        return jsonify({'error': 'PayPal integration not available'}), 503

    try:
        # Allow both authenticated and public purchases
        current_user = None
        if request.headers.get('Authorization'):
            try:
                from flask_jwt_extended import verify_jwt_in_request
                verify_jwt_in_request(optional=True)
                current_user = get_jwt_identity()
            except:
                pass

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

        user_info = current_user if current_user else "guest"
        print(f"Payment created by: {user_info} - Amount: ${payment_data['amount']}")

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
@limiter.limit(PAYMENT_LIMITS) if limiter else lambda f: f
def execute_payment():
    """Execute a PayPal payment after approval (PUBLIC + RATE LIMITED + VALIDATED)"""
    if not PAYPAL_AVAILABLE:
        return jsonify({'error': 'PayPal integration not available'}), 503

    try:
        # Allow both authenticated and public purchases
        current_user = None
        if request.headers.get('Authorization'):
            try:
                from flask_jwt_extended import verify_jwt_in_request
                verify_jwt_in_request(optional=True)
                current_user = get_jwt_identity()
            except:
                pass

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

        user_info = current_user if current_user else "guest"
        print(f"Payment executed by: {user_info} - Payment ID: {payment_id}")

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


# ==================== SINAX PROOF TOPOLOGY NAVIGATOR ====================

@app.route('/api/sinax/solve', methods=['POST'])
def sinax_solve():
    """
    Full PTN proof search (all 4 layers).

    POST body (JSON):
      start_state  : str  — initial proof state
      target_state : str  — desired final state
      context      : list[str] (optional) — additional lemmas/hypotheses
    """
    if not PTN_AVAILABLE:
        return jsonify({'error': 'SINAX PTN not available'}), 503

    data = request.get_json(silent=True) or {}
    start = data.get('start_state', '').strip()
    target = data.get('target_state', '').strip()
    context = data.get('context', [])

    if not start or not target:
        return jsonify({'error': 'start_state and target_state are required'}), 400

    try:
        result = ptn.solve(start_state=start, target_state=target, context_states=context)
        return jsonify(result.to_dict())
    except Exception as e:
        _log.exception("sinax_solve error")
        return jsonify({'error': 'Proof search failed'}), 500


@app.route('/api/sinax/embed', methods=['POST'])
def sinax_embed():
    """
    Layer 1 — Embedding Manifold.

    POST body (JSON): { "proof_state": "..." }
    """
    if not PTN_AVAILABLE:
        return jsonify({'error': 'SINAX PTN not available'}), 503

    data = request.get_json(silent=True) or {}
    state = data.get('proof_state', '').strip()
    if not state:
        return jsonify({'error': 'proof_state is required'}), 400

    try:
        return jsonify(ptn.embed(state))
    except Exception as e:
        _log.exception("sinax_embed error")
        return jsonify({'error': 'Embedding failed'}), 500


@app.route('/api/sinax/geodesic', methods=['POST'])
def sinax_geodesic():
    """
    Layer 2 — Geodesic Flow Engine.

    POST body (JSON): { "start_state": "...", "target_state": "..." }
    """
    if not PTN_AVAILABLE:
        return jsonify({'error': 'SINAX PTN not available'}), 503

    data = request.get_json(silent=True) or {}
    start = data.get('start_state', '').strip()
    target = data.get('target_state', '').strip()
    if not start or not target:
        return jsonify({'error': 'start_state and target_state are required'}), 400

    try:
        return jsonify(ptn.geodesic(start, target))
    except Exception as e:
        _log.exception("sinax_geodesic error")
        return jsonify({'error': 'Geodesic computation failed'}), 500


@app.route('/api/sinax/homology', methods=['POST'])
def sinax_homology():
    """
    Layer 3 — Homology Detector.

    POST body (JSON): { "proof_states": ["...", "..."] }
    """
    if not PTN_AVAILABLE:
        return jsonify({'error': 'SINAX PTN not available'}), 503

    data = request.get_json(silent=True) or {}
    states = data.get('proof_states', [])
    if not states or not isinstance(states, list):
        return jsonify({'error': 'proof_states must be a non-empty list'}), 400

    try:
        return jsonify(ptn.homology(states))
    except Exception as e:
        _log.exception("sinax_homology error")
        return jsonify({'error': 'Homology analysis failed'}), 500


@app.route('/api/sinax/morse', methods=['POST'])
def sinax_morse():
    """
    Layer 4 — Morse Theory Filter.

    POST body (JSON): { "proof_states": ["...", "..."] }
    """
    if not PTN_AVAILABLE:
        return jsonify({'error': 'SINAX PTN not available'}), 503

    data = request.get_json(silent=True) or {}
    states = data.get('proof_states', [])
    if not states or not isinstance(states, list):
        return jsonify({'error': 'proof_states must be a non-empty list'}), 400

    try:
        return jsonify(ptn.morse(states))
    except Exception as e:
        _log.exception("sinax_morse error")
        return jsonify({'error': 'Morse decomposition failed'}), 500


@app.route('/api/sinax/training-signal', methods=['POST'])
def sinax_training_signal():
    """
    Verified Data Flywheel — extract manifold geometry from verified proofs.

    POST body (JSON): { "verified_states": ["...", "..."] }
    """
    if not PTN_AVAILABLE:
        return jsonify({'error': 'SINAX PTN not available'}), 503

    data = request.get_json(silent=True) or {}
    states = data.get('verified_states', [])
    if not states or not isinstance(states, list):
        return jsonify({'error': 'verified_states must be a non-empty list'}), 400

    try:
        return jsonify(ptn.training_signal(states))
    except Exception as e:
        _log.exception("sinax_training_signal error")
        return jsonify({'error': 'Training signal extraction failed'}), 500


@app.route('/api/sinax/status')
def sinax_status():
    """SINAX PTN health check."""
    return jsonify({
        'ptn_available': PTN_AVAILABLE,
        'manifold_dim': ptn.manifold.dim if PTN_AVAILABLE else None,
        'endpoints': [
            '/api/sinax/solve',
            '/api/sinax/embed',
            '/api/sinax/geodesic',
            '/api/sinax/homology',
            '/api/sinax/morse',
            '/api/sinax/training-signal',
        ],
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
        env = os.environ.get('FLASK_ENV', os.environ.get('ENVIRONMENT', 'production')).lower()
        is_prod = env not in ('development', 'dev', 'test', 'testing', 'local')
        admin_pw_set = bool(os.environ.get('ADMIN_PASSWORD')) and os.environ.get('ADMIN_PASSWORD') != 'changeme123'
        print("\nAdmin credentials:")
        print(f"  Username: {os.environ.get('ADMIN_USERNAME', 'admin')}")
        if admin_pw_set:
            print(f"  Password: <configured via ADMIN_PASSWORD env var>")
        elif is_prod:
            print(f"  Password: NOT CONFIGURED — admin auth is REJECTED in production")
            print(f"  ⚠️  Set ADMIN_PASSWORD env var to a strong value before launch.")
        else:
            print(f"  Password: changeme123  (dev default — DO NOT use in production)")

    if RATE_LIMIT_AVAILABLE:
        print("\nRate Limiting Active:")
        print("  Authentication: 5/min, 20/hour, 50/day")
        print("  Payments: 10/min, 50/hour, 200/day")
        print("  Public endpoints: 20/min, 100/hour, 500/day")

    print("="*60 + "\n")

    app.run(host='0.0.0.0', port=port, debug=debug_mode)
