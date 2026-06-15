"""Email reminders for SINC subscription renewals."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("sincor2.subscription_scheduler")

_scheduler = None


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


def start_subscription_scheduler(app=None):
    global _scheduler

    if os.environ.get("SUBSCRIPTION_REMINDER_ENABLED", "true").lower() != "true":
        logger.info("[SUBSCRIPTION] Reminder scheduler disabled")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("[SUBSCRIPTION] APScheduler not installed")
        return None

    hour = int(os.environ.get("SUBSCRIPTION_REMINDER_HOUR", "10"))
    minute = int(os.environ.get("SUBSCRIPTION_REMINDER_MINUTE", "0"))

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _send_renewal_reminders,
        CronTrigger(hour=hour, minute=minute),
        id="subscription_renewal_reminder",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("[SUBSCRIPTION] Renewal reminder scheduler started (%02d:%02d UTC)", hour, minute)
    return _scheduler


def stop_subscription_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None