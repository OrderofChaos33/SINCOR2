"""Merit engine: EigenTrust ranks + honeypot auditors.

Does not replace ``marketplace.reputation.ReputationEngine``. Peer feedback
here is an overlay used for anti-gaming; EMA reputation stays the routing
prior for first-price auctions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Sequence

from .eigentrust import EigenTrust, Rating
from .honeypot import DEFAULT_TASKS, HoneypotAuditor, HoneypotResult, HoneypotTask


@dataclass
class RankRow:
    agent_id: str
    eigentrust: float
    raw_average: float
    honeypot_passes: int
    honeypot_fails: int
    peer_ratings: int
    sybil_suspect: bool

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class MeritEngine:
    def __init__(self, auditor_id: str = "auditor-lynx") -> None:
        self.auditor_id = auditor_id
        self.trust = EigenTrust(pretrusted=(auditor_id,))
        self.auditor = HoneypotAuditor(auditor_id, self.trust)

    def rate(
        self,
        rater: str,
        ratee: str,
        score: float,
        task_id: str,
        *,
        independent: bool = False,
    ) -> None:
        self.trust.add_rating(
            Rating(
                rater=rater,
                ratee=ratee,
                score=score,
                task_id=task_id,
                independent=independent,
            )
        )

    def honeypot(self, agent_id: str, task_id: str, submitted: str) -> HoneypotResult:
        return self.auditor.evaluate(agent_id, task_id, submitted)

    def leaderboard(self) -> List[RankRow]:
        global_t = self.trust.compute()
        raw = self.trust.raw_average()
        passes: Dict[str, int] = {}
        fails: Dict[str, int] = {}
        for result in self.auditor.results:
            if result.passed:
                passes[result.agent_id] = passes.get(result.agent_id, 0) + 1
            else:
                fails[result.agent_id] = fails.get(result.agent_id, 0) + 1
        peer_counts: Dict[str, int] = {}
        for rating in self.trust.ratings:
            if not rating.independent:
                peer_counts[rating.ratee] = peer_counts.get(rating.ratee, 0) + 1
        rows: List[RankRow] = []
        for agent, score in global_t.items():
            if agent == self.auditor_id:
                continue
            hp_fail = fails.get(agent, 0)
            peer = peer_counts.get(agent, 0)
            raw_avg = raw.get(agent, 0.0)
            # Clique heuristic: many peer 10s, no independent passes, low global.
            sybil = peer >= 3 and passes.get(agent, 0) == 0 and score < 0.08
            rows.append(
                RankRow(
                    agent_id=agent,
                    eigentrust=score,
                    raw_average=raw_avg,
                    honeypot_passes=passes.get(agent, 0),
                    honeypot_fails=hp_fail,
                    peer_ratings=peer,
                    sybil_suspect=sybil,
                )
            )
        rows.sort(key=lambda r: (-r.eigentrust, r.agent_id))
        return rows

    def honeypot_catalog(self) -> Sequence[HoneypotTask]:
        return DEFAULT_TASKS
