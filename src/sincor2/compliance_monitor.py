#!/usr/bin/env python3
"""
SINCOR Compliance Monitor Agent
================================
Runs every 30 minutes to audit marketing claims, email content, API responses,
environment variables, database activity, and payment configurations.

Confidential / internal-only: violations are written to the Railway volume
at /data/logs/compliance — no Slack, email, webhooks, or other outbound reporting.
Checks use local files and in-process Flask test requests only (no external HTTP).

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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Config — all values come from environment variables; never hardcoded
# ---------------------------------------------------------------------------

# Outbound compliance reporting is disabled by design (confidential internal audit).
COMPLIANCE_CONFIDENTIAL = os.getenv("COMPLIANCE_CONFIDENTIAL", "true").lower() != "false"
SMTP_PASS = os.getenv("SMTP_PASS", "")


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

_log_handlers: list[logging.Handler] = [logging.FileHandler(LOG_DIR / "monitor.log")]
if os.getenv("COMPLIANCE_LOG_TO_STDOUT", "false").lower() == "true":
    _log_handlers.append(logging.StreamHandler())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [COMPLIANCE] %(levelname)s %(message)s",
    handlers=_log_handlers,
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
    """Append violation to the daily JSONL log (volume only — no outbound reporting)."""
    log_file = LOG_DIR / f"violations_{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(violation.to_dict()) + "\n")
    logger.info(
        "Violation recorded locally id=%s severity=%s check=%s",
        violation.id,
        violation.severity,
        violation.check_name,
    )


def handle_violation(violation: ComplianceViolation):
    """Internal handler: volume log + optional local quarantine only."""
    log_violation(violation)
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


def check_local_templates() -> list[ComplianceViolation]:
    """Scan local marketing templates — no external HTTP."""
    violations = []
    try:
        from sincor2.data_paths import project_root

        templates = project_root() / "templates"
        for name in (
            "home.html",
            "pricing.html",
            "buy_tokens.html",
            "sales.html",
            "earn.html",
            "terms.html",
            "privacy.html",
        ):
            path = templates / name
            if path.is_file():
                html = path.read_text(encoding="utf-8", errors="ignore")
                violations += check_marketing_claims(html, f"local:templates/{name}")
    except Exception as exc:
        logger.error("Local template check failed: %s", exc)
    return violations


def check_api_response_leaks() -> list[ComplianceViolation]:
    """In-process API leak scan via Flask test client — no network egress."""
    violations = []
    endpoints_to_check = [
        "/api/v1/products",
        "/api/agents",
        "/health",
        "/api/status",
        "/api/platform/plans",
    ]
    try:
        from sincor2.mvp_app import app

        with app.test_client() as client:
            for endpoint in endpoints_to_check:
                try:
                    resp = client.get(endpoint)
                except Exception as exc:
                    logger.debug("API check skipped for %s: %s", endpoint, exc)
                    continue
                if resp.status_code not in (200, 404, 401, 403):
                    continue
                if resp.status_code != 200:
                    continue
                body = resp.get_data(as_text=True)
                for pattern, pii_type in PII_PATTERNS:
                    if re.search(pattern, body):
                        violations.append(ComplianceViolation(
                            check_name="API_PII_LEAK",
                            severity="CRITICAL",
                            what=f"Potential {pii_type} in public API response",
                            where=f"internal:{endpoint}",
                            who="API route handler",
                            details=(
                                f"PII pattern '{pattern}' matched in response from {endpoint}. "
                                "This data should never appear in API responses."
                            ),
                            remediation=(
                                "Audit the route handler to ensure PII fields are excluded from serialization. "
                                "Use a response schema/serializer that explicitly whitelists safe fields. "
                                "Rotate any exposed credentials immediately."
                            ),
                        ))
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
                            where=f"internal:{endpoint}",
                            who="Flask error handler / unhandled exception",
                            details=f"Pattern '{pattern}' found in {endpoint} response.",
                            remediation=(
                                "Set FLASK_DEBUG=0 in production. "
                                "Add a global error handler that returns generic error messages. "
                                "Ensure PROPAGATE_EXCEPTIONS=False."
                            ),
                        ))
    except Exception as exc:
        logger.error("Internal API check failed: %s", exc)
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


def check_local_blog_posts() -> list[ComplianceViolation]:
    """Scan local blog markdown — no external HTTP."""
    violations = []
    try:
        from sincor2.data_paths import project_root

        blog_dir = project_root() / "content" / "blog"
        if not blog_dir.is_dir():
            return violations

        defamation_patterns = [
            (r"\b(scam|fraud|criminal|liar|corrupt)\b.*\b(company|ceo|founder|team)\b", "Potential defamation"),
            (r"\bcopy(right|ed)\b.*\bwithout\s+permission\b", "Copyright concern"),
        ]
        for path in sorted(blog_dir.glob("*.md"), reverse=True)[:20]:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            for pattern, desc in defamation_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    violations.append(ComplianceViolation(
                        check_name="BLOG_DEFAMATION_RISK",
                        severity="HIGH",
                        what=f"Blog post defamation/IP risk: {desc}",
                        where=f"local:content/blog/{path.name}",
                        who="Blog author / content agent",
                        details=f"Pattern '{pattern}' matched in {path.name}.",
                        remediation=(
                            "Have legal review this post before it stays live. "
                            "Remove or qualify statements about specific companies/individuals. "
                            "Ensure all quotes are attributed and accurate."
                        ),
                        quarantine_target=str(path),
                    ))
    except Exception as exc:
        logger.error("Local blog check failed: %s", exc)
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
        ("Local Template Claims", check_local_templates),
        ("Internal API Leaks", check_api_response_leaks),
        ("Database Log Audit", check_database_logs),
        ("Local Blog Posts", check_local_blog_posts),
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
        logger.info(
            "Compliance monitor scheduler started — internal-only, runs every 30 minutes"
        )
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
    print(f"\n{len(violations)} violation(s) recorded under {LOG_DIR} (confidential — not exported).")
