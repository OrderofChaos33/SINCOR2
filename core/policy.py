from __future__ import annotations

"""Execution policy rules, middleware chain, and settlement triggers.

Extended from the original lightweight policy rules to include:
- Configurable pre/post execution middleware (auth, SINC balance, schema
  validation, AXIOM settlement).
- Retry-with-backoff enforcement via :class:`PolicyEnforcedExecutor`.
- Timeout enforcement (cooperative, raises :exc:`PolicyTimeoutError`).
- Schema validation hook (accepts any callable or Pydantic model).
- Settlement trigger hook called after successful execution.
"""

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger("sincor.core.policy")


@dataclass
class PolicyRule:
    """Represents a single execution policy rule."""

    name: str
    rule_type: str
    threshold: float
    description: str
    scope: str = 'global'


class PolicyViolationError(RuntimeError):
    """Raised when task context violates a configured policy rule."""


class PolicyTimeoutError(PolicyViolationError):
    """Raised when task execution exceeds the configured timeout."""


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


# ---------------------------------------------------------------------------
# Middleware primitives
# ---------------------------------------------------------------------------

class MiddlewareContext:
    """Carries mutable state through the pre/post middleware chain."""

    def __init__(self, task: Dict[str, Any]) -> None:
        self.task = dict(task)
        self.caller_id: str = task.get('caller_id', '')
        self.sinc_balance: float = float(task.get('sinc_balance', 0.0))
        self.allowed: bool = True
        self.rejection_reason: str = ''
        self.settlement_triggered: bool = False
        self.metadata: Dict[str, Any] = {}


def auth_middleware(ctx: MiddlewareContext) -> None:
    """Pre-execution: verify caller has a non-empty identity."""
    if not ctx.caller_id:
        ctx.allowed = False
        ctx.rejection_reason = 'auth_failed: missing caller_id'
        logger.warning("Policy auth_middleware: missing caller_id")


def sinc_balance_middleware(
    min_sinc_balance: float = 0.0,
) -> Callable[[MiddlewareContext], None]:
    """Pre-execution factory: enforce a minimum SINC token balance.

    Parameters
    ----------
    min_sinc_balance:
        Minimum SINC balance required to proceed (default 0, i.e. free tasks
        pass).  Set > 0 to require SINC staking for premium skills.
    """
    def _check(ctx: MiddlewareContext) -> None:
        if ctx.sinc_balance < min_sinc_balance:
            ctx.allowed = False
            ctx.rejection_reason = (
                f'sinc_balance_check_failed: balance {ctx.sinc_balance} < '
                f'required {min_sinc_balance}'
            )
            logger.warning(
                "Policy sinc_balance_middleware: insufficient balance %.4f < %.4f",
                ctx.sinc_balance,
                min_sinc_balance,
            )
    return _check


def schema_validation_middleware(
    schema: Any,
) -> Callable[[MiddlewareContext], None]:
    """Post-execution: validate the result dict against a Pydantic model or callable.

    Parameters
    ----------
    schema:
        A Pydantic ``BaseModel`` subclass *or* any callable that accepts the
        result dict and raises on invalid input.
    """
    def _validate(ctx: MiddlewareContext) -> None:
        result = ctx.metadata.get('result')
        if result is None:
            return
        try:
            if hasattr(schema, 'model_validate'):
                schema.model_validate(result)
            elif callable(schema):
                schema(result)
        except Exception as exc:
            ctx.allowed = False
            ctx.rejection_reason = f'schema_validation_failed: {exc}'
            logger.warning("Policy schema_validation_middleware: %s", exc)
    return _validate


def axiom_settlement_middleware(
    settlement_hook: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Callable[[MiddlewareContext], None]:
    """Post-execution: trigger AXIOM settlement for completed tasks.

    Parameters
    ----------
    settlement_hook:
        Optional callable invoked with the task dict after successful execution.
        When omitted, a log entry is emitted instead (useful for development).
    """
    def _settle(ctx: MiddlewareContext) -> None:
        if not ctx.allowed:
            return
        task = ctx.task
        if settlement_hook:
            try:
                settlement_hook(task)
                ctx.settlement_triggered = True
                logger.info(
                    "Policy axiom_settlement_middleware: settlement triggered for task %s",
                    task.get('task_id', 'unknown'),
                )
            except Exception as exc:
                logger.error(
                    "Policy axiom_settlement_middleware: settlement failed: %s", exc
                )
        else:
            logger.info(
                "Policy axiom_settlement_middleware: settlement hook not configured "
                "(task=%s). Configure AXIOM_SETTLEMENT_HOOK for production.",
                task.get('task_id', 'unknown'),
            )
    return _settle


# ---------------------------------------------------------------------------
# Policy-enforced executor
# ---------------------------------------------------------------------------

class PolicyEnforcedExecutor:
    """Wraps task handlers with retry, timeout, pre/post middleware, and policy checks.

    Parameters
    ----------
    policy:
        :class:`ExecutionPolicy` instance for rule checks.
    pre_middleware:
        List of callables ``(MiddlewareContext) -> None`` run before execution.
    post_middleware:
        List of callables ``(MiddlewareContext) -> None`` run after execution.
    max_retries:
        Number of retry attempts on transient failure (default 3).
    timeout_seconds:
        Maximum seconds per attempt; raises :exc:`PolicyTimeoutError` if
        exceeded (cooperative check — requires the handler to be fast).
    base_delay:
        Initial backoff delay in seconds (doubles on each retry).
    fallback_handler:
        Optional callable invoked when all retries are exhausted.
    """

    def __init__(
        self,
        policy: Optional[ExecutionPolicy] = None,
        pre_middleware: Optional[List[Callable[[MiddlewareContext], None]]] = None,
        post_middleware: Optional[List[Callable[[MiddlewareContext], None]]] = None,
        max_retries: int = 3,
        timeout_seconds: float = 30.0,
        base_delay: float = 0.2,
        fallback_handler: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        self.policy = policy or ExecutionPolicy()
        # Default: no pre_middleware — auth is enforced at the A2A transport layer.
        # Pass an explicit list (e.g. [auth_middleware]) to activate it here.
        self.pre_middleware = pre_middleware if pre_middleware is not None else []
        self.post_middleware = post_middleware or []
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.base_delay = base_delay
        self.fallback_handler = fallback_handler

    def execute(
        self,
        handler: Callable[[Dict[str, Any]], Dict[str, Any]],
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run *handler* with the full middleware chain and retry logic.

        Parameters
        ----------
        handler:
            Callable that receives the task dict and returns a result dict.
        task:
            Task context dict passed to pre-middleware and the handler.

        Returns
        -------
        dict
            Result dict from the handler, or an error dict if all retries fail.
        """
        ctx = MiddlewareContext(task)

        # --- Pre-execution middleware ---
        for mw in self.pre_middleware:
            mw(ctx)
            if not ctx.allowed:
                self.policy.record_violation('pre_middleware', {
                    'reason': ctx.rejection_reason,
                    'task': task.get('task_id', task.get('correlation_id', '')),
                })
                return {'status': 'error', 'result': {}, 'error': ctx.rejection_reason}

        # --- Policy rule check ---
        policy_result = self.policy.check_policy(task)
        if not policy_result['allowed']:
            for v in policy_result['violations']:
                self.policy.record_violation(v['rule'], {'task': task, 'value': v['value']})
            return {
                'status': 'error',
                'result': {},
                'error': 'policy_violation',
                'violations': policy_result['violations'],
            }

        # --- Retry loop ---
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            start = time.monotonic()
            try:
                result = handler(task)
                elapsed = time.monotonic() - start
                if elapsed > self.timeout_seconds:
                    raise PolicyTimeoutError(
                        f"Task exceeded timeout of {self.timeout_seconds}s "
                        f"(took {elapsed:.1f}s)."
                    )

                # --- Post-execution middleware ---
                ctx.metadata['result'] = result
                for mw in self.post_middleware:
                    mw(ctx)
                    if not ctx.allowed:
                        return {
                            'status': 'error',
                            'result': {},
                            'error': ctx.rejection_reason,
                        }

                return result

            except PolicyTimeoutError:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "PolicyEnforcedExecutor attempt %d/%d failed: %s",
                    attempt + 1, self.max_retries + 1, exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.base_delay * (2 ** attempt))

        # --- All retries exhausted ---
        if self.fallback_handler:
            logger.info("PolicyEnforcedExecutor: invoking fallback handler.")
            return self.fallback_handler(task)

        return {
            'status': 'error',
            'result': {},
            'error': f"All {self.max_retries + 1} attempts failed: {last_exc}",
        }


# ---------------------------------------------------------------------------
# Default singleton (used by vertical_dispatch)
# ---------------------------------------------------------------------------

_default_executor: Optional[PolicyEnforcedExecutor] = None


def get_default_executor() -> PolicyEnforcedExecutor:
    """Return the module-level default :class:`PolicyEnforcedExecutor`."""
    global _default_executor
    if _default_executor is None:
        _default_executor = PolicyEnforcedExecutor()
    return _default_executor
