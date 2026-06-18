"""
Daily partner outreach reminder — due KOLs/curators for July 7 launch.
Runs on Railway (PARTNER_OUTREACH_ENABLED) or Windows scheduled task.
"""

from __future__ import annotations

import html
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("sincor2.partner_outreach_notify")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ALERT_EMAIL = "court@getsincor.com"
_scheduler = None


def _partners_url() -> str:
    base = os.environ.get("APP_BASE_URL", "https://getsincor.com").rstrip("/")
    return f"{base}/launch/partners"


def _alert_email() -> str:
    return (
        os.environ.get("PARTNER_OUTREACH_ALERT_EMAIL")
        or os.environ.get("LAUNCH_REVIEW_ALERT_EMAIL", DEFAULT_ALERT_EMAIL)
    ).strip()


def build_partner_reminder_content() -> tuple[str, str, str, int]:
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from sincor2.partner_outreach import due_outreach, pipeline_summary

    summary = pipeline_summary()
    due = due_outreach(limit=8)
    count = len(due)
    url = _partners_url()
    days = summary.get("days_to_launch", "?")
    partnered = summary.get("by_status", {}).get("partnered", 0)

    if count:
        subject = f"SINCOR partners: {count} outreach due ({days}d to launch)"
        headline = f"{count} partner(s) need outreach today"
        lead = (
            f"Launch {summary.get('launch_date')} · "
            f"{partnered} partnered · "
            f"{summary.get('by_status', {}).get('contacted', 0)} contacted. "
            "Copy DMs from the pipeline, send, then mark contacted."
        )
    else:
        subject = f"SINCOR partners: caught up ({days}d to launch)"
        headline = "No partner outreach due today"
        lead = "Follow up manually on warm leads or check /launch/partners."

    items_html = ""
    items_text = ""
    for p in due:
        name = html.escape(p.get("name", ""))
        tier = html.escape(p.get("tier", ""))
        method = html.escape(p.get("contact_method", ""))
        contact = html.escape(p.get("contact_url", ""))
        pid = html.escape(p.get("id", ""))
        items_html += (
            f"<li><strong>{name}</strong> ({tier}) — {method}<br>"
            f"<a href='{contact}'>{contact}</a><br>"
            f"<span style='font-size:12px;color:#6b7280'>id: {pid}</span></li>"
        )
        items_text += f"- {p.get('name')} [{p.get('tier')}] {method} {contact}\n"

    html_body = f"""
<div style="font-family:Inter,system-ui,sans-serif;max-width:560px;color:#111827">
  <p style="color:#6b7280;font-size:13px">SINCOR launch partners</p>
  <h2 style="margin:0 0 12px">{headline}</h2>
  <p style="line-height:1.5">{lead}</p>
  {"<ul style='padding-left:18px;line-height:1.6'>" + items_html + "</ul>" if items_html else ""}
  <p style="margin:24px 0">
    <a href="{url}" style="display:inline-block;background:#8b5cf6;color:#fff;
      padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">
      Open partner pipeline
    </a>
  </p>
</div>
"""
    text_body = f"{headline}\n\n{lead}\n\n{items_text}\nPipeline: {url}\n"
    return subject, html_body, text_body, count


def send_partner_outreach_reminder() -> dict:
    to_email = _alert_email()
    if not to_email:
        return {"ok": False, "error": "no_alert_email"}

    subject, html_body, text_body, count = build_partner_reminder_content()

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
                metadata={"type": "partner_outreach_reminder", "due_count": count},
            )
            logger.info("[PARTNER_REMINDER] Sent to %s due=%s", to_email, count)
            return {"ok": True, "due": count, "email": to_email, "result": result}
        logger.info("[PARTNER_REMINDER] stub — %s due=%s", subject, count)
        return {"ok": True, "stub": True, "due": count, "subject": subject}
    except Exception as e:
        logger.error("[PARTNER_REMINDER] failed: %s", e)
        return {"ok": False, "error": str(e)}


def start_partner_reminder_scheduler(app=None):
    global _scheduler
    if os.environ.get("PARTNER_OUTREACH_ENABLED", "true").lower() != "true":
        logger.info("[PARTNER_REMINDER] Disabled — PARTNER_OUTREACH_ENABLED=false")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("[PARTNER_REMINDER] APScheduler not installed")
        return None

    hour = int(os.environ.get("PARTNER_OUTREACH_REMINDER_HOUR", "8"))
    minute = int(os.environ.get("PARTNER_OUTREACH_REMINDER_MINUTE", "30"))

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        send_partner_outreach_reminder,
        CronTrigger(hour=hour, minute=minute),
        id="partner_outreach_reminder",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "[PARTNER_REMINDER] Scheduled daily %02d:%02d → %s",
        hour, minute, _alert_email(),
    )
    return _scheduler


def stop_partner_reminder_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[PARTNER_REMINDER] Scheduler stopped")