#!/usr/bin/env python3
"""
SINCOR - Stripe Payment Integration
Complete checkout flow with real payment processing
"""

import os
import html
import logging
import re
from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Email validation pattern
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _validate_email(email: str) -> bool:
    """Return True if email looks valid."""
    return bool(email and _EMAIL_RE.match(email) and len(email) <= 254)

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'sincor-secret-key-change-in-production')

# Import Stripe integration
try:
    from sincor2.stripe_checkout import get_stripe_checkout
    from sincor2.stripe_routes import init_stripe_routes
    STRIPE_AVAILABLE = True
except (ImportError, ModuleNotFoundError) as e:
    logger.warning(f"[STRIPE] Import error: {e}")
    STRIPE_AVAILABLE = False

# Initialize Stripe — always register routes so checkout endpoints exist;
# they'll return a clear error when Stripe is not configured.
stripe_processor = None
if STRIPE_AVAILABLE:
    stripe_processor = get_stripe_checkout()
    if stripe_processor and stripe_processor.enabled:
        logger.info(f"[APP] Stripe integration initialized ({stripe_processor.mode} mode)")
    else:
        logger.warning("[APP] Stripe keys not configured — checkout will be unavailable")
    init_stripe_routes(app, stripe_processor)


# ===== PAGE ROUTES =====

@app.route('/')
def home():
    """Home page"""
    return render_template('home.html')


@app.route('/buy')
def buy():
    """Buy page — always show the high-converting page; checkout JS handles missing Stripe gracefully"""
    return render_template('buy_converting.html')


@app.route('/pricing')
def pricing():
    """Pricing page"""
    return render_template('pricing.html')


@app.route('/payment/success')
def payment_success():
    """Payment success page"""
    session_id = request.args.get('session_id')

    if session_id and stripe_processor and stripe_processor.enabled:
        session = stripe_processor.get_session(session_id)
        if session:
            return render_template(
                'payment_success.html',
                session=session,
                email=session.get('customer_email'),
                amount=session.get('amount_total', 0) / 100,
            )

    return render_template('payment_success.html')


@app.route('/payment/cancel')
def payment_cancel():
    """Payment cancelled page"""
    return render_template('payment_cancel.html')


@app.route('/billing')
def billing_portal():
    """Redirect customer to Stripe Customer Portal for self-service billing management"""
    customer_id = request.args.get('customer_id')
    if not customer_id:
        return redirect('/buy')
    if not stripe_processor or not stripe_processor.enabled:
        return render_template('error.html',
                               error='Billing portal unavailable. Please email support@getsincor.com',
                               code=503), 503
    result = stripe_processor.create_portal_session(
        customer_id=customer_id,
        return_url=request.host_url,
    )
    if result.get('success'):
        return redirect(result['portal_url'])
    return render_template('error.html',
                           error='Could not open billing portal. Please email support@getsincor.com',
                           code=500), 500


# ===== STATIC CONTENT PAGES =====

@app.route('/privacy')
def privacy():
    """Privacy policy"""
    return render_template('privacy.html')


@app.route('/terms')
def terms():
    """Terms of service"""
    return render_template('terms.html')


@app.route('/security')
def security():
    """Security information"""
    return render_template('security.html')


@app.route('/media-packs')
def media_packs():
    """Media packs"""
    return render_template('media-packs.html')


@app.route('/affiliate-program')
def affiliate_program():
    """Affiliate program"""
    return render_template('affiliate-program.html')


@app.route('/discovery-dashboard')
def discovery_dashboard():
    """Discovery / live demo dashboard"""
    return render_template('discovery-dashboard.html')


@app.route('/enterprise-dashboard')
def enterprise_dashboard():
    """Enterprise dashboard"""
    return render_template('enterprise-dashboard.html')


@app.route('/dashboard')
def dashboard():
    """Customer dashboard"""
    return render_template('dashboard.html')


@app.route('/login')
def login():
    """Login page"""
    return render_template('login.html')


@app.route('/franchise-empire')
def franchise_empire():
    """Franchise empire page"""
    return render_template('franchise-empire.html')


@app.route('/whitepaper')
def whitepaper():
    """Whitepaper page"""
    return render_template('whitepaper.html')


# ===== EMAIL CAPTURE (waitlist / contact) =====

@app.route('/api/waitlist', methods=['POST'])
def join_waitlist():
    """Handle waitlist / contact form signups"""
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        email = (data.get('email') or '').strip().lower()
        name  = (data.get('name') or '').strip()[:100]
        plan  = (data.get('plan') or '').strip()[:50]

        if not _validate_email(email):
            return jsonify({'success': False, 'error': 'Valid email required'}), 400

        logger.info(f"[WAITLIST] New signup plan={plan}")

        # Optional: send confirmation email
        safe_name = html.escape(name or 'there')
        try:
            from sincor2.email_sender import get_email_sender
            sender = get_email_sender()
            sender.send_email(
                to_email=email,
                to_name=name or 'there',
                subject="You're on the SINCOR waitlist! 🚀",
                html_content=f"""
                <h2>Welcome, {safe_name}!</h2>
                <p>You've been added to the SINCOR waitlist. We'll be in touch very soon.</p>
                <p>In the meantime, <a href="https://getsincor.com/buy">start your free 30-day trial</a>.</p>
                <p>— The SINCOR Team</p>
                """,
                text_content="Welcome! You're on the SINCOR waitlist. Start your free trial at https://getsincor.com/buy",
            )
        except Exception as email_err:
            logger.warning(f"[WAITLIST] Email send failed: {email_err}")

        return jsonify({'success': True, 'message': "You're on the list! Check your email."})

    except Exception as e:
        logger.error(f"[WAITLIST] Error: {e}")
        return jsonify({'success': False, 'error': 'Something went wrong, please try again'}), 500


@app.route('/api/contact', methods=['POST'])
def contact():
    """Handle contact form submissions"""
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        email   = (data.get('email') or '').strip()[:254]
        name    = (data.get('name') or '').strip()[:100]
        message = (data.get('message') or '').strip()[:4000]

        if not _validate_email(email):
            return jsonify({'success': False, 'error': 'Valid email required'}), 400
        if not message:
            return jsonify({'success': False, 'error': 'Message is required'}), 400

        logger.info("[CONTACT] New contact form submission received")

        safe_name    = html.escape(name or email)
        safe_email   = html.escape(email)
        safe_message = html.escape(message)

        try:
            from sincor2.email_sender import get_email_sender
            sender = get_email_sender()
            sender.send_email(
                to_email='support@getsincor.com',
                to_name='SINCOR Support',
                subject=f"Contact form: {safe_name}",
                html_content=f"<p><strong>From:</strong> {safe_name} &lt;{safe_email}&gt;</p><p>{safe_message}</p>",
                text_content=f"From: {name} <{email}>\n\n{message}",
            )
        except Exception as email_err:
            logger.warning(f"[CONTACT] Email send failed: {email_err}")

        return jsonify({'success': True, 'message': "Message sent! We'll reply within 24 hours."})

    except Exception as e:
        logger.error(f"[CONTACT] Error: {e}")
        return jsonify({'success': False, 'error': 'Something went wrong, please try again'}), 500


# ===== HEALTH CHECK =====

@app.route('/health')
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'stripe': {
            'available': STRIPE_AVAILABLE,
            'enabled': stripe_processor.enabled if stripe_processor else False,
            'mode': stripe_processor.mode if stripe_processor else 'unconfigured',
        },
    })


# ===== ERROR HANDLERS =====

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', error='Page not found', code=404), 404


@app.errorhandler(500)
def server_error(e):
    logger.error(f"[APP] Server error: {str(e)}")
    return render_template('error.html', error='Internal server error', code=500), 500


# ===== START APP =====

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    debug = os.getenv('FLASK_ENV', 'production') == 'development'

    logger.info(f"[APP] Starting SINCOR on port {port}")
    logger.info(f"[APP] Debug mode: {debug}")
    logger.info(f"[APP] Stripe available: {STRIPE_AVAILABLE}")

    app.run(host='0.0.0.0', port=port, debug=debug)
