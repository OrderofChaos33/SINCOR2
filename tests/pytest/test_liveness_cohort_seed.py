"""Liveness cohort startup seed.

The six sincor-liveness-* agents are first-party/ops agents whose IDs sit
under the protected ``sincor-`` prefix: the public POST /v1/a2a/register
route refuses them by design ("reserved agent_id"). ``ensure_liveness_cohort()``
seeds them at startup through the internal path instead (same mechanism as
the platform agent seed). Covered here:

- all six are seeded with the built-in (signing-key-derived) wallets,
  bypassing the reserved-prefix guard;
- the seed is idempotent (re-seeding updates in place, no duplicates);
- LIVENESS_COHORT_WALLETS env overrides the wallets;
- a seeded agent heartbeats with the operator token;
- a seeded agent heartbeats with an EIP-191 signature from the registered
  wallet (proves the wallet binding is the runner's signing identity);
- the public route STILL refuses sincor- prefixed IDs (guard intact).
"""
from __future__ import annotations

import json
import os
import time
import uuid

from eth_account import Account
from eth_account.messages import encode_defunct
import pytest

from conftest import hb_headers
from sincor2.a2a_inbound import reset_fabric
from sincor2.a2a_inbound_ext import (
    _LIVENESS_COHORT_WALLETS_DEFAULT,
    _liveness_cohort_wallets,
    build_heartbeat_message,
    ensure_liveness_cohort,
    get_fabric,
    register_agent_record,
)


EXPECTED_IDS = [f"sincor-liveness-{i:02d}" for i in range(1, 7)]


def _fabric_agent(agent_id):
    fabric = get_fabric()
    with fabric.lock:
        return dict(fabric.agents.get(agent_id) or {})


def _fresh_key_wallet(monkeypatch):
    """Override the cohort wallets with a key this test holds."""
    acct = Account.create()
    wallets = {aid: (acct.address if aid == "sincor-liveness-01"
                     else _LIVENESS_COHORT_WALLETS_DEFAULT[aid])
               for aid in EXPECTED_IDS}
    monkeypatch.setenv("LIVENESS_COHORT_WALLETS", json.dumps(wallets))
    return acct


class TestLivenessCohortSeed:
    def test_seeds_all_six_with_builtin_wallets(self, client):
        reset_fabric()
        seeded = ensure_liveness_cohort()
        assert seeded == EXPECTED_IDS
        for agent_id in EXPECTED_IDS:
            agent = _fabric_agent(agent_id)
            assert agent, agent_id
            assert agent["wallet"].lower() == _LIVENESS_COHORT_WALLETS_DEFAULT[agent_id].lower()

    def test_idempotent_reseed(self, client):
        reset_fabric()
        ensure_liveness_cohort()
        fabric = get_fabric()
        with fabric.lock:
            before = len(fabric.agents)
        seeded = ensure_liveness_cohort()
        assert seeded == EXPECTED_IDS
        with fabric.lock:
            after = len(fabric.agents)
            records = [dict(a) for aid, a in fabric.agents.items()
                       if aid in EXPECTED_IDS]
        assert after == before
        assert len(records) == 6

    def test_env_override(self, client, monkeypatch):
        reset_fabric()
        acct = _fresh_key_wallet(monkeypatch)
        ensure_liveness_cohort()
        agent = _fabric_agent("sincor-liveness-01")
        assert agent["wallet"].lower() == acct.address.lower()

    def test_heartbeat_with_operator_token(self, client, monkeypatch):
        reset_fabric()
        monkeypatch.setenv("AGENT_HEARTBEAT_TOKEN", "test-op-token-" + uuid.uuid4().hex)
        ensure_liveness_cohort()
        token = os.environ["AGENT_HEARTBEAT_TOKEN"]
        r = client.post("/v1/a2a/heartbeat",
                        json={"agent_id": "sincor-liveness-03"},
                        headers=hb_headers())
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["agent_id"] == "sincor-liveness-03"

    def test_heartbeat_with_wallet_signature(self, client, monkeypatch):
        reset_fabric()
        acct = _fresh_key_wallet(monkeypatch)
        ensure_liveness_cohort()
        ts = int(time.time() * 1000)
        sig = acct.sign_message(
            encode_defunct(text=build_heartbeat_message("sincor-liveness-01", ts))
        ).signature
        r = client.post("/v1/a2a/heartbeat", json={
            "agent_id": "sincor-liveness-01",
            "signature": "0x" + bytes(sig).hex(),
            "ts": ts,
        })
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["agent_id"] == "sincor-liveness-01"

    # --- Review-finding regression tests (2026-10-14) ---------------------

    def test_malformed_wallet_skips_only_that_agent(self, client, monkeypatch):
        """Finding 1: one malformed wallet must not abort the other five."""
        reset_fabric()
        wallets = dict(_LIVENESS_COHORT_WALLETS_DEFAULT)
        wallets["sincor-liveness-03"] = "0xNOT-A-WALLET"
        monkeypatch.setenv("LIVENESS_COHORT_WALLETS", json.dumps(wallets))
        seeded = ensure_liveness_cohort()  # must not raise
        assert "sincor-liveness-03" not in seeded
        assert sorted(seeded) == sorted(
            aid for aid in EXPECTED_IDS if aid != "sincor-liveness-03")
        for agent_id in EXPECTED_IDS:
            if agent_id == "sincor-liveness-03":
                assert not _fabric_agent(agent_id)
            else:
                assert _fabric_agent(agent_id)

    def test_origin_is_internal_and_not_spoofable(self, client):
        """Finding 2: system agents are stamped origin='internal',
        server-assigned — never from request input."""
        reset_fabric()
        ensure_liveness_cohort()
        for agent_id in EXPECTED_IDS:
            assert _fabric_agent(agent_id)["origin"] == "internal", agent_id
        # Even if a body claims an origin, the internal path ignores it.
        reset_fabric()
        snap = register_agent_record({
            "agent_id": "sincor-liveness-01",
            "name": "Spoof Attempt",
            "description": "x",
            "version": "1.0.0",
            "capability_tags": ["defi"],
            "rpc_callback": "https://getsincor.com/api/a2a",
            "wallet": _LIVENESS_COHORT_WALLETS_DEFAULT["sincor-liveness-01"],
            "chain_id": 8453,
            "origin": "external",
        }, _internal_reputation=1.0)
        assert snap["origin"] == "internal"

    def test_partial_override_merges_with_defaults(self, client, monkeypatch):
        """Finding 3: overriding one wallet keeps the other five defaults."""
        reset_fabric()
        acct = Account.create()
        monkeypatch.setenv("LIVENESS_COHORT_WALLETS", json.dumps({
            "sincor-liveness-02": acct.address,
        }))
        seeded = ensure_liveness_cohort()
        assert seeded == EXPECTED_IDS
        assert _fabric_agent("sincor-liveness-02")["wallet"].lower() == \
            acct.address.lower()
        for agent_id in EXPECTED_IDS:
            if agent_id == "sincor-liveness-02":
                continue
            assert _fabric_agent(agent_id)["wallet"].lower() == \
                _LIVENESS_COHORT_WALLETS_DEFAULT[agent_id].lower(), agent_id

    def test_json_null_stays_null(self, client, monkeypatch):
        """Finding 4: a JSON null wallet is preserved as None, never the
        literal string 'None' — and skips only that agent."""
        reset_fabric()
        monkeypatch.setenv("LIVENESS_COHORT_WALLETS", json.dumps({
            "sincor-liveness-01": None,
        }))
        wallets = _liveness_cohort_wallets()
        assert wallets["sincor-liveness-01"] is None
        # The other five keep their defaults (finding 3 composes).
        for agent_id in EXPECTED_IDS[1:]:
            assert wallets[agent_id] == _LIVENESS_COHORT_WALLETS_DEFAULT[agent_id]
        seeded = ensure_liveness_cohort()  # must not raise
        assert "sincor-liveness-01" not in seeded
        assert len(seeded) == 5
        for agent_id in EXPECTED_IDS:
            agent = _fabric_agent(agent_id)
            if agent:
                assert agent["wallet"] != "None", agent_id

    def test_public_route_still_refuses_protected_prefix(self, client):
        reset_fabric()
        r = client.post("/v1/a2a/register", json={
            "agent_id": "sincor-liveness-99",
            "name": "Impostor",
            "capability_tags": ["spoof"],
            "rpc_callback": "https://evil.example/rpc",
        })
        assert r.status_code == 400
        assert "reserved" in (r.get_json() or {}).get("error", "")



class TestReseedPreservesEarnedReputation:
    """W-97 regression: startup reseed must not overwrite earned reputation.

    ``ensure_liveness_cohort()`` runs at every startup and re-registers each
    cohort agent through the internal path with ``_internal_reputation=1.0``.
    The seed value is a floor for fresh agents, never a reset: a
    re-registered agent keeps its already-earned reputation.

    Note on scale: reputation is clamped to [0, 1] by ``_apply_reputation``,
    so the literal W-97 write-up's "2.5 -> 1.0" example cannot occur in this
    codebase. The real W-97 effect here is the mirror image: an agent that
    earned 0.4 (two settlements) was silently promoted to full 1.0 on every
    restart, bypassing merit. These tests pin the floor semantics against
    that: earned < seed must be preserved, fresh agents still get the seed.
    """

    def test_reseed_keeps_earned_reputation(self, client):
        from sincor2.a2a_inbound import _apply_reputation

        reset_fabric()
        ensure_liveness_cohort()
        # Simulate earned reputation below the seed floor (two settlements).
        fabric = get_fabric()
        with fabric.lock:
            _apply_reputation(fabric.agents["sincor-liveness-01"], 0.4)
        # Next startup's reseed must not promote it to the seed floor.
        ensure_liveness_cohort()
        assert _fabric_agent("sincor-liveness-01")["reputation"] == pytest.approx(0.4)

    def test_fresh_agent_gets_seed_floor(self, client):
        reset_fabric()
        ensure_liveness_cohort()
        assert _fabric_agent("sincor-liveness-01")["reputation"] == 1.0

    def test_reseed_never_resets_to_seed(self, client):
        """Seed is never written back onto an existing record, in either
        direction: below-floor earned rep is preserved, not promoted."""
        from sincor2.a2a_inbound import _apply_reputation

        reset_fabric()
        ensure_liveness_cohort()
        fabric = get_fabric()
        with fabric.lock:
            _apply_reputation(fabric.agents["sincor-liveness-01"], 0.75)
        ensure_liveness_cohort()
        assert _fabric_agent("sincor-liveness-01")["reputation"] == pytest.approx(0.75)
