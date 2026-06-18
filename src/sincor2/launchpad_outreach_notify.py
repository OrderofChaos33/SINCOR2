"""
Daily launchpad outreach reminder — 5 LBP venues, one-tap mailto batch.
"""

from __future__ import annotations

import html
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("sincor2.launchpad_outreach_notify")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ALERT_EMAIL = "court@getsincor.com"
_scheduler = None


def _launchpads_url() -> str:
    base = os.environ.get("APP_BASE_URL", "https://getsincor.com").rstrip("/")
    return f"{base}/launch/launchpads"


def _alert_email() -> str:
    return (
        os.environ.get("LAUNCHPAD_OUTREACH_ALERT_EMAIL")
        or os.environ.get("PARTNER_OUTREACH_ALERT_EMAIL")
        or os.environ.get("LAUNCH_REVIEW_ALERT_EMAIL", DEFAULT_ALERT_EMAIL)
    ).strip()


def build_launchpad_reminder_content() -> tuple[str, str, str, int]:
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from sincor2.launchpad_outreach import due_outreach, pipeline_summary

    summary = pipeline_summary()
    due = due_outreach(limit=5)
    count = len(due)
    url = _launchpads_url()
    launch_date = summary.get("launch_date", "2026-07-07")

    if count:
        subject = f"SINCOR launchpads: {count} outreach due (TGE {launch_date})"
        headline = f"{count} launchpad(s) need outreach today"
        lead = (
            f"TGE {launch_date} · KPI pre-deposit campaign live. "
            "Open mailto links, send, then mark contacted."
        )
    else:
        subject = f"SINCOR launchpads: caught up (TGE {launch_date})"
        headline = "No launchpad outreach due today"
        lead = "Follow up on warm LBP leads at /launch/launchpads."

    items_html = ""
    items_text = ""
    for lp in due:
        name = html.escape(lp.get("name", ""))
        tier = html.escape(lp.get("tier", ""))
        method = html.escape(lp.get("contact_method", ""))
        contact = html.escape(lp.get("contact_url", ""))
        lid = html.escape(lp.get("id", ""))
        mailto = lp.get("mailto", "")
        mail_link = (
            f"<br><a href='{html.escape(mailto)}'>Send email</a>" if mailto else ""
        )
        items_html += (
            f"<li><strong>{name}</strong> ({tier}) — {method}<br>"
            f"<a href='{contact}'>{contact}</a>{mail_link}<br>"
            f"<span style='font-size:12px;color:#6b7280'>id: {lid}</span></li>"
        )
        items_text += f"- {lp.get('name')} [{lp.get('tier')}] {method} {contact}\n"

    html_body = f"""
<div style="font-family:Inter,system-ui,sans-serif;max-width:560px;color:#111827">
  <p style="color:#6b7280;font-size:13px">SINCOR launchpad outreach</p>
  <h2 style="margin:0 0 12px">{headline}</h2>
  <p style="line-height:1.5">{lead}</p>
  {"<ul style='padding-left:18px;line-height:1.6'>" + items_html + "</ul>" if items_html else ""}
  <p style="margin:24px 0">
    <a href="{url}" style="display:inline-block;background:#22d3ee;color:#042f2e;
      padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">
      Open launchpad pipeline
    </a>
  </p>
</div>
"""
    text_body = f"{headline}\n\n{lead}\n\n{items_text}\nPipeline: {url}\n"
    return subject, html_body, text_body, count


def send_launchpad_outreach_reminder() -> dict:
    to_email = _alert_email()
    if not to_email:
        return {"ok": False, "error": "no_alert_email"}

    subject, html_body, text_body, count = build_launchpad_reminder_content()

    try:
        from sincor2.email_sender import get_email_sender

        sender = get_email_sender()
        if sender and getattr(sender, "mode", "stub") != "stub":
            result = sender.send_email(
                to_email=to_email,
                to_name="SINCOR Launch",
                subject=subject,
                html_content=html_body,
                text_content=text_body,
                metadata={"type": "launchpad_outreach_reminder", "due_count": count},
            )
            logger.info("[LAUNCHPAD_REMINDER] Sent to %s due=%s", to_email, count)
            return {"ok": True, "due": count, "email": to_email, "result": result}
        logger.info("[LAUNCHPAD_REMINDER] stub — %s due=%s", subject, count)
        return {"ok": True, "stub": True, "due": count, "subject": subject}
    except Exception as e:
        logger.error("[LAUNCHPAD_REMINDER] failed: %s", e)
        return {"ok": False, "error": str(e)}


def start_launchpad_reminder_scheduler(app=None):
    global _scheduler
    if os.environ.get("LAUNCHPAD_OUTREACH_ENABLED", "true").lower() != "true":
        logger.info("[LAUNCHPAD_REMINDER] Disabled — LAUNCHPAD_OUTREACH_ENABLED=false")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("[LAUNCHPAD_REMINDER] APScheduler not installed")
        return None

    hour = int(os.environ.get("LAUNCHPAD_OUTREACH_REMINDER_HOUR", "9"))
    minute = int(os.environ.get("LAUNCHPAD_OUTREACH_REMINDER_MINUTE", "0"))

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        send_launchpad_outreach_reminder,
        CronTrigger(hour=hour, minute=minute),
        id="launchpad_outreach_reminder",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "[LAUNCHPAD_REMINDER] Scheduled daily %02d:%02d → %s",
        hour,
        minute,
        _alert_email(),
    )
    return _scheduler


def stop_launchpad_reminder_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[LAUNCHPAD_REMINDER] Scheduler stopped")