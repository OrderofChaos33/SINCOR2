#!/usr/bin/env python3
"""
SINCOR Main Flask Application with Product Showcase and Waitlist System
"""

import os
from flask import Flask, render_template, request, jsonify

# Import waitlist system with error handling
try:
    from waitlist_system import waitlist_manager
    WAITLIST_AVAILABLE = True
except ImportError as e:
    print(f"Waitlist system not available: {e}")
    WAITLIST_AVAILABLE = False

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'development-key-change-in-production')

# Configure template folder
app.template_folder = 'templates'

@app.route('/')
def index():
    """Main landing page with product showcase"""
    return render_template('index.html')

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

    # Check if monetization is available based on PayPal env vars
    monetization_available = bool(
        os.environ.get('PAYPAL_REST_API_ID') and
        os.environ.get('PAYPAL_REST_API_SECRET')
    )

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