"""WP2 comms package: typed shadow adapters for communications.

Owner decisions enforced here:
- D1: Outreach scaffold-ready but DEFAULT OFF (fail closed)
- D2: TRANSACTIONAL EMAIL ONLY — marketing always blocked_policy

Modules:
- classify: transactional vs marketing purpose classification
- suppression: in-memory + file-backed suppression list
- adapters: CommsAdapter (purpose + suppression checks before boundary)
- publishing: WordPress/Farcaster stubs (always blocked_policy)
- pii: signup/onboarding PII data-flow documentation and retention rules
"""

from sincor2.comms.adapters import CommsAdapter
from sincor2.comms.classify import EmailPurpose, classify_email_purpose
from sincor2.comms.suppression import SuppressionList

__all__ = [
    "CommsAdapter",
    "EmailPurpose",
    "classify_email_purpose",
    "SuppressionList",
]
