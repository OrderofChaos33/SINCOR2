"""CNP-6: EAS-backed decentralized reputation — offchain client + graph.

Self-contained (no app imports): eth_account / eth_abi only.
Run:  PYTHONPATH=src:. python -m pytest tests/pytest/test_eas_reputation.py
"""

import pytest
from eth_account import Account

from sincor2.eas_reputation import (
    SCHEMA_EXECUTION_OUTCOME,
    SCHEMA_KYA_TIER,
    SCHEMA_VOUCH,
    SCHEMAS,
    ZERO_UID,
    Attestation,
    AttestationGraph,
    EASClient,
    decode_schema_data,
    encode_schema_data,
    schema_uid,
)

NOW = 1_750_000_000


def _b32(seed: int) -> str:
    return "0x" + ("%064x" % seed)


@pytest.fixture
def keys():
    return {name: Account.create() for name in ("alice", "bob", "carol",
                                                "dave", "mallory")}


@pytest.fixture
def client(keys):
    c = EASClient(chain_id=1)
    for name, acct in keys.items():
        c.register_agent(f"agent-{name}", acct.address)
    return c


def vouch_data(score=80, task=1):
    return {"score": score, "task_id": _b32(task),
            "payload_hash": _b32(task + 1000)}


def make_vouch(client, keys, attester, subject, at=NOW, **data_kw):
    return client.create_attestation(
        keys[attester].key.hex(), f"agent-{subject}",
        SCHEMA_VOUCH.uid, vouch_data(**data_kw), at=at)


# ---------------------------------------------------------------------------
# Schema design
# ---------------------------------------------------------------------------

def test_schema_uids_stable_distinct_and_eas_shaped():
    assert schema_uid(SCHEMA_VOUCH.schema) == SCHEMA_VOUCH.uid
    assert len({s.uid for s in SCHEMAS.values()}) == 3
    # Resolver / revocable feed the UID (EAS derivation), so confusion
    # between deployments cannot collide.
    other = schema_uid(SCHEMA_VOUCH.schema,
                       resolver="0x" + "11" * 20, revocable=False)
    assert other != SCHEMA_VOUCH.uid
    for uid in SCHEMAS:
        assert uid.startswith("0x") and len(uid) == 66


def test_encode_decode_roundtrip_vouch():
    data = vouch_data(score=80, task=7)
    raw = encode_schema_data(SCHEMA_VOUCH, data)
    back = decode_schema_data(SCHEMA_VOUCH, raw)
    assert back["score"] == 80
    assert back["task_id"] == _b32(7)
    assert back["payload_hash"] == _b32(1007)


def test_schema_bounds_fail_closed():
    with pytest.raises(ValueError):
        encode_schema_data(SCHEMA_VOUCH, vouch_data(score=101))
    with pytest.raises(ValueError):
        encode_schema_data(SCHEMA_VOUCH, vouch_data(score=-1))
    with pytest.raises(ValueError):
        encode_schema_data(SCHEMA_KYA_TIER,
                           {"tier": 4, "kya_ref": _b32(1),
                            "payload_hash": _b32(2)})
    with pytest.raises(ValueError):  # missing field
        encode_schema_data(SCHEMA_VOUCH, {"score": 50})
    with pytest.raises(ValueError):  # short bytes32
        encode_schema_data(SCHEMA_VOUCH,
                           {"score": 50, "task_id": "0x1234",
                            "payload_hash": _b32(2)})
    with pytest.raises(ValueError):  # execution outcome quality bound
        encode_schema_data(SCHEMA_EXECUTION_OUTCOME,
                           {"quality": 250, "upheld_disputes": 0,
                            "task_id": _b32(1), "payload_hash": _b32(2)})


# ---------------------------------------------------------------------------
# Client: create / verify
# ---------------------------------------------------------------------------

def test_create_verify_roundtrip(client, keys):
    att = make_vouch(client, keys, "alice", "bob")
    result = client.verify_attestation(att, now=NOW)
    assert result.ok, result.reason
    assert result.attester == keys["alice"].address


def test_forged_attestation_by_non_attester_rejected(client, keys):
    # Mallory signs, but the attestation claims Alice as attester.
    att = make_vouch(client, keys, "mallory", "bob")
    att.attester = keys["alice"].address
    result = client.verify_attestation(att, now=NOW)
    assert not result.ok
    assert result.reason == "attester_mismatch"


def test_swapped_signature_rejected(client, keys):
    a1 = make_vouch(client, keys, "alice", "bob", task=1)
    a2 = make_vouch(client, keys, "alice", "bob", task=2)
    a1.signature = a2.signature  # signature from a different attestation
    result = client.verify_attestation(a1, now=NOW)
    assert not result.ok


def test_tampered_data_rejected(client, keys):
    att = make_vouch(client, keys, "alice", "bob", score=90)
    tampered = bytearray(att.data)
    tampered[31] ^= 0xFF  # flip a byte inside the encoded score
    att.data = bytes(tampered)
    result = client.verify_attestation(att, now=NOW)
    assert not result.ok
    assert result.reason in ("attester_mismatch", "uid_mismatch")


def test_tampered_uid_rejected(client, keys):
    att = make_vouch(client, keys, "alice", "bob")
    att.uid = "0x" + "ff" * 32
    result = client.verify_attestation(att, now=NOW)
    assert not result.ok
    assert result.reason == "uid_mismatch"


def test_schema_id_confusion_rejected(client, keys):
    att = make_vouch(client, keys, "alice", "bob")
    # Attacker re-labels a vouch as an execution-outcome attestation.
    att.schema_uid = SCHEMA_EXECUTION_OUTCOME.uid
    result = client.verify_attestation(att, now=NOW)
    assert not result.ok
    # And vouch bytes do not decode under the execution schema layout.
    with pytest.raises(ValueError):
        decode_schema_data(SCHEMA_EXECUTION_OUTCOME, att.data)


def test_unknown_schema_rejected_at_create_and_verify(client, keys):
    with pytest.raises(ValueError):
        client.create_attestation(keys["alice"].key.hex(), "agent-bob",
                                  "0x" + "ab" * 32, vouch_data(), at=NOW)
    att = make_vouch(client, keys, "alice", "bob")
    att.schema_uid = "0x" + "ab" * 32
    result = client.verify_attestation(att, now=NOW)
    assert not result.ok and result.reason == "unknown_schema_uid"


def test_expired_rejected_but_no_expiration_accepted(client, keys):
    att = client.create_attestation(keys["alice"].key.hex(), "agent-bob",
                                    SCHEMA_VOUCH.uid, vouch_data(),
                                    expiration_time=NOW + 3600, at=NOW)
    assert client.verify_attestation(att, now=NOW + 100).ok
    assert not client.verify_attestation(att, now=NOW + 7200).ok
    assert client.verify_attestation(att, now=NOW + 7200).reason == "expired"
    forever = make_vouch(client, keys, "alice", "bob")
    assert client.verify_attestation(forever, now=NOW + 10 ** 9).ok


def test_future_dated_rejected(client, keys):
    att = make_vouch(client, keys, "alice", "bob", at=NOW + 100_000)
    result = client.verify_attestation(att, now=NOW)
    assert not result.ok and result.reason == "future_dated"


def test_cross_domain_signature_rejected(client, keys):
    att = make_vouch(client, keys, "alice", "bob")
    foreign = EASClient(chain_id=8453)  # same keys, different domain
    result = foreign.verify_attestation(att, now=NOW)
    assert not result.ok
    assert result.reason == "attester_mismatch"


def test_salt_makes_each_issuance_unique(client, keys):
    a1 = make_vouch(client, keys, "alice", "bob")
    a2 = make_vouch(client, keys, "alice", "bob")
    assert a1.uid != a2.uid  # replaying the same content mints a new UID
    assert client.verify_attestation(a1, now=NOW).ok
    assert client.verify_attestation(a2, now=NOW).ok


def test_revocation_flow(client, keys):
    att = make_vouch(client, keys, "alice", "bob")
    assert client.verify_attestation(att, now=NOW).ok
    rev = client.revoke(att.uid, keys["alice"].key.hex())
    assert client.verify_revocation(rev)
    assert client.is_revoked(att.uid)
    result = client.verify_attestation(att, now=NOW)
    assert not result.ok and result.reason == "revoked"
    # Non-attester cannot revoke.
    att2 = make_vouch(client, keys, "alice", "bob", task=9)
    with pytest.raises(ValueError):
        client.revoke(att2.uid, keys["mallory"].key.hex())
    with pytest.raises(ValueError):
        client.revoke("0x" + "99" * 32, keys["alice"].key.hex())


def test_serialization_roundtrip_and_cnp2_shape(client, keys):
    att = make_vouch(client, keys, "alice", "bob")
    restored = Attestation.from_dict(att.to_dict())
    assert restored.uid == att.uid
    assert client.verify_attestation(restored, now=NOW).ok
    attester, subject, schema_id, payload_hash = restored.summary()
    assert attester == keys["alice"].address
    assert subject == "agent-bob"
    assert schema_id == SCHEMA_VOUCH.uid
    assert payload_hash == restored.payload_hash()


def test_kya_tier_schema_roundtrip(client, keys):
    att = client.create_attestation(
        keys["carol"].key.hex(), "agent-dave", SCHEMA_KYA_TIER.uid,
        {"tier": 2, "kya_ref": _b32(5), "payload_hash": _b32(6)}, at=NOW)
    result = client.verify_attestation(att, now=NOW)
    assert result.ok, result.reason
    assert client._seen[att.uid].schema_uid == SCHEMA_KYA_TIER.uid


def test_create_fails_closed_on_bad_inputs(client, keys):
    with pytest.raises(ValueError):  # unknown subject
        client.create_attestation(keys["alice"].key.hex(), "agent-ghost",
                                  SCHEMA_VOUCH.uid, vouch_data(), at=NOW)
    with pytest.raises(ValueError):  # bad key
        client.create_attestation("0xdeadbeef", "agent-bob",
                                  SCHEMA_VOUCH.uid, vouch_data(), at=NOW)
    with pytest.raises(ValueError):  # expiry in the past
        client.create_attestation(keys["alice"].key.hex(), "agent-bob",
                                  SCHEMA_VOUCH.uid, vouch_data(),
                                  expiration_time=NOW - 1, at=NOW)


# ---------------------------------------------------------------------------
# Graph: ingest + queries
# ---------------------------------------------------------------------------

@pytest.fixture
def graph(client):
    return AttestationGraph(client)


def test_ingest_verifies_and_dedupes(graph, client, keys):
    att = make_vouch(client, keys, "alice", "bob")
    first = graph.ingest(att, now=NOW)
    assert first.ok and len(graph) == 1
    second = graph.ingest(att, now=NOW)  # replay of the same UID
    assert not second.ok and second.reason == "duplicate"
    assert len(graph) == 1


def test_ingest_rejects_bad_attestations_without_raising(graph, client,
                                                         keys):
    att = make_vouch(client, keys, "mallory", "bob")
    att.attester = keys["alice"].address  # forged
    result = graph.ingest(att, now=NOW)
    assert not result.ok and result.reason == "attester_mismatch"
    assert len(graph) == 0


def test_self_attestation_rejected(graph, client, keys):
    att = make_vouch(client, keys, "alice", "alice")
    result = graph.ingest(att, now=NOW)
    assert not result.ok and result.reason == "self_attestation"
    assert len(graph) == 0


def test_pair_and_attester_caps_blunt_spam(client, keys):
    g = AttestationGraph(client, max_per_pair=5, max_per_attester=200)
    for task in range(5):
        assert g.ingest(make_vouch(client, keys, "alice", "bob",
                                   task=task), now=NOW).ok
    sixth = g.ingest(make_vouch(client, keys, "alice", "bob", task=99),
                     now=NOW)
    assert not sixth.ok and sixth.reason == "pair_cap_exceeded"

    g2 = AttestationGraph(client, max_per_pair=100, max_per_attester=2)
    assert g2.ingest(make_vouch(client, keys, "carol", "bob", task=1),
                     now=NOW).ok
    assert g2.ingest(make_vouch(client, keys, "carol", "dave", task=2),
                     now=NOW).ok
    third = g2.ingest(make_vouch(client, keys, "carol", "bob", task=3),
                      now=NOW)
    assert not third.ok and third.reason == "attester_cap_exceeded"


def test_aggregate_score_caps_single_attester_influence(graph, client,
                                                        keys):
    # Mallory floods five 100-score vouches; Bob gives one 0-score vouch.
    # Per-attester cap of 3 keeps Mallory to (100,100,100): mean = 0.75.
    for task in range(5):
        assert graph.ingest(make_vouch(client, keys, "mallory", "dave",
                                       task=task, score=100), now=NOW).ok
    assert graph.ingest(make_vouch(client, keys, "bob", "dave",
                                   task=50, score=0), now=NOW).ok
    score = graph.aggregate_vouch_score("agent-dave", now=NOW)
    assert score == pytest.approx(0.75)
    # Without the cap Mallory alone would have pushed it to ~0.833.
    assert score < (5 * 100 + 0) / 6 / 100
    assert graph.attester_diversity("agent-dave", now=NOW) == 2


def test_aggregate_score_zero_without_vouches(graph):
    assert graph.aggregate_vouch_score("agent-nobody", now=NOW) == 0.0


def test_incoming_outgoing_edges_between(graph, client, keys):
    graph.ingest(make_vouch(client, keys, "alice", "bob", task=1), now=NOW)
    graph.ingest(make_vouch(client, keys, "carol", "bob", task=2), now=NOW)
    graph.ingest(make_vouch(client, keys, "alice", "dave", task=3), now=NOW)
    assert len(graph.incoming("agent-bob", now=NOW)) == 2
    assert len(graph.outgoing("agent-alice", now=NOW)) == 2
    assert len(graph.incoming("agent-bob", schema_uid=SCHEMA_VOUCH.uid,
                              now=NOW)) == 2
    assert len(graph.incoming("agent-bob",
                              schema_uid=SCHEMA_KYA_TIER.uid,
                              now=NOW)) == 0
    between = graph.edges_between("agent-alice", "agent-bob", now=NOW)
    assert len(between) == 1


def test_vouch_path_bfs(graph, client, keys):
    graph.ingest(make_vouch(client, keys, "alice", "bob"), now=NOW)
    graph.ingest(make_vouch(client, keys, "bob", "carol"), now=NOW)
    graph.ingest(make_vouch(client, keys, "carol", "dave"), now=NOW)
    assert graph.vouch_path("agent-alice", "agent-dave", now=NOW) == [
        "agent-alice", "agent-bob", "agent-carol", "agent-dave"]
    assert graph.vouch_path("agent-dave", "agent-alice", now=NOW) == []
    assert graph.vouch_path("agent-alice", "agent-alice", now=NOW) == [
        "agent-alice"]


def test_mutual_cycles_flagged(graph, client, keys):
    graph.ingest(make_vouch(client, keys, "alice", "bob"), now=NOW)
    graph.ingest(make_vouch(client, keys, "bob", "alice"), now=NOW)
    graph.ingest(make_vouch(client, keys, "carol", "dave"), now=NOW)
    mutual = graph.mutual_cycles(now=NOW)
    assert ("agent-alice", "agent-bob") in mutual
    assert len(mutual) == 1  # one direction only, no double count


def test_expired_and_revoked_edges_excluded_and_pruned(graph, client,
                                                       keys):
    live = make_vouch(client, keys, "alice", "bob", task=1)
    dying = client.create_attestation(
        keys["carol"].key.hex(), "agent-bob", SCHEMA_VOUCH.uid,
        vouch_data(score=10, task=2), expiration_time=NOW + 100, at=NOW)
    assert graph.ingest(live, now=NOW).ok
    assert graph.ingest(dying, now=NOW).ok
    assert len(graph.incoming("agent-bob", now=NOW)) == 2
    assert len(graph.incoming("agent-bob", now=NOW + 200)) == 1
    assert graph.prune(now=NOW + 200) == 1
    assert len(graph) == 1
    # Revocation also hides the edge from queries.
    client.revoke(live.uid, keys["alice"].key.hex())
    assert graph.incoming("agent-bob", now=NOW) == []
    assert graph.aggregate_vouch_score("agent-bob", now=NOW) == 0.0


def test_top_subjects_ranking(graph, client, keys):
    graph.ingest(make_vouch(client, keys, "alice", "bob", score=90),
                 now=NOW)
    graph.ingest(make_vouch(client, keys, "carol", "bob", score=80),
                 now=NOW)
    graph.ingest(make_vouch(client, keys, "alice", "dave", score=10),
                 now=NOW)
    top = graph.top_subjects(n=2, now=NOW)
    assert [s for s, _ in top] == ["agent-bob", "agent-dave"]
    assert top[0][1] == pytest.approx(0.85)


def test_ref_uid_links_attestations(client, keys):
    base = make_vouch(client, keys, "alice", "bob", task=1)
    follow = client.create_attestation(
        keys["alice"].key.hex(), "agent-bob", SCHEMA_VOUCH.uid,
        vouch_data(score=95, task=1), ref_uid=base.uid, at=NOW)
    assert follow.ref_uid == base.uid
    assert client.verify_attestation(follow, now=NOW).ok
    assert follow.ref_uid != ZERO_UID


def test_offchain_domain_is_not_real_eas():
    from eth_utils import to_checksum_address
    c = EASClient()
    assert c.verifying_contract == "0x0000000000000000000000000000000000000000"
    # Operator must explicitly pin a real EAS deployment to change it.
    pinned = EASClient(chain_id=8453,
                       verifying_contract="0x" + "aa" * 20)
    assert pinned.verifying_contract == to_checksum_address("0x" + "aa" * 20)
