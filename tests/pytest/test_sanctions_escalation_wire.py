"""Sanctions screen -> Phase 1 EscalationEnvelope wire (additive tap).

Proves the wiring contract for ``sincor2.sanctions_screen``'s optional
escalation emission:

- Flag OFF (``SINCOR_ESCALATION_SINK`` unset/empty): no envelope store
  is even constructed and every verdict dict is exactly the dict the
  screen produced before the tap existed.
- Flag ON + OFAC match: one EscalationEnvelope, source surface
  ``sanctions-screen``, triggered rule ``ofac-match``, violation
  confidence None (the matcher is exact set membership — it produces
  no score, and the wire must never fabricate one), recommended
  action ``deny``.
- Flag ON + stale snapshot (verdict ``unknown``): one envelope,
  rule ``snapshot-stale``, recommended action ``escalate``.
- Flag ON + clean verdict: no envelope.
- Broken sink (store constructor or create raises): the verdict is
  still returned, correct and complete — emission failure can never
  leak into the screening path.

Fixtures mirror test_sanctions_screen.py: the real vendored snapshot
with a controlled ``fetched_at`` injected through the module seams.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

import sincor2.escalation as esc
import sincor2.sanctions_screen as ss
from sincor2.defi.ofac_sdn import load_snapshot

# Verified present in the vendored snapshot: MESRI, Behzad (uid 24003).
FLAGGED_ADDR = "0x252a8bd2319d8a555b872990601221b3a2053bce"
CLEAN_ADDR = "0x000000000000000000000000000000000000dEaD"
SINK_ENV = "SINCOR_ESCALATION_SINK"


def _fresh_real_snapshot() -> dict:
    snap = dict(load_snapshot())
    snap["fetched_at"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    return snap


def _stale_real_snapshot() -> dict:
    snap = dict(load_snapshot())
    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        days=10)
    snap["fetched_at"] = old.isoformat()
    return snap


def _envelopes(sink_dir: Path) -> list:
    """Serialized envelopes currently in the sink's state file."""
    state = sink_dir / "escalation_state.json"
    if not state.exists():
        return []
    data = json.loads(state.read_text(encoding="utf-8"))
    return list(data["packages"].values())


# ---------------------------------------------------------------------------
# Flag OFF — byte-for-byte today's behavior
# ---------------------------------------------------------------------------

class TestFlagOff:
    def test_no_store_constructed_and_verdicts_unchanged(
            self, monkeypatch, tmp_path):
        constructions = []

        class CountingStore:
            def __init__(self, *args, **kwargs):
                constructions.append((args, kwargs))

        monkeypatch.setattr(esc, "EscalationStore", CountingStore)
        monkeypatch.delenv(SINK_ENV, raising=False)

        fresh = _fresh_real_snapshot()
        flagged = ss.screen_wallet(FLAGGED_ADDR, snapshot=fresh)
        clean = ss.screen_wallet(CLEAN_ADDR, snapshot=fresh)
        stale = ss.screen_wallet(
            FLAGGED_ADDR, snapshot=_stale_real_snapshot())

        assert flagged["verdict"] == "flagged"
        assert clean["verdict"] == "clean"
        assert stale["verdict"] == "unknown" and stale["stale"] is True
        # The tap never even looked at the store layer.
        assert constructions == []
        assert _envelopes(tmp_path) == []

    def test_empty_string_flag_is_off(self, monkeypatch, tmp_path):
        monkeypatch.setenv(SINK_ENV, "")
        res = ss.screen_wallet(
            FLAGGED_ADDR, snapshot=_fresh_real_snapshot())
        assert res["verdict"] == "flagged"
        assert _envelopes(tmp_path) == []


# ---------------------------------------------------------------------------
# Flag ON — emission shape per verdict class
# ---------------------------------------------------------------------------

class TestEmission:
    def test_match_emits_deny_envelope(self, monkeypatch, tmp_path):
        monkeypatch.setenv(SINK_ENV, str(tmp_path))
        res = ss.screen_wallet(
            FLAGGED_ADDR, snapshot=_fresh_real_snapshot())
        assert res["verdict"] == "flagged"

        envelopes = _envelopes(tmp_path)
        assert len(envelopes) == 1
        env = envelopes[0]
        assert env["schema"] == "EscalationEnvelope"
        assert env["schema_version"] == "escalation-envelope/1.0"
        assert env["source_surface"] == "sanctions-screen"
        assert env["state"] == "PENDING"
        assert env["triggered_rule"]["policy_id"] == "sanctions-screen"
        assert env["triggered_rule"]["rule"] == "ofac-match"
        assert env["triggered_rule"]["detail"]

        risk = env["risk"]
        assert len(risk["violations"]) == 1
        violation = risk["violations"][0]
        assert violation["policy_id"] == "sanctions-screen"
        # Exact-match membership produces no score: None, never invented.
        assert violation["confidence"] is None
        assert risk["recommended_action"] == "deny"
        # The screen prices no transfer: no exposure to report.
        assert risk["exposure"] is None

        snap = env["state_snapshot"]
        assert snap["input_parameters"]["address"] == FLAGGED_ADDR
        assert snap["dry_run_state"]["verdict"] == "flagged"
        assert snap["dry_run_state"]["match"]["uid"] == "24003"

        # The durable audit trail is intact and hash-chained.
        store = esc.EscalationStore(str(tmp_path))
        assert store.verify_audit_chain() is True

    def test_stale_emits_escalate_envelope(self, monkeypatch, tmp_path):
        monkeypatch.setenv(SINK_ENV, str(tmp_path))
        res = ss.screen_wallet(
            CLEAN_ADDR, snapshot=_stale_real_snapshot())
        # Fail-closed verdict is untouched by the tap.
        assert res["verdict"] == "unknown" and res["stale"] is True

        envelopes = _envelopes(tmp_path)
        assert len(envelopes) == 1
        env = envelopes[0]
        assert env["source_surface"] == "sanctions-screen"
        assert env["triggered_rule"]["policy_id"] == "sanctions-screen"
        assert env["triggered_rule"]["rule"] == "snapshot-stale"
        risk = env["risk"]
        assert risk["recommended_action"] == "escalate"
        assert len(risk["violations"]) == 1
        assert risk["violations"][0]["confidence"] is None
        dry = env["state_snapshot"]["dry_run_state"]
        assert dry["verdict"] == "unknown" and dry["stale"] is True
        assert dry["snapshot_age_seconds"] > 48 * 3600

    def test_clean_verdict_emits_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setenv(SINK_ENV, str(tmp_path))
        res = ss.screen_wallet(CLEAN_ADDR, snapshot=_fresh_real_snapshot())
        assert res["verdict"] == "clean"
        assert _envelopes(tmp_path) == []

    def test_invalid_address_emits_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setenv(SINK_ENV, str(tmp_path))
        res = ss.screen_wallet("not-an-address",
                               snapshot=_fresh_real_snapshot())
        assert res["ok"] is False and res["error"] == "invalid_address"
        assert _envelopes(tmp_path) == []

    def test_flag_on_verdicts_identical_to_flag_off(
            self, monkeypatch, tmp_path):
        fresh = _fresh_real_snapshot()
        stale = _stale_real_snapshot()
        # Fixed clock: snapshot_age_seconds must not straddle a second
        # boundary between the off and on passes.
        fresh_now = ss._snapshot_epoch(fresh["fetched_at"]) + 3600
        stale_now = ss._snapshot_epoch(stale["fetched_at"]) + 49 * 3600

        def run_all():
            return {
                "flagged": ss.screen_wallet(
                    FLAGGED_ADDR, snapshot=fresh, now=fresh_now),
                "clean": ss.screen_wallet(
                    CLEAN_ADDR, snapshot=fresh, now=fresh_now),
                "stale": ss.screen_wallet(
                    FLAGGED_ADDR, snapshot=stale, now=stale_now),
            }

        monkeypatch.delenv(SINK_ENV, raising=False)
        off = run_all()
        monkeypatch.setenv(SINK_ENV, str(tmp_path))
        on = run_all()
        assert on == off

    def test_serve_path_emits_and_statuses_unchanged(
            self, monkeypatch, tmp_path):
        # The billing blueprint's entry point: serve_sanctions_screen
        # loads through the module seam, so steer the seam.
        monkeypatch.setenv(SINK_ENV, str(tmp_path))
        monkeypatch.setattr(
            ss, "load_screen_snapshot", _fresh_real_snapshot)
        status, body = ss.serve_sanctions_screen({"address": FLAGGED_ADDR})
        assert status == 200 and body["verdict"] == "flagged"
        status, body = ss.serve_sanctions_screen({"address": CLEAN_ADDR})
        assert status == 200 and body["verdict"] == "clean"
        assert len(_envelopes(tmp_path)) == 1  # only the match emitted

        monkeypatch.setattr(
            ss, "load_screen_snapshot", _stale_real_snapshot)
        status, body = ss.serve_sanctions_screen({"address": CLEAN_ADDR})
        assert status == 503 and body["error"] == "snapshot_stale"
        envelopes = _envelopes(tmp_path)
        assert len(envelopes) == 2
        rules = {e["triggered_rule"]["rule"] for e in envelopes}
        assert rules == {"ofac-match", "snapshot-stale"}


# ---------------------------------------------------------------------------
# Broken sink — the screen must never notice
# ---------------------------------------------------------------------------

class TestSinkFailureSwallowed:
    def test_store_constructor_raises(self, monkeypatch, tmp_path):
        class BoomStore:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("sink on fire")

        monkeypatch.setattr(esc, "EscalationStore", BoomStore)
        monkeypatch.setenv(SINK_ENV, str(tmp_path))

        res = ss.screen_wallet(
            FLAGGED_ADDR, snapshot=_fresh_real_snapshot())
        assert res["verdict"] == "flagged"
        assert res["match"]["uid"] == "24003"
        res = ss.screen_wallet(
            CLEAN_ADDR, snapshot=_stale_real_snapshot())
        assert res["verdict"] == "unknown" and res["stale"] is True

    def test_create_package_raises(self, monkeypatch, tmp_path):
        class CreateBoomStore(esc.EscalationStore):
            def create_package(self, **kwargs):
                raise RuntimeError("create on fire")

        monkeypatch.setattr(esc, "EscalationStore", CreateBoomStore)
        monkeypatch.setenv(SINK_ENV, str(tmp_path))

        res = ss.screen_wallet(
            FLAGGED_ADDR, snapshot=_fresh_real_snapshot())
        assert res["verdict"] == "flagged"
        assert res["match"]["uid"] == "24003"

    def test_sink_path_unwritable(self, monkeypatch, tmp_path):
        # A regular file where the store dir should be: EscalationStore
        # cannot makedirs it. Verdict still returned.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("occupied", encoding="utf-8")
        monkeypatch.setenv(SINK_ENV, str(blocker))
        res = ss.screen_wallet(
            FLAGGED_ADDR, snapshot=_fresh_real_snapshot())
        assert res["verdict"] == "flagged"
