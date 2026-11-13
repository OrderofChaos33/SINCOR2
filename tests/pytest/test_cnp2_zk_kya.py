"""CNP-2: commitment-based ZK attestations for KYA state/capability.

Prove/verify round-trips, tamper resistance, revocation handling, and
fail-closed verification. Run with the repo venv:

    PYTHONPATH=src:. FLASK_ENV=test SECRET_KEY=<32c> JWT_SECRET_KEY=<32c> \
    ADMIN_USERNAME=a ADMIN_PASSWORD=b STRIPE_SECRET_KEY=sk_test_dummy \
    ~/.venvs/sincor2/bin/python -m pytest tests/pytest/test_cnp2_zk_kya.py -q
"""
from __future__ import annotations

import json
import os

import pytest
from eth_account import Account

from sincor2.kya.registry import KYARegistry, MIN_STAKE_WEI
from sincor2.kya.zk_attestations import (
    ATTESTATION_VERSION,
    TIER_T1,
    TIER_T2,
    TIER_T3,
    KYAAttestation,
    ZKAttestationIssuer,
    tier_of,
    tier_satisfies,
    verify_attestation,
)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def registry(data_dir):
    return KYARegistry()


def _verified_agent(registry, agent_id, capabilities=()):
    rec = registry.list_agent(
        {"agent_id": agent_id, "wallet": "0x" + "ab" * 20, "capabilities": list(capabilities)}
    )
    registry.bind(agent_id, "0x" + "ab" * 20)
    registry.apply_stake(rec["kya_id"], str(MIN_STAKE_WEI), "0x" + "cd" * 32)
    return registry.verify(rec["kya_id"])


@pytest.fixture()
def issuer(registry):
    return ZKAttestationIssuer(registry, Account.create().key.hex())


@pytest.fixture()
def agent(registry):
    return _verified_agent(registry, "agent-alpha", ["defi-arbitrage", "lead-enrichment"])


# ---------------------------------------------------------------------------
# Round trips
# ---------------------------------------------------------------------------


def test_tier_attestation_round_trip(issuer, agent, registry):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3)
    res = verify_attestation(att.to_dict(), issuer.issuer_address, issuer.revocation_source())
    assert res.valid, res.reason
    assert res.claim == {"kind": "kya_tier", "tier": TIER_T3}


def test_tier_satisfies_lower_claim(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T1)
    res = verify_attestation(att.to_dict(), issuer.issuer_address, issuer.revocation_source())
    assert res.valid, res.reason


def test_capability_attestation_round_trip(issuer, agent):
    att = issuer.issue_capability_attestation("agent-alpha", "defi-arbitrage")
    res = verify_attestation(att.to_dict(), issuer.issuer_address, issuer.revocation_source())
    assert res.valid, res.reason
    assert res.claim == {"kind": "kya_capability", "capability": "defi-arbitrage"}


def test_attestation_contains_no_agent_identity(issuer, agent, registry):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3)
    blob = json.dumps(att.to_dict())
    rec = registry.lookup(agent_id="agent-alpha")
    assert "agent_id" not in blob
    assert "agent-alpha" not in blob
    assert rec["kya_id"] not in blob
    assert rec["wallet"].lower() not in blob.lower()
    assert att.nullifier and att.nullifier.startswith("0x")


# ---------------------------------------------------------------------------
# Tampering -> rejected
# ---------------------------------------------------------------------------


def _mutate(att_dict, path, value):
    d = json.loads(json.dumps(att_dict))
    d[path] = value
    return d


def test_tampered_claim_tier_rejected(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T2)
    d = _mutate(att.to_dict(), "claim", {"kind": "kya_tier", "tier": TIER_T3})
    res = verify_attestation(d, issuer.issuer_address, issuer.revocation_source())
    assert not res.valid and res.reason == "bad-signature", res.reason


def test_tampered_capability_rejected(issuer, agent):
    att = issuer.issue_capability_attestation("agent-alpha", "lead-enrichment")
    d = _mutate(att.to_dict(), "claim", {"kind": "kya_capability", "capability": "defi-arbitrage"})
    res = verify_attestation(d, issuer.issuer_address, issuer.revocation_source())
    assert not res.valid, res.reason


def test_tampered_signature_rejected(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3)
    sig = att.signature
    flipped = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    d = _mutate(att.to_dict(), "signature", flipped)
    res = verify_attestation(d, issuer.issuer_address, issuer.revocation_source())
    assert not res.valid and res.reason == "bad-signature", res.reason


def test_tampered_expiry_rejected(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3, ttl_sec=3600)
    d = _mutate(att.to_dict(), "expires_at", att.expires_at + 10 * 86400)
    res = verify_attestation(d, issuer.issuer_address, issuer.revocation_source())
    assert not res.valid and res.reason == "bad-signature", res.reason


def test_tampered_nullifier_rejected(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3)
    d = _mutate(att.to_dict(), "nullifier", "0x" + "00" * 32)
    res = verify_attestation(d, issuer.issuer_address, issuer.revocation_source())
    assert not res.valid and res.reason == "bad-signature", res.reason


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def test_revoked_agent_attestation_fails(issuer, agent, registry):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3)
    rec = registry.lookup(agent_id="agent-alpha")
    issuer.revoke_agent(rec["kya_id"], reason="test-revoke")
    res = verify_attestation(att.to_dict(), issuer.issuer_address, issuer.revocation_source())
    assert not res.valid and res.reason == "revoked", res.reason


def test_revoked_agent_cannot_be_issued(issuer, agent, registry):
    rec = registry.lookup(agent_id="agent-alpha")
    issuer.revoke_agent(rec["kya_id"])
    with pytest.raises(PermissionError):
        issuer.issue_tier_attestation("agent-alpha", TIER_T1)
    with pytest.raises(PermissionError):
        issuer.issue_capability_attestation("agent-alpha", "defi-arbitrage")


def test_capability_attestation_revoked_after_revoke(issuer, agent, registry):
    att = issuer.issue_capability_attestation("agent-alpha", "defi-arbitrage")
    rec = registry.lookup(agent_id="agent-alpha")
    issuer.revoke_agent(rec["kya_id"])
    res = verify_attestation(att.to_dict(), issuer.issuer_address, issuer.revocation_source())
    assert not res.valid and res.reason == "revoked", res.reason


# ---------------------------------------------------------------------------
# Fail-closed verification
# ---------------------------------------------------------------------------


def test_expired_attestation_rejected(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3, ttl_sec=3600)
    res = verify_attestation(
        att.to_dict(), issuer.issuer_address, issuer.revocation_source(),
        now=att.expires_at + 1,
    )
    assert not res.valid and res.reason == "expired", res.reason


def test_wrong_issuer_address_rejected(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3)
    other = Account.create().address
    res = verify_attestation(att.to_dict(), other, issuer.revocation_source())
    assert not res.valid and res.reason == "issuer-mismatch", res.reason


def test_forged_key_rejected(issuer, agent):
    rogue = ZKAttestationIssuer(issuer.registry, Account.create().key.hex())
    forged = rogue.issue_tier_attestation("agent-alpha", TIER_T3)
    d = forged.to_dict()
    d["issuer"] = issuer.issuer_address  # attacker claims the trusted issuer
    res = verify_attestation(d, issuer.issuer_address, issuer.revocation_source())
    assert not res.valid and res.reason == "bad-signature", res.reason


def test_revocation_source_unavailable_fail_closed(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3)
    d = att.to_dict()
    r1 = verify_attestation(d, issuer.issuer_address, None)
    assert not r1.valid and r1.reason == "revocation-unverifiable", r1.reason

    def boom(nullifier):
        raise ConnectionError("issuer unreachable")

    r2 = verify_attestation(d, issuer.issuer_address, boom)
    assert not r2.valid and r2.reason == "revocation-unverifiable", r2.reason

    r3 = verify_attestation(d, issuer.issuer_address, lambda n: None)
    assert not r3.valid and r3.reason == "revocation-unverifiable", r3.reason


def test_unknown_claim_kind_rejected(issuer, agent):
    att = KYAAttestation(
        attestation_id="cnp2a_test",
        version=ATTESTATION_VERSION,
        claim={"kind": "superadmin"},
        nullifier="0x" + "11" * 32,
        issuer=issuer.issuer_address,
        issued_at=1700000000,
        expires_at=1700086400,
        commitment="0x" + "22" * 32,
        signature="",
    )
    att.signature = issuer._sign(att)
    res = verify_attestation(att.to_dict(), issuer.issuer_address, issuer.revocation_source())
    assert not res.valid and res.reason == "unknown-claim-kind", res.reason


def test_version_mismatch_rejected(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3)
    d = _mutate(att.to_dict(), "version", "cnp2-zk-kya-v0")
    res = verify_attestation(d, issuer.issuer_address, issuer.revocation_source())
    assert not res.valid and res.reason == "unsupported-version", res.reason


def test_malformed_attestation_rejected(issuer):
    res = verify_attestation({"nope": True}, issuer.issuer_address, issuer.revocation_source())
    assert not res.valid, res.reason


# ---------------------------------------------------------------------------
# Fail-closed issuance
# ---------------------------------------------------------------------------


def test_issue_tier_for_unknown_agent_raises(issuer):
    with pytest.raises(LookupError):
        issuer.issue_tier_attestation("ghost-agent", TIER_T1)


def test_issue_tier_above_agent_tier_raises(issuer, registry):
    rec = registry.list_agent({"agent_id": "agent-beta", "wallet": "0x" + "ef" * 20})
    registry.bind("agent-beta", "0x" + "ef" * 20)  # listed only -> T1
    with pytest.raises(PermissionError):
        issuer.issue_tier_attestation("agent-beta", TIER_T3)
    att = issuer.issue_tier_attestation("agent-beta", TIER_T1)  # T1 ok
    res = verify_attestation(att.to_dict(), issuer.issuer_address, issuer.revocation_source())
    assert res.valid, res.reason


def test_issue_capability_not_held_raises(issuer, agent):
    with pytest.raises(PermissionError):
        issuer.issue_capability_attestation("agent-alpha", "mev-extraction")
    with pytest.raises(ValueError):
        issuer.issue_capability_attestation("agent-alpha", "BAD CAP!!")


# ---------------------------------------------------------------------------
# Nullifier + commitment ledger + reveal
# ---------------------------------------------------------------------------


def test_nullifier_deterministic_and_distinct(issuer, agent, registry):
    a1 = issuer.issue_tier_attestation("agent-alpha", TIER_T2)
    a2 = issuer.issue_tier_attestation("agent-alpha", TIER_T2)
    assert a1.nullifier == a2.nullifier  # same (agent, claim) -> same nullifier
    _verified_agent(registry, "agent-gamma", ["defi-arbitrage"])
    a3 = issuer.issue_tier_attestation("agent-gamma", TIER_T2)
    assert a3.nullifier != a1.nullifier  # different agent -> different nullifier
    a4 = issuer.issue_capability_attestation("agent-alpha", "defi-arbitrage")
    assert a4.nullifier != a1.nullifier  # different claim -> different nullifier


def test_commitment_ledger_anchors_and_opening_verifies(issuer, agent):
    att = issuer.issue_tier_attestation("agent-alpha", TIER_T3)
    assert issuer.anchored(att.attestation_id)
    assert not issuer.anchored("cnp2a_nonexistent")
    opening = issuer.open(att.attestation_id)
    assert opening is not None and opening["agent_id"] == "agent-alpha"
    assert issuer.verify_opening(att, opening)
    tampered = dict(opening, agent_id="agent-evil")
    assert not issuer.verify_opening(att, tampered)


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------


def test_tier_of_revoked_is_none(registry):
    rec = _verified_agent(registry, "agent-delta")
    assert tier_of(rec) == TIER_T3
    registry.revoke(rec["kya_id"])
    assert tier_of(registry.lookup(agent_id="agent-delta")) is None


def test_tier_satisfies_ordering():
    assert tier_satisfies(TIER_T3, TIER_T1)
    assert tier_satisfies(TIER_T2, TIER_T2)
    assert not tier_satisfies(TIER_T1, TIER_T3)
