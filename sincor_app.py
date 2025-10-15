#!/usr/bin/env python3
"""
Standalone SINCOR with promo routes - guaranteed to work
Replace sincor_app.py with this if Railway deployment fails
"""

from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template, session
import os, csv, datetime, re, smtplib
from email.message import EmailMessage

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
STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY","")
STRIPE_PUBLISHABLE_KEY=os.getenv("STRIPE_PUBLISHABLE_KEY","")

def log(msg):
    ts=datetime.datetime.now().isoformat(timespec="seconds")
    with open(LOGFILE,"a",encoding="utf-8") as f: f.write(f"[{ts}] {msg}\n")

# Initialize Flask app
app=Flask(__name__, static_folder=str(ROOT), static_url_path="")

# Configure Flask secret key
flask_secret = os.getenv("FLASK_SECRET_KEY", "")
if not flask_secret:
    if env_source == "development":
        flask_secret = "sincor-dev-secret-key-2025-local-only"
    else:
        flask_secret = "sincor-default-change-in-production-2025"
        log("WARNING: Using default Flask secret key")

app.secret_key = flask_secret

log(f"Environment loaded from: {env_source}")

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
<a href="/admin" class="block bg-blue-600 hover:bg-blue-500 mobile-button px-4 sm:px-6 py-2 sm:py-3 rounded-lg font-semibold text-center">
🎯 Access Your Dashboard
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
<a href="/" class="mt-6 inline-block bg-blue-600 px-4 py-2 rounded">← Back to Home</a>
</div></body></html>'''

# Basic routes
@app.get("/")
def home():
    return send_from_directory("templates", "index.html")

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
<a href="/admin" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
🎯 Access Your Dashboard
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
    return jsonify({"ok": True, "promo_system": "active"})

@app.get("/admin")
def admin():
    # Get business profile from session
    business_profile = session.get('business_profile', {})
    company_name = business_profile.get('company_name', 'Your Business')
    industry = business_profile.get('industry', 'business')
    
    # Create industry-specific metrics
    # Check for real data sources instead of showing fake metrics
    has_google_api = bool(GOOGLE_API_KEY)
    has_stripe = bool(STRIPE_SECRET_KEY)
    has_email = bool(SMTP_USER and SMTP_PASS)
    
    if not any([has_google_api, has_stripe, has_email]):
        # No data sources - prompt for setup
        metrics = {
            "setup_required": "Connect your systems to see real metrics",
            "available_connections": "Google Calendar • Stripe • Email • SMS",
            "status": "Setup Required",
            "next_step": "Click integration buttons below"
        }
        industry_metrics = {
            "leads_database": "No leads yet - run lead generation",
            "campaigns_active": "No campaigns - create your first campaign", 
            "integrations_connected": f"{sum([has_google_api, has_stripe, has_email])}/3 connected",
            "business_profile": "Complete setup to personalize dashboard"
        }
    else:
        # Try to get real business metrics
        try:
            from agents.intelligence.business_intel_agent import BusinessIntelAgent
            config = {"google_api_key": GOOGLE_API_KEY} if GOOGLE_API_KEY else {}
            intel_agent = BusinessIntelAgent(config=config)
            stats = intel_agent.get_database_stats()
            
            if stats.get("total_businesses", 0) > 0:
                metrics = {
                    "leads_in_database": f"{stats.get('total_businesses', 0)} businesses",
                    "high_value_prospects": f"{stats.get('high_value_prospects', 0)} ready to contact",
                    "average_lead_score": f"{stats.get('average_lead_score', 0):.1f}/100 points",
                    "contacted_businesses": f"{stats.get('contacted_businesses', 0)} contacted"
                }
                industry_metrics = {
                    "top_cities": " • ".join(list(stats.get("top_cities", {}).keys())[:3]) or "Run lead generation",
                    "integrations_status": f"{sum([has_google_api, has_stripe, has_email])}/3 systems connected",
                    "database_status": "Active - real data loaded",
                    "ready_for_outreach": f"{stats.get('high_value_prospects', 0)} prospects ready"
                }
            else:
                metrics = {
                    "setup_complete": "Systems connected successfully",
                    "next_action": "Run lead generation to populate dashboard",
                    "database_ready": "Ready to collect real business data",
                    "integrations": f"{sum([has_google_api, has_stripe, has_email])}/3 connected"
                }
                industry_metrics = {
                    "status": "Connected but no data yet",
                    "recommendation": "Click 'Generate Leads' to get started",
                    "systems_ready": "API connections established",
                    "waiting_for": "First lead generation run"
                }
        except Exception as e:
            metrics = {
                "connection_error": "Unable to load business data",
                "check_required": "Verify API configurations",
                "systems_connected": f"{sum([has_google_api, has_stripe, has_email])}/3",
                "status": "Connected with errors"
            }
            industry_metrics = {
                "error_details": "Check logs for configuration issues",
                "suggested_action": "Verify API keys and permissions",
                "fallback_mode": "Basic functions available",
                "support_needed": "Contact support if issues persist"
            }
    
    # Remove fake agent data and use real agent status
    try:
        from master_agent_orchestrator import get_agent_status
        agent_stats = get_agent_status()
        real_agents = {
            "coordination_score": agent_stats.get("coordination_score", 0),
            "active_count": agent_stats.get("active_agents", 0), 
            "total_count": agent_stats.get("total_agents", 42)
        }
    except:
        # If can't get real agent status, show actual system state
        real_agents = {
            "coordination_score": "Ready",
            "active_count": "Available",
            "total_count": "42 agents ready for activation"
        }
    
    from flask import render_template
    import datetime
    
    # Prepare data for professional template
    template_data = {
        'company_name': company_name,
        'industry': industry,
        'current_date': datetime.datetime.now().strftime("%B %d, %Y"),
        'metrics': metrics,
        'industry_metrics': industry_metrics,
        'agents': real_agents
    }
    
    try:
        return render_template("professional_dashboard.html", **template_data)
    except:
        # Fallback if template rendering fails
        return f'''<!DOCTYPE html>
<html><head>
<title>{company_name} - SINCOR Dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen">
<div class="container mx-auto px-4 py-8">
<h1 class="text-3xl font-bold text-green-400 mb-2">🚗 {company_name} Dashboard</h1>
<p class="text-gray-400 mb-8">{industry.title()} Business Automation</p>

<div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-green-300 text-sm">New Leads</h2>
<p class="text-3xl font-bold text-green-400">{metrics["total_leads"]}</p>
<p class="text-green-400 text-xs">This month</p>
</div>

<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-green-300 text-sm">Active Clients</h2>
<p class="text-3xl font-bold text-green-400">{metrics["active_clients"]}</p>
<p class="text-green-400 text-xs">Regular customers</p>
</div>

<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-green-300 text-sm">Vehicles Detailed</h2>
<p class="text-3xl font-bold text-green-400">{industry_metrics.get("vehicles_detailed", "45")}</p>
<p class="text-green-400 text-xs">This month</p>
</div>

<div class="bg-gray-800 p-6 rounded-lg">
<h2 class="text-green-300 text-sm">Avg Service Value</h2>
<p class="text-3xl font-bold text-green-400">{industry_metrics.get("avg_service_value", "$85")}</p>
<p class="text-green-400 text-xs">Per appointment</p>
</div>
</div>

<div class="bg-gray-800 p-6 rounded-lg mb-6">
<h2 class="text-xl font-bold text-green-400 mb-4">🤖 42-Agent Network Status</h2>
<div class="text-center">
<div class="text-4xl font-bold text-green-400 mb-2">{real_agents["coordination_score"]}</div>
<p class="text-green-300">Coordination Score</p>
<p class="text-sm text-gray-400 mt-2">{real_agents["active_count"]} of {real_agents["total_count"]} agents</p>
</div>
</div>

<div class="text-center">
<div class="space-y-4">
<a href="/business-setup" class="inline-block bg-green-600 hover:bg-green-500 px-6 py-3 rounded-lg font-semibold">
🏢 Business Setup
</a>
<button onclick="openCortexChat()" class="bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
🧠 Open CORTEX Chat
</button>
<button class="bg-red-600 hover:bg-red-500 px-6 py-3 rounded-lg font-semibold">
🎬 Media Studio
</button>
</div>
</div>

<p class="text-center text-sm text-gray-400 mt-8">
Welcome to your SINCOR business automation command center!
</p>

<script>
function openCortexChat() {{
    var currentDomain = window.location.hostname;
    var cortexUrl;
    
    if (currentDomain === 'localhost' || currentDomain === '127.0.0.1') {{
        cortexUrl = 'http://localhost:5001/admin/chat';
    }} else {{
        cortexUrl = 'https://' + currentDomain + '/cortex/chat';
    }}
    
    window.open(cortexUrl, '_blank', 'width=1200,height=800');
}}
</script>

</div></body></html>'''

@app.route("/generate-leads", methods=["POST"])
def generate_leads():
    """Generate leads using real business intelligence engine."""
    try:
        from agents.intelligence.business_intel_agent import BusinessIntelAgent
        
        business_profile = session.get('business_profile', {})
        
        # Initialize real business intel agent
        config = {
            "google_api_key": GOOGLE_API_KEY,
            "search_radius": 50000,
            "rate_limit_delay": 1
        }
        
        intel_agent = BusinessIntelAgent(config=config)
        
        # Get business location and type
        location = business_profile.get('location', 'Local area')
        business_type = business_profile.get('industry', 'auto detailing')
        
        # Search multiple directories for comprehensive leads
        if GOOGLE_API_KEY:
            businesses = intel_agent.search_multiple_directories(location, business_type)
            saved_count = intel_agent.save_businesses(businesses) if businesses else 0
            
            # Get high-value prospects
            prospects = intel_agent.get_high_value_prospects(limit=20, min_score=70)
            
            return jsonify({
                "success": True,
                "leads_generated": True,
                "businesses_found": len(businesses),
                "businesses_saved": saved_count,
                "high_value_prospects": len(prospects),
                "prospects": prospects[:5],  # Show top 5
                "location": location,
                "search_type": business_type,
                "engine": "Real Business Intelligence"
            })
        else:
            # Fallback to setup mode
            return jsonify({
                "success": False,
                "setup_required": True,
                "message": "Google API key required for real lead generation",
                "setup_url": "/admin",
                "engine": "Setup Required"
            })
            
    except Exception as e:
        # Fallback to demo leads if real engine fails
        from functional_tools import generate_local_leads
        
        business_profile = session.get('business_profile', {})
    
    try:
        results = generate_local_leads(business_profile)
        
        # Store leads in session for dashboard display
        session['generated_leads'] = results['leads']
        session['last_lead_generation'] = datetime.datetime.now().isoformat()
        
        return jsonify({
            "success": True,
            "message": f"Generated {results['leads_generated']} new leads worth ${results['total_potential_value']}!",
            "leads": results['leads'],
            "next_steps": results['next_steps']
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/create-campaign", methods=["POST"])  
def create_campaign():
    """Create real marketing campaign with automation."""
    try:
        from agents.marketing.campaign_automation_agent import CampaignAutomationAgent
        
        business_profile = session.get('business_profile', {})
        
        # Initialize real campaign automation agent
        config = {
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "smtp_user": SMTP_USER,
            "smtp_pass": SMTP_PASS,
            "from_email": EMAIL_FROM
        }
        
        campaign_agent = CampaignAutomationAgent(config=config)
        
        # Create campaign data
        campaign_data = {
            "campaign_name": f"Auto-Generated Campaign {datetime.datetime.now().strftime('%Y-%m-%d')}",
            "business_name": business_profile.get('business_name', 'Your Business'),
            "industry": business_profile.get('industry', 'auto detailing'),
            "location": business_profile.get('location', 'Local area'),
            "target_audience": "local business owners",
            "campaign_type": "lead_generation"
        }
        
        # Start real campaign
        campaign_id = campaign_agent.create_campaign(campaign_data)
        
        if campaign_id:
            # Start the campaign
            success = campaign_agent.start_campaign(campaign_id)
            
            return jsonify({
                "success": True,
                "campaign_created": True,
                "campaign_id": campaign_id,
                "campaign_started": success,
                "campaign_name": campaign_data["campaign_name"],
                "target_type": campaign_data["target_audience"],
                "engine": "Real Campaign Automation",
                "status": "active" if success else "created"
            })
        else:
            return jsonify({
                "success": False,
                "setup_required": True,
                "message": "Email configuration required for campaigns",
                "setup_url": "/admin",
                "engine": "Setup Required"
            })
            
    except Exception as e:
        # Fallback to demo campaign
        from functional_tools import create_marketing_campaign
        
        business_profile = session.get('business_profile', {})
    campaign_type = request.json.get('type', 'local_seo')
    
    try:
        results = create_marketing_campaign(business_profile, campaign_type)
        
        # Store campaign in session
        session['active_campaigns'] = session.get('active_campaigns', [])
        session['active_campaigns'].append(results['campaign'])
        
        return jsonify({
            "success": True,
            "message": f"Campaign '{results['campaign']['name']}' created and ready to launch!",
            "campaign": results['campaign']
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/analyze-opportunities", methods=["POST"])
def analyze_opportunities():
    """Analyze business for growth opportunities.""" 
    from functional_tools import analyze_business_opportunities
    
    business_profile = session.get('business_profile', {})
    
    try:
        results = analyze_business_opportunities(business_profile)
        
        # Store analysis in session
        session['business_analysis'] = results
        session['analysis_date'] = datetime.datetime.now().isoformat()
        
        return jsonify({
            "success": True,
            "message": f"Found {results['opportunities_found']} growth opportunities worth {results['total_potential_monthly']}/month!",
            "opportunities": results['opportunities']
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/agent-setup")
def agent_setup():
    """Agent selection and configuration interface."""
    return send_from_directory("templates", "agent_selector.html")

@app.route("/get-agent-recommendations", methods=["POST"])
def get_agent_recommendations():
    """Get personalized agent recommendations."""
    from agent_control_system import get_agent_recommendations, SINCOR_AGENTS
    
    business_profile = session.get('business_profile', {})
    
    try:
        recommendations = get_agent_recommendations(business_profile)
        
        # Add agent details to recommendations
        detailed_recs = []
        for rec in recommendations:
            agent_info = SINCOR_AGENTS.get(rec['agent_id'], {})
            rec['agent_name'] = agent_info.get('name', 'Unknown Agent')
            rec['agent_description'] = agent_info.get('description', '')
            rec['agent_icon'] = agent_info.get('icon', '🤖')
            rec['agent_category'] = agent_info.get('category', 'Other')
            detailed_recs.append(rec)
        
        return jsonify({
            "success": True,
            "recommendations": detailed_recs
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/get-agent-config", methods=["POST"])
def get_agent_config():
    """Get configuration details for a specific agent."""
    from agent_control_system import get_agent_configuration
    
    data = request.get_json()
    agent_id = data.get('agent_id')
    level = data.get('level', 2)
    
    try:
        config = get_agent_configuration(agent_id, level)
        return jsonify({"success": True, "config": config})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/save-agent-config", methods=["POST"])
def save_agent_config():
    """Save user's agent configuration."""
    data = request.get_json()
    
    # Store in session
    session['agent_configuration'] = {
        'selected_agents': data.get('selected_agents', []),
        'agent_levels': data.get('agent_levels', {}),
        'setup_completed': data.get('setup_completed', False),
        'setup_date': data.get('setup_date'),
        'configured_at': datetime.datetime.now().isoformat()
    }
    
    log(f"Agent configuration saved: {len(data.get('selected_agents', []))} agents selected")
    
    return jsonify({
        "success": True,
        "message": "Agent configuration saved successfully"
    })

@app.route("/create-onboarding-plan", methods=["POST"])
def create_onboarding_plan():
    """Create a personalized onboarding plan."""
    from agent_control_system import create_onboarding_plan
    
    data = request.get_json()
    selected_agents = data.get('selected_agents', [])
    business_profile = session.get('business_profile', {})
    
    try:
        plan = create_onboarding_plan(selected_agents, business_profile)
        
        # Store plan in session
        session['onboarding_plan'] = plan
        
        return jsonify({
            "success": True,
            "plan": plan
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/connect-calendar", methods=["POST"])
def connect_calendar():
    """Actually connect to Google Calendar."""
    from real_integrations import GoogleCalendarIntegration
    
    try:
        calendar = GoogleCalendarIntegration()
        result = calendar.test_connection()
        
        if result.get('success'):
            session['integrations'] = session.get('integrations', {})
            session['integrations']['calendar'] = {
                'connected': True,
                'connected_at': datetime.datetime.now().isoformat(),
                'service': 'Google Calendar'
            }
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/connect-payments", methods=["POST"])
def connect_payments():
    """Connect to Stripe for payment processing."""
    from real_integrations import StripeIntegration
    
    try:
        stripe = StripeIntegration()
        result = stripe.test_stripe_connection()
        
        if result.get('success'):
            session['integrations'] = session.get('integrations', {})
            session['integrations']['payments'] = {
                'connected': True,
                'connected_at': datetime.datetime.now().isoformat(),
                'service': 'Stripe'
            }
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/connect-email", methods=["POST"])  
def connect_email():
    """Set up email automation."""
    from real_integrations import EmailAutomation
    
    try:
        email = EmailAutomation()
        result = email.setup_email_automation()
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/test-email", methods=["POST"])
def test_email():
    """Send a test email."""
    from real_integrations import EmailAutomation
    
    data = request.get_json()
    test_email = data.get('email', 'test@example.com')
    
    try:
        email_automation = EmailAutomation()
        result = email_automation.send_followup_email(
            test_email, 
            "Test Customer", 
            "Full Detail"
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/connect-sms", methods=["POST"])
def connect_sms():
    """Set up SMS automation with Twilio."""
    from real_integrations import SMSAutomation
    
    try:
        sms = SMSAutomation()
        result = sms.setup_sms_automation()
        
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/discovery-dashboard")
def discovery_dashboard():
    """Live AI Customer Acquisition Demo - B2C Service Industry."""
    return render_template('working_demo.html')

@app.route("/real-demo")
def real_demo():
    """Real customer demo - shows PEOPLE who need services."""
    return render_template('working_demo.html')

@app.route("/demo")
def simple_demo():
    """Simple working demo."""
    return render_template('working_demo.html')

@app.route("/payment-status")
def payment_status():
    """Check payment system status."""
    return f"""
    <h1>SINCOR Payment Status</h1>
    <h2>PayPal Integration</h2>
    <p>Client ID: Ac0_uwVreyKj-vz0l8n5f2PDNs0-LCIuqahsBdeIMsJ-kMEzxXcEiWYI1kse8Ai0qoGH-bpCtZQgaoPh</p>
    <p>Mode: Sandbox</p>
    <h2>Routes</h2>
    {''.join(f'<p>{rule.rule} -> {rule.endpoint}</p>' for rule in app.url_map.iter_rules() if 'checkout' in rule.rule)}
    <h2>Test Links</h2>
    <a href="/checkout/starter">Starter Plan</a><br>
    <a href="/checkout/professional">Professional Plan</a>
    """

@app.route("/customer-demo")
def customer_demo():
    """B2C voiceover demo - shows customer discovery + AI voiceover generation."""
    return render_template('b2c_voiceover_demo.html')

@app.route("/test-integrations", methods=["POST"])
def test_integrations():
    """Test all available integrations."""
    from real_integrations import test_all_integrations
    
    try:
        results = test_all_integrations()
        return jsonify(results)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/integration-status")
def integration_status():
    """Get current integration status."""
    integrations = session.get('integrations', {})
    
    return jsonify({
        "success": True,
        "connected_services": list(integrations.keys()),
        "total_connected": len(integrations),
        "available_integrations": ["calendar", "payments", "email", "sms", "google_my_business"],
        "integrations": integrations
    })

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
<a href="/" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
← Back to Dashboard
</a>
</div>
<p class="text-sm text-gray-400 mt-6">
Current Status: CORTEX backend development mode
</p>
</div></body></html>'''

# FORCE PayPal checkout (disable any existing routes)
@app.route("/checkout/<plan_id>", methods=["GET"])
def paypal_checkout_override(plan_id):
    """PayPal checkout page - OVERRIDES any other checkout routes."""
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
                
                // For demo purposes, create a simple subscription
                return actions.subscription.create({{
                    'plan_id': 'P-5ML4271244454362WXNWU5NQ', // PayPal plan ID
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
                // Handle successful subscription approval
                alert('Subscription approved! ID: ' + data.subscriptionID);
                window.location.href = '/success?subscription_id=' + data.subscriptionID;
            }},
            
            onError: function(err) {{
                console.error('PayPal error:', err);
                alert('An error occurred with PayPal. Please try again.');
            }}
        }}).render('#paypal-button-container');
    </script>
</body>
</html>"""

if __name__=="__main__":
    port=int(os.environ.get("PORT","5001"))  # Use port 5001 to avoid conflicts
    host="0.0.0.0"
    log(f"Starting SINCOR STANDALONE on {host}:{port}")
    log("Promo routes: /free-trial/FRIENDSTEST, /free-trial/PROTOTYPE2025, /free-trial/COURTTESTER")
    app.run(host=host, port=port, debug=False)
