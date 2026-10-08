"""Receipt-content binding for dispute evidence (dev-watch item 77).

Hedge: an x402/payment receipt proves money moved, never that the
deliverable has content.  Every dispute-relevant proof must bind the
deliverable (keccak of canonical bytes + retrieval URI); evidence with a
receipt but no content binding is rejected fail-closed.
"""

from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from sincor2.a2a_inbound import get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.dispute_evidence import (
    DisputeEvidence,
    build_evidence,
    canonicalize_deliverable,
    hash_deliverable,
    validate_dispute_evidence,
    verify_content_binding_sig,
    verify_deliverable_content,
)

RECEIPT = "0x" + "ab" * 16
URI = "https://poster.example/deliverables/1"
AGENT = "evidence-agent-1"


def _evidence(**over):
    data = {
        "payment_receipt_hash": RECEIPT,
        "deliverable_hash": hash_deliverable({"ok": True}),
        "deliverable_uri": URI,
    }
    data.update(over)
    return data


# --- (a) receipt-only evidence is rejected ---------------------------------


def test_receipt_only_evidence_rejected():
    with pytest.raises(ValueError, match="deliverable_hash is required"):
        validate_dispute_evidence({"payment_receipt_hash": RECEIPT})


def test_receipt_only_dataclass_rejected():
    ev = DisputeEvidence(
        payment_receipt_hash=RECEIPT,
        deliverable_hash="",
        deliverable_uri="",
    )
    with pytest.raises(ValueError, match="deliverable_hash is required"):
        validate_dispute_evidence(ev)


def test_empty_evidence_rejected():
    with pytest.raises(ValueError):
        validate_dispute_evidence({})


# --- (b) receipt + binding + retrievable content is accepted ----------------


def test_full_evidence_accepted():
    ev = validate_dispute_evidence(_evidence())
    assert isinstance(ev, DisputeEvidence)
    assert ev.payment_receipt_hash == RECEIPT
    assert ev.deliverable_uri == URI
    assert len(ev.deliverable_hash) == 66


def test_canonicalization_is_deterministic():
    a = canonicalize_deliverable({"z": 1, "a": [3, 2, {"k": "v"}]})
    b = canonicalize_deliverable({"a": [3, 2, {"k": "v"}], "z": 1})
    assert a == b
    assert hash_deliverable({"z": 1, "a": 2}) == hash_deliverable({"a": 2, "z": 1})


def test_malformed_hash_rejected():
    with pytest.raises(ValueError, match="keccak256"):
        validate_dispute_evidence(_evidence(deliverable_hash="0x1234"))


def test_non_retrievable_uri_rejected():
    with pytest.raises(ValueError, match="scheme"):
        validate_dispute_evidence(_evidence(deliverable_uri="data:text/plain,hi"))
    with pytest.raises(ValueError, match="deliverable_uri is required"):
        validate_dispute_evidence(_evidence(deliverable_uri=""))


# --- (c) garbage-content attack ---------------------------------------------
# The paid receipt is valid but the deliverable is garbage.  The evidence
# must still be ACCEPTED for adjudication -- that is the point: the
# binding lets the adjudicator retrieve the garbage and rule against the
# seller instead of being unable to tell what was delivered.


def test_garbage_content_is_bound_and_admissible():
    garbage = {"result": "asdf qwer zxcv garbage " * 20}
    ev = build_evidence(RECEIPT, garbage, URI)
    # Admissible: the hash binds the exact garbage bytes.
    assert verify_deliverable_content(garbage, ev.deliverable_hash) is True
    # The adjudicator can now see it IS garbage (content retrievable via
    # URI, hash proves these are the delivered bytes) and rule against
    # the seller.  Without the binding, the receipt alone would verify.


# --- (d) tampered deliverable (hash mismatch) is rejected --------------------


def test_tampered_deliverable_rejected():
    original = {"report": "real work", "rows": 42}
    ev = build_evidence(RECEIPT, original, URI)
    tampered = {"report": "real work", "rows": 43}
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_deliverable_content(tampered, ev.deliverable_hash)


def test_swapped_bytes_rejected():
    ev = build_evidence(RECEIPT, b"\x00\x01raw-bytes", URI)
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_deliverable_content(b"\x00\x02raw-bytes", ev.deliverable_hash)


# --- content-binding signature ----------------------------------------------


def test_sig_binds_to_agent_wallet():
    acct = Account.create()
    content = {"deliverable": "signed work"}
    dhash = hash_deliverable(content)
    sig = acct.sign_message(encode_defunct(text=dhash)).signature.hex()
    ev = validate_dispute_evidence(_evidence(
        deliverable_hash=dhash, content_binding_sig="0x" + sig))
    assert verify_content_binding_sig(ev, acct.address) is True


def test_sig_from_wrong_wallet_rejected():
    acct = Account.create()
    other = Account.create()
    dhash = hash_deliverable({"x": 1})
    sig = other.sign_message(encode_defunct(text=dhash)).signature.hex()
    ev = validate_dispute_evidence(_evidence(
        deliverable_hash=dhash, content_binding_sig="0x" + sig))
    with pytest.raises(ValueError, match="does not match the agent wallet"):
        verify_content_binding_sig(ev, acct.address)


def test_missing_sig_is_allowed():
    ev = validate_dispute_evidence(_evidence())
    assert verify_content_binding_sig(ev, "0x" + "11" * 20) is True


# --- wired into the proof submission path ------------------------------------


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    from sincor2.onchain.stake_ledger import reset_stake_ledger
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _assigned_task(task_id="task-ev-1", agent_id=AGENT, wallet="0x" + "22" * 20):
    fabric = get_fabric()
    fabric.agents[agent_id] = {
        "agent_id": agent_id, "wallet": wallet, "reputation": 1.0,
    }
    fabric.tasks[task_id] = {
        "task_id": task_id, "state": "assigned",
        "assigned_to": agent_id, "bounty_axm": 1.0, "tags": [],
    }
    return task_id


def test_proofs_route_rejects_receipt_only_evidence(client):
    task_id = _assigned_task()
    r = client.post("/v1/a2a/proofs", json={
        "task_id": task_id, "agent_id": AGENT,
        "receipt_hash": RECEIPT,
        "evidence": {"payment_receipt_hash": RECEIPT},  # no binding
    })
    assert r.status_code == 400, r.get_json()


def test_proofs_route_accepts_bound_deliverable(client):
    task_id = _assigned_task()
    deliverable = {"leads": [{"name": "Acme", "email": "a@acme.co"}]}
    r = client.post("/v1/a2a/proofs", json={
        "task_id": task_id, "agent_id": AGENT,
        "receipt_hash": RECEIPT,
        "deliverable": deliverable,
        "deliverable_uri": URI,
    })
    assert r.status_code == 202, r.get_json()
    proof = r.get_json()
    assert proof["content_bound"] is True
    ev = proof["evidence"]
    assert ev["deliverable_hash"] == hash_deliverable(deliverable)
    assert ev["deliverable_uri"] == URI
    # Adjudicator path: recompute from the stored task evidence.
    task = get_fabric().tasks[task_id]
    assert verify_deliverable_content(
        deliverable, task["deliverable_evidence"]["deliverable_hash"]) is True


def test_proofs_route_bare_receipt_still_settles_but_unbound(client):
    # Legacy callers are not bricked; the proof is flagged unbound so a
    # later quality dispute knows there is no deliverable to recompute.
    task_id = _assigned_task()
    r = client.post("/v1/a2a/proofs", json={
        "task_id": task_id, "agent_id": AGENT, "receipt_hash": RECEIPT,
    })
    assert r.status_code == 202, r.get_json()
    proof = r.get_json()
    assert proof["content_bound"] is False
    assert proof["evidence"] is None


def test_proofs_route_rejects_evidence_receipt_mismatch(client):
    task_id = _assigned_task()
    r = client.post("/v1/a2a/proofs", json={
        "task_id": task_id, "agent_id": AGENT,
        "receipt_hash": RECEIPT,
        "evidence": _evidence(payment_receipt_hash="0x" + "ff" * 16),
    })
    assert r.status_code == 400, r.get_json()


def test_proofs_route_verifies_binding_sig_against_wallet(client):
    acct = Account.create()
    task_id = _assigned_task(wallet=acct.address)
    deliverable = {"work": "signed deliverable"}
    dhash = hash_deliverable(deliverable)
    good_sig = "0x" + acct.sign_message(
        encode_defunct(text=dhash)).signature.hex()
    r = client.post("/v1/a2a/proofs", json={
        "task_id": task_id, "agent_id": AGENT,
        "receipt_hash": RECEIPT,
        "deliverable": deliverable,
        "deliverable_uri": URI,
        "content_binding_sig": good_sig,
    })
    assert r.status_code == 202, r.get_json()

    # Forged attribution: signature from a different key must fail closed.
    task_id2 = _assigned_task(task_id="task-ev-2", wallet=acct.address)
    other = Account.create()
    bad_sig = "0x" + other.sign_message(
        encode_defunct(text=dhash)).signature.hex()
    r = client.post("/v1/a2a/proofs", json={
        "task_id": task_id2, "agent_id": AGENT,
        "receipt_hash": RECEIPT,
        "deliverable": deliverable,
        "deliverable_uri": URI,
        "content_binding_sig": bad_sig,
    })
    assert r.status_code == 400, r.get_json()
