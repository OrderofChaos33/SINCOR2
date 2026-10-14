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

from conftest import hb_headers
from sincor2.a2a_inbound import reset_fabric
from sincor2.a2a_inbound_ext import (
    _LIVENESS_COHORT_WALLETS_DEFAULT,
    build_heartbeat_message,
    ensure_liveness_cohort,
    get_fabric,
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
