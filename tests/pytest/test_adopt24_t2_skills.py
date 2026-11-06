"""Adopt-24 Wave T2 — the 9 ComposioHQ Tier-2 content/document skills.

Skills under test (each carries its own Apache-2.0 LICENSE.txt upstream
in ComposioHQ/awesome-claude-skills, <skill>/LICENSE.txt):

    webapp-testing, mcp-builder, skill-creator, internal-comms,
    brand-guidelines, canvas-design, theme-factory, artifacts-builder,
    slack-gif-creator

Adapted into the SINCOR AgentSkill catalogue format. This file proves:

  (i)   registration — all 9 ids in SINCOR_SKILLS and on the built agent
        card; in a fresh import the catalogue grew by exactly these 9
        over the 18 base entries (no absolute total is asserted here —
        the sibling wave lands separately);
  (ii)  quote for all 9 via the real /api/a2a/quote route, at the
        registered AXM / SINC prices;
  (iii) peek for all 9 via /api/a2a/peek/<id> (labelled metadata);
  (iv)  serve smoke for all 9 through the real message/send task path —
        a completed task with non-empty output;
  (v)   ONE full-loop integration test for internal-comms:
        quote -> free serve (daily ration) -> paid serve -> settle
        accounting (5% platform fee proof + reputation record);
  (vi)  implementation-ref resolution for all 9.

Patterns follow test_b44_new_caller_quote.py / test_a2a_quota_identity.py
(quote + signed sends), test_wave1_hook_funnel.py (register -> ration
full loop), test_wallet_sanctions_a2a_skill.py (standalone A2ARouter
client) and test_a2a_smoke.py (serve path).

Run: PYTHONPATH=src:. <venv>/python -m pytest \
  tests/pytest/test_adopt24_t2_skills.py -q
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import time
import uuid

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from sincor2 import a2a_integration as ai
from sincor2.a2a_integration import (
    SINCOR_SKILLS,
    build_agent_card,
    skill_implementation_ref,
)

# skill_id -> (axm price, sinc price, latency seconds, task type)
NEW_SKILLS = {
    "webapp-testing": (2.0, 2, 45, "quality_audit"),
    "mcp-builder": (3.0, 3, 45, "agent_management"),
    "skill-creator": (1.0, 1, 20, "agent_management"),
    "internal-comms": (2.0, 2, 20, "content_generation"),
    "brand-guidelines": (2.0, 2, 25, "content_generation"),
    "canvas-design": (3.0, 3, 45, "content_generation"),
    "theme-factory": (3.0, 3, 40, "local_business_site"),
    "artifacts-builder": (3.0, 3, 45, "local_business_site"),
    "slack-gif-creator": (2.0, 2, 30, "content_generation"),
}
NEW_IDS = sorted(NEW_SKILLS)

BASE_18_IDS = {
    "lead-enrichment", "competitor-intel", "outreach-sequence",
    "healthcare-credential-check", "dental-billing-scrub",
    "compliance-sbom", "market-forecast", "deal-scoring",
    "content-blog", "cashflow-recovery", "local-business-site-builder",
    "toa-decision", "contract-negotiation", "quality-audit",
    "agent-lifecycle", "axiom-payment", "issue-creator-token",
    "wallet-sanctions-screen",
}

PAID_TX = "0x" + "cd" * 32


def _skill(skill_id):
    matches = [s for s in SINCOR_SKILLS if s.id == skill_id]
    assert len(matches) == 1, f"{skill_id} must be registered exactly once"
    return matches[0]


@pytest.fixture()
def a2a_client():
    """Standalone A2A app: A2ARouter + inbound registration routes."""
    from sincor2.a2a_inbound import register as register_inbound
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.a2a_integration import A2ARouter
    from sincor2.a2a_rate_limits import reset_a2a_limits
    from sincor2.free_ration import daily_ration_tracker

    reset_fabric()
    reset_a2a_limits()
    daily_ration_tracker.clear()
    ai._free_quota_tracker._store.clear()
    app = Flask("adopt24_t2")
    app.register_blueprint(A2ARouter().blueprint)
    register_inbound(app)
    app.config.update(TESTING=True)
    yield app.test_client()
    daily_ration_tracker.clear()
    ai._free_quota_tracker._store.clear()
    reset_fabric()


def _create_auth(acct, caller_id, skill_id):
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    sig = acct.sign_message(
        encode_defunct(text=ai._auth_create_message(caller_id, skill_id, ts, nonce))
    ).signature.hex()
    return {
        "ownerWallet": acct.address,
        "authSignature": "0x" + sig,
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def _send(client, acct, skill_id, text, caller_id, extra_params=None):
    """message/send with create-auth + quota signature for ``acct``."""
    ts_ms = int(time.time() * 1000)
    qmsg = ai.quota_message_for_send(skill_id, text, ts_ms)
    qsig = acct.sign_message(encode_defunct(text=qmsg)).signature.hex()
    params = {
        "skillId": skill_id,
        "callerId": caller_id,
        "message": {
            "role": "user",
            "parts": [{"text": text}],
            "contextId": f"ctx-{caller_id}-{uuid.uuid4().hex[:8]}",
            "metadata": {
                "signature": "0x" + qsig,
                "quota_ts": str(ts_ms),
                "wallet": acct.address,
            },
        },
        **_create_auth(acct, caller_id, skill_id),
    }
    if extra_params:
        params.update(extra_params)
    resp = client.post("/api/a2a", json={
        "jsonrpc": "2.0", "id": 1, "method": "message/send", "params": params,
    })
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "result" in body, body
    return body["result"]


def _quote(client, acct, skill_id):
    ts = int(time.time() * 1000)
    message = ai.quota_message_for_quote(skill_id, ts)
    sig = acct.sign_message(encode_defunct(text=message)).signature.hex()
    resp = client.post("/api/a2a/quote", json={
        "skill_id": skill_id, "signature": "0x" + sig,
        "quota_ts": str(ts), "wallet": acct.address,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _register_agent(client, acct, agent_id):
    from sincor2.a2a_identity import register_message
    from sincor2.a2a_inbound import _now_ms

    ts = _now_ms()
    sig = acct.sign_message(
        encode_defunct(text=register_message(agent_id, ts))).signature
    body = {
        "agent_id": agent_id,
        "name": "Adopt24 T2 Agent",
        "description": "adopt24 t2 test agent",
        "version": "1.0.0",
        "capability_tags": ["internal-comms"],
        "skills": [{"id": "internal-comms", "name": "Internal Comms"}],
        "rpc_callback": "https://agent.example/rpc",
        "wallet": acct.address,
        "registration_wallet": acct.address,
        "registration_ts": ts,
        "registration_signature": "0x" + bytes(sig).hex(),
    }
    resp = client.post("/v1/a2a/register", json=body)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


# ---------------------------------------------------------------------------
# (i) Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_all_nine_in_catalogue(self):
        ids = [s.id for s in SINCOR_SKILLS]
        for sid in NEW_IDS:
            assert ids.count(sid) == 1, sid
        # No duplicate ids anywhere in the catalogue.
        assert len(ids) == len(set(ids))

    def test_pricing_latency_and_shape(self):
        for sid, (axm, sinc, latency, _task_type) in NEW_SKILLS.items():
            skill = _skill(sid)
            assert skill.axm_price_wei == int(axm * 10**18), sid
            assert skill.sinc_price == sinc, sid
            assert skill.estimated_latency_seconds == latency, sid
            assert skill.reputation_floor == 0, sid
            assert skill.free_quota == 0, sid
            assert len(skill.description) >= 40, sid
            assert len(skill.examples) == 2, sid
            assert skill.tags, sid

    def test_agent_card_includes_all_nine(self):
        card = build_agent_card().to_dict()
        advertised = {s["id"]: s for s in card["skills"]}
        for sid, (axm, sinc, latency, _t) in NEW_SKILLS.items():
            assert sid in advertised, sid
            entry = advertised[sid]
            assert entry["axmPriceWei"] == str(int(axm * 10**18)), sid
            assert entry["sincPrice"] == sinc, sid
            assert entry["estimatedLatencySeconds"] == latency, sid
            assert entry["freeQuota"] == 0, sid
            assert entry["freeDailyRation"] == ai.DAILY_RATION_LIMIT, sid

    def test_fresh_import_grew_by_exactly_nine_over_base(self):
        """Fresh interpreter: the 18 base + these 9 are all registered.

        Wave-local version asserted the fresh catalogue was exactly
        18 + 9; the combined Adopt-24 assembly also carries the 15 T1
        skills, so the assembled truth is 42 ids — the base and these 9
        must be present, with no duplicates and nothing beyond the
        adopted 24 extra ids (checked via the total)."""
        code = (
            "import json;"
            "from sincor2.a2a_integration import SINCOR_SKILLS;"
            "print(json.dumps(sorted(s.id for s in SINCOR_SKILLS)))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=120,
        )
        assert out.returncode == 0, out.stderr
        import json

        fresh_ids = json.loads(out.stdout.strip().splitlines()[-1])
        assert BASE_18_IDS | set(NEW_IDS) <= set(fresh_ids)
        assert len(fresh_ids) == len(set(fresh_ids)), "duplicate skill ids"
        assert len(fresh_ids) == 42, (
            "assembled Adopt-24 catalogue = 18 base + 15 T1 + 9 T2")


# ---------------------------------------------------------------------------
# (vi) Implementation-ref resolution
# ---------------------------------------------------------------------------

class TestImplementationRef:
    def test_ref_resolves_for_all_nine(self):
        from sincor2 import vertical_dispatch as vd

        for sid, (_a, _s, _l, task_type) in NEW_SKILLS.items():
            assert vd._KERNEL_SKILL_MAP.get(sid) == task_type, sid
            assert vd._SKILL_TASK_TYPE_MAP.get(sid) == task_type, sid
            ref = skill_implementation_ref(sid)
            assert ref is not None, sid
            module_name, _, qual = ref.partition(":")
            module = importlib.import_module(module_name)
            # Qualname is "_KERNEL_SKILL_MAP[<id>]" / map form: the map
            # attribute itself must exist on the module.
            attr = qual.split("[")[0]
            assert hasattr(module, attr), (sid, ref)


# ---------------------------------------------------------------------------
# (ii) Quote + (iii) Peek through the real routes
# ---------------------------------------------------------------------------

class TestQuoteAndPeek:
    @pytest.mark.parametrize("skill_id", NEW_IDS)
    def test_quote_route(self, a2a_client, skill_id):
        axm, sinc, latency, _t = NEW_SKILLS[skill_id]
        resp = a2a_client.get(f"/api/a2a/quote?skill_id={skill_id}")
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        assert data["skill_id"] == skill_id
        assert data["axm_price_wei"] == str(int(axm * 10**18))
        assert int(data.get("sinc_amount") or 0) == 0  # AXM-primary quote
        assert data["estimated_latency_seconds"] == latency
        assert data["free_daily_ration_limit"] == ai.DAILY_RATION_LIMIT
        # Registered SINC price lives on the skill / card.
        assert _skill(skill_id).sinc_price == sinc

    @pytest.mark.parametrize("skill_id", NEW_IDS)
    def test_peek_route(self, a2a_client, skill_id):
        resp = a2a_client.get(f"/api/a2a/peek/{skill_id}")
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["ok"] is True
        assert body["skill_id"] == skill_id
        assert body["free"] is True
        assert body["peek_kind"] == "metadata"
        assert body["metadata"]["id"] == skill_id
        axm, _s, _l, _t = NEW_SKILLS[skill_id]
        assert body["metadata"]["axmPriceWei"] == str(int(axm * 10**18))


# ---------------------------------------------------------------------------
# (iv) Serve smoke through the real task path
# ---------------------------------------------------------------------------

class TestServeSmoke:
    @pytest.mark.parametrize("skill_id", NEW_IDS)
    def test_serve_completes_with_output(self, a2a_client, skill_id):
        acct = Account.create()
        result = _send(
            a2a_client, acct, skill_id,
            f"Run {skill_id} on a sample request.",
            f"t2-smoke-{skill_id}",
        )
        assert result["status"]["state"] == "completed", result
        artifacts = result.get("artifacts") or []
        assert artifacts, result
        text = "".join(
            part.get("text", "")
            for art in artifacts for part in art.get("parts", [])
        )
        assert text.strip(), f"{skill_id} produced empty output"


# ---------------------------------------------------------------------------
# (v) Full loop: internal-comms quote -> free serve -> paid serve -> settle
# ---------------------------------------------------------------------------

class TestInternalCommsFullLoop:
    def test_quote_free_paid_settle(self, a2a_client):
        skill_id = "internal-comms"
        axm, _sinc, _lat, _t = NEW_SKILLS[skill_id]
        acct = Account.create()
        agent_id = "t2-loop-agent"

        # Register so the wallet owns the universal daily ration.
        reg = _register_agent(a2a_client, acct, agent_id)
        assert reg["agent_id"] == agent_id

        # 1. Quote: registered wallet sees the full daily ration, free.
        quote = _quote(a2a_client, acct, skill_id)
        assert quote["free_daily_ration_remaining"] == ai.DAILY_RATION_LIMIT
        assert quote["is_free"] is True

        # 2. Free serve via the daily ration, then drain the ration.
        first = _send(a2a_client, acct, skill_id,
                      "Write a 3P update for the platform team.", agent_id)
        assert first["status"]["state"] == "completed"
        assert first["metadata"]["free_call"] is True
        assert first["metadata"]["free_daily_ration"] is True
        quote = _quote(a2a_client, acct, skill_id)
        assert quote["free_daily_ration_remaining"] == ai.DAILY_RATION_LIMIT - 1
        for i in range(ai.DAILY_RATION_LIMIT - 1):
            drained = _send(a2a_client, acct, skill_id,
                            f"Draft internal update number {i}.", agent_id)
            assert drained["metadata"]["free_call"] is True
        quote = _quote(a2a_client, acct, skill_id)
        assert quote["free_daily_ration_remaining"] == 0

        # 3. Paid serve: ration exhausted, caller pays the registered
        #    price with a tx hash (test env verifies via dev bypass).
        rep_key = "wallet:" + acct.address.lower()
        rep_before = ai._reputation_ledger.score(rep_key)
        paid = _send(
            a2a_client, acct, skill_id,
            "Write the weekly leadership update.", agent_id,
            extra_params={
                "txHash": PAID_TX,
                "axmPaidWei": str(int(axm * 10**18)),
            },
        )
        assert paid["status"]["state"] == "completed"
        assert paid["metadata"]["free_call"] is False
        task_id = paid["id"]
        proof = paid["metadata"]["proof_of_settlement"]
        assert proof["axm_paid_wei"] == str(int(axm * 10**18))
        assert int(proof["platform_fee_wei"]) == int(axm * 10**18) // 20

        # 4. Settle accounting: the settle route issues a verifiable
        #    proof carrying the 5% platform fee to treasury, and the
        #    reputation ledger recorded the paid settlement.
        resp = a2a_client.post("/api/a2a/settle", json={
            "task_id": task_id, "tx_hash": PAID_TX,
        })
        assert resp.status_code == 200, resp.get_json()
        settled = resp.get_json()["proof_of_settlement"]
        assert settled["task_id"] == task_id
        assert settled["tx_hash"] == PAID_TX
        assert settled["axm_paid_wei"] == str(int(axm * 10**18))
        assert int(settled["platform_fee_wei"]) == int(axm * 10**18) // 20
        from sincor2.settlement_proofs import verify_proof_of_settlement

        ok, _reason = verify_proof_of_settlement(resp.get_json())
        assert ok
        assert ai._reputation_ledger.score(rep_key) == rep_before + 1
