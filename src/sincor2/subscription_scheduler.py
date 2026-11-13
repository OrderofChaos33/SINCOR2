"""Email reminders for SINC subscription renewals.

One-scheduler consolidation (2026-10-11): this module no longer owns an
APScheduler instance. The central scheduler (sincor2.scheduler) registers the
``subscription_reminders`` job exactly once (default ON); the job function
below is the callable it uses. Scheduling is defined once in
sincor2.scheduler.SCHEDULER_DEFAULTS.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("sincor2.subscription_scheduler")


def _send_renewal_reminders() -> None:
    try:
        from sincor2.platform_payments import PLATFORM_PLANS, subscriptions_needing_renewal
        from sincor2.email_sender import get_email_sender

        due = subscriptions_needing_renewal(within_days=7)
        if not due:
            logger.info("[SUBSCRIPTION] No renewals due in next 7 days")
            return

        sender = get_email_sender()
        for sub in due:
            email = (sub.get("email") or "").strip()
            wallet = sub.get("wallet", "")
            plan_id = sub.get("plan_id", "")
            plan = PLATFORM_PLANS.get(plan_id, {})
            period_end = (sub.get("period_end") or "")[:10]
            if not email:
                logger.info("[SUBSCRIPTION] Renewal due for %s plan=%s (no email)", wallet[:10], plan_id)
                continue

            body = f"""
            <p>Your SINCOR <strong>{plan.get('label', plan_id)}</strong> subscription renews soon.</p>
            <p><strong>Period ends:</strong> {period_end}</p>
            <p>Pay in <strong>SINC</strong> on Base: <a href="https://getsincor.com/buy?plan={plan_id}">getsincor.com/buy</a></p>
            <p>Wallet on file: <code>{wallet}</code></p>
            <p>Questions? support@getsincor.com</p>
            """
            try:
                sender.send_email(
                    to_email=email,
                    to_name="",
                    subject=f"SINCOR renewal — {plan.get('label', plan_id)}",
                    html_content=body,
                    text_content=f"Renew at https://getsincor.com/buy?plan={plan_id} before {period_end}",
                )
                logger.info("[SUBSCRIPTION] Reminder sent to %s plan=%s", email, plan_id)
            except Exception as e:
                logger.warning("[SUBSCRIPTION] Reminder failed %s: %s", email, e)
    except Exception as e:
        logger.error("[SUBSCRIPTION] Renewal cycle error: %s", e, exc_info=True)

