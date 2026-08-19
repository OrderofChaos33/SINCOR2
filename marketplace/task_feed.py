"""Public task feed and posting surface for demand-side activation.

Agents (and humans) can post bounties / RFPs / recurring jobs.
New agents are preferentially matched for activation metrics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class TaskPosting:
    task_id: str
    title: str
    description: str
    required_skills: List[str]
    tags: List[str] = field(default_factory=list)
    bounty_axm: float = 0.0
    vertical: str = "general"  # healthcare, dental, trading, home-services, etc.
    status: str = "open"       # open | matched | in_progress | completed | cancelled
    poster: str = "anonymous"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    activation_priority: bool = False  # True for seed / new-agent activation tasks
    metadata: Dict[str, Any] = field(default_factory=dict)


class TaskFeed:
    """In-memory public task feed (replace with durable store in production)."""

    def __init__(self) -> None:
        self._tasks: Dict[str, TaskPosting] = {}

    def post(
        self,
        title: str,
        description: str,
        required_skills: List[str],
        bounty_axm: float = 0.0,
        tags: Optional[List[str]] = None,
        vertical: str = "general",
        poster: str = "anonymous",
        activation_priority: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskPosting:
        task = TaskPosting(
            task_id=f"task-{uuid4().hex[:12]}",
            title=title,
            description=description,
            required_skills=list(required_skills),
            tags=list(tags or []),
            bounty_axm=float(bounty_axm),
            vertical=vertical,
            poster=poster,
            activation_priority=activation_priority,
            metadata=dict(metadata or {}),
        )
        self._tasks[task.task_id] = task
        return task

    def list_open(
        self,
        vertical: Optional[str] = None,
        skill: Optional[str] = None,
        activation_only: bool = False,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        items = []
        for t in self._tasks.values():
            if t.status != "open":
                continue
            if vertical and t.vertical != vertical:
                continue
            if skill and skill.lower() not in [s.lower() for s in t.required_skills]:
                continue
            if activation_only and not t.activation_priority:
                continue
            items.append(asdict(t))
        # Prefer activation + higher bounty
        items.sort(key=lambda x: (-int(x["activation_priority"]), -x["bounty_axm"], x["created_at"]))
        return items[:limit]

    def seed_healthcare_rcm(self) -> List[TaskPosting]:
        """Seed high-ROI healthcare credentialing / RCM tasks for demand activation."""
        seeds = [
            {
                "title": "Provider credentialing packet review — CA multi-payer",
                "description": "Review and complete a standard credentialing packet for a multi-specialty group. Output structured readiness report + missing items list. HIPAA decision-support only.",
                "required_skills": ["credentialing", "healthcare-rcm", "provider-enrollment"],
                "tags": ["healthcare", "rcm", "credentialing"],
                "bounty_axm": 25.0,
                "vertical": "healthcare",
                "activation_priority": True,
            },
            {
                "title": "Denial root-cause analysis — sample of 50 claims",
                "description": "Analyze a de-identified sample of denied claims and produce top denial codes + recommended corrective actions. Decision support only.",
                "required_skills": ["denial-management", "healthcare-rcm", "claims"],
                "tags": ["healthcare", "rcm", "denials"],
                "bounty_axm": 40.0,
                "vertical": "healthcare",
                "activation_priority": True,
            },
            {
                "title": "Eligibility verification workflow design",
                "description": "Design a reusable eligibility verification flow for a mid-size practice. Include payer prioritization and exception handling. No PHI.",
                "required_skills": ["eligibility", "healthcare-rcm"],
                "tags": ["healthcare", "eligibility"],
                "bounty_axm": 15.0,
                "vertical": "healthcare",
                "activation_priority": True,
            },
            {
                "title": "Prior-auth checklist generation — PT/OT",
                "description": "Generate payer-agnostic prior-authorization checklist templates for outpatient PT/OT. Decision support.",
                "required_skills": ["prior-auth", "healthcare-rcm"],
                "tags": ["healthcare", "prior-auth", "pt"],
                "bounty_axm": 20.0,
                "vertical": "healthcare",
                "activation_priority": True,
            },
            {
                "title": "AR aging triage ruleset",
                "description": "Produce a ruleset that prioritizes AR follow-up by payer, age bucket, and dollar value. No PHI required.",
                "required_skills": ["ar-followup", "healthcare-rcm"],
                "tags": ["healthcare", "ar", "rcm"],
                "bounty_axm": 18.0,
                "vertical": "healthcare",
                "activation_priority": True,
            },
        ]
        created = []
        for s in seeds:
            created.append(self.post(**s, poster="sincor-seed"))
        return created
