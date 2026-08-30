"""Auditor agents that issue honeypot tasks with known reference answers.

Passing a honeypot is independently verified evidence. Failing it is a
hard integrity penalty — speed and cheap tokens cannot fake the answer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from .eigentrust import EigenTrust, Rating


@dataclass(frozen=True)
class HoneypotTask:
    task_id: str
    prompt: str
    answer: str
    skill: str


@dataclass
class HoneypotResult:
    task_id: str
    agent_id: str
    auditor_id: str
    submitted: str
    expected: str
    passed: bool
    reason: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


DEFAULT_TASKS: List[HoneypotTask] = [
    HoneypotTask(
        task_id="hp-treasury",
        prompt="Return the canonical SINCOR treasury address on Base.",
        answer="0x09e2891432827d8835d2e9b83b25e2a5ba9612ac",
        skill="settlement",
    ),
    HoneypotTask(
        task_id="hp-vickrey",
        prompt="In a reverse Vickrey auction the winner is paid which price?",
        answer="second",
        skill="market",
    ),
    HoneypotTask(
        task_id="hp-decay",
        prompt="Ebbinghaus retrieval score is similarity times what function of age?",
        answer="exp(-lambda t)",
        skill="memory",
    ),
    HoneypotTask(
        task_id="hp-chain",
        prompt="Numeric chain id of Base mainnet.",
        answer="8453",
        skill="settlement",
    ),
]


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower().strip() if ch.isalnum() or ch in "-")


class HoneypotAuditor:
    def __init__(
        self,
        auditor_id: str,
        trust: EigenTrust,
        tasks: Optional[List[HoneypotTask]] = None,
    ) -> None:
        self.auditor_id = auditor_id
        self.trust = trust
        self.tasks = {task.task_id: task for task in (tasks or DEFAULT_TASKS)}
        self.results: List[HoneypotResult] = []

    def evaluate(self, agent_id: str, task_id: str, submitted: str) -> HoneypotResult:
        task = self.tasks[task_id]
        expected = _normalize(task.answer)
        got = _normalize(submitted)
        passed = got == expected or expected in got
        result = HoneypotResult(
            task_id=task_id,
            agent_id=agent_id,
            auditor_id=self.auditor_id,
            submitted=submitted,
            expected=task.answer,
            passed=passed,
            reason="match" if passed else "reference_mismatch",
        )
        self.results.append(result)
        self.trust.add_rating(
            Rating(
                rater=self.auditor_id,
                ratee=agent_id,
                score=10.0 if passed else 0.0,
                task_id=task_id,
                independent=True,
            )
        )
        return result
