from __future__ import annotations

"""Execution policy rules for safety, rate limiting, and budget controls."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class PolicyRule:
    """Represents a single execution policy rule."""

    name: str
    rule_type: str
    threshold: float
    description: str
    scope: str = 'global'


class ExecutionPolicy:
    """Applies lightweight execution rules to task requests."""

    def __init__(self, rules: Optional[List[PolicyRule]] = None) -> None:
        self.rules = rules or [
            PolicyRule(name='max-budget', rule_type='budget_cap', threshold=1000.0, description='Maximum budget per task.'),
            PolicyRule(name='max-retries', rule_type='retry_cap', threshold=3, description='Maximum retries per task.'),
            PolicyRule(name='max-requests-per-minute', rule_type='rate_limit', threshold=120, description='Maximum request rate.'),
        ]
        self.violations: List[Dict[str, Any]] = []
        self.counters: Dict[str, float] = {}

    def check_policy(self, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a task context against all configured rules."""
        violations = []
        budget = float(task_context.get('budget', 0.0))
        retries = float(task_context.get('retries', 0.0))
        request_rate = float(task_context.get('request_rate', 0.0))
        for rule in self.rules:
            current_value = {'budget_cap': budget, 'retry_cap': retries, 'rate_limit': request_rate}.get(rule.rule_type, 0.0)
            if current_value > rule.threshold:
                violations.append({'rule': rule.name, 'value': current_value, 'threshold': rule.threshold})
        return {'allowed': not violations, 'violations': violations}

    def apply_limits(self, key: str, increment: float = 1.0) -> float:
        """Increment and return a named runtime counter."""
        self.counters[key] = self.counters.get(key, 0.0) + increment
        return self.counters[key]

    def record_violation(self, rule_name: str, details: Dict[str, Any]) -> Dict[str, Any]:
        """Record a policy violation for later review."""
        violation = {
            'rule_name': rule_name,
            'details': details,
            'recorded_at': datetime.now(timezone.utc).isoformat(),
        }
        self.violations.append(violation)
        return violation

    def get_policy_summary(self) -> Dict[str, Any]:
        """Return configured rules and observed violations."""
        return {
            'rules': [asdict(rule) for rule in self.rules],
            'violation_count': len(self.violations),
            'counters': dict(self.counters),
        }
