"""Hunt-50 W-167: decode_toa_penalty must be the STRICT inverse of
encode_toa_penalty.

Before the fix, stdlib json.loads silently accepted duplicate keys
(last value wins — the signed bytes and the executed decision could
carry different values for the same field) and extra fields (silently
dropped), and raised a bare KeyError on missing fields.  Multiple
distinct blobs decoded to the same decision.  These tests pin the
strict contract: one blob -> one decision, or InvalidToaDecision.
"""

import json

import pytest

from sincor2.onchain import toa_penalty as tp

TAG = b"TOAPEN\x01:"

KWARGS = dict(
    agent_id="agent-1", task_id="task-1", offense="quality_failure",
    severity=40, prior_offenses=1, anchor_bps=5000,
    recommended_slash_bps=6250, adjudicator_id="judge-1", nonce=7,
)


def _decision():
    return tp.make_toa_decision(**KWARGS)


def _payload_text():
    blob = tp.encode_toa_penalty(_decision())
    assert blob.startswith(TAG)
    return blob[len(TAG):].decode("utf-8")


def _reblob(obj) -> bytes:
    return TAG + json.dumps(
        obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_duplicate_key_rejected_even_when_signature_covers_raw_bytes():
    # The blob's raw bytes are what a signer signs / a digest binds.
    # The FIRST severity says 0 (benign, what a strict reader sees);
    # the duplicated LAST severity says 100 (what stdlib json would
    # execute).  Decode must refuse the ambiguity outright.
    payload = _payload_text().replace(
        '"severity":40', '"severity":0,"severity":100')
    assert payload != _payload_text()
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(TAG + payload.encode("utf-8"))


def test_duplicate_key_rejected_even_with_identical_values():
    payload = _payload_text().replace(
        '"nonce":7', '"nonce":7,"nonce":7')
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(TAG + payload.encode("utf-8"))


def test_trailing_garbage_rejected():
    good = tp.encode_toa_penalty(_decision())
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(good + b"GARBAGE")


def test_trailing_second_object_rejected():
    good = tp.encode_toa_penalty(_decision())
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(good + b"{}")


def test_trailing_whitespace_rejected():
    # The canonical encoding has no trailing bytes at all; the strict
    # inverse accepts nothing after the JSON value.
    good = tp.encode_toa_penalty(_decision())
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(good + b"\n")


def test_extra_field_rejected():
    fields = json.loads(_payload_text())
    fields["smuggled"] = "x"
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(_reblob(fields))


def test_missing_field_raises_invalid_toa_decision_not_keyerror():
    fields = json.loads(_payload_text())
    del fields["nonce"]
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(_reblob(fields))


def test_non_strict_json_constant_rejected():
    payload = _payload_text().replace('"nonce":7', '"nonce":NaN')
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(TAG + payload.encode("utf-8"))


def test_non_object_payload_rejected():
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(TAG + b"[1,2,3]")


def test_wellformed_control_decodes_and_reencodes_byte_identically():
    decision = _decision()
    blob = tp.encode_toa_penalty(decision)
    decoded = tp.decode_toa_penalty(blob)
    assert decoded == decision
    # Byte-identical round trip: decode is the exact inverse of encode
    # for every legitimate encoding.
    assert tp.encode_toa_penalty(decoded) == blob
    assert tp.toa_penalty_digest(decoded) == tp.toa_penalty_digest(decision)
