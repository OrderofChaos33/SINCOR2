"""Tests for core execution policy and reliability controls."""
from __future__ import annotations

import sys
import os
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from core.policy import ExecutionPolicy, PolicyRule
from core.reliability import CircuitState, ReliabilityControls


# ===========================================================================
# ExecutionPolicy
# ===========================================================================

@pytest.fixture()
def policy():
    return ExecutionPolicy()


def test_policy_allows_within_budget(policy):
    result = policy.check_policy({"budget": 500.0, "retries": 0, "request_rate": 10})
    assert result["allowed"] is True
    assert result["violations"] == []


def test_policy_blocks_over_budget(policy):
    result = policy.check_policy({"budget": 1001.0, "retries": 0, "request_rate": 0})
    assert result["allowed"] is False
    violated_rules = [v["rule"] for v in result["violations"]]
    assert "max-budget" in violated_rules


def test_policy_blocks_over_retry_cap(policy):
    result = policy.check_policy({"budget": 0, "retries": 5, "request_rate": 0})
    assert result["allowed"] is False
    violated_rules = [v["rule"] for v in result["violations"]]
    assert "max-retries" in violated_rules


def test_policy_blocks_over_rate_limit(policy):
    result = policy.check_policy({"budget": 0, "retries": 0, "request_rate": 200})
    assert result["allowed"] is False
    violated_rules = [v["rule"] for v in result["violations"]]
    assert "max-requests-per-minute" in violated_rules


def test_policy_multiple_violations_reported(policy):
    result = policy.check_policy({"budget": 9999, "retries": 99, "request_rate": 9999})
    assert len(result["violations"]) == 3


def test_policy_apply_limits_increments_counter(policy):
    policy.apply_limits("api_calls", 1.0)
    policy.apply_limits("api_calls", 1.0)
    summary = policy.get_policy_summary()
    assert summary["counters"]["api_calls"] == 2.0


def test_policy_record_violation_is_stored(policy):
    policy.record_violation("max-budget", {"budget": 5000})
    summary = policy.get_policy_summary()
    assert summary["violation_count"] == 1


def test_policy_summary_contains_rules_and_counters(policy):
    summary = policy.get_policy_summary()
    assert "rules" in summary
    assert "violation_count" in summary
    assert "counters" in summary
    rule_names = [r["name"] for r in summary["rules"]]
    assert "max-budget" in rule_names


def test_policy_exact_threshold_is_allowed(policy):
    """Values exactly at the threshold should NOT trigger a violation."""
    result = policy.check_policy({"budget": 1000.0, "retries": 3, "request_rate": 120})
    assert result["allowed"] is True


# ===========================================================================
# ReliabilityControls — circuit breaker
# ===========================================================================

@pytest.fixture()
def controls():
    return ReliabilityControls()


def _failing_op():
    raise ValueError("boom")


def _ok_op():
    return "ok"


def test_circuit_breaker_passes_through_success(controls):
    result = controls.call_with_breaker("svc", _ok_op)
    assert result == "ok"


def test_circuit_breaker_trips_after_threshold(controls):
    circuit = controls.circuits.setdefault("svc", CircuitState(threshold=2))
    # First failure
    with pytest.raises(ValueError):
        controls.call_with_breaker("svc", _failing_op)
    # Second failure — should trip
    with pytest.raises(ValueError):
        controls.call_with_breaker("svc", _failing_op)
    assert controls.circuits["svc"].status == "open"


def test_circuit_breaker_open_raises_runtime_error(controls):
    controls.circuits["svc"] = CircuitState(status="open")
    with pytest.raises(RuntimeError, match="open"):
        controls.call_with_breaker("svc", _ok_op)


def test_circuit_breaker_uses_fallback_when_open(controls):
    controls.circuits["svc"] = CircuitState(status="open")
    controls.register_fallback("svc", lambda: "fallback-value")
    result = controls.call_with_breaker("svc", _ok_op)
    assert result == "fallback-value"


def test_circuit_breaker_resets_on_success(controls):
    circuit = controls.circuits.setdefault("svc", CircuitState(threshold=3, failures=2))
    # Success should reset the counter
    controls.call_with_breaker("svc", _ok_op)
    assert circuit.failures == 0
    assert circuit.status == "closed"


def test_get_circuit_status_returns_all_circuits(controls):
    controls.circuits["alpha"] = CircuitState(failures=1, status="closed")
    controls.circuits["beta"] = CircuitState(failures=3, status="open")
    status = controls.get_circuit_status()
    assert "alpha" in status
    assert status["beta"]["status"] == "open"


# ===========================================================================
# ReliabilityControls — retry with backoff
# ===========================================================================

def test_retry_succeeds_on_first_attempt(controls):
    call_count = {"n": 0}

    def op():
        call_count["n"] += 1
        return "done"

    result = controls.retry_with_backoff(op, retries=3, base_delay=0.0)
    assert result == "done"
    assert call_count["n"] == 1


def test_retry_retries_on_transient_failure(controls):
    call_count = {"n": 0}

    def flaky():
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ConnectionError("transient")
        return "recovered"

    result = controls.retry_with_backoff(flaky, retries=5, base_delay=0.0)
    assert result == "recovered"
    assert call_count["n"] == 3


def test_retry_raises_after_exhausting_retries(controls):
    def always_fail():
        raise IOError("persistent")

    with pytest.raises(IOError, match="persistent"):
        controls.retry_with_backoff(always_fail, retries=2, base_delay=0.0)


def test_retry_only_catches_specified_exceptions(controls):
    """Non-matching exception types must propagate immediately."""
    def op():
        raise KeyError("unexpected")

    with pytest.raises(KeyError):
        controls.retry_with_backoff(
            op,
            retries=5,
            base_delay=0.0,
            retry_exceptions=(ValueError,),
        )
