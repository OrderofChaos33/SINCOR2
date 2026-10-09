"""WP5 dead-man, kill-switch, and alert-ack tests.

(c) dead-man check marks unhealthy on alert failure
(d) agent cannot clear kill-switch (engage or disengage)
(e) alert delivery acknowledgment ledger + bounded dedup
"""

import pytest

from sincor2.shadow_monitor.alerting import (
    Alert,
    AlertManager,
    AnomalyClass,
    Severity,
)
from sincor2.shadow_monitor.audit import AuditStore, OperatorKillSwitch
from sincor2.shadow_monitor.deadman import (
    AlertingUnhealthy,
    DeadManCheck,
    get_system_health,
)


def _make_alert(**overrides):
    base = dict(
        severity=Severity.WARNING,
        anomaly_id="anom-1",
        anomaly_class=AnomalyClass.QUALITY_DRIFT,
        title="test alert",
        explanation="test",
        dedup_key=("trace-1", "wf-1", "reason-1"),
    )
    base.update(overrides)
    return Alert(**base)


# -- dead-man --------------------------------------------------------------


def test_deadman_healthy_when_canary_delivered_and_acked():
    mgr = AlertManager()
    check = DeadManCheck(
        canary_fn=lambda: mgr.send(_make_alert()),
        ack_fn=lambda receipt: True,
    )
    verdict = check.run()
    assert verdict.healthy is True
    assert verdict.canary_delivered is True
    assert verdict.canary_acknowledged is True
    assert verdict.consecutive_failures == 0


def test_deadman_unhealthy_when_delivery_raises():
    def boom(alert):
        raise ConnectionError("smtp down")

    mgr = AlertManager(delivery_fn=boom)
    check = DeadManCheck(canary_fn=lambda: mgr.send(_make_alert()))
    verdict = check.run()
    assert verdict.healthy is False
    assert verdict.canary_delivered is False
    assert "not delivered" in verdict.failure_reason
    assert verdict.consecutive_failures == 1


def test_deadman_unhealthy_when_not_acknowledged():
    mgr = AlertManager()
    check = DeadManCheck(
        canary_fn=lambda: mgr.send(_make_alert()),
        ack_fn=lambda receipt: False,  # delivered but never acked
    )
    verdict = check.run()
    assert verdict.healthy is False
    assert verdict.canary_delivered is True
    assert verdict.canary_acknowledged is False


def test_deadman_require_healthy_raises_when_unhealthy():
    def boom(alert):
        raise ConnectionError("down")

    mgr = AlertManager(delivery_fn=boom)
    check = DeadManCheck(canary_fn=lambda: mgr.send(_make_alert()))
    check.run()
    with pytest.raises(AlertingUnhealthy):
        check.require_healthy()


def test_deadman_require_healthy_raises_before_first_run():
    mgr = AlertManager()
    check = DeadManCheck(canary_fn=lambda: mgr.send(_make_alert()))
    with pytest.raises(AlertingUnhealthy, match="no dead-man check has run"):
        check.require_healthy()


def test_deadman_recovers_after_success():
    calls = {"n": 0}

    def flaky(alert):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("flaky")
        return {"delivered": True}

    mgr = AlertManager(delivery_fn=flaky)
    check = DeadManCheck(canary_fn=lambda: mgr.send(_make_alert()))
    bad = check.run()
    assert bad.healthy is False
    good = check.run()
    assert good.healthy is True
    assert good.consecutive_failures == 0
    check.require_healthy()  # must not raise


def test_system_health_reflects_last_run():
    mgr = AlertManager()
    check = DeadManCheck(canary_fn=lambda: mgr.send(_make_alert()))
    check.run()
    health = get_system_health()
    assert health["status"] == "healthy"


# -- kill switch -----------------------------------------------------------


def _human():
    return {"principal_type": "human", "principal_id": "founder-1"}


def _agent():
    return {"principal_type": "agent", "principal_id": "agent-007"}


def test_operator_can_engage_and_disengage():
    ks = OperatorKillSwitch()
    assert ks.engaged is False
    ks.engage(_human(), reason="test")
    assert ks.engaged is True
    ks.disengage(_human(), reason="test done")
    assert ks.engaged is False


def test_agent_cannot_engage_kill_switch():
    ks = OperatorKillSwitch()
    with pytest.raises(PermissionError, match="may not engage"):
        ks.engage(_agent(), reason="malicious")
    assert ks.engaged is False


def test_agent_cannot_clear_kill_switch():
    ks = OperatorKillSwitch()
    ks.engage(_human(), reason="incident")
    assert ks.engaged is True
    with pytest.raises(PermissionError, match="may not disengage"):
        ks.disengage(_agent(), reason="let me out")
    # Still engaged: the agent's attempt changed nothing.
    assert ks.engaged is True


def test_unknown_principal_cannot_touch_kill_switch():
    ks = OperatorKillSwitch()
    with pytest.raises(PermissionError):
        ks.engage({"principal_type": "service"}, reason="x")
    with pytest.raises(PermissionError):
        ks.disengage({}, reason="x")


def test_kill_switch_transitions_are_audited():
    audit = AuditStore()
    ks = OperatorKillSwitch(audit_store=audit)
    ks.engage(_human(), reason="incident-42")
    ks.disengage(_human(), reason="resolved")
    records = audit.records()
    kinds = [r.kind for r in records]
    assert kinds == ["final_state", "final_state"]
    assert "incident-42" in records[0].detail
    assert audit.verify_chain() is True


def test_no_agent_disengage_code_path():
    """Source scan: every .disengage( call site must be principal-gated.

    The only disengage methods in the codebase are OperatorKillSwitch's
    (principal-gated) and the raw KillSwitch's (not exposed to agents).
    This test fails if anyone adds an ungated disengage path.
    """
    import inspect

    import sincor2.shadow_monitor.audit as audit_mod
    import sincor2.shadow_monitor.boundary as boundary_mod

    # OperatorKillSwitch.disengage requires a principal; agent principals raise.
    sig = inspect.signature(audit_mod.OperatorKillSwitch.disengage)
    assert "principal" in sig.parameters

    # The raw KillSwitch.disengage takes no principal -- verify it is NOT
    # reachable from any agent-facing module (runtime, effect_boundary).
    import sincor2.shadow_monitor.effect_boundary as eb_mod
    import sincor2.shadow_monitor.runtime as rt_mod

    for mod in (eb_mod, rt_mod):
        source = inspect.getsource(mod)
        assert ".disengage(" not in source, (
            f"{mod.__name__} calls disengage() -- agent-reachable kill-switch "
            "clear path forbidden"
        )


# -- alert ack ledger ------------------------------------------------------


def test_send_records_delivery_ack():
    mgr = AlertManager()
    alert = _make_alert()
    receipt = mgr.send(alert)
    assert receipt["delivered"] is True
    status = mgr.delivery_status(alert.alert_id)
    assert status["status"] == "delivered"


def test_failed_send_records_failed_ack():
    def boom(alert):
        raise ConnectionError("down")

    mgr = AlertManager(delivery_fn=boom)
    alert = _make_alert()
    receipt = mgr.send(alert)
    assert receipt["delivered"] is False
    status = mgr.delivery_status(alert.alert_id)
    assert status["status"] == "failed"


def test_acknowledge_flow():
    mgr = AlertManager()
    alert = _make_alert()
    mgr.send(alert)
    result = mgr.acknowledge(alert.alert_id, by="founder-1")
    assert result["acknowledged"] is True
    status = mgr.delivery_status(alert.alert_id)
    assert status["status"] == "acknowledged"
    assert status["acknowledged_by"] == "founder-1"
    assert mgr.unacknowledged() == []


def test_unacknowledged_lists_pending():
    mgr = AlertManager()
    a1 = _make_alert(dedup_key=("t1", "w1", "r1"))
    a2 = _make_alert(dedup_key=("t2", "w2", "r2"))
    mgr.send(a1)
    mgr.send(a2)
    mgr.acknowledge(a1.alert_id, by="founder-1")
    pending = mgr.unacknowledged()
    assert pending == [a2.alert_id]


def test_acknowledge_unknown_alert_raises():
    mgr = AlertManager()
    with pytest.raises(KeyError):
        mgr.acknowledge("nope")


def test_dedup_memory_is_bounded():
    mgr = AlertManager()
    mgr._dedup_capacity = 5
    alerts = []
    for i in range(10):
        a = _make_alert(dedup_key=(f"t{i}", f"w{i}", f"r{i}"))
        alerts.append(a)
        mgr.send(a)
    # Capacity enforced: only the 5 most recent keys retained.
    assert len(mgr._seen_dedup_keys) <= 5
    # Recent keys still dedupe.
    again = _make_alert(dedup_key=("t9", "w9", "r9"))
    assert mgr.send(again)["deduped"] is True
