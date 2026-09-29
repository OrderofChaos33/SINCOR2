"""P26 TOA recursive feedback loop.

Feeds the ranker's output back into the next allocation cycle as weight
adjustments: top-quartile protocols get a bounded boost, killed ticks go to
zero, unproven protocols are capped. Pure function of (rankings, kills) —
no hidden state, no invented signals.
"""

from __future__ import annotations

from typing import Dict, List

from .killswitch import KillDecision
from .ranker import RankEntry

BOOST_TOP_QUARTILE = 0.10   # +10% weight multiplier for top quartile
CAP_UNPROVEN = 0.05         # unproven protocols capped at 5% weight


def feedback(
    rankings: List[RankEntry],
    kills: List[KillDecision],
) -> Dict[str, float]:
    """Return protocol_id -> weight multiplier for the next cycle."""
    killed = {d.tick.protocol_id for d in kills if d.action == "KILL_TICK"}
    n = len(rankings)
    quartile = max(1, n // 4)
    multipliers: Dict[str, float] = {}
    for i, entry in enumerate(rankings):
        pid = entry.protocol_id
        if pid in killed:
            multipliers[pid] = 0.0
        elif entry.confidence == "unproven":
            multipliers[pid] = CAP_UNPROVEN
        elif i < quartile:
            multipliers[pid] = 1.0 + BOOST_TOP_QUARTILE
        else:
            multipliers[pid] = 1.0
    return multipliers
