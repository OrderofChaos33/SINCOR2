"""EigenTrust / PageRank weighting for peer feedback.

Raw 10/10 clique ratings cannot inflate career rank: local trust is
row-normalized and global trust is pulled toward a pre-trusted prior
(the auditor set). Agents with no path from a pre-trusted node converge
near zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set, Tuple

DAMPING = 0.15
MAX_ITERS = 64
TOL = 1e-10


@dataclass(frozen=True)
class Rating:
    rater: str
    ratee: str
    score: float  # 0–10
    task_id: str
    independent: bool = False  # True when issued by a verified auditor / honeypot


def _agents(ratings: Sequence[Rating], extra: Iterable[str] = ()) -> List[str]:
    names = set(extra)
    for rating in ratings:
        names.add(rating.rater)
        names.add(rating.ratee)
    return sorted(names)


class EigenTrust:
    def __init__(
        self,
        pretrusted: Sequence[str],
        damping: float = DAMPING,
    ) -> None:
        if not pretrusted:
            raise ValueError("pretrusted set must be non-empty")
        if not (0.0 <= damping <= 1.0):
            raise ValueError("damping must be in [0, 1]")
        self.pretrusted: Set[str] = set(pretrusted)
        self.damping = damping
        self.ratings: List[Rating] = []

    def add_rating(self, rating: Rating) -> None:
        if rating.rater == rating.ratee:
            raise ValueError("self-rating is forbidden")
        if not (0.0 <= rating.score <= 10.0):
            raise ValueError("score must be in [0, 10]")
        self.ratings.append(rating)

    def raw_average(self, *, peer_only: bool = True) -> Dict[str, float]:
        totals: Dict[str, List[float]] = {}
        for rating in self.ratings:
            if peer_only and rating.independent:
                continue
            totals.setdefault(rating.ratee, []).append(rating.score)
        return {agent: sum(vals) / len(vals) for agent, vals in totals.items()}

    def compute(self) -> Dict[str, float]:
        agents = _agents(self.ratings, self.pretrusted)
        n = len(agents)
        index = {name: i for i, name in enumerate(agents)}
        # Local trust C[i][j] = normalized weight i → j
        weights = [[0.0] * n for _ in range(n)]
        for rating in self.ratings:
            # Independent auditor ratings carry full weight; peer ratings
            # are linearly scaled. Negative evidence (score < 5) is dropped
            # from local trust and applied as a later penalty.
            if rating.score <= 0:
                continue
            scale = 1.0 if rating.independent else rating.score / 10.0
            if rating.score < 5.0 and not rating.independent:
                continue
            i = index[rating.rater]
            j = index[rating.ratee]
            weights[i][j] += scale * rating.score
        C = [[0.0] * n for _ in range(n)]
        pre_idx = [index[name] for name in agents if name in self.pretrusted]
        share = 1.0 / len(pre_idx)
        for i in range(n):
            row_sum = sum(weights[i])
            if row_sum > 0:
                C[i] = [w / row_sum for w in weights[i]]
            else:
                for j in pre_idx:
                    C[i][j] = share
        p = [share if name in self.pretrusted else 0.0 for name in agents]
        t = p[:]
        a = self.damping
        for _ in range(MAX_ITERS):
            nxt = [0.0] * n
            for j in range(n):
                acc = 0.0
                for i in range(n):
                    acc += C[i][j] * t[i]
                nxt[j] = (1.0 - a) * acc + a * p[j]
            delta = sum(abs(nxt[k] - t[k]) for k in range(n))
            t = nxt
            if delta < TOL:
                break
        # Apply independent auditor penalties (honeypot fails).
        penalty = {name: 1.0 for name in agents}
        for rating in self.ratings:
            if rating.independent and rating.score <= 1.0:
                penalty[rating.ratee] *= 0.15
        out = {agents[i]: t[i] * penalty[agents[i]] for i in range(n)}
        total = sum(out.values()) or 1.0
        return {k: v / total for k, v in out.items()}
