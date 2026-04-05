#!/usr/bin/env python3
"""
SINCOR Safety Guardrails System
=================================
Hard blocks for agents attempting prohibited actions.
Every block is logged with: attempted action, agent, timestamp, remediation.

Usage:
    from sincor2.compliance_guardrails import GuardrailsEngine, GuardrailViolation

    guardrails = GuardrailsEngine()

    # In agent code:
    guardrails.check_content(text, agent_name="content_agent")
    guardrails.check_email_send(recipients, content, agent_name="outreach_agent")
    guardrails.check_data_access(user_id, resource, agent_name="analytics_agent")
    guardrails.check_payment_credentials(key_value, agent_name="stripe_agent")

    # Decorator for agent functions:
    @guardrails.guard(checks=["content", "pii"])
    def publish_blog_post(content, **kwargs):
        ...
"""

import os
import re
import json
import logging
import hashlib
import functools
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GUARDRAILS_LOG_DIR = Path(os.getenv("GUARDRAILS_LOG_DIR", "logs/guardrails"))
GUARDRAILS_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GUARDRAILS] %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(GUARDRAILS_LOG_DIR / "guardrails.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("sincor.guardrails")

ENVIRONMENT = os.getenv("FLASK_ENV", os.getenv("ENVIRONMENT", "production")).lower()
IS_PRODUCTION = ENVIRONMENT not in ("development", "dev", "test", "testing", "local")


# ---------------------------------------------------------------------------
# Violation & Block classes
# ---------------------------------------------------------------------------

class GuardrailBlock(Exception):
    """Raised when a guardrail hard-blocks an action."""

    def __init__(self, rule: str, agent: str, action: str, reason: str, remediation: str):
        self.rule = rule
        self.agent = agent
        self.action = action
        self.reason = reason
        self.remediation = remediation
        self.timestamp = datetime.utcnow().isoformat() + "Z"
        self.id = hashlib.sha256(
            f"{self.timestamp}{rule}{agent}".encode()
        ).hexdigest()[:12]
        super().__init__(
            f"[GUARDRAIL BLOCK {self.id}] Rule={rule} Agent={agent}: {reason}"
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "rule": self.rule,
            "agent": self.agent,
            "action": self.action,
            "reason": self.reason,
            "remediation": self.remediation,
        }


def _log_block(block: GuardrailBlock):
    log_file = GUARDRAILS_LOG_DIR / f"blocks_{datetime.utcnow().strftime('%Y-%m-%d')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(block.to_dict()) + "\n")
    logger.warning(
        f"BLOCKED [{block.rule}] agent={block.agent} action={block.action}: {block.reason}"
    )


def _block(rule: str, agent: str, action: str, reason: str, remediation: str) -> None:
    """Log and raise a GuardrailBlock."""
    b = GuardrailBlock(rule, agent, action, reason, remediation)
    _log_block(b)
    raise b


# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

# 1. Fake testimonials / reviews / stats
FAKE_TESTIMONIAL_PATTERNS = [
    r"\[insert\s+(name|testimonial|review|customer)\]",
    r"lorem\s+ipsum",
    r"fake\s+(review|testimonial|customer|stat)",
    r"(john|jane)\s+doe\s*,?\s*(ceo|founder|customer)",  # Generic placeholder names
    r"\bplaceholder\s+(review|testimonial)\b",
    r'"[^"]{5,30}"\s*[-—]\s*(anonymous|user\s+\d+)',   # "Quote" - Anonymous / User123
    r"<testimonial>",
    r"\{\{\s*testimonial\s*\}\}",
]

# 2. Unsubstantiated health/financial claims
PROHIBITED_CLAIMS = [
    (r"\bearn\s+\$[\d,]+\b", "Specific earnings promise"),
    (r"\bmake\s+\$[\d,]+\b", "Specific earnings promise"),
    (r"\bguaranteed?\s+(roi|return|income|revenue|earnings?|profit)\b", "Guaranteed ROI/income"),
    (r"\bproven\s+to\s+(work|make|earn|generate|cure|treat|heal)\b", "Unsubstantiated 'proven to' claim"),
    (r"\b100%\s+(effective|guaranteed|safe|proven|success)\b", "Absolute efficacy/safety claim"),
    (r"\b(cure|treat|heal|prevent)\s+(disease|illness|cancer|diabetes|depression)\b", "Health cure claim"),
    (r"\bno\s+side\s+effects\b", "No-side-effects health claim"),
    (r"\bfinancial\s+(freedom|independence)\s+guaranteed\b", "Guaranteed financial freedom"),
    (r"\bget\s+rich\s+(quick|fast|overnight)\b", "Get-rich-quick scheme language"),
    (r"\bdouble\s+your\s+(money|income|revenue)\b", "Double-your-money claim"),
    (r"\bpassive\s+income\s+(guaranteed|automatic|effortless)\b", "Guaranteed passive income"),
    (r"\bwork\s+from\s+home\s+and\s+(earn|make)\s+\$", "Work-from-home earnings promise"),
    (r"\b(fda|sec|ftc)\s+approved\b", "False regulatory approval claim (verify before using)"),
]

# 3. Competitor comparisons without citations
COMPETITOR_MENTION_PATTERN = r"\b(better\s+than|worse\s+than|beats?|outperforms?|surpasses?)\s+\w+"
CITATION_PATTERN = r'(https?://|source:|according\s+to\s+|study\s+by|data\s+from\s+|\[\d+\])'

# 4. Bulk email patterns
BULK_EMAIL_THRESHOLD = 50  # more than 50 recipients = bulk

# 5. PII patterns (for storage check)
PII_STORAGE_PATTERNS = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "SSN"),
    (r"\b4[0-9]{12}(?:[0-9]{3})?\b", "Visa card number"),
    (r"\b5[1-5][0-9]{14}\b", "MasterCard number"),
    (r'"password"\s*:\s*"[^"]{4,}"', "Plaintext password"),
    (r"'password'\s*:\s*'[^']{4,}'", "Plaintext password"),
    (r'"ssn"\s*:\s*"\d{3}-\d{2}-\d{4}"', "SSN in storage"),
    (r'"credit_card"\s*:\s*"\d{13,19}"', "Credit card in storage"),
]

# 6. Test/fake payment credentials
TEST_PAYMENT_PATTERNS = [
    r"sk_test_",
    r"pk_test_",
    r"4111111111111111",   # Classic test Visa
    r"4242424242424242",   # Stripe test card
    r"sandbox\.paypal\.com",
    r"api-3t\.sandbox",
    r"fake.*payment.*key",
    r"test.*stripe.*key",
]

# 7. Legal review flag — content must have been reviewed
LEGAL_REVIEW_FLAG = "__legal_reviewed__"


# ---------------------------------------------------------------------------
# Guardrails Engine
# ---------------------------------------------------------------------------

class GuardrailsEngine:
    """
    Central safety guardrails for SINCOR agents.
    Call check_* methods before performing sensitive actions.
    """

    def __init__(self, strict_mode: bool = True):
        """
        strict_mode=True: raise GuardrailBlock on violations (default).
        strict_mode=False: log only, don't raise (use for monitoring only).
        """
        self.strict_mode = strict_mode
        logger.info(
            f"GuardrailsEngine initialized — strict_mode={strict_mode}, "
            f"environment={ENVIRONMENT}, is_production={IS_PRODUCTION}"
        )

    def _enforce(self, rule: str, agent: str, action: str, reason: str, remediation: str):
        b = GuardrailBlock(rule, agent, action, reason, remediation)
        _log_block(b)
        if self.strict_mode:
            raise b
        else:
            logger.warning(f"GUARDRAIL WARN (non-strict): {b}")

    # ------------------------------------------------------------------
    # 1. Fake testimonials / stats
    # ------------------------------------------------------------------

    def check_content_authenticity(self, content: str, agent: str = "unknown") -> None:
        """Block fake testimonials, reviews, and placeholder stats."""
        for pattern in FAKE_TESTIMONIAL_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                self._enforce(
                    rule="NO_FAKE_TESTIMONIALS",
                    agent=agent,
                    action="publish_content",
                    reason=f"Content contains fake testimonial/review pattern: '{pattern}'",
                    remediation=(
                        "Replace placeholder testimonials with real customer quotes "
                        "(with permission). Ensure all stats are sourced from real data. "
                        "FTC endorsement guide: https://www.ftc.gov/tips-advice/business-center/guidance/ftcs-endorsement-guides-what-people-are-asking"
                    ),
                )

    # ------------------------------------------------------------------
    # 2. Unsubstantiated claims
    # ------------------------------------------------------------------

    def check_claims(self, content: str, agent: str = "unknown") -> None:
        """Block unsubstantiated financial and health claims."""
        text_lower = content.lower()
        for pattern, description in PROHIBITED_CLAIMS:
            if re.search(pattern, text_lower, re.IGNORECASE):
                self._enforce(
                    rule="NO_UNSUBSTANTIATED_CLAIMS",
                    agent=agent,
                    action="publish_content",
                    reason=f"Prohibited claim detected: {description} (pattern: '{pattern}')",
                    remediation=(
                        f"Remove or rewrite the '{description}' claim. "
                        "Replace with factual, qualified language: "
                        "'Customers have reported...', 'Results vary', 'Not a guarantee of income.' "
                        "Add required disclaimers per FTC: https://www.ftc.gov/news-events/topics/truth-advertising"
                    ),
                )

    # ------------------------------------------------------------------
    # 3. Competitor comparisons
    # ------------------------------------------------------------------

    def check_competitor_comparison(self, content: str, agent: str = "unknown") -> None:
        """Block unverified competitor comparisons without citations."""
        if re.search(COMPETITOR_MENTION_PATTERN, content, re.IGNORECASE):
            if not re.search(CITATION_PATTERN, content, re.IGNORECASE):
                self._enforce(
                    rule="NO_UNVERIFIED_COMPETITOR_COMPARISON",
                    agent=agent,
                    action="publish_content",
                    reason="Competitor comparison made without citation or source",
                    remediation=(
                        "Add a verifiable citation for every comparative claim. "
                        "Format: '[Source: Company X blog, 2024-01]' or link to study. "
                        "Alternatively, rephrase to avoid direct comparison."
                    ),
                )

    # ------------------------------------------------------------------
    # 4. CAN-SPAM / bulk email
    # ------------------------------------------------------------------

    def check_email_send(
        self,
        recipients: list,
        email_content: str,
        subject: str = "",
        agent: str = "unknown",
    ) -> None:
        """Block non-compliant bulk email sends."""

        # Bulk threshold check
        if len(recipients) > BULK_EMAIL_THRESHOLD:
            # Verify CAN-SPAM elements in content
            text = re.sub(r"<[^>]+>", " ", email_content).lower()
            missing = []

            if not re.search(r"unsubscribe|opt.?out|remove me", text, re.IGNORECASE):
                missing.append("unsubscribe link")
            if not re.search(
                r"\d+\s+\w+\s+(st|ave|blvd|rd|lane|drive|way|ct|court|pl|place)", text, re.IGNORECASE
            ):
                missing.append("physical mailing address")

            if missing:
                self._enforce(
                    rule="CANSPAM_COMPLIANCE",
                    agent=agent,
                    action=f"send_bulk_email to {len(recipients)} recipients",
                    reason=f"Bulk email missing required CAN-SPAM elements: {', '.join(missing)}",
                    remediation=(
                        f"Add to email: {', '.join(missing)}. "
                        "CAN-SPAM requires: (1) honest subject line, (2) sender identity, "
                        "(3) physical address, (4) opt-out mechanism, (5) honor opt-outs within 10 days. "
                        "Guide: https://www.ftc.gov/tips-advice/business-center/guidance/can-spam-act-compliance-guide-business"
                    ),
                )

        # Content check for claims
        self.check_claims(email_content, agent=agent)
        self.check_content_authenticity(email_content, agent=agent)

    # ------------------------------------------------------------------
    # 5. Customer data access scope
    # ------------------------------------------------------------------

    def check_data_access(
        self,
        requesting_agent: str,
        resource: str,
        resource_owner_id: Optional[str] = None,
        requesting_user_id: Optional[str] = None,
    ) -> None:
        """Block agents from accessing customer data outside their scope."""
        # Agents that are not analytics or admin may not access all user records
        privileged_agents = {"analytics_agent", "admin_agent", "billing_agent", "support_agent"}
        if requesting_agent not in privileged_agents:
            # Check for bulk/cross-user access patterns
            bulk_patterns = [
                r"all_users",
                r"user_list",
                r"export_customers",
                r"bulk_email_list",
                r"full_database",
            ]
            for pattern in bulk_patterns:
                if re.search(pattern, resource, re.IGNORECASE):
                    self._enforce(
                        rule="DATA_ACCESS_SCOPE",
                        agent=requesting_agent,
                        action=f"access_data:{resource}",
                        reason=(
                            f"Agent '{requesting_agent}' attempted to access out-of-scope resource: {resource}. "
                            "Only privileged agents may access bulk customer data."
                        ),
                        remediation=(
                            f"Grant '{requesting_agent}' explicit permission for this resource, "
                            "or delegate to an analytics/admin agent. "
                            "Principle of least privilege: each agent should only access what it needs."
                        ),
                    )

        # Cross-user data access (agent accessing data for user other than requester)
        if resource_owner_id and requesting_user_id:
            if resource_owner_id != requesting_user_id and requesting_agent not in privileged_agents:
                self._enforce(
                    rule="DATA_ACCESS_CROSS_USER",
                    agent=requesting_agent,
                    action=f"access_data:{resource} owner={resource_owner_id}",
                    reason=(
                        f"Agent '{requesting_agent}' (acting for user {requesting_user_id}) "
                        f"attempted to access data owned by user {resource_owner_id}."
                    ),
                    remediation=(
                        "Ensure agents are scoped to the authenticated user's data. "
                        "Add user_id filter to all database queries. "
                        "Review OWASP IDOR: https://owasp.org/www-project-top-ten/"
                    ),
                )

    # ------------------------------------------------------------------
    # 6. Legal review gate
    # ------------------------------------------------------------------

    def check_legal_review(self, content: str, content_type: str, agent: str = "unknown") -> None:
        """Block publishing without the legal review flag for high-risk content."""
        high_risk_types = {"terms_of_service", "privacy_policy", "legal_disclaimer", "press_release", "blog_post"}
        if content_type.lower() in high_risk_types:
            if LEGAL_REVIEW_FLAG not in content:
                self._enforce(
                    rule="LEGAL_REVIEW_REQUIRED",
                    agent=agent,
                    action=f"publish_{content_type}",
                    reason=(
                        f"Content of type '{content_type}' must be reviewed before publishing. "
                        f"Missing flag: {LEGAL_REVIEW_FLAG}"
                    ),
                    remediation=(
                        f"Have Court or legal counsel review and approve this {content_type}. "
                        f"Once approved, add '{LEGAL_REVIEW_FLAG}' to the document metadata/header. "
                        "Do not auto-publish legal documents without human review."
                    ),
                )

    # ------------------------------------------------------------------
    # 7. PII storage
    # ------------------------------------------------------------------

    def check_pii_storage(self, data: Any, destination: str, agent: str = "unknown") -> None:
        """Block storing plaintext PII."""
        if isinstance(data, (dict, list)):
            data_str = json.dumps(data)
        elif isinstance(data, str):
            data_str = data
        else:
            return

        for pattern, pii_type in PII_STORAGE_PATTERNS:
            if re.search(pattern, data_str, re.IGNORECASE):
                self._enforce(
                    rule="NO_PLAINTEXT_PII",
                    agent=agent,
                    action=f"store_data:{destination}",
                    reason=f"Attempt to store plaintext {pii_type} in {destination}",
                    remediation=(
                        f"Encrypt or hash {pii_type} before storage. "
                        "Use bcrypt/argon2 for passwords; tokenize payment card data via Stripe. "
                        "Never store raw SSNs — use a tokenization service. "
                        "GDPR/CCPA require data minimization: only collect what you need."
                    ),
                )

    # ------------------------------------------------------------------
    # 8. Payment credentials
    # ------------------------------------------------------------------

    def check_payment_credentials(self, key_value: str, key_name: str = "", agent: str = "unknown") -> None:
        """Block test/fake payment credentials in production."""
        if not IS_PRODUCTION:
            return  # Allow test keys in dev/test environments

        for pattern in TEST_PAYMENT_PATTERNS:
            if re.search(pattern, key_value, re.IGNORECASE):
                self._enforce(
                    rule="NO_TEST_PAYMENT_CREDS_IN_PROD",
                    agent=agent,
                    action=f"use_payment_credential:{key_name}",
                    reason=(
                        f"Test/fake payment credential detected in PRODUCTION environment. "
                        f"Pattern: '{pattern}'. Using test keys in production means no real payments processed."
                    ),
                    remediation=(
                        f"Replace {key_name} with a live payment credential. "
                        "Stripe live keys: https://dashboard.stripe.com/apikeys (ensure live mode). "
                        "PayPal live keys: https://developer.paypal.com/dashboard/applications/live. "
                        "Set FLASK_ENV=production in Railway."
                    ),
                )

    # ------------------------------------------------------------------
    # Composite content check
    # ------------------------------------------------------------------

    def check_content(self, content: str, agent: str = "unknown", content_type: str = "general") -> None:
        """Run all content-related checks."""
        self.check_content_authenticity(content, agent=agent)
        self.check_claims(content, agent=agent)
        self.check_competitor_comparison(content, agent=agent)
        self.check_pii_storage(content, destination="content_publish", agent=agent)
        if content_type in {"terms_of_service", "privacy_policy", "legal_disclaimer",
                            "press_release", "blog_post"}:
            self.check_legal_review(content, content_type=content_type, agent=agent)

    # ------------------------------------------------------------------
    # Decorator
    # ------------------------------------------------------------------

    def guard(
        self,
        checks: list = None,
        agent_kwarg: str = "agent_name",
        content_kwarg: str = "content",
        content_type: str = "general",
    ):
        """
        Decorator to automatically apply guardrails to an agent function.

        @guardrails.guard(checks=["content", "claims"])
        def publish_post(content, agent_name="content_agent"):
            ...
        """
        if checks is None:
            checks = ["content"]

        def decorator(func: Callable):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                agent = kwargs.get(agent_kwarg, func.__name__)
                content = kwargs.get(content_kwarg, args[0] if args else "")

                if isinstance(content, str):
                    if "content" in checks or "all" in checks:
                        self.check_content(content, agent=agent, content_type=content_type)
                    if "claims" in checks:
                        self.check_claims(content, agent=agent)
                    if "authenticity" in checks:
                        self.check_content_authenticity(content, agent=agent)

                return func(*args, **kwargs)
            return wrapper
        return decorator


# ---------------------------------------------------------------------------
# Pre-built singleton for app-wide use
# ---------------------------------------------------------------------------

# Strict in production, warn-only in dev
_strict = IS_PRODUCTION
guardrails = GuardrailsEngine(strict_mode=_strict)


# ---------------------------------------------------------------------------
# Flask integration — middleware/decorator
# ---------------------------------------------------------------------------

def require_guardrails(content_type: str = "general", agent: str = "api"):
    """
    Flask view decorator that checks request JSON body against guardrails.

    @app.route("/api/publish", methods=["POST"])
    @require_guardrails(content_type="blog_post", agent="content_api")
    def publish():
        ...
    """
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            from flask import request, jsonify
            try:
                data = request.get_json(silent=True) or {}
                content = data.get("content", data.get("text", data.get("body", "")))
                if content:
                    guardrails.check_content(content, agent=agent, content_type=content_type)
            except GuardrailBlock as block:
                return jsonify({
                    "error": "guardrail_block",
                    "rule": block.rule,
                    "reason": block.reason,
                    "remediation": block.remediation,
                    "block_id": block.id,
                }), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# ---------------------------------------------------------------------------
# CLI — demo/test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("SINCOR Safety Guardrails — Self-Test\n")
    g = GuardrailsEngine(strict_mode=False)  # warn-only for demo

    tests = [
        ("Fake testimonial", lambda: g.check_content_authenticity(
            "Customers love us! [Insert testimonial here]", agent="test"
        )),
        ("Earnings claim", lambda: g.check_claims(
            "Start earning $10,000 per month guaranteed!", agent="test"
        )),
        ("Competitor without citation", lambda: g.check_competitor_comparison(
            "SINCOR beats HubSpot in every metric.", agent="test"
        )),
        ("Bulk email no unsubscribe", lambda: g.check_email_send(
            recipients=[f"user{i}@example.com" for i in range(100)],
            email_content="<html><body>Buy now!</body></html>",
            agent="test"
        )),
        ("Plaintext PII storage", lambda: g.check_pii_storage(
            {"password": "mySecretPass123"}, destination="users_table", agent="test"
        )),
        ("Test payment key (skipped in non-prod)", lambda: g.check_payment_credentials(
            "sk_test_abc123", key_name="STRIPE_SECRET", agent="test"
        )),
    ]

    for name, test_fn in tests:
        try:
            test_fn()
            print(f"  ✅ {name}: passed (no block)")
        except GuardrailBlock as b:
            print(f"  🚫 {name}: BLOCKED — {b.rule}: {b.reason[:80]}...")

    print(f"\nCheck logs at: {GUARDRAILS_LOG_DIR}")
