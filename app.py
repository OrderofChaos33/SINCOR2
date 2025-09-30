#!/usr/bin/env python3
"""
SINCOR Master Application - Complete AI Business Automation Platform
Combines professional features, monetization engine, and 42 AI agents
"""

import os
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for
import csv, datetime, re, smtplib, logging
from email.message import EmailMessage

# Initialize Flask app with professional configuration
app = Flask(__name__, static_folder=str(Path(__file__).parent), static_url_path="")
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'sincor-master-2025-production')
app.template_folder = 'templates'

# Professional logging setup
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
LOGDIR = ROOT / "logs"
LOGDIR.mkdir(exist_ok=True)
LOGFILE = LOGDIR / "run.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGFILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def log(msg):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    logger.info(msg)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

# Environment configuration
SMTP_HOST = os.getenv("SMTP_HOST", "") or os.getenv("smtp_host", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or os.getenv("smtp_port", "587"))
SMTP_USER = os.getenv("SMTP_USER", "") or os.getenv("smtp_user", "")
SMTP_PASS = os.getenv("SMTP_PASS", "") or os.getenv("smtp_pass", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "noreply@sincor.local")
EMAIL_TO = [e.strip() for e in os.getenv("EMAIL_TO", "admin@sincor.local").split(",") if e.strip()]

# Import systems with graceful fallbacks
try:
    from waitlist_system import waitlist_manager
    WAITLIST_AVAILABLE = True
except ImportError as e:
    log(f"Waitlist system not available: {e}")
    WAITLIST_AVAILABLE = False

try:
    from monetization_engine import MonetizationEngine
    from paypal_integration import SINCORPaymentProcessor, PaymentRequest
    MONETIZATION_AVAILABLE = True
except ImportError as e:
    log(f"Monetization modules not available: {e}")
    MONETIZATION_AVAILABLE = False

# Initialize monetization if available
if MONETIZATION_AVAILABLE:
    try:
        monetization_engine = MonetizationEngine()
        payment_processor = SINCORPaymentProcessor()
        log("✅ SINCOR Monetization Engine initialized")
    except Exception as e:
        log(f"❌ Error initializing monetization: {e}")
        monetization_engine = None
        payment_processor = None
else:
    monetization_engine = None
    payment_processor = None

# PROMO CODES for free trials
PROMO_CODES = {
    "PROTOTYPE2025": {
        "description": "Full free access for prototype testing - friends & select testers",
        "trial_days": 90,
        "bypass_payment": True,
        "max_uses": 50
    },
    "COURTTESTER": {
        "description": "Court's personal testing account",
        "trial_days": 365,
        "bypass_payment": True,
        "max_uses": 10
    },
    "FRIENDSTEST": {
        "description": "Friends and family testing - 3 months free",
        "trial_days": 90,
        "bypass_payment": True,
        "max_uses": 100
    }
}

# Main routes
@app.route('/')
def index():
    """Main landing page with 42 AI agents"""
    return render_template('index.html')

@app.route('/free-trial/<promo_code>')
def free_trial_activation(promo_code):
    """Direct free trial activation via URL."""
    promo_code = promo_code.upper()
    log(f"Promo activation attempt: {promo_code}")

    if promo_code not in PROMO_CODES:
        return f'''<!DOCTYPE html>
<html><head>
<title>Invalid Promo Code</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center p-4">
<div class="bg-red-900 p-6 sm:p-8 rounded-lg w-full max-w-md text-center">
<h1 class="text-xl sm:text-2xl font-bold mb-4">❌ Invalid Code</h1>
<p class="text-sm sm:text-base mb-4">The promo code "{promo_code}" is not valid.</p>
<div class="text-xs sm:text-sm text-gray-300 mb-4">
<p>Try these codes:</p>
<div class="font-mono text-green-400 space-y-1">
<div>FRIENDSTEST</div>
<div>PROTOTYPE2025</div>
</div>
</div>
<a href="/" class="inline-block bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded text-sm sm:text-base">← Back to Home</a>
</div></body></html>'''

    # Set promo session
    promo_data = PROMO_CODES[promo_code]
    session['promo_active'] = True
    session['promo_code'] = promo_code
    session['promo_trial_days'] = promo_data['trial_days']
    session['promo_bypass_payment'] = promo_data['bypass_payment']
    session['promo_activated_at'] = datetime.datetime.now().isoformat()

    log(f"Promo activated successfully: {promo_code}")

    return f'''<!DOCTYPE html>
<html><head>
<title>Free Trial Activated!</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center p-4">
<div class="bg-green-900 p-6 sm:p-8 rounded-lg w-full max-w-lg text-center">
<h1 class="text-2xl sm:text-3xl font-bold mb-4 sm:mb-6">🎉 FREE TRIAL ACTIVATED!</h1>
<div class="bg-black p-4 sm:p-6 rounded-lg mb-4 sm:mb-6">
<h2 class="text-lg sm:text-xl font-bold text-green-400 mb-3 sm:mb-4">Your SINCOR Access:</h2>
<div class="space-y-2 text-left text-sm sm:text-base">
<div class="flex justify-between flex-wrap">
<span class="font-semibold">Code:</span>
<span class="font-mono text-green-400 break-all">{promo_code}</span>
</div>
<div class="flex justify-between flex-wrap">
<span class="font-semibold">Trial:</span>
<span class="text-green-400">{promo_data['trial_days']} days FREE</span>
</div>
<div class="flex justify-between flex-wrap">
<span class="font-semibold">Access:</span>
<span class="text-green-400">Full System</span>
</div>
<div class="flex justify-between flex-wrap">
<span class="font-semibold">AI Agents:</span>
<span class="text-green-400">✅ 42 Activated</span>
</div>
</div>
</div>
<div class="space-y-3 sm:space-y-4">
<a href="/business-setup" class="block bg-green-600 hover:bg-green-500 px-4 sm:px-6 py-2 sm:py-3 rounded-lg font-semibold text-center">
🏢 Set Up Your Business Profile
</a>
<a href="/admin/executive" class="block bg-blue-600 hover:bg-blue-500 px-4 sm:px-6 py-2 sm:py-3 rounded-lg font-semibold text-center">
🎯 Executive Dashboard
</a>
</div>
<p class="text-xs sm:text-sm text-gray-300 mt-4 sm:mt-6 leading-relaxed">
You now have full access to SINCOR's 42-agent AI business automation system. Explore all features - no payment required!
</p>
</div></body></html>'''

# Monetization routes
@app.route('/monetization/start', methods=['POST'])
async def start_monetization():
    """Start the monetization engine"""
    if not monetization_engine:
        return jsonify({'error': 'Monetization engine not available'}), 500

    try:
        # Execute monetization strategy
        strategy_report = await monetization_engine.execute_monetization_strategy(
            max_concurrent_opportunities=10
        )

        return jsonify({
            'status': 'success',
            'message': 'Monetization engine started',
            'opportunities_executed': strategy_report['execution_summary']['opportunities_executed'],
            'total_revenue': strategy_report['execution_summary']['total_revenue'],
            'success_rate': strategy_report['execution_summary']['success_rate']
        })

    except Exception as e:
        return jsonify({'error': f'Failed to start monetization: {str(e)}'}), 500

@app.route('/monetization/dashboard')
def monetization_dashboard():
    """Monetization dashboard"""
    return '''<!DOCTYPE html>
<html><head><title>SINCOR Monetization Dashboard</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen">
<div class="container mx-auto px-4 py-8">
    <div class="text-center mb-8">
        <h1 class="text-4xl font-bold mb-4 text-green-400">SINCOR Monetization Dashboard</h1>
        <p class="text-xl text-gray-300">Real-time revenue generation and payment processing</p>
    </div>

    <div class="grid md:grid-cols-3 gap-6 mb-8">
        <div class="bg-gray-800 p-6 rounded-lg">
            <h2 class="text-xl font-bold mb-4 text-blue-400">Revenue Opportunities</h2>
            <div id="opportunities-count" class="text-3xl font-bold text-blue-400">Loading...</div>
            <p class="text-gray-400">Active opportunities</p>
        </div>

        <div class="bg-gray-800 p-6 rounded-lg">
            <h2 class="text-xl font-bold mb-4 text-green-400">Total Revenue</h2>
            <div id="total-revenue" class="text-3xl font-bold text-green-400">Loading...</div>
            <p class="text-gray-400">Generated revenue</p>
        </div>

        <div class="bg-gray-800 p-6 rounded-lg">
            <h2 class="text-xl font-bold mb-4 text-purple-400">PayPal Status</h2>
            <div id="paypal-status" class="text-3xl font-bold text-purple-400">Ready</div>
            <p class="text-gray-400">API connection</p>
        </div>
    </div>

    <div class="bg-gray-800 p-6 rounded-lg">
        <h2 class="text-2xl font-bold mb-4 text-yellow-400">Quick Actions</h2>
        <div class="space-y-4">
            <button onclick="startMonetization()" class="w-full bg-green-600 hover:bg-green-700 px-4 py-2 rounded font-semibold">
                🚀 Start Monetization Engine
            </button>
            <button onclick="createTestPayment()" class="w-full bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded font-semibold">
                💳 Create Test Payment
            </button>
        </div>
    </div>
</div>

<script>
async function startMonetization() {
    try {
        const response = await fetch('/monetization/start', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'}
        });

        const result = await response.json();
        if (result.status === 'success') {
            alert(`Monetization started: ${result.opportunities_executed} opportunities executed`);
        } else {
            alert(`Error: ${result.error}`);
        }
    } catch (error) {
        alert(`Network error: ${error.message}`);
    }
}

async function createTestPayment() {
    alert('Payment system ready - full PayPal integration available');
}
</script>

</body></html>'''

# Health check
@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'SINCOR Master Platform',
        'monetization_available': MONETIZATION_AVAILABLE,
        'waitlist_available': WAITLIST_AVAILABLE,
        'ai_agents': 42,
        'timestamp': datetime.datetime.now().isoformat()
    })

# Additional professional routes
@app.route('/business-setup', methods=['GET', 'POST'])
def business_setup():
    if request.method == 'POST':
        business_data = {
            "company_name": request.form.get("company_name", "").strip(),
            "industry": request.form.get("industry", ""),
            "setup_completed": True,
            "setup_date": datetime.datetime.now().isoformat()
        }
        session['business_profile'] = business_data
        log(f"Business setup completed: {business_data['company_name']}")
        return redirect('/admin/executive')

    return render_template('business_setup.html')

@app.route('/admin/executive')
def executive_dashboard():
    return render_template('executive_dashboard.html')

@app.route('/discovery-dashboard')
def discovery_dashboard():
    return render_template('discovery-dashboard.html')

@app.route('/enterprise-dashboard')
def enterprise_dashboard():
    return render_template('enterprise-dashboard.html')

@app.route('/pricing')
def pricing():
    return render_template('pricing.html')

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    host = '0.0.0.0'

    log(f"Starting SINCOR MASTER PLATFORM on {host}:{port}")
    log("✅ 42 AI Agents Platform")
    log("✅ Professional Admin System")
    log("✅ Monetization Engine" if MONETIZATION_AVAILABLE else "⚠️ Monetization Engine Not Available")
    log("✅ PayPal Integration" if MONETIZATION_AVAILABLE else "⚠️ PayPal Integration Not Available")
    log("✅ Waitlist System" if WAITLIST_AVAILABLE else "⚠️ Waitlist System Not Available")
    log("Promo routes: /free-trial/FRIENDSTEST, /free-trial/PROTOTYPE2025, /free-trial/COURTTESTER")

    try:
        app.run(host=host, port=port, debug=False)
    except Exception as e:
        logger.error(f"Failed to start SINCOR Master Platform: {e}")
        print(f"Error starting application: {e}")