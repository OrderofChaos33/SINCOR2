"""Tests for DAE governance and decentralized identity modules."""
from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dae.governance import GovernanceEngine
from dae.identity import DecentralizedIdentity


# ===========================================================================
# GovernanceEngine
# ===========================================================================

@pytest.fixture()
def engine():
    return GovernanceEngine(approval_threshold=0.6)


def test_submit_proposal_creates_draft(engine):
    proposal = engine.submit_proposal(
        title="Increase Budget Cap",
        description="Raise to 2000",
        author="did:key:alice",
        policy_updates={"max-budget": 2000},
    )
    assert proposal.proposal_id.startswith("prop-")
    assert proposal.status == "draft"
    assert proposal.title == "Increase Budget Cap"


def test_cast_vote_moves_draft_to_voting(engine):
    proposal = engine.submit_proposal("T", "D", "alice", {})
    updated = engine.cast_vote(proposal.proposal_id, voter="bob", support=True)
    assert updated.status in ("voting", "approved")


def test_proposal_approved_when_threshold_met(engine):
    proposal = engine.submit_proposal("T", "D", "alice", {})
    # 3 for, 1 against  → 75 % ≥ 60 %
    engine.cast_vote(proposal.proposal_id, "a", support=True)
    engine.cast_vote(proposal.proposal_id, "b", support=True)
    engine.cast_vote(proposal.proposal_id, "c", support=True)
    updated = engine.cast_vote(proposal.proposal_id, "d", support=False)
    assert updated.status == "approved"


def test_proposal_stays_voting_below_threshold(engine):
    proposal = engine.submit_proposal("T", "D", "alice", {})
    # 1 for, 2 against → ~33 % < 60 %
    engine.cast_vote(proposal.proposal_id, "a", support=True)
    engine.cast_vote(proposal.proposal_id, "b", support=False)
    updated = engine.cast_vote(proposal.proposal_id, "c", support=False)
    assert updated.status == "voting"


def test_proposal_weighted_votes_respected(engine):
    proposal = engine.submit_proposal("T", "D", "alice", {})
    # 1 heavy "for" (weight=10) vs 5 regular "against"
    engine.cast_vote(proposal.proposal_id, "whale", support=True, weight=10.0)
    for i in range(5):
        engine.cast_vote(proposal.proposal_id, f"voter-{i}", support=False, weight=1.0)
    result = engine.get_proposal_status(proposal.proposal_id)
    # 10 / 15 ≈ 66.7 % → approved
    assert result["status"] == "approved"


def test_execute_approved_proposal(engine):
    proposal = engine.submit_proposal("T", "D", "alice", {})
    engine.cast_vote(proposal.proposal_id, "a", support=True)
    engine.cast_vote(proposal.proposal_id, "b", support=True)
    executed = engine.execute_proposal(proposal.proposal_id)
    assert executed.status == "executed"
    assert executed.execution_notes != ""


def test_execute_unapproved_proposal_raises(engine):
    proposal = engine.submit_proposal("T", "D", "alice", {})
    # No votes — still in draft/voting
    with pytest.raises(ValueError, match="approved"):
        engine.execute_proposal(proposal.proposal_id)


def test_get_proposal_status_returns_serializable_dict(engine):
    proposal = engine.submit_proposal("T", "D", "alice", {})
    status = engine.get_proposal_status(proposal.proposal_id)
    assert isinstance(status, dict)
    assert "votes" in status


# ===========================================================================
# DecentralizedIdentity
# ===========================================================================

@pytest.fixture()
def identity():
    return DecentralizedIdentity()


def test_create_did_has_correct_prefix(identity):
    doc = identity.create_did("public-key-material")
    assert doc["id"].startswith("did:key:")


def test_create_did_is_deterministic(identity):
    doc1 = identity.create_did("same-material")
    doc2 = identity.create_did("same-material")
    assert doc1["id"] == doc2["id"]


def test_create_did_different_inputs_differ(identity):
    doc1 = identity.create_did("material-A")
    doc2 = identity.create_did("material-B")
    assert doc1["id"] != doc2["id"]


def test_create_did_stored_in_documents(identity):
    doc = identity.create_did("key-xyz")
    assert doc["id"] in identity.documents


def test_resolve_did_returns_document(identity):
    doc = identity.create_did("key-abc")
    resolved = identity.resolve_did(doc["id"])
    assert resolved == doc


def test_issue_attestation_returns_dict(identity):
    issuer_doc = identity.create_did("issuer-key")
    subject_doc = identity.create_did("subject-key")
    att = identity.issue_attestation(
        issuer_did=issuer_doc["id"],
        subject_did=subject_doc["id"],
        claim_type="kyc",
        value={"level": "standard"},
    )
    assert att["claim_type"] == "kyc"
    assert att["attestation_id"].startswith("att-")


def test_verify_credential_known_dids(identity):
    issuer = identity.create_did("i-key")
    subject = identity.create_did("s-key")
    credential = {"issuer_did": issuer["id"], "subject_did": subject["id"]}
    assert identity.verify_credential(credential) is True


def test_verify_credential_unknown_issuer(identity):
    subject = identity.create_did("s-key")
    credential = {"issuer_did": "did:key:unknown", "subject_did": subject["id"]}
    assert identity.verify_credential(credential) is False


def test_verify_credential_unknown_subject(identity):
    issuer = identity.create_did("i-key")
    credential = {"issuer_did": issuer["id"], "subject_did": "did:key:unknown"}
    assert identity.verify_credential(credential) is False
