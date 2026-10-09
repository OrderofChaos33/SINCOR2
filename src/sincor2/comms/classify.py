"""Email purpose classification: transactional vs marketing (D2).

D2 (owner decision 2026-10-09): TRANSACTIONAL EMAIL ONLY.
Marketing email is never sent; any marketing-purpose intent resolves to
``blocked_policy`` in shadow and must never reach a provider.

Classification rules (code, not docs):
- TRANSACTIONAL: the recipient has a direct service relationship and the
  email concerns that relationship (welcome, receipt, alert, security,
  support reply, onboarding step).
- MARKETING: cold outreach, newsletters, promotions, lead nurture, or any
  email to a recipient without a direct service relationship.
- UNKNOWN defaults to MARKETING (fail closed): if we cannot prove the
  recipient has a service relationship, we treat it as marketing.
"""

from __future__ import annotations

from typing import Literal

EmailPurpose = Literal["transactional", "marketing"]

# Subjects / templates known to be transactional (service relationship).
_TRANSACTIONAL_SIGNALS = frozenset(
    {
        "welcome",
        "thank_you",
        "thank-you",
        "receipt",
        "invoice",
        "password_reset",
        "password-reset",
        "security_alert",
        "security-alert",
        "support_reply",
        "onboarding",
        "verification",
        "verify_email",
    }
)

# Signals that force marketing classification.
_MARKETING_SIGNALS = frozenset(
    {
        "outreach",
        "cold",
        "newsletter",
        "promo",
        "promotion",
        "nurture",
        "lead",
        "campaign",
    }
)


def classify_email_purpose(
    *,
    template: str = "",
    subject: str = "",
    has_service_relationship: bool = False,
) -> EmailPurpose:
    """Classify an email as transactional or marketing.

    Fail-closed: unknown or ambiguous → "marketing" (which is blocked).
    A service relationship alone is not enough; the template/subject must
    also carry a transactional signal, otherwise marketing.
    """
    haystack = f"{template} {subject}".lower()

    # Explicit marketing signals always win.
    if any(sig in haystack for sig in _MARKETING_SIGNALS):
        return "marketing"

    # Transactional requires BOTH a service relationship AND a
    # transactional signal. Either alone is insufficient.
    if has_service_relationship and any(
        sig in haystack for sig in _TRANSACTIONAL_SIGNALS
    ):
        return "transactional"

    # Default: marketing (blocked).
    return "marketing"


def is_marketing_allowed() -> bool:
    """D2: marketing email is never allowed. Always False.

    This is a code-level kill switch, not a configuration flag. There is
    no code path that sets this to True.
    """
    return False
