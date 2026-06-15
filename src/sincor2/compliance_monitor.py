#!/usr/bin/env python3
"""
SINCOR Compliance Monitor Agent
================================
Runs every 30 minutes to audit marketing claims, email content, API responses,
environment variables, database activity, and payment configurations.

On violation: Slack alert + email alert + detailed log + auto-quarantine.

Usage:
    # Run once:
    python -m sincor2.compliance_monitor

    # Schedule via APScheduler (called from app startup):
    from sincor2.compliance_monitor import start_compliance_scheduler
    start_compliance_scheduler()
"""

import os
import re
import json
import time
import logging
import hashlib
import smtplib
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ---------------------------------------------------------------------------
# Config — all values come from environment variables; never hardcoded
# ---------------------------------------------------------------------------

SLACK_WEBHOOK_URL = os.getenv("SLACK_COMPLIANCE_WEBHOOK_URL", "")
ALERT_EMAIL_TO = os.getenv(
    "COMPLIANCE_ALERT_EMAIL",
    os.getenv("LAUNCH_REVIEW_ALERT_EMAIL", "court@getsincor.com"),
)
ALERT_EMAIL_FROM = os.getenv(
    "ALERT_FROM_EMAIL",
    os.getenv("SUPPORT_EMAIL", "support@getsincor.com"),
)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.sendgrid.net")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "apikey")
SMTP_PASS = os.getenv("SMTP_PASS", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://getsincor.com")


def _resolve_log_dir() -> Path:
    override = os.getenv("COMPLIANCE_LOG_DIR", "").strip()
    if override:
        p = Path(override)
    else:
        try:
            from sincor2.data_paths import compliance_log_dir

            p = compliance_log_dir()
        except Exception:
            p = Path("logs/compliance")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_quarantine_dir() -> Path:
    override = os.getenv("QUARANTINE_DIR", "").strip()
    if override:
        p = Path(override)
    else:
        try:
            from sincor2.data_paths import quarantine_dir

            p = quarantine_dir()
        except Exception:
            p = Path("/tmp/sincor_quarantine")
    p.mkdir(parents=True, exist_ok=True)
    return p


QUARANTINE_DIR = _resolve_quarantine_dir()
LOG_DIR = _resolve_log_dir()
DATABASE_LOG_PATH = LOG_DIR.parent / "db_audit.log"
JWT_SECRET_ENV_KEY = "JWT_SECRET_KEY"
STRIPE_ENV_KEYS = ["STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"]
PAYPAL_ENV_KEYS = ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"]

# Patterns that indicate fake/test credentials
FAKE_CREDENTIAL_PATTERNS = [
    r"sk_test_",          # Stripe test secret
    r"pk_test_",          # Stripe test publishable
    r"PLACEHOLDER",
    r"YOUR_SECRET",
    r"changeme",
    r"supersecret",
    r"your-jwt-secret",
    r"your_jwt_secret",
    r"hardcoded",
    r"example\.com/webhook",
]

# Marketing claim patterns that are unsubstantiated
UNSUBSTANTIATED_CLAIM_PATTERNS = [
    (r"\bguaranteed?\s+(roi|return|income|revenue|profit|earnings?)\b", "Guaranteed ROI/income claim"),
    (r"\bproven\s+to\s+(work|make|earn|generate)\b", "Unsubstantiated 'proven to' claim"),
    (r"\b(earn|make|generate)\s+\$[\d,]+\b.*\b(day|week|month|year)\b", "Specific earnings claim"),
    (r"\b100%\s+(success|guaranteed|effective|proven)\b", "100% guarantee claim"),
    (r"\bno\s+risk\b", "No-risk claim (use '30-day money back' instead)"),
    (r"\bget\s+rich\b", "Get-rich claim"),
    (r"\bpassive\s+income\s+(guaranteed|proven|automatic)\b", "Guaranteed passive income claim"),
    (r"\b10x\b", "Unsubstantiated multiplier claim (10x)"),
    (r"\b\d+x\s+(faster|output|capacity|better|more)\b", "Unsubstantiated multiplier claim"),
    (r"\b(under|in)\s+5\s+minutes?\b", "Unverified setup-time claim"),
    (r"\bsetup\s+(in|takes?)\s+(under\s+)?5\s+min", "Unverified setup-time claim"),
    (r"\bdeploy\s+in\s+5\s+minutes?\b", "Unverified setup-time claim"),
    (r"\blive\s+in\s+under\s+5\s+minutes?\b", "Unverified setup-time claim"),
]

# PII patterns
PII_PATTERNS = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "SSN"),
    (r"\b4[0-9]{12}(?:[0-9]{3})?\b", "Visa card number"),
    (r"\b5[1-5][0-9]{14}\b", "MasterCard number"),
    (r"\b[A-Z]{2}\d{6}[A-Z]\b", "Passport number"),
    (r"password\s*[=:]\s*['\"]?\S+", "Plaintext password in response"),
]

# Database patterns indicating suspicious activity
SUSPICIOUS_DB_PATTERNS = [
    (r"SELECT \*.*LIMIT [5-9]\d{3,}", "Mass data export (>5000 rows)"),
    (r"SELECT \*.*LIMIT [1-9]\d{4,}", "Mass data export (>10000 rows)"),
    (r"DROP\s+TABLE", "Destructive DDL: DROP TABLE"),
    (r"TRUNCATE\s+TABLE", "Destructive DDL: TRUNCATE TABLE"),
    (r"DELETE\s+FROM.*WHERE\s+1\s*=\s*1", "Full table delete attempt"),
    (r"UNION\s+SELECT", "Potential SQL injection pattern"),
    (r"--\s*$", "SQL comment injection pattern"),
    (r"xp_cmdshell", "SQL Server command execution attempt"),
]

LOG_DIR.mkdir(parents=True, exist_ok=True)
QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [COMPLIANCE] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "monitor.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("sincor.compliance")


# ---------------------------------------------------------------------------
# Violation record
# ---------------------------------------------------------------------------

class ComplianceViolation:
    def __init__(
        self,
        check_name: str,
        severity: str,            # "CRITICAL", "HIGH", "MEDIUM", "LOW"
        what: str,
        where: str,
        who: str,
        details: str,
        remediation: str,
        quarantine_target: Optional[str] = None,
    ):
        self.check_name = check_name
        self.severity = severity
        self.what = what
        self.where = where
        self.who = who
        self.details = details
        self.remediation = remediation
        self.quarantine_target = quarantine_target
        self.timestamp = datetime.utcnow().isoformat() + "Z"
        self.id = hashlib.sha256(
            f"{self.timestamp}{self.what}{self.where}".encode()
        ).hexdigest()[:12]

    def to_dict(self) -> dict:
        return self.__dict__

    def to_slack_blocks(self) -> list:
        severity_emoji = {
            "CRITICAL": "🚨",
            "HIGH": "⚠️",
            "MEDIUM": "🔶",
            "LOW": "ℹ️",
        }.get(self.severity, "⚠️")
        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity_emoji} SINCOR COMPLIANCE VIOLATION [{self.severity}]",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Check:*\n{self.check_name}"},
                    {"type": "mrkdwn", "text": f"*Severity:*\n{self.severity}"},
                    {"type": "mrkdwn", "text": f"*What:*\n{self.what}"},
                    {"type": "mrkdwn", "text": f"*Where:*\n{self.where}"},
                    {"type": "mrkdwn", "text": f"*Who/What Caused It:*\n{self.who}"},
                    {"type": "mrkdwn", "text": f"*Time:*\n{self.timestamp}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Details:*\n{self.details}"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*✅ Remediation:*\n{self.remediation}",
                },
            },
        ]


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def send_slack_alert(violation: ComplianceViolation) -> bool:
    if not SLACK_WEBHOOK_URL:
        logger.warning("SLACK_COMPLIANCE_WEBHOOK_URL not set — skipping Slack alert")
        return False
    try:
        payload = {"blocks": violation.to_slack_blocks()}
        resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"Slack alert sent for violation {violation.id}")
            return True
        else:
            logger.error(f"Slack alert failed: {resp.status_code} {resp.text}")
            return False
    except Exception as exc:
        logger.error(f"Slack alert exception: {exc}")
        return False


def _violation_email_bodies(violation: ComplianceViolation) -> tuple[str, str, str]:
    subject = (
        f"[URGENT] SINCOR Compliance Violation [{violation.severity}] — {violation.check_name}"
    )
    text_body = f"""
SINCOR COMPLIANCE VIOLATION DETECTED
=====================================
ID: {violation.id}
Time: {violation.timestamp}
Severity: {violation.severity}
Check: {violation.check_name}

WHAT:        {violation.what}
WHERE:       {violation.where}
WHO/CAUSE:   {violation.who}

DETAILS:
{violation.details}

REMEDIATION:
{violation.remediation}

--- This is an automated alert from the SINCOR Compliance Monitor ---
    """.strip()

    html_body = f"""
<html><body style="font-family:sans-serif;max-width:600px;margin:auto">
<div style="background:#dc2626;color:white;padding:16px;border-radius:8px 8px 0 0">
  <h2 style="margin:0">🚨 SINCOR Compliance Violation [{violation.severity}]</h2>
</div>
<div style="border:2px solid #dc2626;padding:16px;border-radius:0 0 8px 8px">
  <table style="width:100%;border-collapse:collapse">
    <tr><td style="font-weight:bold;padding:4px 8px;width:130px">ID</td><td style="padding:4px 8px">{violation.id}</td></tr>
    <tr style="background:#f9fafb"><td style="font-weight:bold;padding:4px 8px">Time</td><td style="padding:4px 8px">{violation.timestamp}</td></tr>
    <tr><td style="font-weight:bold;padding:4px 8px">Check</td><td style="padding:4px 8px">{violation.check_name}</td></tr>
    <tr style="background:#f9fafb"><td style="font-weight:bold;padding:4px 8px">What</td><td style="padding:4px 8px">{violation.what}</td></tr>
    <tr><td style="font-weight:bold;padding:4px 8px">Where</td><td style="padding:4px 8px">{violation.where}</td></tr>
    <tr style="background:#f9fafb"><td style="font-weight:bold;padding:4px 8px">Who/Cause</td><td style="padding:4px 8px">{violation.who}</td></tr>
  </table>
  <h3>Details</h3>
  <p style="background:#fef2f2;padding:12px;border-radius:6px">{violation.details}</p>
  <h3 style="color:#16a34a">✅ Remediation</h3>
  <p style="background:#f0fdf4;padding:12px;border-radius:6px">{violation.remediation}</p>
</div>
<p style="color:#9ca3af;font-size:12px;text-align:center;margin-top:16px">
  Automated alert — SINCOR Compliance Monitor
</p>
</body></html>
    """.strip()
    return subject, text_body, html_body


def send_email_alert(violation: ComplianceViolation) -> bool:
    subject, text_body, html_body = _violation_email_bodies(violation)
    from_addr = ALERT_EMAIL_FROM

    if RESEND_API_KEY:
        try:
            from resend import Resend

            client = Resend(api_key=RESEND_API_KEY)
            client.emails.send({
                "from": f"SINCOR Compliance <{from_addr}>",
                "to": ALERT_EMAIL_TO,
                "subject": subject,
                "html": html_body,
                "text": text_body,
            })
            logger.info(f"Resend compliance alert sent for violation {violation.id}")
            return True
        except Exception as exc:
            logger.error(f"Resend alert exception: {exc}")

    if not SMTP_PASS:
        logger.warning("No RESEND_API_KEY or SMTP_PASS — skipping email alert")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ALERT_EMAIL_TO
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(from_addr, ALERT_EMAIL_TO, msg.as_string())
        logger.info(f"Email alert sent for violation {violation.id}")
        return True
    except Exception as exc:
        logger.error(f"Email alert exception: {exc}")
        return False


def quarantine_content(target_path: str, violation: ComplianceViolation) -> str:
    """Move a file or mark a DB record as quarantined. Returns quarantine path."""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = QUARANTINE_DIR / f"{ts}_{violation.id}_{Path(target_path).name}"
    try:
        import shutil
        if Path(target_path).exists():
            shutil.move(target_path, dest)
            logger.warning(f"Quarantined {target_path} → {dest}")
        else:
            # Record as virtual quarantine for non-file targets
            dest.with_suffix(".quarantine_notice.json").write_text(
                json.dumps({"original": target_path, "violation": violation.to_dict()}, indent=2)
            )
    except Exception as exc:
        logger.error(f"Quarantine failed for {target_path}: {exc}")
    return str(dest)


def log_violation(violation: ComplianceViolation):
    """Append violation to the daily JSONL log."""
    log_file = LOG_DIR / f"violations_{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(violation.to_dict()) + "\n")
    logger.warning(
        f"VIOLATION [{violation.severity}] {violation.check_name}: {violation.what} @ {violation.where}"
    )


def handle_violation(violation: ComplianceViolation):
    """Central handler: log + alert + optionally quarantine."""
    log_violation(violation)
    send_slack_alert(violation)
    send_email_alert(violation)
    if violation.quarantine_target:
        quarantine_content(violation.quarantine_target, violation)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_env_variables() -> list[ComplianceViolation]:
    """Verify env vars are properly set — no defaults, test keys, or hardcoded secrets."""
    violations = []

    # JWT must not be default/test value
    jwt_secret = os.getenv(JWT_SECRET_ENV_KEY, "")
    for pattern in FAKE_CREDENTIAL_PATTERNS:
        if re.search(pattern, jwt_secret, re.IGNORECASE):
            violations.append(ComplianceViolation(
                check_name="ENV_JWT_SECRET",
                severity="CRITICAL",
                what=f"JWT_SECRET_KEY matches unsafe pattern: '{pattern}'",
                where=f"Environment variable: {JWT_SECRET_ENV_KEY}",
                who="System configuration / deployment",
                details=(
                    f"The JWT secret key contains a suspicious pattern ('{pattern}'). "
                    "If this is a test or placeholder value in production, all JWTs are compromised."
                ),
                remediation=(
                    "Generate a strong random secret: "
                    "`python -c \"import secrets; print(secrets.token_hex(64))\"` "
                    "and set JWT_SECRET_KEY in Railway/environment. "
                    "Rotate all active JWT tokens after changing."
                ),
            ))

    # Stripe — must not use test keys in production
    env = os.getenv("FLASK_ENV", os.getenv("ENVIRONMENT", "production")).lower()
    is_prod = env not in ("development", "dev", "test", "testing", "local")

    for key in STRIPE_ENV_KEYS:
        val = os.getenv(key, "")
        if not val:
            violations.append(ComplianceViolation(
                check_name="ENV_STRIPE_MISSING",
                severity="HIGH",
                what=f"Stripe env var {key} is not set",
                where=f"Environment variable: {key}",
                who="System configuration",
                details=f"{key} is empty. Payment processing may fail silently.",
                remediation=f"Set {key} in Railway environment variables from Stripe dashboard.",
            ))
        elif is_prod and re.search(r"sk_test_|pk_test_", val):
            violations.append(ComplianceViolation(
                check_name="ENV_STRIPE_TEST_IN_PROD",
                severity="CRITICAL",
                what=f"Stripe TEST key detected in production environment ({key})",
                where=f"Environment variable: {key}",
                who="System configuration / deployment",
                details=(
                    f"{key} contains a test key prefix in a production environment. "
                    "Test keys do not process real payments — customers will not be charged."
                ),
                remediation=(
                    f"Replace {key} with the live Stripe key from "
                    "https://dashboard.stripe.com/apikeys. "
                    "Ensure FLASK_ENV=production is set."
                ),
            ))

    # PayPal
    for key in PAYPAL_ENV_KEYS:
        val = os.getenv(key, "")
        if not val:
            violations.append(ComplianceViolation(
                check_name="ENV_PAYPAL_MISSING",
                severity="MEDIUM",
                what=f"PayPal env var {key} is not set",
                where=f"Environment variable: {key}",
                who="System configuration",
                details=f"{key} is empty. PayPal payment route may fail.",
                remediation=f"Set {key} in Railway from PayPal developer dashboard.",
            ))

    # SMTP — needed for transactional email
    if not SMTP_PASS:
        violations.append(ComplianceViolation(
            check_name="ENV_SMTP_MISSING",
            severity="MEDIUM",
            what="SMTP_PASS not configured — transactional email disabled",
            where="Environment variable: SMTP_PASS",
            who="System configuration",
            details="Emails (receipts, password resets) will silently fail.",
            remediation="Set SMTP_PASS in Railway from SendGrid/Postmark API key.",
        ))

    return violations


def check_marketing_claims(html_content: str, source_url: str) -> list[ComplianceViolation]:
    """Scan homepage/blog/email HTML for unsubstantiated claims."""
    violations = []
    text = re.sub(r"<[^>]+>", " ", html_content).lower()

    for pattern, description in UNSUBSTANTIATED_CLAIM_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            excerpt = text[max(0, match.start() - 60): match.end() + 60].strip()
            violations.append(ComplianceViolation(
                check_name="MARKETING_CLAIM",
                severity="HIGH",
                what=f"Unsubstantiated marketing claim: {description}",
                where=source_url,
                who="Marketing content / agent-generated copy",
                details=(
                    f"Pattern '{pattern}' matched in content.\n"
                    f"Excerpt: \"...{excerpt}...\""
                ),
                remediation=(
                    "Remove or qualify the claim with factual evidence. "
                    "Replace earnings claims with testimonials attributed to real customers. "
                    "Add disclaimer: 'Results vary. No income guarantee.' "
                    "Review FTC guidelines on endorsements: https://www.ftc.gov/tips-advice/business-center/guidance/ftcs-endorsement-guides-what-people-are-asking"
                ),
            ))
    return violations


def check_homepage_claims() -> list[ComplianceViolation]:
    """Fetch live homepage and check for unsubstantiated claims."""
    violations = []
    try:
        resp = requests.get(APP_BASE_URL, timeout=15)
        violations += check_marketing_claims(resp.text, APP_BASE_URL)
        buy_url = APP_BASE_URL.rstrip("/") + "/buy"
        resp2 = requests.get(buy_url, timeout=15)
        violations += check_marketing_claims(resp2.text, buy_url)
    except Exception as exc:
        logger.error(f"Homepage check failed: {exc}")
    return violations


def check_api_response_leaks() -> list[ComplianceViolation]:
    """Hit public API endpoints and check for accidental data leaks."""
    violations = []
    endpoints_to_check = [
        "/api/v1/products",
        "/api/agents",
        "/health",
        "/api/status",
    ]
    for endpoint in endpoints_to_check:
        url = APP_BASE_URL.rstrip("/") + endpoint
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code not in (200, 404, 401, 403):
                continue
            if resp.status_code == 200:
                body = resp.text
                for pattern, pii_type in PII_PATTERNS:
                    if re.search(pattern, body):
                        violations.append(ComplianceViolation(
                            check_name="API_PII_LEAK",
                            severity="CRITICAL",
                            what=f"Potential {pii_type} in public API response",
                            where=url,
                            who="API route handler",
                            details=(
                                f"PII pattern '{pattern}' matched in response from {url}. "
                                "This data should never appear in API responses."
                            ),
                            remediation=(
                                "Audit the route handler to ensure PII fields are excluded from serialization. "
                                "Use a response schema/serializer that explicitly whitelists safe fields. "
                                "Rotate any exposed credentials immediately."
                            ),
                        ))
                # Check for debug info leaks
                debug_patterns = [
                    (r"traceback\s*\(most recent call last\)", "Python traceback exposed"),
                    (r"sqlalchemy\.exc\.", "SQLAlchemy exception exposed"),
                    (r'"SECRET_KEY"\s*:', "SECRET_KEY in JSON response"),
                    (r'"password"\s*:\s*"[^"]{4,}"', "Password field in response"),
                ]
                for pattern, desc in debug_patterns:
                    if re.search(pattern, body, re.IGNORECASE):
                        violations.append(ComplianceViolation(
                            check_name="API_DEBUG_LEAK",
                            severity="HIGH",
                            what=f"Debug/sensitive info in API response: {desc}",
                            where=url,
                            who="Flask error handler / unhandled exception",
                            details=f"Pattern '{pattern}' found in {url} response.",
                            remediation=(
                                "Set FLASK_DEBUG=0 in production. "
                                "Add a global error handler that returns generic error messages. "
                                "Ensure PROPAGATE_EXCEPTIONS=False."
                            ),
                        ))
        except Exception as exc:
            logger.debug(f"API check skipped for {url}: {exc}")
    return violations


def check_database_logs() -> list[ComplianceViolation]:
    """Parse DB audit log for suspicious queries."""
    violations = []
    if not DATABASE_LOG_PATH.exists():
        return violations
    try:
        lines = DATABASE_LOG_PATH.read_text(errors="ignore").splitlines()
        # Only check last 1000 lines (last ~30 min of activity)
        recent = lines[-1000:]
        for line in recent:
            for pattern, desc in SUSPICIOUS_DB_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    violations.append(ComplianceViolation(
                        check_name="DB_SUSPICIOUS_QUERY",
                        severity="CRITICAL",
                        what=f"Suspicious DB activity: {desc}",
                        where=str(DATABASE_LOG_PATH),
                        who="Database user (see log line below)",
                        details=f"Pattern: '{pattern}'\nLog line: {line[:300]}",
                        remediation=(
                            "Review the full database audit log. "
                            "Identify the user/service account that ran the query. "
                            "Revoke or restrict DB permissions if unauthorized. "
                            "Run a data integrity check on affected tables."
                        ),
                    ))
    except Exception as exc:
        logger.error(f"DB log check failed: {exc}")
    return violations


def check_email_content(email_html: str, subject: str, sender: str) -> list[ComplianceViolation]:
    """Check outbound email content before sending."""
    violations = []
    violations += check_marketing_claims(email_html, f"email:subject={subject}")

    # CAN-SPAM checks
    canspam_required = [
        (r"unsubscribe|opt.?out|remove me", "Unsubscribe mechanism"),
        (r"\d+\s+\w+\s+(st|ave|blvd|rd|lane|drive|way|ct|court|pl|place)", "Physical mailing address"),
    ]
    text = re.sub(r"<[^>]+>", " ", email_html).lower()
    for pattern, requirement in canspam_required:
        if not re.search(pattern, text, re.IGNORECASE):
            violations.append(ComplianceViolation(
                check_name="EMAIL_CANSPAM",
                severity="HIGH",
                what=f"CAN-SPAM violation: missing '{requirement}'",
                where=f"Email from {sender}, subject: {subject}",
                who=sender,
                details=(
                    f"CAN-SPAM Act requires: {requirement}. "
                    "This element was not found in the email body."
                ),
                remediation=(
                    f"Add '{requirement}' to every marketing email. "
                    "See CAN-SPAM Act requirements: https://www.ftc.gov/tips-advice/business-center/guidance/can-spam-act-compliance-guide-business"
                ),
            ))
    return violations


def check_blog_posts() -> list[ComplianceViolation]:
    """Check published blog posts for defamation and IP risks."""
    violations = []
    blog_url = APP_BASE_URL.rstrip("/") + "/blog"
    try:
        resp = requests.get(blog_url, timeout=10)
        if resp.status_code != 200:
            return violations

        # Extract links to individual posts
        post_links = re.findall(r'href=["\'](/blog/[^"\']+)["\']', resp.text)
        for link in post_links[:20]:  # Limit to 20 most recent
            post_url = APP_BASE_URL.rstrip("/") + link
            try:
                post_resp = requests.get(post_url, timeout=10)
                text = re.sub(r"<[^>]+>", " ", post_resp.text).lower()

                # Defamation red flags
                defamation_patterns = [
                    (r"\b(scam|fraud|criminal|liar|corrupt)\b.*\b(company|ceo|founder|team)\b", "Potential defamation"),
                    (r"\bcopy(right|ed)\b.*\bwithout\s+permission\b", "Copyright concern"),
                ]
                for pattern, desc in defamation_patterns:
                    if re.search(pattern, text, re.IGNORECASE):
                        violations.append(ComplianceViolation(
                            check_name="BLOG_DEFAMATION_RISK",
                            severity="HIGH",
                            what=f"Blog post defamation/IP risk: {desc}",
                            where=post_url,
                            who="Blog author / content agent",
                            details=f"Pattern '{pattern}' matched in {post_url}.",
                            remediation=(
                                "Have legal review this post before it stays live. "
                                "Remove or qualify statements about specific companies/individuals. "
                                "Ensure all quotes are attributed and accurate."
                            ),
                            quarantine_target=post_url,
                        ))
            except Exception:
                pass
    except Exception as exc:
        logger.error(f"Blog check failed: {exc}")
    return violations


# ---------------------------------------------------------------------------
# Master runner
# ---------------------------------------------------------------------------

def run_all_checks() -> list[ComplianceViolation]:
    """Run every compliance check and return all violations found."""
    all_violations = []
    start = time.time()
    logger.info("=== SINCOR Compliance Monitor starting run ===")

    checks = [
        ("Environment Variables", check_env_variables),
        ("Homepage Marketing Claims", check_homepage_claims),
        ("API Response Leaks", check_api_response_leaks),
        ("Database Log Audit", check_database_logs),
        ("Blog Posts", check_blog_posts),
    ]

    for name, check_fn in checks:
        try:
            logger.info(f"Running check: {name}")
            results = check_fn()
            all_violations.extend(results)
            logger.info(f"  → {len(results)} violation(s)")
        except Exception as exc:
            logger.error(f"Check '{name}' raised an exception: {exc}", exc_info=True)

    elapsed = round(time.time() - start, 2)
    logger.info(
        f"=== Run complete in {elapsed}s — {len(all_violations)} total violation(s) ==="
    )

    for v in all_violations:
        handle_violation(v)

    # Write summary
    summary = {
        "run_time": datetime.utcnow().isoformat() + "Z",
        "elapsed_seconds": elapsed,
        "violations_count": len(all_violations),
        "by_severity": {
            sev: sum(1 for v in all_violations if v.severity == sev)
            for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        },
        "violations": [v.to_dict() for v in all_violations],
    }
    summary_path = LOG_DIR / f"summary_{datetime.utcnow().strftime('%Y-%m-%d_%H-%M')}.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    logger.info(f"Summary written to {summary_path}")

    return all_violations


def start_compliance_scheduler():
    """Attach the compliance monitor to APScheduler (call from app.py startup)."""
    if os.getenv("COMPLIANCE_MONITOR_ENABLED", "false").lower() != "true":
        logger.info(
            "Compliance monitor disabled (set COMPLIANCE_MONITOR_ENABLED=true to activate)"
        )
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            func=run_all_checks,
            trigger=IntervalTrigger(minutes=30),
            id="compliance_monitor",
            name="SINCOR Compliance Monitor",
            replace_existing=True,
            misfire_grace_time=300,
        )
        scheduler.start()
        logger.info("Compliance monitor scheduler started — runs every 30 minutes")
        return scheduler
    except ImportError:
        logger.warning(
            "APScheduler not installed — compliance monitor will not auto-run. "
            "Install with: pip install apscheduler"
        )
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    violations = run_all_checks()
    if violations:
        print(f"\n⚠️  {len(violations)} violation(s) found. Check logs/compliance/ for details.")
        for v in violations:
            print(f"  [{v.severity}] {v.check_name}: {v.what}")
    else:
        print("\n✅ No compliance violations detected.")
