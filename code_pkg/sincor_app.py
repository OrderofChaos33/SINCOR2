from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import os, csv, datetime, re, smtplib
from email.message import EmailMessage

ROOT=Path(__file__).resolve().parent
OUT=ROOT/"outputs"; OUT.mkdir(exist_ok=True)
LOGDIR=ROOT/"logs"; LOGDIR.mkdir(exist_ok=True)
LOGFILE=LOGDIR/"run.log"
LEADSCSV=OUT/"leads.csv"

SMTP_HOST=os.getenv("SMTP_HOST","")   # leave blank = write .eml draft instead of sending
SMTP_PORT=int(os.getenv("SMTP_PORT","587"))
SMTP_USER=os.getenv("SMTP_USER","")
SMTP_PASS=os.getenv("SMTP_PASS","")
EMAIL_FROM=os.getenv("EMAIL_FROM","courtjansma33@gmail.com")
EMAIL_TO=[e.strip() for e in os.getenv("EMAIL_TO","courtjansma33@gmail.com").split(",") if e.strip()]
NOTIFY_PHONE=os.getenv("NOTIFY_PHONE","18157188936")

app=Flask(__name__, static_folder=str(ROOT), static_url_path="")

def log(msg):
    ts=datetime.datetime.now().isoformat(timespec="seconds")
    with open(LOGFILE,"a",encoding="utf-8") as f: f.write(f"[{ts}] {msg}\n")

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
    if SMTP_HOST and SMTP_USER and SMTP_PASS and EMAIL_FROM and EMAIL_TO:
        msg=EmailMessage(); msg["From"]=EMAIL_FROM; msg["To"]=", ".join(EMAIL_TO); msg["Subject"]=subject; msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST,SMTP_PORT) as s:
            s.starttls(); s.login(SMTP_USER,SMTP_PASS); s.send_message(msg)
        log(f"email sent: {subject} -> {EMAIL_TO}"); return {"sent":True,"method":"smtp"}
    draft_dir=OUT/"email_drafts"; draft_dir.mkdir(parents=True,exist_ok=True)
    fn=draft_dir/f"lead_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.eml"
    msg=EmailMessage(); msg["From"]=EMAIL_FROM; msg["To"]=", ".join(EMAIL_TO); msg["Subject"]=subject; msg.set_content(body)
    fn.write_bytes(msg.as_bytes()); log(f"email draft written: {fn}")
    return {"sent":False,"method":"draft","file":str(fn.relative_to(ROOT))}

def clean_phone(p):
    p=re.sub(r"[^\d+]","",p or "")
    if p and not p.startswith("+"): p="+1"+re.sub(r"\D","",p)
    return p

@app.get("/")
def home():
    return ("""
<!doctype html><meta charset="utf-8"><title>SINCOR</title>
<body style="font-family:system-ui;margin:2rem">
<h2>SINCOR Lead Engine</h2>
<p><a href="/lead">Lead form</a> · <a href="/logs">Logs</a> · <a href="/outputs">Outputs</a> · <a href="/health">Health</a></p>
</body>""",200,{"Content-Type":"text/html"})

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
    name=(request.form.get("name") or "").strip()
    phone=clean_phone(request.form.get("phone") or "")
    service=(request.form.get("service") or "").strip()
    notes=(request.form.get("notes") or "").strip()
    ip=request.headers.get("X-Forwarded-For", request.remote_addr)
    if not (name and phone): return ("Missing name/phone",400)
    save_lead(name,phone,service,notes,ip)
    subject=f"NEW LEAD: {name} — {service}"
    body=f"""New lead captured.

Name: {name}
Phone: {phone}
Service: {service}
Notes: {notes}
IP: {ip}

Owner phone (stored): {NOTIFY_PHONE}
File: {LEADSCSV.relative_to(ROOT)}
"""
    info=send_email(subject,body)
    msg="Thanks! We'll email confirmation shortly."
    extra=f"<p>Email notification: <b>{info.get('method')}</b></p>"
    if info.get("file"): extra+=f"<p>Draft: <a href='/outputs/{info['file']}'>download .eml</a></p>"
    return (f"<!doctype html><body style='font-family:system-ui;margin:2rem'><h3>Request received</h3><p>{msg}</p>{extra}<p><a href='/'>Back</a></p></body>",200)

@app.get("/logs")
def logs():
    if not LOGFILE.exists(): return jsonify({"path":str(LOGFILE),"tail":[]})
    tail=LOGFILE.read_text(encoding="utf-8").splitlines()[-200:]
    return jsonify({"path":str(LOGFILE),"tail":tail})

@app.get("/outputs/")
@app.get("/outputs/<path:p>")
def outputs(p=""):
    pth=ROOT/p if p else OUT
    if p and pth.exists() and pth.is_file(): return send_from_directory(str(pth.parent), pth.name)
    tree=[]
    for base,dirs,files in os.walk(OUT):
        for f in files:
            from pathlib import Path as P; rel=str(P(base,f).relative_to(ROOT)); tree.append(rel)
    return jsonify({"files":tree})

@app.get("/health")
def health(): return jsonify({"ok":True})

if __name__=="__main__":
    port=int(os.environ.get("PORT","5000"))
    log(f"Starting on :{port}")
    app.run(host="127.0.0.1",port=port)