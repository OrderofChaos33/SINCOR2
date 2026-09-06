"""CHROMA — auto detailing vertical agent (production A2A entry)."""

from __future__ import annotations

from typing import Any, Dict

from ..agent import VerticalAgent
from .booking_agent import DetailingBookingAgent
from .copy_agent import DetailingCopyAgent
from .engagement_agent import DetailingEngagementAgent
from .lead_agent import DetailingLeadAgent
from .outreach_agent import DetailingOutreachAgent
from .presence_agent import DetailingPresenceAgent
from .schemas import TaskInput, TaskOutput
from .social_agent import DetailingSocialAgent


class AutoDetailingAgent(VerticalAgent):
    name = "auto_detailing_agent"
    version = "1.0.0"
    description = (
        "Autonomous growth OS for auto detailing shops: lead aggregation, "
        "intent scoring, instant quotes, Calendly self-booking, website SEO / "
        "metadata / gallery / reviews, landing engagement, social autopilot, "
        "and email/SMS outreach that steers to a booked bay."
    )
    capabilities = [
        "lead_ingest",
        "lead_score",
        "quote_estimate",
        "calendly_handoff",
        "ad_copy",
        "seo_optimize",
        "metadata_optimize",
        "gallery_curate",
        "review_showcase",
        "review_reply",
        "landing_engage",
        "social_schedule",
        "social_engage",
        "email_outreach",
        "membership_nudge",
        "missed_call_textback",
        "photo_quote",
        "seasonal_campaign",
    ]
    tags = ["auto_detailing", "detailing", "ceramic", "ppf", "booking", "local_seo", "chroma"]

    def __init__(self) -> None:
        super().__init__()
        self.leads = DetailingLeadAgent()
        self.booking = DetailingBookingAgent()
        self.copy = DetailingCopyAgent()
        self.presence = DetailingPresenceAgent()
        self.social = DetailingSocialAgent()
        self.engage_agent = DetailingEngagementAgent()
        self.outreach = DetailingOutreachAgent()

    def execute(self, task: dict) -> dict:  # type: ignore[override]
        task_input = TaskInput.model_validate(task)
        task_type = task_input.task_type
        payload: Dict[str, Any] = dict(task_input.payload or {})
        cid = task_input.correlation_id

        # Skill-id aliases from A2A dispatch.
        aliases = {
            "detailing-lead-ingest": "lead_ingest",
            "detailing-booking": "calendly_handoff",
            "detailing-presence": "seo_optimize",
            "detailing-social": "social_schedule",
            "detailing-copy": "ad_copy",
            "detailing-engage": "landing_engage",
        }
        task_type = aliases.get(task_type, task_type)

        try:
            result = self._route(task_type, payload)
            return TaskOutput(status="success", result=result, correlation_id=cid).model_dump()
        except ValueError as exc:
            return TaskOutput(
                status="error", result={}, error=str(exc), correlation_id=cid
            ).model_dump()

    def _route(self, task_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if task_type in {"lead_ingest", "photo_quote"}:
            return self.leads.ingest(payload)
        if task_type in {"lead_score", "lead_qualify"}:
            return self.leads.score_batch(payload)
        if task_type == "quote_estimate":
            return self.booking.quote(payload)
        if task_type in {"calendly_handoff", "booking"}:
            return self.booking.calendly_handoff(payload)
        if task_type in {"ad_copy", "copy"}:
            return self.copy.generate(payload)
        if task_type in {"seo_optimize", "metadata_optimize", "gallery_curate", "review_showcase"}:
            return self.presence.optimize(payload)
        if task_type == "review_reply":
            return self.presence.review_reply(payload)
        if task_type in {"landing_engage", "engage"}:
            return self.engage_agent.engage(payload)
        if task_type == "missed_call_textback":
            return self.engage_agent.missed_call(payload)
        if task_type == "social_schedule":
            return self.social.schedule(payload)
        if task_type == "social_engage":
            return self.social.engage(payload)
        if task_type == "seasonal_campaign":
            return self.social.seasonal(payload)
        if task_type in {"email_outreach", "membership_nudge"}:
            if task_type == "membership_nudge":
                payload = {**payload, "kind": "membership_nudge"}
            return self.outreach.sequence(payload)
        raise ValueError(f"Unsupported detailing task: {task_type}")
