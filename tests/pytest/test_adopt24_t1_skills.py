"""Adopt-24 Wave T1 — the 15 Tier-1 methodology skills (obra/superpowers, MIT).

Founder-approved catalogue adoption (2026-11-06): the 15 methodology
skills from obra/superpowers are re-expressed as SINCOR AgentSkill
entries (see THIRD_PARTY_NOTICES.md for the upstream list, the MIT
licence, and the two brand renames:
``using-superpowers`` -> ``agent-methodology-guide`` and
``diagnosing-superpowers`` -> ``agent-methodology-self-check``).

Count note: the base catalogue DEFINED in a2a_integration.py grows
18 -> 33 on this branch (18 + 15 T1 skills). 42 is the combined
Adopt-24 total once Wave T2's 9 skills land alongside these; the
count guards on THIS branch pin the standalone-correct 33 so the
branch is green on its own, and the coordinator raises them to 42
at assembly.

Pins:
* registration — all 15 ids in SINCOR_SKILLS with the approved
  pricing / latency, free_quota=0, reputation_floor=0, valid
  card rendering via build_agent_card();
* implementation references — every id resolves via
  skill_implementation_ref() and both vertical_dispatch maps carry
  the approved task types;
* quote for all 15 via the real /api/a2a/quote route;
* peek for all 15 via /api/a2a/peek/<id>;
* serve smoke for all 15 through the real message/send serve path
  (completed task, non-empty output);
* one full-loop integration test for verification-before-completion:
  quote -> register -> free serve (daily ration) -> paid serve ->
  settle accounting.

Run: PYTHONPATH=src:. <venv>/python -m pytest \
  tests/pytest/test_adopt24_t1_skills.py -q
"""
from __future__ import annotations

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
    compile_skill_schemas,
    skill_implementation_ref,
)
from sincor2.free_ration import DAILY_RATION_LIMIT

# skill_id -> (axm_price, sinc_price, estimated_latency_seconds)
NEW_SKILLS = {
    "verification-before-completion": (2.0, 2, 20),
    "test-driven-development": (2.0, 2, 30),
    "systematic-debugging": (2.0, 2, 30),
    "requesting-code-review": (2.0, 2, 20),
    "receiving-code-review": (2.0, 2, 20),
    "agent-methodology-self-check": (2.0, 2, 20),
    "brainstorming": (2.0, 2, 25),
    "subagent-driven-development": (1.0, 1, 20),
    "dispatching-parallel-agents": (1.0, 1, 15),
    "writing-plans": (1.0, 1, 25),
    "executing-plans": (1.0, 1, 30),
    "using-git-worktrees": (1.0, 1, 15),
    "writing-skills": (1.0, 1, 25),
    "finishing-a-development-branch": (1.0, 1, 15),
    "agent-methodology-guide": (1.0, 1, 10),
}

EXPECTED_TASK_TYPES = {
    "verification-before-completion": "quality_audit",
    "test-driven-development": "quality_audit",
    "systematic-debugging": "quality_audit",
    "requesting-code-review": "quality_audit",
    "receiving-code-review": "quality_audit",
    "agent-methodology-self-check": "quality_audit",
    "brainstorming": "toa_decision",
    "subagent-driven-development": "agent_management",
    "dispatching-parallel-agents": "agent_management",
    "writing-plans": "agent_management",
    "executing-plans": "agent_management",
    "using-git-worktrees": "agent_management",
    "writing-skills": "agent_management",
    "finishing-a-development-branch": "agent_management",
    "agent-methodology-guide": "agent_management",
}

SERVE_INPUTS = {
    "verification-before-completion": "Verify the claim that the new quote route passes its tests.",
    "test-driven-development": "Add a retry helper that retries a failed operation three times.",
    "systematic-debugging": "The settlement test fails only when run after the quota tests.",
    "requesting-code-review": "Review the branch that adds the new catalogue skills.",
    "receiving-code-review": "A reviewer asked for the price constants to be named values.",
    "agent-methodology-self-check": "The last session re-ran a finished task twice and cost too much.",
    "brainstorming": "Design a booking reminder flow for a local detailing shop.",
    "subagent-driven-development": "Execute the catalogue adoption plan task by task with reviews.",
    "dispatching-parallel-agents": "Three unrelated test files are failing in different subsystems.",
    "writing-plans": "Plan the multi-step rollout of the new skill catalogue.",
    "executing-plans": "Execute the written catalogue adoption plan in this session.",
    "using-git-worktrees": "Start the catalogue feature in an isolated workspace.",
    "writing-skills": "Create a skill that teaches the sanctions screening workflow.",
    "finishing-a-development-branch": "The catalogue branch is green and ready to integrate.",
    "agent-methodology-guide": "Starting a new task: which methodology skill applies first?",
}

FULL_LOOP_SKILL = "verification-before-completion"

# Base catalogue ids captured at import (collection time, before any
# app fixture boots the platform bootstrap) — mirrors the pattern in
# test_skill_registry_integrity.py so counts stay suite-order safe.
_BASE_IDS_AT_IMPORT = [s.id for s in SINCOR_SKILLS]
BASE_CATALOGUE_COUNT = 42  # 18 pre-existing + 15 T1 + 9 T2 (combined Adopt-24 assembly; T1 branch alone pinned 33)


def _skill(skill_id):
    matches = [s for s in SINCOR_SKILLS if s.id == skill_id]
    assert len(matches) == 1, f"{skill_id} must be registered exactly once"
    return matches[0]


@pytest.fixture()
def a2a_client():
    """Minimal A2A-only Flask client (b44 / wallet-sanctions pattern)."""
    from sincor2.free_ration import daily_ration_tracker

    ai._free_quota_tracker._store.clear()
    daily_ration_tracker.clear()
    app = Flask("adopt24_t1_a2a")
    app.register_blueprint(ai.A2ARouter().blueprint)
    app.config.update(TESTING=True)
    yield app.test_client()
    ai._free_quota_tracker._store.clear()
    daily_ration_tracker.clear()


def _signed_params(extra: dict) -> dict:
    """message/send create-auth fields (test_a2a_smoke pattern)."""
    acct = Account.from_key("0x" + "c3" * 32)
    skill_id = extra.get("skillId", "lead-enrichment")
    label = extra.get("callerId", "adopt24-t1")
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    sig = acct.sign_message(
        encode_defunct(text=ai._auth_create_message(label, skill_id, ts, nonce))
    ).signature.hex()
    return {
        **extra,
        "callerId": label,
        "ownerWallet": acct.address,
        "authSignature": "0x" + sig,
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def _create_auth_params(acct, caller_id, skill_id, **extra):
    """Create-auth fields signed by an arbitrary wallet."""
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    message = (
        "SINCOR-A2A task-create\n"
        f"agent_id:{caller_id}\n"
        f"skill_id:{skill_id}\n"
        f"timestamp:{ts}\n"
        f"nonce:{nonce}"
    )
    sig = acct.sign_message(encode_defunct(text=message)).signature.hex()
    params = {
        "skillId": skill_id,
        "callerId": caller_id,
        "authSignature": "0x" + sig,
        "authTimestamp": ts,
        "authNonce": nonce,
        "ownerWallet": acct.address,
    }
    params.update(extra)
    return params


def _output_text(result: dict) -> str:
    parts = []
    for artifact in result.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            if part.get("text"):
                parts.append(part["text"])
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# (i) Registration
# ---------------------------------------------------------------------------

def test_t1_skills_registered_with_approved_pricing():
    assert len(_BASE_IDS_AT_IMPORT) == BASE_CATALOGUE_COUNT, (
        "base catalogue changed — T1 branch pins 33 (18 + 15); "
        "the combined Adopt-24 assembly raises this to 42")
    for skill_id, (axm, sinc, latency) in NEW_SKILLS.items():
        skill = _skill(skill_id)
        assert skill.axm_price_wei == int(axm * 10**18), skill_id
        assert skill.sinc_price == sinc, skill_id
        assert skill.estimated_latency_seconds == latency, skill_id
        assert skill.reputation_floor == 0, skill_id
        assert skill.free_quota == 0, skill_id
        assert skill_id not in ai.FREE_QUOTA_SKILLS, skill_id
        assert len(skill.description.strip()) >= 40, skill_id
        assert len(skill.examples) == 2, skill_id
        assert all(e.strip() for e in skill.examples), skill_id
        assert skill.tags and all(t.strip() for t in skill.tags), skill_id


def test_t1_schemas_compile():
    compiled = compile_skill_schemas([_skill(s) for s in NEW_SKILLS])
    assert len(compiled) == len(NEW_SKILLS)


def test_agent_card_lists_all_t1_skills():
    card = build_agent_card().to_dict()
    entries = {s["id"]: s for s in card["skills"]}
    # The card's base-catalogue entries are exactly the import-time base
    # catalogue (vertical packs appended at bootstrap are extra entries).
    base_on_card = [sid for sid in entries if sid in set(_BASE_IDS_AT_IMPORT)]
    assert len(base_on_card) == BASE_CATALOGUE_COUNT
    for skill_id, (axm, sinc, latency) in NEW_SKILLS.items():
        assert skill_id in entries, skill_id
        entry = entries[skill_id]
        assert entry["axmPriceWei"] == str(int(axm * 10**18)), skill_id
        assert entry["sincPrice"] == sinc, skill_id
        assert entry["estimatedLatencySeconds"] == latency, skill_id
        assert entry["reputationFloor"] == 0, skill_id
        assert entry["freeQuota"] == 0, skill_id
        assert entry["freeDailyRation"] == DAILY_RATION_LIMIT, skill_id
        assert entry["inputSchema"], skill_id
        assert entry["outputSchema"], skill_id
        # to_dict round-trips through the owning skill object
        assert entry == _skill(skill_id).to_dict(), skill_id


# ---------------------------------------------------------------------------
# (vi) Implementation references
# ---------------------------------------------------------------------------

def test_t1_implementation_refs_resolve():
    import importlib

    from sincor2 import vertical_dispatch as vd

    for skill_id, task_type in EXPECTED_TASK_TYPES.items():
        assert vd._KERNEL_SKILL_MAP[skill_id] == task_type, skill_id
        assert vd._SKILL_TASK_TYPE_MAP[skill_id] == task_type, skill_id
        ref = skill_implementation_ref(skill_id)
        assert ref, f"{skill_id}: no implementation reference"
        module_name, _, qual = ref.partition(":")
        module = importlib.import_module(module_name)
        if "[" in qual:
            map_name = qual.split("[", 1)[0]
            skill_key = qual.split("[", 1)[1].rstrip("]")
            assert skill_key in getattr(module, map_name), skill_id
        elif qual:
            assert hasattr(module, qual), skill_id


# ---------------------------------------------------------------------------
# (ii) Quote for all 15 via the real route
# ---------------------------------------------------------------------------

def test_quote_for_all_t1_skills(a2a_client):
    for skill_id, (axm, sinc, latency) in NEW_SKILLS.items():
        resp = a2a_client.post(
            "/api/a2a/quote",
            json={"skill_id": skill_id, "caller_id": "adopt24-t1-quote"},
        )
        assert resp.status_code == 200, (skill_id, resp.get_json())
        data = resp.get_json()
        assert data["skill_id"] == skill_id
        # Unsigned caller: nothing is granted, the paid price is visible.
        assert data["is_free"] is False, skill_id
        assert data["axm_price_wei"] == str(int(axm * 10**18)), skill_id
        assert data["axm_price_display"] == f"{axm:.4f} AXM", skill_id
        assert data["estimated_latency_seconds"] == latency, skill_id
        assert data["primary_token"] == "AXIOM", skill_id
        assert data["free_quota_remaining"] == 0, skill_id
        assert data["free_daily_ration_remaining"] == DAILY_RATION_LIMIT, (
            skill_id)
        expected_fee = int(axm * 10**18) * 500 // 10_000
        assert int(data["platform_fee_wei"]) == expected_fee, skill_id
        assert data["input_schema"], skill_id
        assert data["output_schema"], skill_id


# ---------------------------------------------------------------------------
# (iii) Peek for all 15 via the real route
# ---------------------------------------------------------------------------

def test_peek_for_all_t1_skills(a2a_client):
    for skill_id, (axm, sinc, latency) in NEW_SKILLS.items():
        resp = a2a_client.get(f"/api/a2a/peek/{skill_id}")
        assert resp.status_code == 200, (skill_id, resp.get_json())
        body = resp.get_json()
        assert body["ok"] is True, skill_id
        assert body["skill_id"] == skill_id
        assert body["free"] is True, skill_id
        # These skills run as agent tasks: labelled metadata, no fake sample.
        assert body["sample"] is False, skill_id
        assert body["peek_kind"] == "metadata", skill_id
        metadata = body["metadata"]
        assert metadata["id"] == skill_id
        assert metadata["axmPriceWei"] == str(int(axm * 10**18)), skill_id
        assert metadata["sincPrice"] == sinc, skill_id
        assert metadata["freeDailyRation"] == DAILY_RATION_LIMIT, skill_id
        assert metadata["estimatedLatencySeconds"] == latency, skill_id


# ---------------------------------------------------------------------------
# (iv) Serve smoke for all 15 through the real task serve path
# ---------------------------------------------------------------------------

def test_serve_smoke_all_t1_skills(client):
    for skill_id, text in SERVE_INPUTS.items():
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": _signed_params({
                "skillId": skill_id,
                "callerId": f"adopt24-t1-{skill_id}",
                "message": {
                    "role": "user",
                    "parts": [{"text": text}],
                    "contextId": f"ctx-adopt24-t1-{skill_id}",
                },
            }),
        }
        resp = client.post("/api/a2a", json=body)
        assert resp.status_code == 200, (skill_id, resp.get_json())
        payload = resp.get_json()
        assert "error" not in payload, (skill_id, payload)
        result = payload["result"]
        assert result["status"]["state"] == "completed", (skill_id, result)
        assert _output_text(result).strip(), (
            f"{skill_id}: completed task with empty output")
        proof = result["metadata"]["proof_of_settlement"]
        assert proof["skill_id"] == skill_id
        assert proof["result_hash"], skill_id


# ---------------------------------------------------------------------------
# (v) Full loop: quote -> free serve (daily ration) -> paid serve -> settle
# ---------------------------------------------------------------------------

def _register_agent(client, key, agent_id):
    from sincor2.a2a_identity import register_message
    from sincor2.a2a_inbound import _now_ms

    ts = _now_ms()
    sig = key.sign_message(
        encode_defunct(text=register_message(agent_id, ts))).signature
    body = {
        "agent_id": agent_id,
        "name": "Adopt24 T1 Loop Agent",
        "description": "full-loop test agent",
        "version": "1.0.0",
        "capability_tags": [FULL_LOOP_SKILL],
        "skills": [{"id": FULL_LOOP_SKILL, "name": "Verification"}],
        "rpc_callback": "https://agent.example/rpc",
        "wallet": key.address,
        "registration_wallet": key.address,
        "registration_ts": ts,
        "registration_signature": "0x" + bytes(sig).hex(),
    }
    resp = client.post("/v1/a2a/register", json=body)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _signed_quote(client, key, skill_id):
    ts = int(time.time() * 1000)
    message = ai.quota_message_for_quote(skill_id, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature.hex()
    resp = client.post("/api/a2a/quote", json={
        "skill_id": skill_id, "signature": "0x" + sig,
        "quota_ts": str(ts), "wallet": key.address,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _ration_send(client, key, skill_id, text, caller_id):
    ts_ms = int(time.time() * 1000)
    qmsg = ai.quota_message_for_send(skill_id, text, ts_ms)
    qsig = key.sign_message(encode_defunct(text=qmsg)).signature.hex()
    params = _create_auth_params(
        key, caller_id, skill_id,
        message={
            "role": "user",
            "parts": [{"text": text}],
            "metadata": {
                "signature": "0x" + qsig,
                "quota_ts": str(ts_ms),
                "wallet": key.address,
            },
        },
    )
    resp = client.post(
        "/api/a2a", json={"jsonrpc": "2.0", "id": 1,
                          "method": "message/send", "params": params})
    assert resp.status_code == 200, resp.get_json()
    payload = resp.get_json()
    assert "error" not in payload, payload
    return payload["result"]


def test_full_loop_verification_before_completion(client):
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.free_ration import daily_ration_tracker
    from sincor2.settlement_proofs import verify_proof_of_settlement

    skill_id = FULL_LOOP_SKILL
    price_wei = int(NEW_SKILLS[skill_id][0] * 10**18)
    fee_wei = price_wei * 500 // 10_000
    agent_id = f"adopt24-t1-loop-{uuid.uuid4().hex[:8]}"
    paid_caller = f"adopt24-t1-paid-{uuid.uuid4().hex[:8]}"

    reset_fabric()
    daily_ration_tracker.clear()
    try:
        # 1. Quote (unsigned): documented new-caller entitlement is
        #    displayed, nothing is granted, the paid price is visible.
        resp = client.post("/api/a2a/quote", json={"skill_id": skill_id})
        assert resp.status_code == 200
        quote = resp.get_json()
        assert quote["axm_price_wei"] == str(price_wei)
        assert quote["is_free"] is False
        assert quote["free_daily_ration_remaining"] == DAILY_RATION_LIMIT

        # 2. Register, then the signed quote shows the ration as free.
        key = Account.create()
        reg = _register_agent(client, key, agent_id)
        assert reg["status"] == "registered"
        quote = _signed_quote(client, key, skill_id)
        assert quote["is_free"] is True
        assert quote["free_daily_ration_remaining"] == DAILY_RATION_LIMIT

        # 3. Free serve via the daily ration: completed, no payment,
        #    ration decremented for the next quote.
        result = _ration_send(
            client, key, skill_id,
            "Verify the claim that the catalogue tests pass.", agent_id)
        assert result["status"]["state"] == "completed"
        assert _output_text(result).strip()
        assert result["metadata"]["free_call"] is True
        assert result["metadata"]["free_daily_ration"] is True
        free_proof = result["metadata"]["proof_of_settlement"]
        assert free_proof["free_call"] is True
        assert free_proof["axm_paid_wei"] == "0"
        assert free_proof["platform_fee_wei"] == "0"
        quote = _signed_quote(client, key, skill_id)
        assert quote["free_daily_ration_remaining"] == DAILY_RATION_LIMIT - 1

        # 4. Paid serve: an unregistered wallet pays the quoted price
        #    (dev-env payment bypass records the caller's verified
        #    claim exactly as the existing paid-path tests do).
        paid_key = Account.create()
        tx_hash = "0x" + "cd" * 32
        rep_key = ai._reputation_key(None, paid_caller)
        rep_before = ai._reputation_ledger.score(rep_key)
        params = _create_auth_params(
            paid_key, paid_caller, skill_id,
            message={"role": "user",
                     "parts": [{"text": "Verify this paid claim end to end."}]},
            axmPaidWei=str(price_wei),
            txHash=tx_hash,
        )
        resp = client.post(
            "/api/a2a", json={"jsonrpc": "2.0", "id": 1,
                              "method": "message/send", "params": params})
        assert resp.status_code == 200, resp.get_json()
        payload = resp.get_json()
        assert "error" not in payload, payload
        result = payload["result"]
        assert result["status"]["state"] == "completed"
        assert _output_text(result).strip()
        assert result["metadata"]["free_call"] is False
        assert result["metadata"]["free_daily_ration"] is False
        proof = result["metadata"]["proof_of_settlement"]
        task_id = result["id"]
        assert proof["task_id"] == task_id
        assert proof["axm_paid_wei"] == str(price_wei)
        assert proof["platform_fee_wei"] == str(fee_wei)
        assert proof["treasury_amount_wei"] == str(fee_wei)
        assert proof["burn_amount_wei"] == "0"
        assert proof["tx_hash"] == tx_hash

        # Settlement accounting: the paid settlement is recorded in the
        # reputation ledger under the task's caller key.
        assert ai._reputation_ledger.score(rep_key) == rep_before + 1

        # 5. Settle: the proof-of-settlement route returns a verifiable
        #    proof carrying the same accounting.
        resp = client.post(
            "/api/a2a/settle", json={"task_id": task_id, "tx_hash": tx_hash})
        assert resp.status_code == 200, resp.get_json()
        settled = resp.get_json()
        pos = settled["proof_of_settlement"]
        assert pos["task_id"] == task_id
        assert pos["skill_id"] == skill_id
        assert pos["axm_paid_wei"] == str(price_wei)
        assert pos["platform_fee_wei"] == str(fee_wei)
        assert pos["treasury_amount_wei"] == str(fee_wei)
        assert pos["tx_hash"] == tx_hash
        ok, reason = verify_proof_of_settlement(settled)
        assert ok, reason
    finally:
        daily_ration_tracker.clear()
        reset_fabric()
