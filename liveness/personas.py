#!/usr/bin/env python3
"""Opt-in bidding personas for the liveness cohort (sincor-liveness-01..06).

Each persona is a distinct bidding strategy: different bid fractions,
different skip/bid rules, different specialties.  Persona selection is
OPT-IN ONLY:

* The runner only consults personas when the environment variable
  ``SINCOR_LIVENESS_PERSONAS=1`` is set, AND the agent record carries a
  ``"persona"`` key matching one of :data:`PERSONA_IDS`.
* When disabled (the default) or when the agent has no recognised persona,
  the runner keeps its exact legacy behaviour: every eligible agent bids a
  uniform random 85-98% of bounty.  Production behaviour is never changed
  silently.

All decisions take an explicit ``random.Random`` instance so behaviour is
reproducible under test (seeded) and non-deterministic in production.

A decision returns a bid amount in AXM, or ``None`` to skip the task.
Money-path semantics (fee percentages, stake rules, slash rules) are NOT
touched here — personas only choose the bid price; every bid still passes
through the server's stake, auth, and anti-gaming gates unchanged.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional

# ---------------------------------------------------------------------------
# persona table
# ---------------------------------------------------------------------------

PERSONA_IDS = (
    "aggressive",      # sincor-liveness-01 — Apex Bidder
    "value_hunter",    # sincor-liveness-02 — Bargain Hunter
    "sniper",          # sincor-liveness-03 — Deadline Sniper
    "conservative",    # sincor-liveness-04 — Cautious Keeper
    "specialist",      # sincor-liveness-05 — Domain Specialist
    "contrarian",      # sincor-liveness-06 — Contrarian Undercutter
)


@dataclass(frozen=True)
class PersonaConfig:
    """A named bidding strategy.

    * ``bid_fraction_lo/hi`` — bid as a fraction of the public bounty.
    * ``min_bounty_axm`` — skip tasks whose bounty is below this.
    * ``max_bounty_axm`` — skip tasks whose bounty is above this (risk cap).
    * ``specialty_tags`` — when set, only bid tasks whose tags overlap.
    * ``skips_merit_tasks`` — conservative personas sit out merit-gated tasks.
    * ``snipe_only`` — only bid inside the last ``snipe_tail_fraction`` of
      the commit window (Deadline Sniper).
    * ``random_skip_prob`` — baseline probability of sitting a task out.
    """

    id: str
    name: str
    description: str
    bid_fraction_lo: float
    bid_fraction_hi: float
    min_bounty_axm: float = 0.0
    max_bounty_axm: float = float("inf")
    specialty_tags: FrozenSet[str] = frozenset()
    skips_merit_tasks: bool = False
    snipe_only: bool = False
    snipe_tail_fraction: float = 0.10
    random_skip_prob: float = 0.0

    def decide(
        self,
        task: Dict,
        bounty_axm: float,
        now_ms: int,
        rng: random.Random,
    ) -> Optional[float]:
        """Return a bid amount (AXM) or None to skip. Deterministic for a
        fixed (task, bounty, now, rng-state) tuple."""
        if bounty_axm <= 0:
            return None
        if bounty_axm < self.min_bounty_axm:
            return None
        if bounty_axm > self.max_bounty_axm:
            return None
        if self.specialty_tags:
            task_tags = set(task.get("tags") or [])
            if not (self.specialty_tags & task_tags):
                return None
        if self.skips_merit_tasks and task.get("requires_merit"):
            return None
        if self.snipe_only:
            cd = task.get("commit_deadline")
            created = task.get("created_at")
            if cd and created:
                window = int(cd) - int(created)
                remaining = int(cd) - int(now_ms)
                if window > 0 and remaining > window * self.snipe_tail_fraction:
                    return None  # too early — wait for the tail of the window
            # unknown window: stay out rather than guess (fail safe)
            else:
                return None
        if self.random_skip_prob > 0 and rng.random() < self.random_skip_prob:
            return None
        frac = rng.uniform(self.bid_fraction_lo, self.bid_fraction_hi)
        return round(bounty_axm * frac, 6)


def _cfg(**kw) -> PersonaConfig:
    return PersonaConfig(**kw)


PERSONAS: Dict[str, PersonaConfig] = {
    # 01 — Apex Bidder: bids everything, bids high.  Wins often, pays near
    # full bounty.  The cohort's price-setter.
    "aggressive": _cfg(
        id="aggressive",
        name="Apex Bidder",
        description="Bids on every eligible task at 94-99.5% of bounty.",
        bid_fraction_lo=0.94,
        bid_fraction_hi=0.995,
    ),
    # 02 — Bargain Hunter: only chases bounties worth the effort and bids
    # well under, hunting margin.
    "value_hunter": _cfg(
        id="value_hunter",
        name="Bargain Hunter",
        description="Skips bounties under 2 AXM; bids 78-87% hunting margin.",
        bid_fraction_lo=0.78,
        bid_fraction_hi=0.87,
        min_bounty_axm=2.0,
    ),
    # 03 — Deadline Sniper: waits for the last 10% of the commit window,
    # then strikes.  Minimises exposure time; misses if the window slips.
    "sniper": _cfg(
        id="sniper",
        name="Deadline Sniper",
        description="Bids only inside the last 10% of the commit window.",
        bid_fraction_lo=0.90,
        bid_fraction_hi=0.96,
        snipe_only=True,
        snipe_tail_fraction=0.10,
    ),
    # 04 — Cautious Keeper: small risk envelope, avoids merit-gated tasks,
    # and sits out a third of the time on principle.
    "conservative": _cfg(
        id="conservative",
        name="Cautious Keeper",
        description="Bids 85-90%; skips merit tasks and bounties over 5 AXM.",
        bid_fraction_lo=0.85,
        bid_fraction_hi=0.90,
        max_bounty_axm=5.0,
        skips_merit_tasks=True,
        random_skip_prob=0.33,
    ),
    # 05 — Domain Specialist: only DeFi/forecasting/risk work; deep but
    # narrow.  Out-of-specialty tasks are never touched.
    "specialist": _cfg(
        id="specialist",
        name="Domain Specialist",
        description="Bids only defi/forecasting/risk tasks at 88-93%.",
        bid_fraction_lo=0.88,
        bid_fraction_hi=0.93,
        specialty_tags=frozenset({"defi", "forecasting", "risk"}),
    ),
    # 06 — Contrarian Undercutter: flips a coin; when it bids it undercuts
    # the herd hard (70-80%), betting on thin competition.
    "contrarian": _cfg(
        id="contrarian",
        name="Contrarian Undercutter",
        description="Sits out half the time; bids 70-80% when it plays.",
        bid_fraction_lo=0.70,
        bid_fraction_hi=0.80,
        random_skip_prob=0.50,
    ),
}


def get_persona(persona_id: str) -> Optional[PersonaConfig]:
    """Look up a persona config by id; None when unknown."""
    return PERSONAS.get(str(persona_id or "").strip().lower())


def persona_bid_amount(
    agent: Dict,
    task: Dict,
    bounty_axm: float,
    now_ms: int,
    rng: random.Random,
) -> Optional[float]:
    """Opt-in persona bid decision for one agent on one task.

    Returns None when the agent has no recognised persona (caller falls back
    to legacy behaviour) or when the persona chooses to skip.
    """
    persona = get_persona(agent.get("persona") or "")
    if persona is None:
        return None
    return persona.decide(task, bounty_axm, now_ms, rng)


def enabled() -> bool:
    """Persona bidding is enabled only via explicit operator opt-in."""
    import os

    return os.environ.get("SINCOR_LIVENESS_PERSONAS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
