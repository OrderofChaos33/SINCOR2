import re, dns.resolver, smtplib, socket

EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I)

def email_syntax(email:str|None)->bool:
    return bool(email and EMAIL_RE.match(email))

def mx_exists(email:str)->bool:
    domain = email.split("@")[1]
    try:
        answers = dns.resolver.resolve(domain, 'MX')
        return len(answers) > 0
    except Exception:
        return False

def smtp_ping(email:str)->bool:
    domain = email.split("@")[1]
    try:
        mx_records = dns.resolver.resolve(domain,'MX')
        host = str(sorted(mx_records, key=lambda r: r.preference)[0].exchange).rstrip('.')
        s = smtplib.SMTP(timeout=7)
        s.connect(host)
        s.helo("example.com")
        s.mail("test@example.com")
        code, _ = s.rcpt(email)
        s.quit()
        return code in (250, 251)
    except Exception:
        return False

def phone_basic(phone:str|None)->bool:
    return bool(phone and re.fullmatch(r"\+?\d{10,15}", phone))

def is_proxy_ip(ip:str|None)->bool:
    # placeholder: later add known proxy ranges or 3rd-party-free heuristics
    return False