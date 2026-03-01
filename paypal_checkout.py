"""
SINCOR PayPal Checkout Plans
Defines subscription plans and their associated PayPal plan IDs.
PayPal plan IDs are read from environment variables so they can be
configured per environment (sandbox vs. live).
"""

import logging
import os

logger = logging.getLogger(__name__)

# PayPal plan IDs - set these in your Railway/production environment variables.
# Create subscription plans in your PayPal developer dashboard and paste the
# generated plan IDs (e.g. P-XXXXXXXXXXXXXXXXXXXXXXXX) into the env vars below.
_STARTER_PLAN_ID = os.getenv("PAYPAL_PLAN_ID_STARTER", "")
_PROFESSIONAL_PLAN_ID = os.getenv("PAYPAL_PLAN_ID_PROFESSIONAL", "")
_ENTERPRISE_PLAN_ID = os.getenv("PAYPAL_PLAN_ID_ENTERPRISE", "")

# Warn at import time so misconfiguration is visible in startup logs.
_missing = [
    name
    for name, value in [
        ("PAYPAL_PLAN_ID_STARTER", _STARTER_PLAN_ID),
        ("PAYPAL_PLAN_ID_PROFESSIONAL", _PROFESSIONAL_PLAN_ID),
        ("PAYPAL_PLAN_ID_ENTERPRISE", _ENTERPRISE_PLAN_ID),
    ]
    if not value
]
if _missing:
    logger.warning(
        "PayPal subscription plan IDs not configured for: %s. "
        "Set these environment variables to enable checkout payments.",
        ", ".join(_missing),
    )

PLANS = {
    "starter": {
        "name": "SINCOR Starter Plan",
        "price": 297.00,
        "paypal_plan_id": _STARTER_PLAN_ID,
        "features": [
            "10 AI Agents (Scout, Synthesizer, Builder)",
            "Basic lead generation and outreach automation",
            "Email support with 24-hour response time",
            "Monthly performance reports",
            "Cancel anytime, no long-term contracts",
        ],
    },
    "professional": {
        "name": "SINCOR Professional Plan",
        "price": 997.00,
        "paypal_plan_id": _PROFESSIONAL_PLAN_ID,
        "features": [
            "25 AI Agents - Full automation suite across all business functions",
            "Advanced lead generation with intelligent qualification",
            "AI-powered content creation for marketing and social media",
            "Priority support with 2-hour response time",
            "Weekly reports and real-time insights dashboard",
            "Custom automation workflows tailored to your business",
            "1-on-1 onboarding call with implementation specialist",
        ],
    },
    "enterprise": {
        "name": "SINCOR Enterprise Plan",
        "price": 2997.00,
        "paypal_plan_id": _ENTERPRISE_PLAN_ID,
        "features": [
            "All 42 AI Agents - Complete business automation system",
            "Comprehensive automation: sales, operations, content, and intelligence",
            "Dedicated success manager assigned to your account",
            "24/7 priority support with 30-minute response SLA",
            "Daily reports and real-time analytics dashboard",
            "White-label options for agencies reselling to clients",
            "Custom integrations and API access",
            "Strategic planning and optimization sessions",
        ],
    },
}
