"""
SINCOR Platform Routes Blueprint
Extracted from sincor_app.py - business setup, agent config, promo codes,
discovery dashboard, integrations, and autonomous system endpoints.

Registered into mvp_app.py so getsincor.com has ALL routes in one app.
"""

from flask import Blueprint, request, jsonify, render_template, session
import os
import datetime
import csv
import re
from pathlib import Path

sincor_bp = Blueprint('sincor', __name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

ROOT = Path(__file__).resolve().parent.parent.parent  # Up to project root
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
LOGDIR = ROOT / "logs"
LOGDIR.mkdir(exist_ok=True)
LOGFILE = LOGDIR / "run.log"
LEADSCSV = OUT / "leads.csv"

# Environment
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", "noreply@sincor.local")
EMAIL_TO = [e.strip() for e in os.getenv("EMAIL_TO", "admin@sincor.local").split(",") if e.strip()]
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "") or os.getenv("GOOGLE_PLACES_API_KEY", "")


def log(msg):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def clean_phone(p):
    s = str(p or "")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    if len(digits) == 10:
        return "+1" + digits
    if digits.startswith('1') and len(digits) == 11:
        return "+" + digits
    return "+" + digits


def save_lead(name, phone, service, notes, ip):
    try:
        LEADSCSV.parent.mkdir(parents=True, exist_ok=True)
        with open(LEADSCSV, 'a', encoding='utf-8') as f:
            f.write(f'"{name}","{phone}","{service}","{notes}","{ip}","{datetime.datetime.now().isoformat()}"\n')
        return True
    except Exception as e:
        log(f"Failed to save lead: {e}")
        return False


# ============================================================================
# PROMO SYSTEM
# ============================================================================

PROMO_CODES = {
    "PROTOTYPE2025": {
        "description": "Full free access for prototype testing",
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


@sincor_bp.route("/free-trial/<promo_code>")
def free_trial_activation(promo_code):
    promo_code = promo_code.upper()
    log(f"Promo activation attempt: {promo_code}")

    if promo_code not in PROMO_CODES:
        return f'''<!DOCTYPE html>
<html><head><title>Invalid Promo Code</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center p-4">
<div class="bg-red-900 p-6 sm:p-8 rounded-lg w-full max-w-md text-center">
<h1 class="text-xl sm:text-2xl font-bold mb-4">Invalid Code</h1>
<p class="text-sm sm:text-base mb-4">The promo code "{promo_code}" is not valid.</p>
<div class="text-xs sm:text-sm text-gray-300 mb-4">
<p>Try these codes:</p>
<div class="font-mono text-green-400 space-y-1">
<div>FRIENDSTEST</div><div>PROTOTYPE2025</div>
</div></div>
<a href="/" class="inline-block bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded text-sm sm:text-base">Back to Home</a>
</div></body></html>'''

    promo_data = PROMO_CODES[promo_code]
    session['promo_active'] = True
    session['promo_code'] = promo_code
    session['promo_trial_days'] = promo_data['trial_days']
    session['promo_bypass_payment'] = promo_data['bypass_payment']
    session['promo_activated_at'] = datetime.datetime.now().isoformat()

    log(f"Promo activated: {promo_code}")

    return f'''<!DOCTYPE html>
<html><head><title>Free Trial Activated!</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center p-4">
<div class="bg-green-900 p-6 sm:p-8 rounded-lg w-full max-w-lg text-center">
<h1 class="text-2xl sm:text-3xl font-bold mb-4 sm:mb-6">FREE TRIAL ACTIVATED!</h1>
<div class="bg-black p-4 sm:p-6 rounded-lg mb-4 sm:mb-6">
<h2 class="text-lg sm:text-xl font-bold text-green-400 mb-3 sm:mb-4">Your SINCOR Access:</h2>
<div class="space-y-2 text-left text-sm sm:text-base">
<div class="flex justify-between"><span class="font-semibold">Code:</span><span class="font-mono text-green-400">{promo_code}</span></div>
<div class="flex justify-between"><span class="font-semibold">Trial:</span><span class="text-green-400">{promo_data['trial_days']} days FREE</span></div>
<div class="flex justify-between"><span class="font-semibold">Access:</span><span class="text-green-400">Full System</span></div>
<div class="flex justify-between"><span class="font-semibold">AI Agents:</span><span class="text-green-400">42 Activated</span></div>
</div></div>
<div class="space-y-3 sm:space-y-4">
<a href="/business-setup" class="block bg-green-600 hover:bg-green-500 px-4 sm:px-6 py-2 sm:py-3 rounded-lg font-semibold text-center">Set Up Your Business Profile</a>
<a href="/admin" class="block bg-blue-600 hover:bg-blue-500 px-4 sm:px-6 py-2 sm:py-3 rounded-lg font-semibold text-center">Access Your Dashboard</a>
</div>
<p class="text-xs sm:text-sm text-gray-300 mt-4 sm:mt-6 leading-relaxed">
You now have full access to SINCOR's 42-agent AI business automation system.</p>
</div></body></html>'''


@sincor_bp.route("/promo-status")
def promo_status():
    if not session.get('promo_active'):
        return '''<!DOCTYPE html>
<html><head><title>No Active Promo</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-gray-800 p-8 rounded-lg max-w-md text-center">
<h1 class="text-2xl font-bold mb-4">No Active Trial</h1>
<p class="mb-6">You don't have an active free trial.</p>
<div class="space-y-2 text-sm"><p>Available promo codes:</p>
<div class="font-mono text-green-400">PROTOTYPE2025<br>FRIENDSTEST<br>COURTTESTER</div>
</div></div></body></html>'''

    return f'''<!DOCTYPE html>
<html><head><title>Active Free Trial</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-green-900 p-8 rounded-lg max-w-md text-center">
<h1 class="text-2xl font-bold mb-4">Active Free Trial</h1>
<div class="space-y-2 text-left">
<div class="flex justify-between"><span>Code:</span><span class="font-mono text-green-400">{session.get('promo_code')}</span></div>
<div class="flex justify-between"><span>Trial Days:</span><span class="text-green-400">{session.get('promo_trial_days', 0)} days</span></div>
</div>
<a href="/" class="mt-6 inline-block bg-blue-600 px-4 py-2 rounded">Back to Home</a>
</div></body></html>'''


# ============================================================================
# BUSINESS SETUP
# ============================================================================

@sincor_bp.route("/business-setup", methods=["GET", "POST"])
def business_setup():
    if request.method == "POST":
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
        session['business_profile'] = business_data
        log(f"Business setup completed: {business_data['company_name']}")

        return f'''<!DOCTYPE html>
<html><head><title>Business Setup Complete!</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center p-4">
<div class="bg-green-900 p-6 sm:p-8 rounded-lg w-full max-w-lg text-center">
<h1 class="text-2xl sm:text-3xl font-bold mb-6">Business Profile Created!</h1>
<div class="bg-black p-4 sm:p-6 rounded-lg mb-6">
<h2 class="text-lg font-bold text-green-400 mb-4">Your SINCOR is now configured for:</h2>
<div class="space-y-2 text-left">
<div><span class="font-semibold">Company:</span> {business_data['company_name']}</div>
<div><span class="font-semibold">Industry:</span> {business_data['industry']}</div>
<div><span class="font-semibold">Type:</span> {business_data['business_type']}</div>
<div><span class="font-semibold">Size:</span> {business_data['employee_count']} employees</div>
</div></div>
<div class="space-y-4">
<a href="/admin" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">Access Your Dashboard</a>
<a href="/business-setup" class="block bg-gray-600 hover:bg-gray-500 px-6 py-3 rounded-lg font-semibold">Edit Business Profile</a>
</div></div></body></html>'''

    # GET - show form
    return render_template("business_setup.html") if _template_exists("business_setup.html") else _business_setup_form()


def _template_exists(name):
    try:
        from flask import current_app
        current_app.jinja_env.get_template(name)
        return True
    except Exception:
        return False


def _business_setup_form():
    """Inline business setup form fallback"""
    return '''<!DOCTYPE html>
<html><head><title>Business Setup - SINCOR</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen">
<div class="container mx-auto px-4 py-8 max-w-2xl">
<h1 class="text-3xl font-bold text-green-400 mb-2 text-center">Business Setup</h1>
<p class="text-gray-300 text-center mb-8">Tell SINCOR about your business so it can serve you better</p>
<form method="POST" class="space-y-6">
<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-xl font-bold text-green-400 mb-4">Company Information</h2>
<div class="space-y-4">
<div><label class="block text-sm font-semibold mb-2">Company Name *</label>
<input type="text" name="company_name" required class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white" placeholder="Acme Corp"></div>
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
<div><label class="block text-sm font-semibold mb-2">Industry *</label>
<select name="industry" required class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Industry</option>
<option value="Auto Detailing">Auto Detailing</option>
<option value="Technology">Technology</option>
<option value="Healthcare">Healthcare</option>
<option value="Finance">Finance</option>
<option value="Retail">Retail</option>
<option value="Consulting">Consulting</option>
<option value="Real Estate">Real Estate</option>
<option value="Construction">Construction</option>
<option value="Other">Other</option></select></div>
<div><label class="block text-sm font-semibold mb-2">Business Type</label>
<select name="business_type" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Type</option>
<option value="B2B">B2B</option>
<option value="B2C">B2C</option>
<option value="B2B2C">B2B2C (Mixed)</option></select></div>
</div></div></div>
<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-xl font-bold text-green-400 mb-4">Business Size & Goals</h2>
<div class="space-y-4">
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
<div><label class="block text-sm font-semibold mb-2">Employee Count</label>
<select name="employee_count" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Size</option>
<option value="1">Just me (Solo)</option>
<option value="2-5">2-5</option>
<option value="6-10">6-10</option>
<option value="11-25">11-25</option>
<option value="26-50">26-50</option>
<option value="100+">100+</option></select></div>
<div><label class="block text-sm font-semibold mb-2">Monthly Revenue</label>
<select name="monthly_revenue" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Range</option>
<option value="<$10K">Under $10K</option>
<option value="$10K-$25K">$10K-$25K</option>
<option value="$25K-$50K">$25K-$50K</option>
<option value="$50K-$100K">$50K-$100K</option>
<option value="$100K-$500K">$100K-$500K</option>
<option value="$500K+">$500K+</option></select></div></div>
<div><label class="block text-sm font-semibold mb-2">Main Challenge</label>
<select name="main_challenge" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
<option value="">Select Challenge</option>
<option value="Lead Generation">Getting more leads</option>
<option value="Sales Conversion">Converting leads to sales</option>
<option value="Customer Retention">Keeping customers</option>
<option value="Scaling">Scaling the business</option></select></div>
<div><label class="block text-sm font-semibold mb-2">Goals (Optional)</label>
<textarea name="goals" rows="3" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white"
 placeholder="What do you want to achieve with SINCOR?"></textarea></div>
</div></div>
<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-xl font-bold text-green-400 mb-4">Contact</h2>
<div><label class="block text-sm font-semibold mb-2">Email Address</label>
<input type="email" name="contact_email" class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white" placeholder="you@company.com"></div></div>
<div class="text-center">
<button type="submit" class="bg-green-600 hover:bg-green-500 px-8 py-3 rounded-lg font-semibold text-lg">Configure My SINCOR</button>
</div></form></div></body></html>'''


# ============================================================================
# ADMIN DASHBOARD
# ============================================================================

@sincor_bp.route("/admin")
def admin_dashboard():
    business_profile = session.get('business_profile', {})
    company_name = business_profile.get('company_name', 'Your Business')
    industry = business_profile.get('industry', 'business')

    has_google_api = bool(GOOGLE_API_KEY)
    has_email = bool(SMTP_USER and SMTP_PASS)

    try:
        return render_template("professional_dashboard.html",
                               company_name=company_name,
                               industry=industry,
                               current_date=datetime.datetime.now().strftime("%B %d, %Y"),
                               metrics={"status": "Active"},
                               industry_metrics={},
                               agents={"coordination_score": "Ready", "active_count": "Available", "total_count": 42})
    except Exception:
        return f'''<!DOCTYPE html>
<html><head><title>{company_name} - SINCOR Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen">
<div class="container mx-auto px-4 py-8">
<h1 class="text-3xl font-bold text-green-400 mb-2">{company_name} Dashboard</h1>
<p class="text-gray-400 mb-8">{industry} Business Automation</p>
<div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
<div class="bg-gray-800 p-6 rounded-lg text-center">
<h2 class="text-green-300 text-sm">AI Agents</h2>
<p class="text-3xl font-bold text-green-400">42</p>
<p class="text-green-400 text-xs">Ready for activation</p></div>
<div class="bg-gray-800 p-6 rounded-lg text-center">
<h2 class="text-green-300 text-sm">System Status</h2>
<p class="text-3xl font-bold text-green-400">Active</p>
<p class="text-green-400 text-xs">All systems running</p></div>
<div class="bg-gray-800 p-6 rounded-lg text-center">
<h2 class="text-green-300 text-sm">Integrations</h2>
<p class="text-3xl font-bold text-green-400">{sum([has_google_api, has_email])}/2</p>
<p class="text-green-400 text-xs">Connected</p></div></div>
<div class="text-center space-y-4">
<a href="/business-setup" class="inline-block bg-green-600 hover:bg-green-500 px-6 py-3 rounded-lg font-semibold">Business Setup</a>
<a href="/buy" class="inline-block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">Buy Services</a>
<a href="/checkout" class="inline-block bg-purple-600 hover:bg-purple-500 px-6 py-3 rounded-lg font-semibold">Checkout</a>
</div></div></body></html>'''


# ============================================================================
# LEAD GENERATION & CAMPAIGNS
# ============================================================================

@sincor_bp.route("/generate-leads", methods=["POST"])
def generate_leads():
    try:
        from agents.intelligence.business_intel_agent import BusinessIntelAgent
        business_profile = session.get('business_profile', {})
        config = {"google_api_key": GOOGLE_API_KEY, "search_radius": 50000, "rate_limit_delay": 1}
        intel_agent = BusinessIntelAgent(config=config)
        location = business_profile.get('location', 'Local area')
        business_type = business_profile.get('industry', 'auto detailing')

        if GOOGLE_API_KEY:
            businesses = intel_agent.search_multiple_directories(location, business_type)
            saved_count = intel_agent.save_businesses(businesses) if businesses else 0
            prospects = intel_agent.get_high_value_prospects(limit=20, min_score=70)
            return jsonify({"success": True, "businesses_found": len(businesses),
                          "businesses_saved": saved_count, "high_value_prospects": len(prospects),
                          "prospects": prospects[:5], "engine": "Real Business Intelligence"})
        else:
            return jsonify({"success": False, "setup_required": True,
                          "message": "Google API key required for real lead generation"})
    except Exception as e:
        log(f"Lead generation error: {e}")
        return jsonify({"success": False, "error": str(e)})


@sincor_bp.route("/create-campaign", methods=["POST"])
def create_campaign():
    try:
        from agents.marketing.campaign_automation_agent import CampaignAutomationAgent
        business_profile = session.get('business_profile', {})
        config = {"smtp_host": SMTP_HOST, "smtp_port": SMTP_PORT, "smtp_user": SMTP_USER,
                  "smtp_pass": SMTP_PASS, "from_email": EMAIL_FROM}
        campaign_agent = CampaignAutomationAgent(config=config)
        campaign_data = {
            "campaign_name": f"Auto-Generated Campaign {datetime.datetime.now().strftime('%Y-%m-%d')}",
            "business_name": business_profile.get('business_name', 'Your Business'),
            "industry": business_profile.get('industry', 'auto detailing'),
            "campaign_type": "lead_generation"
        }
        campaign_id = campaign_agent.create_campaign(campaign_data)
        if campaign_id:
            success = campaign_agent.start_campaign(campaign_id)
            return jsonify({"success": True, "campaign_id": campaign_id, "status": "active" if success else "created"})
        return jsonify({"success": False, "message": "Email configuration required"})
    except Exception as e:
        log(f"Campaign creation error: {e}")
        return jsonify({"success": False, "error": str(e)})


@sincor_bp.route("/analyze-opportunities", methods=["POST"])
def analyze_opportunities():
    try:
        from functional_tools import analyze_business_opportunities
        business_profile = session.get('business_profile', {})
        results = analyze_business_opportunities(business_profile)
        session['business_analysis'] = results
        return jsonify({"success": True, "opportunities": results.get('opportunities', [])})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================================
# AGENT SETUP & CONFIGURATION
# ============================================================================

@sincor_bp.route("/agent-setup")
def agent_setup():
    try:
        return render_template("agent_selector.html")
    except Exception as e:
        log(f"Agent setup error: {e}")
        return jsonify({"error": "Agent setup unavailable"}), 500


@sincor_bp.route("/get-agent-recommendations", methods=["POST"])
def get_agent_recommendations_route():
    try:
        from agent_control_system import get_agent_recommendations, SINCOR_AGENTS
        business_profile = session.get('business_profile', {})
        recommendations = get_agent_recommendations(business_profile)
        for rec in recommendations:
            agent_info = SINCOR_AGENTS.get(rec.get('agent_id'), {})
            rec['agent_name'] = agent_info.get('name', 'Unknown Agent')
            rec['agent_description'] = agent_info.get('description', '')
        return jsonify({"success": True, "recommendations": recommendations})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@sincor_bp.route("/get-agent-config", methods=["POST"])
def get_agent_config():
    try:
        from agent_control_system import get_agent_configuration
        data = request.get_json()
        config = get_agent_configuration(data.get('agent_id'), data.get('level', 2))
        return jsonify({"success": True, "config": config})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@sincor_bp.route("/save-agent-config", methods=["POST"])
def save_agent_config():
    data = request.get_json()
    session['agent_configuration'] = {
        'selected_agents': data.get('selected_agents', []),
        'agent_levels': data.get('agent_levels', {}),
        'setup_completed': data.get('setup_completed', False),
        'configured_at': datetime.datetime.now().isoformat()
    }
    log(f"Agent config saved: {len(data.get('selected_agents', []))} agents")
    return jsonify({"success": True, "message": "Agent configuration saved"})


@sincor_bp.route("/create-onboarding-plan", methods=["POST"])
def create_onboarding_plan():
    try:
        from agent_control_system import create_onboarding_plan as _create_plan
        data = request.get_json()
        plan = _create_plan(data.get('selected_agents', []), session.get('business_profile', {}))
        session['onboarding_plan'] = plan
        return jsonify({"success": True, "plan": plan})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================================
# INTEGRATION CONNECTIONS
# ============================================================================

@sincor_bp.route("/connect-calendar", methods=["POST"])
def connect_calendar():
    try:
        from real_integrations import GoogleCalendarIntegration
        calendar = GoogleCalendarIntegration()
        result = calendar.test_connection()
        if result.get('success'):
            session.setdefault('integrations', {})['calendar'] = {
                'connected': True, 'connected_at': datetime.datetime.now().isoformat(), 'service': 'Google Calendar'}
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@sincor_bp.route("/connect-payments", methods=["POST"])
def connect_payments():
    try:
        from paypal_integration import SINCORPaymentProcessor
        import asyncio
        processor = SINCORPaymentProcessor()
        token = asyncio.run(processor.paypal.get_access_token())
        session.setdefault('integrations', {})['payments'] = {
            'connected': True, 'connected_at': datetime.datetime.now().isoformat(), 'service': 'PayPal'}
        return jsonify({'success': True, 'message': 'PayPal connected'})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@sincor_bp.route("/connect-email", methods=["POST"])
def connect_email():
    try:
        from real_integrations import EmailAutomation
        return jsonify(EmailAutomation().setup_email_automation())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@sincor_bp.route("/test-email", methods=["POST"])
def test_email():
    try:
        from real_integrations import EmailAutomation
        data = request.get_json()
        return jsonify(EmailAutomation().send_followup_email(data.get('email', 'test@example.com'), "Test Customer", "Full Detail"))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@sincor_bp.route("/connect-sms", methods=["POST"])
def connect_sms():
    try:
        from real_integrations import SMSAutomation
        return jsonify(SMSAutomation().setup_sms_automation())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@sincor_bp.route("/integration-status")
def integration_status():
    integrations = session.get('integrations', {})
    return jsonify({"success": True, "connected_services": list(integrations.keys()),
                   "total_connected": len(integrations), "integrations": integrations})


# ============================================================================
# DEMOS & DISCOVERY
# ============================================================================

@sincor_bp.route("/discovery-dashboard")
def discovery_dashboard():
    try:
        return render_template('working_demo.html')
    except Exception:
        return jsonify({"error": "Dashboard template not found"}), 404


@sincor_bp.route("/real-demo")
def real_demo():
    try:
        return render_template('working_demo.html')
    except Exception:
        return jsonify({"error": "Demo template not found"}), 404


@sincor_bp.route("/demo")
def simple_demo():
    try:
        return render_template('working_demo.html')
    except Exception:
        return jsonify({"error": "Demo template not found"}), 404


@sincor_bp.route("/customer-demo")
def customer_demo():
    try:
        return render_template('b2c_voiceover_demo.html')
    except Exception:
        return jsonify({"error": "Demo template not found"}), 404


# ============================================================================
# LEAD FORM (Simple landing page lead capture)
# ============================================================================

@sincor_bp.route("/lead", methods=["GET", "POST"])
def lead_form():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        phone = clean_phone(request.form.get("phone") or "")
        service = (request.form.get("service") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if not (name and phone):
            return ("Missing name/phone", 400)
        save_lead(name, phone, service, notes, ip)
        return f'''<!doctype html><body style="font-family:system-ui;margin:2rem">
<h3>Request received</h3><p>Thanks! We'll be in touch shortly.</p>
<p><a href="/">Back</a></p></body>'''

    return '''<!doctype html><meta charset="utf-8"><title>Book a Detail</title>
<body style="font-family:system-ui;margin:2rem;max-width:640px">
<h2>Book a Detail</h2>
<form method="post" action="/lead">
  <label>Name<br><input name="name" required style="width:100%"></label><br><br>
  <label>Phone<br><input name="phone" required placeholder="+1..." style="width:100%"></label><br><br>
  <label>Service<br><select name="service" style="width:100%">
    <option>Full Detail</option><option>Interior Only</option><option>Exterior + Wax</option></select></label><br><br>
  <label>Notes<br><textarea name="notes" rows="4" style="width:100%"></textarea></label><br><br>
  <button type="submit">Request Booking</button>
</form></body>'''


# ============================================================================
# LOGS & OUTPUTS
# ============================================================================

@sincor_bp.route("/logs")
def logs_route():
    if not LOGFILE.exists():
        return jsonify({"path": str(LOGFILE), "tail": []})
    try:
        with open(LOGFILE, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f.readlines()[-50:]]
        return jsonify({"path": str(LOGFILE), "tail": lines})
    except Exception as e:
        return jsonify({"path": str(LOGFILE), "tail": [], "error": str(e)})


@sincor_bp.route("/outputs/")
def outputs_route():
    files = []
    for root, dirs, fs in os.walk(OUT):
        for f in fs:
            files.append(f)
    return jsonify({"files": files})


# ============================================================================
# DIGITAL STORE (PayPal + On-chain)
# ============================================================================

try:
    from digital_store import list_products, create_purchase, execute_purchase

    @sincor_bp.route("/store/products")
    def store_products():
        return list_products()

    @sincor_bp.route("/store/create", methods=["POST"])
    def store_create():
        import asyncio
        return asyncio.run(create_purchase())

    @sincor_bp.route("/store/execute", methods=["POST"])
    def store_execute():
        import asyncio
        return asyncio.run(execute_purchase())

    @sincor_bp.route("/webhook/paypal", methods=["POST"])
    def webhook_paypal():
        try:
            import digital_store
            payload = request.get_json() or {}
            result = digital_store.handle_paypal_webhook(payload)
            return jsonify({'success': result.get('success', False)}), 200 if result.get('success') else 400
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
except Exception:
    pass  # digital_store not available - graceful degradation


# ============================================================================
# MARKETPLACE ON-CHAIN PAYMENTS
# ============================================================================

try:
    from sinc_marketplace import SincMarketplace
    _marketplace = SincMarketplace()

    @sincor_bp.route("/marketplace/create_onchain_payment", methods=["POST"])
    def marketplace_create_payment():
        data = request.get_json() or {}
        if not data.get('agent_id') or not data.get('address'):
            return jsonify({'success': False, 'error': 'Missing agent_id or address'}), 400
        result = _marketplace.generate_payment_request(data['agent_id'], data['address'], data.get('hours', 1))
        return jsonify(result), 200 if result.get('success') else 400

    @sincor_bp.route("/marketplace/check_payment", methods=["POST"])
    def marketplace_check_payment():
        data = request.get_json() or {}
        if not data.get('payment_id'):
            return jsonify({'success': False, 'error': 'Missing payment_id'}), 400
        local = _marketplace.check_payment_received(data['payment_id'])
        if local.get('success'):
            return jsonify(local), 200
        return jsonify(_marketplace.check_payments_onchain(data['payment_id'])), 200

    @sincor_bp.route("/marketplace/settle", methods=["POST"])
    def marketplace_settle():
        data = request.get_json() or {}
        if not data.get('payment_id') or not data.get('agent_address'):
            return jsonify({'success': False, 'error': 'Missing payment_id or agent_address'}), 400
        result = _marketplace.settle_payment(data['payment_id'], data['agent_address'], float(data.get('share', 0.8)))
        return jsonify(result), 200 if result.get('success') else 400
except Exception:
    pass  # sinc_marketplace not available - graceful degradation


# ============================================================================
# AUTONOMOUS SYSTEM API
# ============================================================================

@sincor_bp.route("/api/autonomous/status")
def autonomous_status():
    try:
        from src.sincor2.lead_discovery_engine import LeadDiscoveryEngine
        engine = LeadDiscoveryEngine(db_path='data/sincor.db')
        stats = engine.get_lead_stats()
        return jsonify({"status": "running", "timestamp": datetime.datetime.utcnow().isoformat(),
                       "lead_stats": stats, "next_tasks": [
                           {"task": "Lead discovery", "frequency": "Every 12 hours"},
                           {"task": "Lead scoring", "frequency": "Every 6 hours"},
                           {"task": "Autonomous outreach", "frequency": "Every 3 hours"},
                           {"task": "Follow-ups", "frequency": "Every 24 hours"}]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@sincor_bp.route("/api/autonomous/test-run", methods=["POST"])
def autonomous_test_run():
    try:
        from src.sincor2.agent_outreach_handler import AgentOutreachHandler
        from src.sincor2.lead_discovery_engine import LeadDiscoveryEngine
        engine = LeadDiscoveryEngine(db_path='data/sincor.db')
        handler = AgentOutreachHandler(engine)
        result = handler.run_outreach_cycle()
        return jsonify({"status": "complete", "timestamp": datetime.datetime.utcnow().isoformat(), "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@sincor_bp.route("/api/leads", methods=["GET", "POST"])
def manage_leads():
    try:
        from src.sincor2.lead_discovery_engine import LeadDiscoveryEngine
        engine = LeadDiscoveryEngine(db_path='data/sincor.db')

        if request.method == "POST":
            data = request.json
            lead_id = engine.add_lead(
                company_name=data.get('company_name'), website=data.get('website'),
                industry=data.get('industry'), company_size=data.get('company_size'),
                decision_maker_name=data.get('decision_maker_name'),
                decision_maker_email=data.get('decision_maker_email'),
                decision_maker_title=data.get('decision_maker_title'),
                evidence=data.get('evidence'))
            return jsonify({"status": "created", "lead_id": lead_id}), 201
        else:
            return jsonify(engine.get_lead_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# CORTEX CHAT
# ============================================================================

@sincor_bp.route("/cortex/chat")
def cortex_chat():
    return '''<!DOCTYPE html>
<html><head><title>SINCOR CORTEX Chat</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-gray-800 p-8 rounded-lg max-w-lg text-center">
<h1 class="text-3xl font-bold mb-6">CORTEX Chat Interface</h1>
<div class="bg-yellow-900 p-4 rounded-lg mb-6">
<p class="text-yellow-300">CORTEX is running locally during development.</p></div>
<a href="/" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">Back to Dashboard</a>
</div></body></html>'''


# ============================================================================
# PAYMENT STATUS
# ============================================================================

@sincor_bp.route("/payment-status")
def payment_status():
    return '''<!DOCTYPE html>
<html><head><title>Payment Status - SINCOR</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen p-8">
<div class="max-w-2xl mx-auto">
<h1 class="text-3xl font-bold text-green-400 mb-6">Payment System Status</h1>
<div class="space-y-4">
<div class="bg-gray-800 p-4 rounded-lg">
<h2 class="text-xl font-bold text-green-400">Stripe (Customer Payments)</h2>
<p class="text-green-300">Status: Active</p></div>
<div class="bg-gray-800 p-4 rounded-lg">
<h2 class="text-xl font-bold text-green-400">Crypto (Agent Payouts)</h2>
<p class="text-green-300">Status: Active (USDC/ETH/BTC)</p></div>
<div class="bg-gray-800 p-4 rounded-lg">
<h2 class="text-xl font-bold text-green-400">PayPal (Legacy)</h2>
<p class="text-green-300">Status: Available</p></div>
</div>
<a href="/" class="mt-6 inline-block bg-blue-600 px-4 py-2 rounded">Back to Home</a>
</div></body></html>'''


# ============================================================================
# TEST INTEGRATIONS
# ============================================================================

@sincor_bp.route("/test-integrations", methods=["POST"])
def test_integrations():
    try:
        from real_integrations import test_all_integrations
        return jsonify(test_all_integrations())
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
