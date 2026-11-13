"""WP0 contract tests: the frozen shadow safety contract.

These tests pin the WP0 contract. Any change to the vocabulary, originator
rules, or policy decision structure must be a deliberate, reviewed contract
revision — not a silent drift.
"""

import pytest

from sincor2.shadow_monitor.contract import (
    CONTRACT_VERSION,
    EFFECT_ORIGINATORS,
    EFFECT_VOCABULARY,
    SHADOW_RECEIPT_STATUSES,
    VALUE_MOVING_EFFECTS,
    PolicyDecision,
    is_valid_originator,
)


def test_vocabulary_has_eight_effect_types():
    assert len(EFFECT_VOCABULARY) == 8
    assert EFFECT_VOCABULARY == frozenset(
        {
            "email.send",
            "social.post",
            "crm.write",
            "crm.delete",
            "payment.transfer",
            "trade.swap",
            "contract.call",
            "message.send",
        }
    )


def test_vocabulary_is_immutable():
    with pytest.raises(AttributeError):
        EFFECT_VOCABULARY.add("evil.effect")  # type: ignore[attr-defined]


def test_value_moving_effects_are_value_moving():
    assert VALUE_MOVING_EFFECTS == frozenset(
        {"payment.transfer", "trade.swap", "contract.call"}
    )
    assert VALUE_MOVING_EFFECTS <= EFFECT_VOCABULARY


def test_every_effect_type_has_originator_allowlist():
    assert set(EFFECT_ORIGINATORS.keys()) == set(EFFECT_VOCABULARY)


def test_originator_allowlist_is_immutable():
    with pytest.raises(TypeError):
        EFFECT_ORIGINATORS["email.send"] = frozenset({"everyone"})  # type: ignore[index]


def test_crm_delete_is_operator_only():
    assert EFFECT_ORIGINATORS["crm.delete"] == frozenset({"operator"})
    assert not is_valid_originator("crm.delete", "agent")
    assert is_valid_originator("crm.delete", "operator")


def test_customer_cannot_originate_agent_effects():
    # Customer-initiated flows stay on separate authorized paths,
    # not forced into the shadow proposal boundary.
    for effect_type in EFFECT_VOCABULARY:
        assert not is_valid_originator(effect_type, "customer"), effect_type


def test_unknown_originator_rejected():
    assert not is_valid_originator("email.send", "superuser")
    assert not is_valid_originator("email.send", "")


def test_unknown_effect_type_rejected():
    assert not is_valid_originator("nuke.launch", "operator")


def _make_decision(**overrides):
    base = dict(
        effect_id="abc123",
        effect_type="email.send",
        originator="agent",
        verdict="allow_proposal",
        reason="test",
        idempotency_key="idem-1",
    )
    base.update(overrides)
    return PolicyDecision(**base)  # type: ignore[arg-type]


def test_policy_decision_requires_idempotency_key():
    with pytest.raises(ValueError, match="idempotency_key"):
        _make_decision(idempotency_key="")


def test_policy_decision_enforces_originator_allowlist():
    with pytest.raises(ValueError, match="may not originate"):
        _make_decision(effect_type="crm.delete", originator="agent")


def test_policy_decision_rejects_unknown_effect():
    with pytest.raises(ValueError, match="unknown effect_type"):
        _make_decision(effect_type="nuke.launch")


def test_policy_decision_is_immutable():
    d = _make_decision()
    with pytest.raises(Exception):
        d.verdict = "allow_live"  # type: ignore[misc]


def test_shadow_receipt_statuses_exclude_live_claims():
    for forbidden in ("sent", "settled", "delivered", "paid", "executed"):
        assert forbidden not in SHADOW_RECEIPT_STATUSES


def test_contract_version_pinned():
    assert CONTRACT_VERSION == "wp0-2026-10-09"
