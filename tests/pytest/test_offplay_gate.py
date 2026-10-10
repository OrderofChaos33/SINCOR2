"""Tests for the offensive playbook multi-sig approval gate."""

import os
import sys
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from sincor2.offensive_playbook import gate
from sincor2.offensive_playbook.gate import (
    PlaybookGate,
    ApprovalLedger,
    play_hash,
    approval_message,
    register_approver,
)


def _key():
    return Account.create()


def _sign(acct, play_id, action, target_scope, created_at, agent_id):
    digest = play_hash(play_id, action, target_scope, created_at)
    msg = encode_defunct(approval_message(digest, agent_id))
    return acct.sign_message(msg).signature.hex()


@pytest.fixture()
def approvers():
    gate._registry.clear()
    accts = {aid: _key() for aid in gate.APPROVER_AGENTS}
    for aid, acct in accts.items():
        register_approver(aid, acct.address)
    return accts


PLAY = dict(
    play_id="play-speed-001",
    action="accelerate ship cadence to daily deploys",
    target_scope="internal operations only",
    created_at=int(time.time()),
)


def test_quorum_approves(approvers):
    g = PlaybookGate()
    aids = list(gate.APPROVER_AGENTS)[:3]
    for aid in aids:
        sig = _sign(approvers[aid], agent_id=aid, **PLAY)
        g.submit_approval(agent_id=aid, signature="0x" + sig, **PLAY)
    ok, reason = g.is_approved(**PLAY)
    assert ok, reason
    assert "quorum" in reason


def test_below_quorum_rejected(approvers):
    g = PlaybookGate()
    aids = list(gate.APPROVER_AGENTS)[:2]
    for aid in aids:
        sig = _sign(approvers[aid], agent_id=aid, **PLAY)
        g.submit_approval(agent_id=aid, signature="0x" + sig, **PLAY)
    ok, reason = g.is_approved(**PLAY)
    assert not ok
    assert "2/3" in reason


def test_founder_bypass(approvers):
    founder = _key()
    g = PlaybookGate()
    sig = _sign(founder, agent_id="founder", **PLAY)
    g.submit_approval(
        agent_id="founder", signature="0x" + sig,
        founder_address=founder.address, **PLAY,
    )
    ok, reason = g.is_approved(founder_address=founder.address, **PLAY)
    assert ok and reason == "founder"


def test_wrong_signer_rejected(approvers):
    g = PlaybookGate()
    impostor = _key()
    aid = gate.APPROVER_AGENTS[0]
    sig = _sign(impostor, agent_id=aid, **PLAY)
    with pytest.raises(ValueError, match="does not match|expected"):
        g.submit_approval(agent_id=aid, signature="0x" + sig, **PLAY)


def test_high_s_signature_rejected(approvers):
    # Flip s to n-s to create the malleability twin; gate must reject it.
    from sincor2.sig_canonical import SECP256K1_N

    g = PlaybookGate()
    aid = gate.APPROVER_AGENTS[0]
    acct = approvers[aid]
    digest = play_hash(PLAY["play_id"], PLAY["action"], PLAY["target_scope"], PLAY["created_at"])
    msg = encode_defunct(approval_message(digest, aid))
    raw = acct.sign_message(msg).signature
    r = int.from_bytes(raw[0:32], "big")
    s = int.from_bytes(raw[32:64], "big")
    v = raw[64]
    twin_s = SECP256K1_N - s
    twin = r.to_bytes(32, "big") + twin_s.to_bytes(32, "big") + bytes([v])
    with pytest.raises(ValueError, match="non-canonical high-s"):
        g.submit_approval(agent_id=aid, signature="0x" + twin.hex(), **PLAY)


def test_duplicate_approval_rejected(approvers):
    g = PlaybookGate()
    aid = gate.APPROVER_AGENTS[0]
    sig = _sign(approvers[aid], agent_id=aid, **PLAY)
    g.submit_approval(agent_id=aid, signature="0x" + sig, **PLAY)
    with pytest.raises(ValueError, match="duplicate approval"):
        g.submit_approval(agent_id=aid, signature="0x" + sig, **PLAY)


def test_unregistered_approver_rejected():
    gate._registry.clear()
    g = PlaybookGate()
    acct = _key()
    sig = _sign(acct, agent_id="E-toa-44", **PLAY)
    with pytest.raises(ValueError, match="not registered"):
        g.submit_approval(agent_id="E-toa-44", signature="0x" + sig, **PLAY)


def test_ledger_chain_verifies(approvers):
    g = PlaybookGate()
    for aid in list(gate.APPROVER_AGENTS)[:3]:
        sig = _sign(approvers[aid], agent_id=aid, **PLAY)
        g.submit_approval(agent_id=aid, signature="0x" + sig, **PLAY)
    assert g.ledger.verify_chain()
    # Tamper with a record -> chain breaks
    g.ledger._records[0].signature = "0x" + "ab" * 65
    assert not g.ledger.verify_chain()


def test_tampered_play_params_do_not_count(approvers):
    g = PlaybookGate()
    aids = list(gate.APPROVER_AGENTS)[:3]
    for aid in aids:
        sig = _sign(approvers[aid], agent_id=aid, **PLAY)
        g.submit_approval(agent_id=aid, signature="0x" + sig, **PLAY)
    # Same signatures checked against altered action -> not approved
    altered = dict(PLAY, action="something else entirely")
    ok, _ = g.is_approved(**altered)
    assert not ok
