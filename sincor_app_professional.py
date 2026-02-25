#!/usr/bin/env python3
"""
SINCOR Professional Application - Production Ready
Enhanced with professional admin system and real metrics
"""

from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template, session
import os, csv, datetime, re, smtplib
from email.message import EmailMessage
import logging

# Load environment variables
def load_environment():
    try:
        from dotenv import load_dotenv
        config_dir = Path(__file__).parent / "config"
        local_env = config_dir / ".env"
        if local_env.exists():
            load_dotenv(local_env)
            return "development"
        prod_env = config_dir / "production.env"
        if prod_env.exists():
            load_dotenv(prod_env)
            return "production"
        return "system"
    except ImportError:
        return "system"

env_source = load_environment()

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"outputs"; OUT.mkdir(exist_ok=True)
LOGDIR=ROOT/"logs"; LOGDIR.mkdir(exist_ok=True)
LOGFILE=LOGDIR/"run.log"
LEADSCSV=OUT/"leads.csv"

# Set up professional logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOGFILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Environment configuration
SMTP_HOST=os.getenv("SMTP_HOST","") or os.getenv("smtp_host","")
SMTP_PORT=int(os.getenv("SMTP_PORT","587") or os.getenv("smtp_port","587"))
SMTP_USER=os.getenv("SMTP_USER","") or os.getenv("smtp_user","")
SMTP_PASS=os.getenv("SMTP_PASS","") or os.getenv("smtp_pass","")
EMAIL_FROM=os.getenv("EMAIL_FROM","noreply@sincor.local") or os.getenv("email_from","noreply@sincor.local")
EMAIL_TO=[e.strip() for e in os.getenv("EMAIL_TO","admin@sincor.local").split(",") if e.strip()]
NOTIFY_PHONE=os.getenv("NOTIFY_PHONE","+15551234567")

# API Keys
GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY","") or os.getenv("GOOGLE_PLACES_API_KEY","")
# PayPal REST API (preferred)
PAYPAL_REST_API_ID=os.getenv("PAYPAL_REST_API_ID","")
PAYPAL_REST_API_SECRET=os.getenv("PAYPAL_REST_API_SECRET","")
PAYPAL_MODE=os.getenv("PAYPAL_MODE","sandbox")
# On-chain marketplace config
BASE_RPC_URL=os.getenv("BASE_RPC_URL","")
MARKETPLACE_PRIVATE_KEY=os.getenv("MARKETPLACE_PRIVATE_KEY","")
# Legacy Stripe compatibility removed. Primary payment providers are PayPal and on-chain SINC.
# To re-enable legacy Stripe support, set STRIPE_SECRET_KEY and install the official 'stripe' package and reintroduce guarded imports if required.

def log(msg):
    ts=datetime.datetime.now().isoformat(timespec="seconds")
    logger.info(msg)
    with open(LOGFILE,"a",encoding="utf-8") as f: f.write(f"[{ts}] {msg}\n")

# Initialize Flask app
app = Flask(__name__,
            static_folder=str(ROOT),
            static_url_path="",
            template_folder=str(ROOT / "templates"))

# Configure Flask secret key
flask_secret = os.getenv("FLASK_SECRET_KEY", "")
if not flask_secret:
    if env_source == "development":
        flask_secret = "sincor-dev-secret-key-2025-local-only"
    else:
        flask_secret = "sincor-default-change-in-production-2025"
        log("WARNING: Using default Flask secret key")

app.secret_key = flask_secret

log(f"SINCOR Professional Application starting - Environment loaded from: {env_source}")

# PROMO CODES - DIRECT IMPLEMENTATION
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

# Import and add professional admin routes
try:
    from admin_api_routes import add_admin_api_routes
    add_admin_api_routes(app)
    logger.info("Professional admin API routes loaded successfully")
except ImportError as e:
    logger.warning(f"Professional admin routes not available: {e}")

@app.route("/free-trial/<promo_code>")
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
    
    css_styles = """
    @media (max-width: 640px) {
        .mobile-text { font-size: 1.5rem !important; }
        .mobile-container { padding: 1rem !important; margin: 0.5rem !important; }
        .mobile-button { padding: 0.75rem 1rem !important; font-size: 0.9rem !important; }
    }
    """
    
    return f'''<!DOCTYPE html>
<html><head>
<title>Free Trial Activated!</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
<style>
{css_styles}
</style>
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center p-4">
<div class="bg-green-900 mobile-container p-6 sm:p-8 rounded-lg w-full max-w-lg text-center">
<h1 class="mobile-text text-2xl sm:text-3xl font-bold mb-4 sm:mb-6">🎉 FREE TRIAL ACTIVATED!</h1>
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
<a href="/business-setup" class="block bg-green-600 hover:bg-green-500 mobile-button px-4 sm:px-6 py-2 sm:py-3 rounded-lg font-semibold text-center">
🏢 Set Up Your Business Profile
</a>
<a href="/admin/executive" class="block bg-blue-600 hover:bg-blue-500 mobile-button px-4 sm:px-6 py-2 sm:py-3 rounded-lg font-semibold text-center">
🎯 Executive Dashboard
</a>
</div>
<p class="text-xs sm:text-sm text-gray-300 mt-4 sm:mt-6 leading-relaxed">
You now have full access to SINCOR's 42-agent AI business automation system. Explore all features - no payment required!
</p>
</div></body></html>'''

@app.route("/promo-status")
def promo_status():
    """Check current promo status."""
    if not session.get('promo_active'):
        return '''<!DOCTYPE html>
<html><head><title>No Active Promo</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-gray-800 p-8 rounded-lg max-w-md text-center">
<h1 class="text-2xl font-bold mb-4">No Active Trial</h1>
<p class="mb-6">You don't have an active free trial.</p>
<div class="space-y-2 text-sm">
<p>Available promo codes:</p>
<div class="font-mono text-green-400">
PROTOTYPE2025<br>FRIENDSTEST<br>COURTTESTER
</div>
</div>
</div></body></html>'''
    
    promo_code = session.get('promo_code')
    trial_days = session.get('promo_trial_days', 0)
    activated_at = session.get('promo_activated_at')
    
    return f'''<!DOCTYPE html>
<html><head><title>Active Free Trial</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-green-900 p-8 rounded-lg max-w-md text-center">
<h1 class="text-2xl font-bold mb-4">✅ Active Free Trial</h1>
<div class="space-y-2 text-left">
<div class="flex justify-between">
<span>Code:</span>
<span class="font-mono text-green-400">{promo_code}</span>
</div>
<div class="flex justify-between">
<span>Trial Days:</span>
<span class="text-green-400">{trial_days} days</span>
</div>
</div>
<a href="/admin/executive" class="mt-6 inline-block bg-blue-600 px-4 py-2 rounded">🎯 Executive Dashboard</a>
</div></body></html>'''

# Basic routes
@app.get("/")
def home():
    try:
        return render_template("index.html")
    except Exception as e:
        logger.error(f"Error loading home page: {e}")
        return jsonify({"error": "Home page temporarily unavailable"}), 500

@app.route("/business-setup", methods=["GET", "POST"])
def business_setup():
    """Business setup form for new users."""
    if request.method == "POST":
        # Save business info to session
        business_data = {
            "company_name": request.form.get("company_name", "").strip(),
            "industry": request.form.get("industry", ""),
            "business_type": request.form.get("business_type", ""),
            "employee_count": request.form.get("employee_count", ""),
            "monthly_revenue": request.form.get("monthly_revenue", ""),
            "main_challenge": request.form.get("main_challenge", ""),
            "goals": request.form.get("goals", ""),
            "contact_email": request.form.get("contact_email", "").strip(),
            "setup_completed": True,
            "setup_date": datetime.datetime.now().isoformat()
        }
        
        # Store in session
        session['business_profile'] = business_data
        
        log(f"Business setup completed: {business_data['company_name']} ({business_data['industry']})")
        
        return f'''<!DOCTYPE html>
<html><head>
<title>Business Setup Complete!</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center p-4">
<div class="bg-green-900 p-6 sm:p-8 rounded-lg w-full max-w-lg text-center">
<h1 class="text-2xl sm:text-3xl font-bold mb-6">🎉 Business Profile Created!</h1>
<div class="bg-black p-4 sm:p-6 rounded-lg mb-6">
<h2 class="text-lg font-bold text-green-400 mb-4">Your SINCOR is now configured for:</h2>
<div class="space-y-2 text-left">
<div><span class="font-semibold">Company:</span> {business_data['company_name']}</div>
<div><span class="font-semibold">Industry:</span> {business_data['industry']}</div>
<div><span class="font-semibold">Type:</span> {business_data['business_type']}</div>
<div><span class="font-semibold">Size:</span> {business_data['employee_count']} employees</div>
</div>
</div>
<div class="space-y-4">
<a href="/admin/executive" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
🎯 Executive Dashboard
</a>
<a href="/business-setup" class="block bg-gray-600 hover:bg-gray-500 px-6 py-3 rounded-lg font-semibold">
✏️ Edit Business Profile
</a>
</div>
<p class="text-sm text-gray-300 mt-6">
Your AI agents are now personalized for {business_data['company_name']}!
</p>
</div></body></html>'''
    
    # GET request - show the form
    return '''<!DOCTYPE html>
<html><head>
<title>Business Setup - SINCOR</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen">
<div class="container mx-auto px-4 py-8 max-w-2xl">
<h1 class="text-3xl font-bold text-green-400 mb-2 text-center">🏢 Business Setup</h1>
<p class="text-gray-300 text-center mb-8">Tell SINCOR about your business so it can serve you better</p>

<form method="POST" class="space-y-6">
<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-xl font-bold text-green-400 mb-4">Company Information</h2>

<div class="space-y-4">
<div>
<label class="block text-sm font-semibold mb-2">Company Name *</label>
<input type="text" name="company_name" required
       class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white"
       placeholder="Acme Corp">
</div>

<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
<div>
<label class="block text-sm font-semibold mb-2">Industry *</label>
<select name="industry" required class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Industry</option>
<option value="Auto Detailing">Auto Detailing</option>
<option value="Automotive">Automotive Services</option>
<option value="Technology">Technology</option>
<option value="Healthcare">Healthcare</option>
<option value="Finance">Finance</option>
<option value="Retail">Retail</option>
<option value="Manufacturing">Manufacturing</option>
<option value="Consulting">Consulting</option>
<option value="Real Estate">Real Estate</option>
<option value="Construction">Construction</option>
<option value="Food & Beverage">Food & Beverage</option>
<option value="Education">Education</option>
<option value="Legal">Legal</option>
<option value="Other">Other</option>
</select>
</div>

<div>
<label class="block text-sm font-semibold mb-2">Business Type</label>
<select name="business_type" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Type</option>
<option value="B2B">B2B (Business to Business)</option>
<option value="B2C">B2C (Business to Consumer)</option>
<option value="B2B2C">B2B2C (Mixed)</option>
<option value="Nonprofit">Nonprofit</option>
<option value="Government">Government</option>
</select>
</div>
</div>
</div>
</div>

<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-xl font-bold text-green-400 mb-4">Business Size & Goals</h2>

<div class="space-y-4">
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
<div>
<label class="block text-sm font-semibold mb-2">Employee Count</label>
<select name="employee_count" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Size</option>
<option value="1">Just me (Solo)</option>
<option value="2-5">2-5 employees</option>
<option value="6-10">6-10 employees</option>
<option value="11-25">11-25 employees</option>
<option value="26-50">26-50 employees</option>
<option value="51-100">51-100 employees</option>
<option value="100+">100+ employees</option>
</select>
</div>

<div>
<label class="block text-sm font-semibold mb-2">Monthly Revenue</label>
<select name="monthly_revenue" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Range</option>
<option value="<$10K">Under $10K</option>
<option value="$10K-$25K">$10K - $25K</option>
<option value="$25K-$50K">$25K - $50K</option>
<option value="$50K-$100K">$50K - $100K</option>
<option value="$100K-$500K">$100K - $500K</option>
<option value="$500K+">$500K+</option>
<option value="prefer-not-to-say">Prefer not to say</option>
</select>
</div>
</div>

<div>
<label class="block text-sm font-semibold mb-2">Main Business Challenge</label>
<select name="main_challenge" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Challenge</option>
<option value="Lead Generation">Getting more leads</option>
<option value="Sales Conversion">Converting leads to sales</option>
<option value="Customer Retention">Keeping customers</option>
<option value="Operational Efficiency">Streamlining operations</option>
<option value="Marketing">Marketing effectively</option>
<option value="Data Management">Managing data/insights</option>
<option value="Competition">Staying competitive</option>
<option value="Scaling">Scaling the business</option>
</select>
</div>

<div>
<label class="block text-sm font-semibold mb-2">Primary Goals (Optional)</label>
<textarea name="goals" rows="3" 
          class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white"
          placeholder="What do you want to achieve with SINCOR? (e.g., increase leads by 50%, automate customer follow-ups, etc.)"></textarea>
</div>
</div>
</div>

<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-xl font-bold text-green-400 mb-4">Contact Information</h2>
<div>
<label class="block text-sm font-semibold mb-2">Email Address</label>
<input type="email" name="contact_email"
       class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white"
       placeholder="you@company.com">
</div>
</div>

<div class="text-center">
<button type="submit" class="bg-green-600 hover:bg-green-500 px-8 py-3 rounded-lg font-semibold text-lg">
🚀 Configure My SINCOR
</button>
</div>

<p class="text-xs text-gray-400 text-center mt-4">
This information helps SINCOR personalize your experience and configure the right AI agents for your business.
</p>
</form>

</div></body></html>'''

@app.get("/health")
def health(): 
    return jsonify({"ok": True, "version": "professional", "admin_system": "active"})

@app.route("/login", methods=["GET", "POST"])
def login():
    """Professional login system with promo integration."""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        remember = request.form.get("remember")
        
        # Credentials from environment variables only (no hardcoded defaults)
        # Set via: export LOGIN_EMAIL="..." export LOGIN_PASSWORD="..."
        correct_email = os.getenv("LOGIN_EMAIL")
        correct_password = os.getenv("LOGIN_PASSWORD")

        # Require credentials to be configured
        if not correct_email or not correct_password:
            log("ERROR: LOGIN_EMAIL/LOGIN_PASSWORD not configured")
            error_message = "Authentication system not configured. Contact administrator."
            return render_template('login.html', error=error_message)

        # Strict comparison - must match configured credentials exactly
        if email == correct_email and password == correct_password:
            # Valid login - set session
            session['logged_in'] = True
            session['user_email'] = email
            session['login_time'] = datetime.datetime.now().isoformat()
            session['user_type'] = 'admin'

            log(f"User logged in: {email}")

            # Redirect to dashboard
            return '''<!DOCTYPE html>
<html><head>
<title>Login Successful</title>
<meta http-equiv="refresh" content="2;url=/admin/executive">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-green-900 p-8 rounded-lg max-w-md text-center">
<h1 class="text-2xl font-bold mb-4">✅ Login Successful!</h1>
<p class="text-green-300 mb-4">Welcome to SINCOR Executive Dashboard</p>
<div class="animate-spin rounded-full h-8 w-8 border-b-2 border-white mx-auto"></div>
<p class="text-sm text-green-400 mt-4">Redirecting to your dashboard...</p>
</div></body></html>'''

        else:
            # Invalid credentials - show error
            log(f"Failed login attempt for: {email}")
            error_message = "Invalid email or password."
            return render_template('login.html', error=error_message)
    
    # GET request - show login form
    return render_template('login.html')

@app.route("/logout")
def logout():
    """Logout and clear session."""
    user_email = session.get('user_email', 'unknown')
    session.clear()
    log(f"User logged out: {user_email}")
    
    return '''<!DOCTYPE html>
<html><head>
<title>Logged Out</title>
<meta http-equiv="refresh" content="3;url=/">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-gray-800 p-8 rounded-lg max-w-md text-center">
<h1 class="text-2xl font-bold mb-4">👋 Logged Out</h1>
<p class="text-gray-300 mb-4">You have been successfully logged out of SINCOR</p>
<div class="animate-spin rounded-full h-8 w-8 border-b-2 border-white mx-auto"></div>
<p class="text-sm text-gray-400 mt-4">Redirecting to home page...</p>
</div></body></html>'''

@app.get("/admin")
def admin():
    """Redirect to executive dashboard."""
    return '''<!DOCTYPE html>
<html><head>
<title>SINCOR Admin</title>
<meta http-equiv="refresh" content="0;url=/admin/executive">
</head><body>
<p>Redirecting to Executive Dashboard...</p>
</body></html>'''

# Legacy admin routes for compatibility
@app.get("/cortex/chat")
def cortex_chat():
    """CORTEX chat interface for Railway deployment."""
    return '''<!DOCTYPE html>
<html><head><title>SINCOR CORTEX Chat</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-gray-800 p-8 rounded-lg max-w-lg text-center">
<h1 class="text-3xl font-bold mb-6">🧠 CORTEX Chat Interface</h1>
<div class="bg-yellow-900 p-4 rounded-lg mb-6">
<p class="text-yellow-300">⚠️ CORTEX is running locally during development.</p>
<p class="text-sm text-yellow-400 mt-2">For full deployment, CORTEX needs to be deployed as a separate Railway service.</p>
</div>
<div class="space-y-4">
<a href="/admin/executive" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
← Executive Dashboard
</a>
</div>
<p class="text-sm text-gray-400 mt-6">
Current Status: CORTEX backend development mode
</p>
</div></body></html>'''

# FORCE PayPal checkout (keep existing route)
@app.route("/checkout/<plan_id>", methods=["GET"])
def paypal_checkout_override(plan_id):
    """PayPal checkout page - preserved for compatibility."""
    from paypal_checkout import PLANS
    if plan_id not in PLANS:
        return "Plan not found", 404
    
    plan = PLANS[plan_id]
    
    return f"""<!DOCTYPE html>
<html>
<head>
    <title>SINCOR Checkout - {plan['name']}</title>
    <script src="https://www.paypal.com/sdk/js?client-id=Ac0_uwVreyKj-vz0l8n5f2PDNs0-LCIuqahsBdeIMsJ-kMEzxXcEiWYI1kse8Ai0qoGH-bpCtZQgaoPh&vault=true&intent=subscription"></script>
    <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50">
    <div class="max-w-2xl mx-auto py-12 px-4">
        <div class="bg-white rounded-lg shadow-lg p-8">
            <h1 class="text-2xl font-bold mb-6">Complete Your SINCOR Subscription</h1>
            
            <div class="border-b pb-6 mb-6">
                <h2 class="text-xl font-semibold">{plan['name']}</h2>
                <p class="text-3xl font-bold text-blue-600">${plan['price']:.0f}<span class="text-base text-gray-500">/month</span></p>
                <div class="mt-4">
                    <h3 class="font-semibold mb-2">What's included:</h3>
                    <ul class="space-y-1">
                        {''.join(f'<li class="flex items-center"><span class="text-green-500 mr-2">✓</span>{feature}</li>' for feature in plan['features'])}
                    </ul>
                </div>
            </div>
            
            <div class="mb-4">
                <label class="block text-sm font-medium text-gray-700 mb-2">Email</label>
                <input type="email" id="email" required class="w-full px-3 py-2 border border-gray-300 rounded-md">
            </div>
            
            <div class="mb-6">
                <label class="block text-sm font-medium text-gray-700 mb-2">Company Name</label>
                <input type="text" id="company" required class="w-full px-3 py-2 border border-gray-300 rounded-md">
            </div>
            
            <div class="mb-6">
                <label class="block text-sm font-medium text-gray-700 mb-2">Payment Method</label>
                <div id="paypal-button-container" class="mt-4">
                    <!-- PayPal button will be rendered here -->
                </div>
            </div>
            
            <p class="text-sm text-gray-500 mt-4 text-center">
                Powered by PayPal. Full access to SINCOR. Cancel anytime through PayPal.
            </p>
        </div>
    </div>
    
    <script>
        paypal.Buttons({{
            style: {{
                shape: 'rect',
                color: 'blue',
                layout: 'vertical',
                label: 'subscribe'
            }},
            
            createSubscription: function(data, actions) {{
                const email = document.getElementById('email').value;
                const company = document.getElementById('company').value;
                
                if (!email || !company) {{
                    alert('Please fill in email and company name');
                    return;
                }}
                
                return actions.subscription.create({{
                    'plan_id': 'P-5ML4271244454362WXNWU5NQ',
                    'subscriber': {{
                        'name': {{
                            'given_name': company,
                            'surname': 'Customer'
                        }},
                        'email_address': email
                    }},
                    'application_context': {{
                        'brand_name': 'SINCOR',
                        'shipping_preference': 'NO_SHIPPING',
                        'user_action': 'SUBSCRIBE_NOW'
                    }}
                }});
            }},
            
            onApprove: function(data, actions) {{
                alert('Subscription approved! ID: ' + data.subscriptionID);
                window.location.href = '/admin/executive?subscription_id=' + data.subscriptionID;
            }},
            
            onError: function(err) {{
                console.error('PayPal error:', err);
                alert('An error occurred with PayPal. Please try again.');
            }}
        }}).render('#paypal-button-container');
    </script>
</body>
</html>"""

@app.route("/pricing")
def pricing():
    """Pricing page."""
    try:
        return render_template("pricing.html")
    except Exception as e:
        log(f"Error loading pricing page: {e}")
        return jsonify({"error": "Pricing page unavailable"}), 500

@app.route("/whitepaper")
def whitepaper():
    """Whitepaper page."""
    try:
        return render_template("whitepaper.html")
    except Exception as e:
        log(f"Error loading whitepaper: {e}")
        return jsonify({"error": "Whitepaper unavailable"}), 500

@app.route("/buy")
def buy():
    """Buy page."""
    try:
        return render_template("buy.html")
    except Exception as e:
        log(f"Error loading buy page: {e}")
        return jsonify({"error": "Buy page unavailable"}), 500

@app.route("/dashboard")
def user_dashboard():
    """User dashboard."""
    if not session.get('logged_in'):
        return '<html><head><meta http-equiv="refresh" content="0;url=/login"></head><body>Redirecting...</body></html>'
    try:
        return render_template("dashboard.html")
    except Exception as e:
        log(f"Error loading dashboard: {e}")
        return jsonify({"error": "Dashboard unavailable"}), 500

if __name__=="__main__":
    port=int(os.environ.get("PORT","5001"))
    host="0.0.0.0"
    log(f"Starting SINCOR PROFESSIONAL on {host}:{port}")
    log("✅ Professional admin system active")
    log("✅ Executive dashboard available at /admin/executive")
    log("✅ Real metrics and monitoring enabled")
    log("Promo routes: /free-trial/FRIENDSTEST, /free-trial/PROTOTYPE2025, /free-trial/COURTTESTER")
    
    try:
        app.run(host=host, port=port, debug=False)
    except Exception as e:
        logger.error(f"Failed to start SINCOR Professional: {e}")
        print(f"Error starting application: {e}")