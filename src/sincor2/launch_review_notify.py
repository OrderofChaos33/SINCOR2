"""
Daily email reminder — pending launch drafts need ~5 min approval at /launch/review.
"""

from __future__ import annotations

import html
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("sincor2.launch_review_notify")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ALERT_EMAIL = "eenergy@protonmail.com"
_scheduler = None


def _review_url() -> str:
    base = os.environ.get("APP_BASE_URL", "https://getsincor.com").rstrip("/")
    return f"{base}/launch/review"


def _alert_email() -> str:
    return os.environ.get("LAUNCH_REVIEW_ALERT_EMAIL", DEFAULT_ALERT_EMAIL).strip()


def _pending_drafts() -> list[dict]:
    root = str(_PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from launch_content_engine.review_queue import list_drafts

    return list_drafts(status="pending", limit=20)


def build_reminder_content() -> tuple[str, str, str, int]:
    """Returns subject, html, text, pending_count."""
    drafts = _pending_drafts()
    count = len(drafts)
    url = _review_url()

    if count:
        subject = f"SINCOR: {count} draft(s) ready — ~5 min review"
        headline = f"{count} draft(s) waiting for your approval"
        lead = "Agents drafted overnight content. Approve or reject — Farcaster posts when you approve (if Neynar keys are set)."
    else:
        subject = "SINCOR: launch review queue clear"
        headline = "No pending drafts — you're caught up"
        lead = "Nothing in the queue right now. New drafts arrive after the daily content cycle."

    items_html = ""
    items_text = ""
    for d in drafts[:8]:
        title = html.escape(d.get("title") or "(no title)")
        pipeline = html.escape(d.get("pipeline", ""))
        channel = html.escape(d.get("channel", ""))
        preview = html.escape((d.get("body") or "")[:120].replace("\n", " "))
        items_html += (
            f"<li><strong>{title}</strong> "
            f"<span style='color:#6b7280'>({pipeline} · {channel})</span><br>"
            f"<span style='font-size:13px;color:#9ca3af'>{preview}…</span></li>"
        )
        items_text += f"- {d.get('title') or '(no title)'} ({pipeline} · {channel})\n"

    html_body = f"""
<div style="font-family:Inter,system-ui,sans-serif;max-width:560px;color:#111827">
  <p style="color:#6b7280;font-size:13px">SINCOR launch ops</p>
  <h2 style="margin:0 0 12px">{headline}</h2>
  <p style="line-height:1.5">{lead}</p>
  {"<ul style='padding-left:18px;line-height:1.6'>" + items_html + "</ul>" if items_html else ""}
  <p style="margin:24px 0">
    <a href="{url}" style="display:inline-block;background:#22d3ee;color:#042f2e;
      padding:12px 18px;border-radius:10px;text-decoration:none;font-weight:700">
      Open review queue
    </a>
  </p>
  <p style="font-size:12px;color:#9ca3af">
    Also: <a href="{url.replace('/launch/review', '/sinc')}">/sinc</a> ·
    <a href="{url.replace('/launch/review', '/refer')}">/refer</a>
  </p>
</div>
"""
    text_body = (
        f"{headline}\n\n{lead}\n\n"
        f"{items_text}\n"
        f"Review: {url}\n"
    )
    return subject, html_body, text_body, count


def send_launch_review_reminder() -> dict:
    """Email daily approval reminder. Works with Resend/SendGrid or logs in stub mode."""
    to_email = _alert_email()
    if not to_email:
        return {"ok": False, "error": "no_alert_email"}

    subject, html_body, text_body, count = build_reminder_content()

    try:
        from sincor2.email_sender import get_email_sender

        sender = get_email_sender()
        result = sender.send_email(
            to_email=to_email,
            to_name=os.environ.get("LAUNCH_REVIEW_ALERT_NAME", "SINCOR Ops"),
            subject=subject,
            html_content=html_body,
            text_content=text_body,
            metadata={"type": "launch_review_reminder", "pending_count": count},
        )
        status = result.get("status", "unknown")
        if status == "sent":
            logger.info("[REVIEW_REMINDER] Sent to %s (%s pending)", to_email, count)
            return {"ok": True, "to": to_email, "pending": count, "provider": result.get("provider")}
        if status == "stub":
            logger.warning(
                "[REVIEW_REMINDER] Stub mode — set RESEND_API_KEY. %s pending → %s",
                count,
                to_email,
            )
            log_dir = _PROJECT_ROOT / "logs" / "ops"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "review_reminder_latest.txt").write_text(text_body, encoding="utf-8")
            return {"ok": True, "mode": "stub", "to": to_email, "pending": count}
        return {"ok": False, "error": result.get("error"), "to": to_email}
    except Exception as e:
        logger.error("[REVIEW_REMINDER] Failed: %s", e, exc_info=True)
        return {"ok": False, "error": str(e)}


def start_review_reminder_scheduler(app=None):
    """Daily cron reminder (default 9:15 AM America/Chicago)."""
    global _scheduler

    if os.environ.get("LAUNCH_REVIEW_REMINDER_ENABLED", "true").lower() == "false":
        logger.info("[REVIEW_REMINDER] Disabled (LAUNCH_REVIEW_REMINDER_ENABLED=false)")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("[REVIEW_REMINDER] APScheduler not installed")
        return None

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    hour = int(os.environ.get("LAUNCH_REVIEW_REMINDER_HOUR", "9"))
    minute = int(os.environ.get("LAUNCH_REVIEW_REMINDER_MINUTE", "15"))
    tz = os.environ.get("LAUNCH_OPS_TIMEZONE", "America/Chicago")

    _scheduler = BackgroundScheduler(timezone=tz)
    _scheduler.add_job(
        send_launch_review_reminder,
        trigger=CronTrigger(hour=hour, minute=minute),
        id="launch_review_reminder",
        name="SINCOR Launch Review Daily Reminder",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info(
        "[REVIEW_REMINDER] Scheduled daily %02d:%02d %s → %s",
        hour,
        minute,
        tz,
        _alert_email(),
    )
    return _scheduler


def stop_review_reminder_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[REVIEW_REMINDER] Scheduler stopped")