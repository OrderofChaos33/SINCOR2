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
<html><head><title>Invalid Promo Code</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-red-900 p-8 rounded-lg max-w-md text-center">
<h1 class="text-2xl font-bold mb-4">❌ Invalid Promo Code</h1>
<p>The promo code "{promo_code}" is not valid.</p>
<a href="/" class="mt-4 inline-block bg-blue-600 px-4 py-2 rounded">← Back to Home</a>
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
<html><head><title>Free Trial Activated!</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-green-900 p-8 rounded-lg max-w-lg text-center">
<h1 class="text-3xl font-bold mb-6">🎉 FREE TRIAL ACTIVATED!</h1>
<div class="bg-black p-6 rounded-lg mb-6">
<h2 class="text-xl font-bold text-green-400 mb-4">Your SINCOR Access:</h2>
<div class="space-y-2 text-left">
<div class="flex justify-between">
<span>Promo Code:</span>
<span class="font-mono text-green-400">{promo_code}</span>
</div>
<div class="flex justify-between">
<span>Trial Period:</span>
<span class="text-green-400">{promo_data['trial_days']} days FREE</span>
</div>
<div class="flex justify-between">
<span>Access Level:</span>
<span class="text-green-400">Full SINCOR System</span>
</div>
<div class="flex justify-between">
<span>42 AI Agents:</span>
<span class="text-green-400">✅ Activated</span>
</div>
</div>
</div>
<div class="space-y-4">
<a href="/admin" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
🎯 Access Admin Dashboard
</a>
</div>
<p class="text-sm text-gray-300 mt-6">
You now have full access to SINCOR's 42-agent AI business automation system.
<br>Explore all features - no payment required during trial period!
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

@app.get("/health")
def health(): 
    return jsonify({"ok": True, "promo_system": "active"})

@app.get("/admin")
def admin():
    return send_from_directory("templates", "admin_dashboard.html")

if __name__=="__main__":
    port=int(os.environ.get("PORT","5000"))
    host="0.0.0.0"
    log(f"Starting SINCOR STANDALONE on {host}:{port}")
    log("Promo routes: /free-trial/FRIENDSTEST, /free-trial/PROTOTYPE2025, /free-trial/COURTTESTER")
    app.run(host=host, port=port, debug=False)