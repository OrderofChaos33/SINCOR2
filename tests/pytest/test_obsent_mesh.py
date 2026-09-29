"""OBS-ENT Enterprise Mesh tests (B6).

Covers fleet rollup rules, adapter payload schemas + disabled-by-default,
SLA evaluation on fixture histories, breach detection, runbook generation,
and a full end-to-end walkthrough against a local stub HTTP endpoint.

All sibling-module data arrives as dict fixtures matching the documented
schemas in enterprise_mesh's docstring (the sibling modules do not exist
yet; the mesh must work with plain dicts). No live network calls — the
only socket traffic is to a localhost stub server.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from sincor2.obs_skus import enterprise_mesh as m

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = 1_750_000_000_000  # fixed epoch ms for deterministic tests
HOUR = 3_600_000

AGENTS = [f"sincor-liveness-0{i}" for i in range(1, 7)]


def vitals(agent_id, status="healthy", heartbeat_age_ms=10_000, **kw):
    rec = {
        "agent_id": agent_id,
        "status": status,
        "heartbeat_ms": NOW - heartbeat_age_ms,
        "checked_at_ms": NOW,
        "tasks_completed": 12,
        "tasks_failed": 0,
        "avg_latency_ms": 240.0,
        "error_rate": 0.0,
        "uptime_24h": 1.0,
        "last_error": None,
    }
    rec.update(kw)
    return rec


def incident(event_id, agent_id, occurred_ago_ms, alert_lag_ms=None, resolved=False, severity="error"):
    occurred = NOW - occurred_ago_ms
    return {
        "event_id": event_id,
        "event_type": "incident",
        "agent_id": agent_id,
        "severity": severity,
        "summary": f"test incident {event_id}",
        "occurred_at_ms": occurred,
        "alerted_at_ms": None if alert_lag_ms is None else occurred + alert_lag_ms,
        "resolved_at_ms": occurred + 60_000 if resolved else None,
        "details": {},
    }


def drift(agent_id, verdict="drift", drift_pct=-18.5):
    return {
        "agent_id": agent_id,
        "metric": "task_success_rate",
        "baseline": 0.97,
        "current": 0.79,
        "drift_pct": drift_pct,
        "verdict": verdict,
        "assessed_at_ms": NOW - 30_000,
    }


# ---------------------------------------------------------------------------
# Rollup rules
# ---------------------------------------------------------------------------


class TestRollup:
    def test_all_healthy(self):
        view = m.build_fleet_view("fleet-a", [vitals(a) for a in AGENTS], now_ms=NOW)
        assert view.health.status == m.STATUS_HEALTHY
        assert view.health.agent_count == 6
        assert view.health.counts[m.STATUS_HEALTHY] == 6
        assert any("all 6 agents reporting healthy" in r for r in view.health.reasons)

    def test_one_critical_wins(self):
        recs = [vitals(a) for a in AGENTS]
        recs[2] = vitals(AGENTS[2], status="critical")
        view = m.build_fleet_view("fleet-a", recs, now_ms=NOW)
        assert view.health.status == m.STATUS_CRITICAL
        assert any(AGENTS[2] in r and "critical" in r for r in view.health.reasons)

    def test_one_degraded(self):
        recs = [vitals(a) for a in AGENTS]
        recs[0] = vitals(AGENTS[0], status="degraded")
        view = m.build_fleet_view("fleet-a", recs, now_ms=NOW)
        assert view.health.status == m.STATUS_DEGRADED
        assert any(AGENTS[0] in r for r in view.health.reasons)

    def test_unknown_outranks_degraded(self):
        """Unknown (unobserved) escalates above a known-degraded agent."""
        recs = [vitals(a) for a in AGENTS]
        recs[0] = vitals(AGENTS[0], status="degraded")
        recs[1] = vitals(AGENTS[1], heartbeat_age_ms=120_000)  # stale -> unknown
        view = m.build_fleet_view("fleet-a", recs, now_ms=NOW)
        assert view.agents[1].status == m.STATUS_UNKNOWN
        assert view.agents[1].heartbeat_stale is True
        assert view.health.status == m.STATUS_UNKNOWN
        assert any("heartbeat stale" in r for r in view.health.reasons)

    def test_empty_fleet_is_unknown(self):
        view = m.build_fleet_view("fleet-a", [], now_ms=NOW)
        assert view.health.status == m.STATUS_UNKNOWN
        assert any("no agents reporting" in r for r in view.health.reasons)

    def test_stale_heartbeat_forces_unknown_with_reason(self):
        rec = vitals(AGENTS[0], status="healthy", heartbeat_age_ms=61_000)
        view = m.build_fleet_view("fleet-a", [rec], now_ms=NOW)
        entry = view.agents[0]
        assert entry.reported_status == "healthy"
        assert entry.status == m.STATUS_UNKNOWN
        assert any("heartbeat stale" in r for r in entry.reasons)

    def test_mass_failure_ratio_triggers_critical(self):
        # 3 of 6 unknown hits the 0.5 default ratio -> critical, no critical agent.
        recs = [vitals(a, heartbeat_age_ms=120_000) for a in AGENTS[:3]]
        recs += [vitals(a) for a in AGENTS[3:]]
        view = m.build_fleet_view("fleet-a", recs, now_ms=NOW)
        assert view.health.status == m.STATUS_CRITICAL
        assert any("mass failure" in r for r in view.health.reasons)

    def test_reasons_always_present_and_name_agents(self):
        recs = [vitals(a) for a in AGENTS]
        recs[4] = vitals(AGENTS[4], status="critical")
        view = m.build_fleet_view("fleet-a", recs, now_ms=NOW)
        assert view.health.reasons, "rollup must always explain itself"
        assert any(AGENTS[4] in r for r in view.health.reasons)

    def test_open_incidents_and_drift_attach(self):
        recs = [vitals(a) for a in AGENTS]
        events = [incident("inc-1", AGENTS[0], occurred_ago_ms=300_000),
                  incident("inc-2", AGENTS[1], occurred_ago_ms=300_000, resolved=True)]
        signals = [drift(AGENTS[0]), drift(AGENTS[2], verdict="nominal")]
        view = m.build_fleet_view("fleet-a", recs, audit_events=events,
                                  drift_signals=signals, now_ms=NOW)
        by_id = {e.agent_id: e for e in view.agents}
        assert len(by_id[AGENTS[0]].open_incidents) == 1  # resolved one excluded
        assert len(by_id[AGENTS[1]].open_incidents) == 0
        assert view.open_incident_count == 1
        assert view.drift_alert_count == 1  # nominal verdict excluded
        assert any("open incident inc-1" in r for r in by_id[AGENTS[0]].reasons)

    def test_view_serialises_to_json(self):
        view = m.build_fleet_view("fleet-a", [vitals(a) for a in AGENTS], now_ms=NOW)
        json.dumps(m.fleet_view_to_dict(view))  # must not raise


# ---------------------------------------------------------------------------
# Defensive coercion (dicts, dataclasses, attribute objects)
# ---------------------------------------------------------------------------


@dataclass
class VitalsDataclass:
    agent_id: str
    status: str
    heartbeat_ms: int
    checked_at_ms: int


class VitalsObject:
    def __init__(self, agent_id, status, heartbeat_ms):
        self.agent_id = agent_id
        self.status = status
        self.heartbeat_ms = heartbeat_ms


class TestCoercion:
    def test_dataclass_record(self):
        rec = VitalsDataclass("dc-agent", "healthy", NOW - 5_000, NOW)
        view = m.build_fleet_view("f", [rec], now_ms=NOW)
        assert view.agents[0].agent_id == "dc-agent"
        assert view.agents[0].status == "healthy"

    def test_attribute_object_record(self):
        rec = VitalsObject("obj-agent", "degraded", NOW - 5_000)
        view = m.build_fleet_view("f", [rec], now_ms=NOW)
        assert view.agents[0].status == "degraded"

    def test_garbage_record_degrades_to_unknown(self):
        view = m.build_fleet_view("f", [object()], now_ms=NOW)
        assert view.agents[0].status == m.STATUS_UNKNOWN

    def test_sibling_modules_absent_is_fine(self):
        assert m.vitals_module() is None
        assert m.audit_trail_module() is None
        assert m.drift_quality_module() is None


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class TestAdapterSchemas:
    def test_disabled_by_default(self):
        cfg = m.AdapterConfig(name="acme-webhook", kind="generic_webhook",
                              url="https://example.com/hook")
        assert cfg.enabled is False

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError):
            m.AdapterConfig(name="bad", kind="carrier_pigeon")

    def test_sentry_style_capability_is_honest(self):
        cfg = m.AdapterConfig(name="s", kind="sentry_style")
        assert "NOT a verified Sentry integration" in cfg.capability
        assert "no Sentry account is linked" in cfg.capability

    def test_healthchecks_style_capability_is_honest(self):
        cfg = m.AdapterConfig(name="h", kind="healthchecks_style")
        assert "NOT a verified healthchecks.io integration" in cfg.capability
        assert "no account is linked" in cfg.capability

    def test_inventory_never_claims_verified(self):
        cfgs = [
            m.AdapterConfig(name="a", kind="generic_webhook",
                            url="https://example.com/1", enabled=True),
            m.AdapterConfig(name="b", kind="sentry_style"),
        ]
        inv = m.adapter_inventory(cfgs)
        assert all(entry["verified"] is False for entry in inv)
        assert "disabled" in inv[1]["status"]
        assert inv[0]["url_configured"] is True
        assert inv[1]["url_configured"] is False

    def test_secret_not_in_repr(self):
        cfg = m.AdapterConfig(name="x", secret="super-secret-value")
        assert "super-secret-value" not in repr(cfg)

    def test_sentry_style_payload_schema(self):
        alert = {"summary": "fleet critical", "severity": "critical",
                 "agent_id": "sincor-liveness-03", "fleet_status": "critical",
                 "occurred_at_ms": NOW - 60_000, "reasons": ["r1"]}
        payload = m.build_sentry_style_payload(alert, "fleet-a")
        for key in ("event_id", "timestamp", "level", "message",
                    "culprit", "tags", "extra"):
            assert key in payload, f"missing sentry key {key}"
        assert payload["level"] == "fatal"
        assert payload["culprit"] == "sincor-liveness-03"
        assert payload["tags"]["fleet"] == "fleet-a"
        assert payload["tags"]["source"] == "sincor-obs-ent"

    def test_healthchecks_style_query(self):
        assert m.build_healthchecks_style_query("healthy")["status"] == "up"
        assert m.build_healthchecks_style_query("critical")["status"] == "down"
        assert m.build_healthchecks_style_query("weird")["status"] == "down"
        q = m.build_healthchecks_style_query("degraded", message="x" * 600)
        assert len(q["msg"]) == 500

    def test_signature_roundtrip_and_tamper(self):
        payload = {"event": "sincor.mesh.alert", "fleet": "fleet-a", "n": 3}
        sig = m.sign_payload(payload, "s3cr3t")
        assert m.verify_signature(payload, "s3cr3t", sig) is True
        tampered = dict(payload, n=4)
        assert m.verify_signature(tampered, "s3cr3t", sig) is False
        assert m.verify_signature(payload, "wrong", sig) is False

    def test_signature_ignores_signature_key(self):
        payload = {"a": 1}
        with_sig = {"a": 1, "signature": "sha256=whatever"}
        assert m.sign_payload(payload, "k") == m.sign_payload(with_sig, "k")

    def test_canonical_form_is_deterministic(self):
        p1 = {"b": 2, "a": 1}
        p2 = {"a": 1, "b": 2}
        assert m.canonical_json(p1) == m.canonical_json(p2) == '{"a":1,"b":2}'


class TestDispatcher:
    def test_dry_run_touches_no_network(self):
        d = m.SignedWebhookDispatcher()
        res = d.dispatch("http://127.0.0.1:1/unroutable", {"a": 1}, "s", dry_run=True)
        assert res.ok is True and res.dry_run is True and res.attempts == 0

    def test_unserializable_payload_is_fail_closed(self):
        d = m.SignedWebhookDispatcher()
        res = d.dispatch("https://example.com/hook", {"a": object()}, "s", dry_run=False)
        assert res.ok is False
        assert "JSON-serializable" in (res.error or "")

    def test_refuses_non_http_url(self):
        d = m.SignedWebhookDispatcher()
        res = d.dispatch("ftp://example.com/x", {"a": 1}, "s", dry_run=False)
        assert res.ok is False and "http(s)" in (res.error or "")

    def test_connection_failure_is_fail_closed(self):
        d = m.SignedWebhookDispatcher(timeout_s=0.2, max_attempts=2)
        res = d.dispatch("http://127.0.0.1:1/unroutable", {"a": 1}, "s", dry_run=False)
        assert res.ok is False
        assert res.attempts == 2
        assert res.error, "fail-closed result must carry the reason"

    def test_send_alert_skips_disabled_by_default(self):
        cfgs = [m.AdapterConfig(name="w", kind="generic_webhook",
                                url="https://example.com/hook")]
        results = m.send_alert_via_adapters({"summary": "x"}, cfgs)  # dry_run default
        assert len(results) == 1
        assert results[0].ok is False
        assert "disabled" in (results[0].error or "")

    def test_send_alert_dry_run_default_never_sends(self):
        cfgs = [m.AdapterConfig(name="w", kind="generic_webhook",
                                url="http://127.0.0.1:1/nope", enabled=True)]
        results = m.send_alert_via_adapters({"summary": "x"}, cfgs)
        assert results[0].ok is True and results[0].dry_run is True


# ---------------------------------------------------------------------------
# SLA evaluation
# ---------------------------------------------------------------------------


def history_all_healthy(hours=24, step_h=1):
    return [
        {"agent_id": "fleet", "status": "healthy",
         "checked_at_ms": NOW - (hours - i) * HOUR}
        for i in range(int(hours / step_h))
    ] + [{"agent_id": "fleet", "status": "healthy", "checked_at_ms": NOW}]


class TestSLA:
    def test_uptime_all_healthy_met(self):
        sla = m.SLADefinition(name="U", metric=m.METRIC_UPTIME_PCT, target=99.5)
        ev = m.evaluate_sla(sla, vitals_history=history_all_healthy(), now_ms=NOW)
        assert ev.status == "met" and ev.met is True
        assert ev.measured == 100.0

    def test_uptime_one_hour_outage_breaches(self):
        hist = history_all_healthy()
        # outage: the sample covering [NOW-2h, NOW-1h) reports critical
        hist = [dict(h, status="critical") if h["checked_at_ms"] == NOW - 2 * HOUR else h
                for h in hist]
        sla = m.SLADefinition(name="U", metric=m.METRIC_UPTIME_PCT, target=99.5)
        ev = m.evaluate_sla(sla, vitals_history=hist, now_ms=NOW)
        assert ev.status == "breached" and ev.met is False
        assert ev.measured == pytest.approx(95.833, abs=0.01)
        assert ev.margin is not None and ev.margin < 0

    def test_uptime_unknown_counts_as_down(self):
        hist = history_all_healthy()
        hist = [dict(h, status="unknown") if h["checked_at_ms"] == NOW - 2 * HOUR else h
                for h in hist]
        sla = m.SLADefinition(name="U", metric=m.METRIC_UPTIME_PCT, target=99.5)
        ev = m.evaluate_sla(sla, vitals_history=hist, now_ms=NOW)
        assert ev.status == "breached", "unknown time must not be assumed healthy"

    def test_uptime_empty_history_insufficient_data(self):
        sla = m.SLADefinition(name="U", metric=m.METRIC_UPTIME_PCT, target=99.5)
        ev = m.evaluate_sla(sla, vitals_history=[], now_ms=NOW)
        assert ev.status == "insufficient_data" and ev.measured is None

    def test_alert_latency_met(self):
        events = [incident(f"i{i}", AGENTS[0], occurred_ago_ms=(i + 1) * HOUR,
                           alert_lag_ms=10_000) for i in range(4)]
        sla = m.SLADefinition(name="L", metric=m.METRIC_ALERT_LATENCY_S, target=60.0,
                              higher_is_better=False)
        ev = m.evaluate_sla(sla, audit_events=events, now_ms=NOW)
        assert ev.status == "met" and ev.measured == pytest.approx(10.0)

    def test_alert_latency_breach(self):
        events = [incident(f"i{i}", AGENTS[0], occurred_ago_ms=(i + 1) * HOUR,
                           alert_lag_ms=120_000) for i in range(4)]
        sla = m.SLADefinition(name="L", metric=m.METRIC_ALERT_LATENCY_S, target=60.0,
                              higher_is_better=False)
        ev = m.evaluate_sla(sla, audit_events=events, now_ms=NOW)
        assert ev.status == "breached" and ev.measured == pytest.approx(120.0)

    def test_missed_alert_counts_as_infinite_latency(self):
        events = [incident("missed", AGENTS[0], occurred_ago_ms=HOUR, alert_lag_ms=None)]
        sla = m.SLADefinition(name="L", metric=m.METRIC_ALERT_LATENCY_S, target=60.0,
                              higher_is_better=False)
        ev = m.evaluate_sla(sla, audit_events=events, now_ms=NOW)
        assert ev.status == "breached", "a missed alert must breach, not vanish"
        assert ev.measured is None
        assert "missed" in ev.note

    def test_no_incidents_latency_not_exercised(self):
        sla = m.SLADefinition(name="L", metric=m.METRIC_ALERT_LATENCY_S, target=60.0,
                              higher_is_better=False)
        ev = m.evaluate_sla(sla, audit_events=[], now_ms=NOW)
        assert ev.status == "met" and "not exercised" in ev.note

    def test_report_cadence_met_and_breached(self):
        sla = m.SLADefinition(name="R", metric=m.METRIC_REPORT_CADENCE, target=1.0)
        met = m.evaluate_sla(sla, report_timestamps_ms=[NOW - HOUR], now_ms=NOW)
        assert met.status == "met"
        breached = m.evaluate_sla(sla, report_timestamps_ms=[NOW - 25 * HOUR], now_ms=NOW)
        assert breached.status == "breached"

    def test_unknown_metric_insufficient_data(self):
        sla = m.SLADefinition(name="?", metric="teleportation", target=1.0)
        ev = m.evaluate_sla(sla, now_ms=NOW)
        assert ev.status == "insufficient_data"

    def test_fleet_health_history_feeds_uptime(self):
        views = []
        for i in range(25):
            ts = NOW - (24 - i) * HOUR
            status = "critical" if i == 22 else "healthy"  # 1h outage
            views.append(m.FleetView(
                fleet_name="fleet-a", generated_at_ms=ts,
                health=m.FleetHealth(status=status, reasons=[], agent_count=6,
                                     counts={}, evaluated_at_ms=ts),
                agents=[], open_incident_count=0, drift_alert_count=0))
        hist = m.fleet_health_history(views)
        assert len(hist) == 25 and hist[0]["checked_at_ms"] == NOW - 24 * HOUR
        sla = m.SLADefinition(name="U", metric=m.METRIC_UPTIME_PCT, target=99.5)
        ev = m.evaluate_sla(sla, vitals_history=hist, now_ms=NOW)
        assert ev.status == "breached"
        assert ev.measured == pytest.approx(95.833, abs=0.01)

    def test_evaluate_sla_set(self):
        evs = m.evaluate_sla_set(
            vitals_history=history_all_healthy(),
            audit_events=[],
            report_timestamps_ms=[NOW - HOUR],
            now_ms=NOW,
        )
        assert len(evs) == 3
        assert all(e.status == "met" for e in evs)


# ---------------------------------------------------------------------------
# Breach report
# ---------------------------------------------------------------------------


class TestBreachReport:
    def _evals(self):
        up = m.evaluate_sla(
            m.SLADefinition(name="Fleet uptime", metric=m.METRIC_UPTIME_PCT, target=99.5),
            vitals_history=[], now_ms=NOW)  # insufficient_data
        lat = m.evaluate_sla(
            m.SLADefinition(name="Alert latency (p95)", metric=m.METRIC_ALERT_LATENCY_S,
                            target=60.0, higher_is_better=False),
            audit_events=[incident("i1", AGENTS[0], HOUR, alert_lag_ms=120_000)],
            now_ms=NOW)  # breached
        rep = m.evaluate_sla(
            m.SLADefinition(name="Daily status report", metric=m.METRIC_REPORT_CADENCE,
                            target=1.0),
            report_timestamps_ms=[NOW - HOUR], now_ms=NOW)  # met
        return [up, lat, rep]

    def test_report_counts_and_positive_framing(self):
        report = m.build_breach_report("fleet-a", self._evals(), now_ms=NOW)
        assert report.met_count == 1
        assert len(report.breached) == 1
        assert report.breached[0].sla_name == "Alert latency (p95)"
        assert len(report.insufficient_data) == 1
        # Positive framing: no alarmist language in customer-facing summary.
        assert "FAILED" not in report.summary
        assert "need attention" in report.summary
        assert "1 of 3 service commitments met" in report.summary

    def test_all_met_summary(self):
        evs = m.evaluate_sla_set(vitals_history=history_all_healthy(), audit_events=[],
                                 report_timestamps_ms=[NOW - HOUR], now_ms=NOW)
        report = m.build_breach_report("fleet-a", evs, now_ms=NOW)
        assert report.met_count == 3 and not report.breached
        assert "All 3 service commitments met" in report.summary

    def test_report_serialises_to_json(self):
        report = m.build_breach_report("fleet-a", self._evals(), now_ms=NOW)
        json.dumps(m.breach_report_to_dict(report))  # must not raise


# ---------------------------------------------------------------------------
# Runbook
# ---------------------------------------------------------------------------


class TestRunbook:
    def test_runbook_sections(self):
        cfgs = [m.AdapterConfig(name="ops-webhook", kind="generic_webhook",
                                url="https://ops.example.com/hook", enabled=True)]
        rb = m.generate_runbook("fleet-a", adapter_configs=cfgs, now_ms=NOW)
        md = rb.markdown
        assert "Escalation matrix" in md
        assert "Onboarding checklist" in md
        assert "- [ ]" in md
        assert "Service commitments (SLA)" in md
        assert "Adapter inventory" in md
        assert "disabled by default" in md
        assert "Incident response" in md
        assert "TBD — assign during onboarding" in md
        assert "ops-webhook" in md
        # Honest adapter labeling inside the customer artifact.
        assert "What it does:" in md
        assert rb.sections["onboarding_items"] == 8
        assert len(rb.sections["sla_commitments"]) == 3
        assert rb.sections["adapters"][0]["verified"] is False

    def test_runbook_no_adapter_section(self):
        rb = m.generate_runbook("fleet-a", now_ms=NOW)
        assert "No adapters configured yet" in rb.markdown

    def test_write_runbook(self, tmp_path):
        rb = m.generate_runbook("fleet-a", now_ms=NOW)
        path = m.write_runbook(str(tmp_path / "runbook.md"), rb)
        with open(path, encoding="utf-8") as fh:
            assert "Enterprise Support Runbook" in fh.read()


# ---------------------------------------------------------------------------
# End-to-end walkthrough (the founder's "working end-to-end" proof)
# ---------------------------------------------------------------------------


class StubHandler(BaseHTTPRequestHandler):
    """Local stub endpoint: captures POSTs for the walkthrough."""

    received = []  # list of (path, headers dict, body bytes)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        StubHandler.received.append((self.path, dict(self.headers), body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


@pytest.fixture()
def stub_endpoint():
    StubHandler.received = []
    server = HTTPServer(("127.0.0.1", 0), StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/hook"
    finally:
        server.shutdown()
        thread.join()


class TestEndToEnd:
    def test_walkthrough(self, stub_endpoint):
        """Fleet rollup -> SLA evaluation -> breach report -> signed alert
        delivered to a stub endpoint, signature verified server-side."""

        # 1. Fleet rollup over vitals fixtures: one agent critical.
        recs = [vitals(a) for a in AGENTS]
        recs[2] = vitals(AGENTS[2], status="critical", error_rate=0.4,
                         last_error="executor timeout")
        view = m.build_fleet_view("liveness-fleet", recs, now_ms=NOW)
        assert view.health.status == m.STATUS_CRITICAL

        # 2. SLA evaluation over a 24h history containing a 1h outage.
        hist = history_all_healthy()
        hist = [dict(h, status="critical") if h["checked_at_ms"] == NOW - 2 * HOUR else h
                for h in hist]
        evaluations = m.evaluate_sla_set(
            vitals_history=hist, audit_events=[],
            report_timestamps_ms=[NOW - HOUR], now_ms=NOW)

        # 3. Breach report names the breached commitment.
        report = m.build_breach_report("liveness-fleet", evaluations, now_ms=NOW)
        assert any(e.sla_name == "Fleet uptime" for e in report.breached)

        # 4. Alert payload emitted to the stub endpoint via two adapters.
        alert = {
            "summary": "Fleet uptime breached: 95.83% vs 99.5% target",
            "severity": "error",
            "agent_id": AGENTS[2],
            "fleet_status": view.health.status,
            "occurred_at_ms": NOW - 30_000,
            "reasons": view.health.reasons,
        }
        secret = "walkthrough-secret"
        cfgs = [
            m.AdapterConfig(name="walkthrough-webhook", kind="generic_webhook",
                            url=stub_endpoint, secret=secret, enabled=True),
            m.AdapterConfig(name="walkthrough-sentry", kind="sentry_style",
                            url=stub_endpoint, secret=secret, enabled=True),
        ]
        results = m.send_alert_via_adapters(
            alert, cfgs, fleet_name="liveness-fleet", dry_run=False)
        assert len(results) == 2
        assert all(r.ok for r in results), [r.error for r in results]

        # 5. The stub received both payloads with verifiable signatures.
        assert len(StubHandler.received) == 2
        for path, headers, body in StubHandler.received:
            assert path == "/hook"
            assert headers.get("X-Sincor-Event") == "sincor.mesh.alert"
            sig = headers.get("X-Sincor-Signature", "")
            assert sig.startswith("sha256=")
            payload = json.loads(body.decode("utf-8"))
            assert m.verify_signature(payload, secret, sig[len("sha256="):]) is True
        # The sentry-style payload really is sentry-shaped.
        sentry_body = json.loads(StubHandler.received[1][2].decode("utf-8"))
        assert "event_id" in sentry_body and "culprit" in sentry_body
