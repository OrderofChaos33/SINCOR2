"""AXM-only quotes, 500 bps platform fee, realized fee inflow."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sincor2 import a2a_integration  # noqa: E402
from sincor2.treasury_settlement import record_platform_fee_inflow  # noqa: E402


def test_default_primary_token_is_axiom():
    assert a2a_integration.A2A_PRIMARY_TOKEN == "AXIOM"
    assert a2a_integration.A2A_PLATFORM_FEE_BPS == 500


def test_platform_fee_500_bps_on_1e18():
    assert a2a_integration._compute_platform_fee_wei(10**18) == 5 * 10**16


def test_reject_sinc_accept_axm():
    assert a2a_integration._reject_non_axm("SINC")
    assert a2a_integration._reject_non_axm("AXM") is None
    assert a2a_integration._reject_non_axm("AXIOM") is None
    assert a2a_integration._reject_non_axm(None) is None
    assert a2a_integration._reject_non_axm("") is None


def test_record_platform_fee_inflow_skips_zero():
    assert record_platform_fee_inflow(fee_amount=0) is None
    assert record_platform_fee_inflow(fee_amount=Decimal("0")) is None


def test_skill_id_aliases():
    assert a2a_integration._resolve_skill_id({"skill": "lead-enrichment"}) == "lead-enrichment"
    assert a2a_integration._resolve_skill_id({"skillId": "competitor-intel"}) == "competitor-intel"
    assert a2a_integration._resolve_skill_id({"skill_id": "toa-decision"}) == "toa-decision"
    assert a2a_integration._resolve_skill_id({"skill_id": ""}, {"skill": "deal-scoring"}) == "deal-scoring"
    assert a2a_integration._resolve_skill_id({}) == ""


def test_maybe_record_fee_one_realized_call():
    with patch("sincor2.a2a_integration.record_platform_fee_inflow") as rec:
        rec.return_value = {"ok": True}
        meta: dict = {}
        out = a2a_integration.maybe_record_a2a_platform_fee(
            axm_paid_wei=10**18,
            tx_hash="0x" + "ab" * 32,
            task_id="task-1",
            metadata=meta,
        )
        assert out == {"ok": True}
        rec.assert_called_once()
        kwargs = rec.call_args.kwargs
        assert kwargs["projected"] is False if "projected" in kwargs else True
        assert kwargs["source"] == "a2a_settlement"
        assert kwargs["tx_hash"].startswith("0x")
        assert kwargs["asset"] == "AXM"
        again = a2a_integration.maybe_record_a2a_platform_fee(
            axm_paid_wei=10**18,
            tx_hash="0x" + "ab" * 32,
            task_id="task-1",
            metadata=meta,
        )
        assert again is None
        rec.assert_called_once()


def test_maybe_record_fee_skips_simulated_and_free():
    with patch("sincor2.a2a_integration.record_platform_fee_inflow") as rec:
        assert a2a_integration.maybe_record_a2a_platform_fee(
            axm_paid_wei=10**18, tx_hash="0xSIMULATEDDEAD", task_id="t"
        ) is None
        assert a2a_integration.maybe_record_a2a_platform_fee(
            axm_paid_wei=10**18, tx_hash="0x" + "cd" * 32, free_call=True
        ) is None
        assert a2a_integration.maybe_record_a2a_platform_fee(
            axm_paid_wei=0, tx_hash="0x" + "cd" * 32
        ) is None
        rec.assert_not_called()


def test_record_a2a_settlement_records_fee_without_platform_state():
    task = a2a_integration.A2ATask(
        id="task-no-platform",
        context_id="ctx",
        skill_id="lead-enrichment",
        input_text="hi",
        caller_id="anon",
        state=a2a_integration.TaskState.COMPLETED,
        created_at="now",
        updated_at="now",
        axm_paid=10**18,
        tx_hash="0x" + "11" * 32,
        metadata={"free_call": False},
    )
    with patch("sincor2.a2a_integration.record_platform_fee_inflow") as rec:
        rec.return_value = {"ok": True}
        a2a_integration._record_a2a_settlement(task, 10**18, task.tx_hash)
        rec.assert_called_once()


def test_agent_card_top_level_protocol_fields():
    card = a2a_integration.build_agent_card().to_dict()
    assert card["protocolVersion"] == "1.0.1"
    assert card["url"].endswith("/api/a2a")
    assert card["preferredTransport"] == "JSONRPC"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
