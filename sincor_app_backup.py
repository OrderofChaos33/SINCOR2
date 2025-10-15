from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template
import os, csv, datetime, re, smtplib
from email.message import EmailMessage

# Load environment variables from .env files
def load_environment():
    """Load environment variables from .env files with fallback priority."""
    try:
        from dotenv import load_dotenv
        
        # Load in priority order: .env (dev) -> production.env -> system env
        config_dir = Path(__file__).parent / "config"
        
        # First try local .env for development
        local_env = config_dir / ".env"
        if local_env.exists():
            load_dotenv(local_env)
            return "development"
        
        # Fall back to production.env
        prod_env = config_dir / "production.env"
        if prod_env.exists():
            load_dotenv(prod_env)
            return "production"
            
        return "system"
        
    except ImportError:
        return "system"  # python-dotenv not installed, use system env vars

env_source = load_environment()

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"outputs"; OUT.mkdir(exist_ok=True)
LOGDIR=ROOT/"logs"; LOGDIR.mkdir(exist_ok=True)
LOGFILE=LOGDIR/"run.log"
LEADSCSV=OUT/"leads.csv"

# Environment configuration with safe defaults
SMTP_HOST=os.getenv("SMTP_HOST","") or os.getenv("smtp_host","")
SMTP_PORT=int(os.getenv("SMTP_PORT","587") or os.getenv("smtp_port","587"))
SMTP_USER=os.getenv("SMTP_USER","") or os.getenv("smtp_user","")
SMTP_PASS=os.getenv("SMTP_PASS","") or os.getenv("smtp_pass","")
EMAIL_FROM=os.getenv("EMAIL_FROM","noreply@sincor.local") or os.getenv("email_from","noreply@sincor.local")
EMAIL_TO=[e.strip() for e in os.getenv("EMAIL_TO","admin@sincor.local").split(",") if e.strip()]
NOTIFY_PHONE=os.getenv("NOTIFY_PHONE","+15551234567")

# API Keys with safe handling
GOOGLE_API_KEY=os.getenv("GOOGLE_API_KEY","") or os.getenv("GOOGLE_PLACES_API_KEY","")
STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY","")
YELP_API_KEY=os.getenv("YELP_API_KEY","")

def log(msg):
    ts=datetime.datetime.now().isoformat(timespec="seconds")
    with open(LOGFILE,"a",encoding="utf-8") as f: f.write(f"[{ts}] {msg}\n")

# Initialize Flask app
app=Flask(__name__, static_folder=str(ROOT), static_url_path="")

# Configure Flask secret key with environment-aware defaults
flask_secret = os.getenv("FLASK_SECRET_KEY", "")
if not flask_secret:
    if env_source == "development":
        flask_secret = "sincor-dev-secret-key-2025-local-only"
    else:
        flask_secret = "sincor-default-change-in-production-2025"
        log("WARNING: Using default Flask secret key - set FLASK_SECRET_KEY environment variable")

app.secret_key = flask_secret

# Log environment configuration
log(f"Environment loaded from: {env_source}")
if SMTP_HOST:
    log("SMTP configured - emails will be sent")
else:
    log("SMTP not configured - emails will be saved as .eml files")
if GOOGLE_API_KEY:
    log("Google API key configured - business intelligence features enabled")
else:
    log("Google API key not configured - business intelligence features disabled")

# Start SINCOR agents
try:
    import threading
    from agents.daetime.scheduler_harness import main as start_agents
    agent_thread = threading.Thread(target=start_agents, daemon=True)
    agent_thread.start()
    log("SINCOR agents started successfully")
except Exception as e:
    log(f"Warning: Could not start agents: {e}")

# Import checkout routes
try:
    from checkout import add_checkout_routes
    add_checkout_routes(app)
    log("Checkout routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import checkout module: {e}")
except Exception as e:
    log(f"Error adding checkout routes: {e}")

# Import dashboard routes
try:
    from dashboard_routes import add_dashboard_routes
    add_dashboard_routes(app)
    log("Dashboard routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import dashboard module: {e}")
except Exception as e:
    log(f"Error adding dashboard routes: {e}")

# Import media pack routes
try:
    from media_pack_routes import add_media_pack_routes
    add_media_pack_routes(app)
    log("Media pack routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import media pack module: {e}")
except Exception as e:
    log(f"Error adding media pack routes: {e}")

# Import auto detailing authority routes
try:
    from auto_detailing_routes import add_auto_detailing_routes
    add_auto_detailing_routes(app)
    log("Auto detailing authority routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import auto detailing module: {e}")
except Exception as e:
    log(f"Error adding auto detailing routes: {e}")

# Import authority expansion system
try:
    from authority_expansion import add_authority_expansion_routes
    add_authority_expansion_routes(app)
    log("Authority expansion routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import authority expansion module: {e}")
except Exception as e:
    log(f"Error adding authority expansion routes: {e}")

# Import business discovery engine
try:
    from business_discovery import add_discovery_routes
    add_discovery_routes(app)
    log("Business discovery engine routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import business discovery module: {e}")
except Exception as e:
    log(f"Error adding business discovery routes: {e}")

# Import email automation system
try:
    from email_automation import add_automation_routes
    add_automation_routes(app)
    log("Email automation system routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import email automation module: {e}")
except Exception as e:
    log(f"Error adding email automation routes: {e}")

# Import analytics dashboard
try:
    from analytics_dashboard import add_analytics_routes
    add_analytics_routes(app)
    log("Analytics dashboard routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import analytics dashboard module: {e}")
except Exception as e:
    log(f"Error adding analytics dashboard routes: {e}")

# Import enterprise domination system
try:
    from enterprise_domination import add_enterprise_routes
    add_enterprise_routes(app)
    log("Enterprise domination system routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import enterprise domination module: {e}")
except Exception as e:
    log(f"Error adding enterprise domination routes: {e}")

# Import security and compliance system
try:
    from security_compliance import add_compliance_routes
    add_compliance_routes(app)
    log("Security and compliance system routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import security compliance module: {e}")
except Exception as e:
    log(f"Error adding security compliance routes: {e}")

# Import value maximization system
try:
    from value_maximization import add_value_routes
    add_value_routes(app)
    log("Value maximization system routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import value maximization module: {e}")
except Exception as e:
    log(f"Error adding value maximization routes: {e}")

# Import conversion optimization system  
try:
    from conversion_optimization import add_conversion_routes
    add_conversion_routes(app)
    log("Conversion optimization system routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import conversion optimization module: {e}")
except Exception as e:
    log(f"Error adding conversion optimization routes: {e}")

# Import SEO domination system
try:
    from seo_domination import add_seo_routes
    add_seo_routes(app)
    log("SEO domination system routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import SEO domination module: {e}")
except Exception as e:
    log(f"Error adding SEO domination routes: {e}")

# Import admin control panel
try:
    from admin_control import add_admin_routes
    add_admin_routes(app)
    log("Admin control panel routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import admin control module: {e}")
except Exception as e:
    log(f"Error adding admin control routes: {e}")

# Import Railway-compatible promo bypass system
try:
    from railway_promo_bypass import add_promo_bypass_routes
    add_promo_bypass_routes(app)
    log("Railway promo bypass routes added successfully")
except ImportError as e:
    log(f"Warning: Could not import railway promo bypass: {e}")
except Exception as e:
    log(f"Error adding railway promo bypass routes: {e}")

def ensure_leads_csv():
    if not LEADSCSV.exists():
        with open(LEADSCSV,"w",newline="",encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp","name","phone","service","notes","ip"])

def save_lead(name,phone,service,notes,ip):
    ensure_leads_csv()
    with open(LEADSCSV,"a",newline="",encoding="utf-8") as f:
        csv.writer(f).writerow([datetime.datetime.now().isoformat(),name,phone,service,notes,ip])
    log(f"lead saved: {name} {phone} {service}")

def send_email(subject,body):
    if not EMAIL_TO:
        log("ERROR: EMAIL_TO not configured")
        return {"sent":False,"method":"error","error":"EMAIL_TO not configured"}
    
    if SMTP_HOST and SMTP_USER and SMTP_PASS and EMAIL_FROM and EMAIL_TO:
        try:
            msg=EmailMessage(); msg["From"]=EMAIL_FROM; msg["To"]=", ".join(EMAIL_TO); msg["Subject"]=subject; msg.set_content(body)
            with smtplib.SMTP(SMTP_HOST,SMTP_PORT) as s:
                s.starttls(); s.login(SMTP_USER,SMTP_PASS); s.send_message(msg)
            log(f"email sent: {subject} -> {EMAIL_TO}"); return {"sent":True,"method":"smtp"}
        except Exception as e:
            log(f"SMTP error: {e}")
            # Fall back to draft on SMTP error
    
    draft_dir=OUT/"email_drafts"; draft_dir.mkdir(parents=True,exist_ok=True)
    fn=draft_dir/f"lead_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.eml"
    msg=EmailMessage(); msg["From"]=EMAIL_FROM or "noreply@sincor.local"; msg["To"]=", ".join(EMAIL_TO); msg["Subject"]=subject; msg.set_content(body)
    fn.write_bytes(msg.as_bytes()); log(f"email draft written: {fn}")
    return {"sent":False,"method":"draft","file":str(fn.relative_to(ROOT))}

def clean_phone(p):
    p=re.sub(r"[^\d+]","",p or "")
    if p and not p.startswith("+"): p="+1"+re.sub(r"\D","",p)
    return p

@app.get("/")
def home():
    try:
        # Serve the professional marketing website
        template_path = ROOT / "templates" / "index.html"
        if template_path.exists():
            return template_path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}
        else:
            # Fallback to simple version
            return ("""
<!doctype html><meta charset="utf-8"><title>SINCOR</title>
<body style="font-family:system-ui;margin:2rem">
<h2>SINCOR Lead Engine</h2>
<p><a href="/lead">Lead form</a> · <a href="/logs">Logs</a> · <a href="/outputs">Outputs</a> · <a href="/health">Health</a></p>
</body>""", 200, {"Content-Type": "text/html"})
    except Exception as e:
        log(f"Error serving home page: {e}")
        return ("Error loading page", 500)

@app.get("/lead")
def lead_form():
    return ("""
<!doctype html><meta charset="utf-8"><title>Book a Detail</title>
<body style="font-family:system-ui;margin:2rem;max-width:640px">
<h2>Book a Detail</h2>
<form method="post" action="/lead">
  <label>Name<br><input name="name" required style="width:100%"></label><br><br>
  <label>Phone<br><input name="phone" required placeholder="+1..." style="width:100%"></label><br><br>
  <label>Service<br>
    <select name="service" style="width:100%">
      <option>Full Detail</option><option>Interior Only</option><option>Exterior + Wax</option><option>Engine Bay</option>
    </select></label><br><br>
  <label>Notes<br><textarea name="notes" rows="4" style="width:100%"></textarea></label><br><br>
  <button type="submit">Request Booking</button>
</form>
</body>""",200,{"Content-Type":"text/html"})

@app.post("/lead")
def lead_submit():
    try:
        name=(request.form.get("name") or "").strip()
        phone=clean_phone(request.form.get("phone") or "")
        service=(request.form.get("service") or "").strip()
        notes=(request.form.get("notes") or "").strip()
        ip=request.headers.get("X-Forwarded-For", request.remote_addr)
        
        if not (name and phone): 
            log(f"Invalid lead submission: name='{name}' phone='{phone}' ip={ip}")
            return ("Missing name/phone",400)
        
        save_lead(name,phone,service,notes,ip)
        subject=f"NEW LEAD: {name} — {service}"
        body=f"""New lead captured.

Name: {name}
Phone: {phone}
Service: {service}
Notes: {notes}
IP: {ip}

Owner phone (stored): {NOTIFY_PHONE or 'Not configured'}
File: {LEADSCSV.relative_to(ROOT)}
"""
        info=send_email(subject,body)
        msg="Thanks! We'll email confirmation shortly."
        
        if info.get("method") == "error":
            msg="Thanks! Your request was saved. We'll contact you soon."
            extra=f"<p>Notification: <b>Saved locally</b></p>"
        else:
            extra=f"<p>Email notification: <b>{info.get('method')}</b></p>"
            if info.get("file"): extra+=f"<p>Draft: <a href='/outputs/{info['file']}'>download .eml</a></p>"
        
        return (f"<!doctype html><body style='font-family:system-ui;margin:2rem'><h3>Request received</h3><p>{msg}</p>{extra}<p><a href='/'>Back</a></p></body>",200)
    
    except Exception as e:
        log(f"ERROR in lead_submit: {e}")
        return ("Internal server error. Please try again.",500)

@app.get("/logs")
def logs():
    try:
        if not LOGFILE.exists(): return jsonify({"path":str(LOGFILE),"tail":[]})
        tail=LOGFILE.read_text(encoding="utf-8").splitlines()[-200:]
        return jsonify({"path":str(LOGFILE),"tail":tail})
    except Exception as e:
        return jsonify({"error":f"Could not read logs: {e}"}),500

@app.get("/outputs/")
@app.get("/outputs/<path:p>")
def outputs(p=""):
    try:
        pth=ROOT/p if p else OUT
        if p and pth.exists() and pth.is_file(): 
            return send_from_directory(str(pth.parent), pth.name)
        tree=[]
        for base,dirs,files in os.walk(OUT):
            for f in files:
                from pathlib import Path as P; 
                rel=str(P(base,f).relative_to(ROOT)); 
                tree.append(rel)
        return jsonify({"files":tree})
    except Exception as e:
        log(f"ERROR in outputs: {e}")
        return jsonify({"error":f"Could not access outputs: {e}"}),500

# Admin dashboard is now handled by admin_control.py module
# @app.get("/admin")
# def admin_dashboard():
#     """SINCOR Admin Council - Enhanced business command center with real data."""
#     try:
#         # Import the data service to get real metrics
#         from admin_data_service import admin_data_service
#         
#         # Get real system data
#         system_metrics = admin_data_service.get_system_metrics()
#         agent_network = admin_data_service.get_agent_network_status()
#         recent_activity = admin_data_service.get_recent_activity()
#         database_info = admin_data_service.get_database_info()
#         
#         return render_template('admin_dashboard.html', 
#                              metrics=system_metrics,
#                              agents=agent_network,
#                              activity=recent_activity,
#                              databases=database_info)
#     except Exception as e:
#         log(f"Error loading admin dashboard: {e}")
#         # Fallback to basic dashboard
#         return render_template('admin_dashboard.html')

@app.get("/admin/panel")
def admin_panel():
    """Legacy admin panel."""
    return render_template('admin_panel.html')

@app.get("/admin/cortex")
def admin_cortex_chat():
    """Direct link to CORTEX chat interface."""
    return f"""
    <script>window.location.href = 'http://localhost:5001/admin/chat';</script>
    <p>Redirecting to CORTEX chat interface...</p>
    <p>If not redirected, <a href="http://localhost:5001/admin/chat">click here</a></p>
    """

# Checkout routes are now handled by checkout.py module
# @app.get("/checkout")
# @app.get("/checkout/<plan>")
# def checkout_page(plan=None):
#     """Checkout page for CORTEX plans."""
#     return render_template('checkout_demo.html')

@app.route("/api/validate-promo", methods=["POST"])
def validate_promo_code():
    """Validate a promo code."""
    try:
        from promo_codes import promo_system
        
        data = request.get_json()
        code = data.get("code", "").strip() if data else ""
        
        if not code:
            return jsonify({"valid": False, "error": "Promo code required"})
        
        result = promo_system.validate_code(code)
        return jsonify(result)
    except ImportError:
        return jsonify({"valid": False, "error": "Promo code system not available"})
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)})

@app.route("/api/apply-promo", methods=["POST"])
def apply_promo_code():
    """Apply a promo code and create account."""
    try:
        from promo_codes import promo_system
        
        data = request.get_json()
        code = data.get("code", "").strip() if data else ""
        email = data.get("email", "").strip() if data else ""
        company = data.get("company", "").strip() if data else ""
        plan_id = data.get("plan_id", "") if data else ""
        
        if not all([code, email, company]):
            return jsonify({"success": False, "error": "Missing required fields"})
        
        # Use the promo code
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
        result = promo_system.use_code(code, email, company, company, ip_address)
        
        if result["valid"]:
            # Create free trial account
            return jsonify({
                "success": True, 
                "message": "Free trial account activated!", 
                "trial_days": result["data"]["free_trial_days"],
                "bypass_payment": result["data"]["bypass_payment"]
            })
        else:
            return jsonify({"success": False, "error": result["error"]})
    except ImportError:
        return jsonify({"success": False, "error": "Promo code system not available"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

# Direct Promo Routes (avoiding import issues)
PROMO_CODES = {
    "PROTOTYPE2025": {"description": "Prototype testing", "trial_days": 90, "bypass_payment": True, "max_uses": 50},
    "COURTTESTER": {"description": "Court's personal testing", "trial_days": 365, "bypass_payment": True, "max_uses": 10},
    "FRIENDSTEST": {"description": "Friends testing", "trial_days": 90, "bypass_payment": True, "max_uses": 100}
}

@app.route("/free-trial/<promo_code>")
def free_trial_activation(promo_code):
    promo_code = promo_code.upper()
    if promo_code not in PROMO_CODES:
        return f'<h1>Invalid Code: {promo_code}</h1><a href="/">Back to Home</a>'
    
    # Set session
    from flask import session
    promo_data = PROMO_CODES[promo_code]
    session['promo_active'] = True
    session['promo_code'] = promo_code
    session['promo_trial_days'] = promo_data['trial_days']
    session['promo_activated_at'] = datetime.datetime.now().isoformat()
    
    return f'''<!DOCTYPE html>
<html><head><title>Welcome to SINCOR!</title>
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@2.2.19/dist/tailwind.min.css" rel="stylesheet">
</head><body class="bg-gray-900 text-white min-h-screen flex items-center justify-center">
<div class="bg-green-900 p-8 rounded-lg max-w-lg text-center">
<h1 class="text-3xl font-bold mb-6">🎉 FREE TRIAL ACTIVATED!</h1>
<div class="bg-black p-6 rounded-lg mb-6">
<h2 class="text-xl font-bold text-green-400 mb-4">Your SINCOR Access:</h2>
<p class="text-green-400">Code: {promo_code} | {promo_data['trial_days']} days FREE</p>
<p class="text-green-400">42 AI Agents: ✅ Activated</p>
</div>
<div class="space-y-4">
<a href="/admin" class="block bg-blue-600 hover:bg-blue-500 px-6 py-3 rounded-lg font-semibold">
🎯 Access Your Business Dashboard</a>
</div>
<p class="text-sm text-gray-300 mt-6">Full access to SINCOR's 42-agent AI automation system!</p>
</div></body></html>'''

@app.get("/health")
def health(): return jsonify({"ok":True})

@app.get("/test-promo")
def test_promo(): 
    return jsonify({
        "message": "Promo system test route working",
        "routes_added": "railway_promo_bypass, onboarding_system, media_routes"
    })

if __name__=="__main__":
    port=int(os.environ.get("PORT","5000"))
    host="0.0.0.0"  # Railway requires binding to 0.0.0.0
    log(f"Starting SINCOR on {host}:{port}")
    app.run(host=host, port=port, debug=False)