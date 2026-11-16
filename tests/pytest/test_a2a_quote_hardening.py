"""Canon assertions for the A2A quote payload (founder CONFORM, 2026-11-16).

Settlement canon is 5% platform fee to treasury, NO burn. The legacy
50% burn split once advertised by a2a_quote_hardening is retired; the
burn fields remain in the payload shape, always zero.
"""

from __future__ import annotations

from sincor2 import a2a_quote_hardening as qh


def test_quote_constants_are_canon():
    assert qh.PLATFORM_FEE_BPS == 500
    assert qh.AXM_BURN_BPS == 0
    assert qh.AXM_TREASURY_BPS == 10_000


def test_quote_payload_fee_split_is_canon():
    payload = qh.build_quote_response("lead-enrichment", "Lead Enrichment")
    split = payload["fee_split"]
    # Fee is 5%, treasury receives the full fee, burn is exactly zero.
    assert split["platform_fee_bps"] == 500
    assert split["platform_fee_pct"] == "5.00%"
    assert split["treasury_share_on_axm_receipt_bps"] == 10_000
    assert split["treasury_share_on_axm_receipt_pct"] == "100%"
    assert split["burn_share_on_axm_receipt_bps"] == 0
    assert split["burn_share_on_axm_receipt_pct"] == "0%"
    assert split["treasury"] == payload["pay_to"] == qh.TREASURY_WALLET
    assert "No burn" in split["note"]
    assert "50%" not in split["note"]


def test_quote_split_math_treasury_gets_full_fee():
    # For any settled amount, the advertised split routes the entire
    # 5% platform fee to treasury and burns nothing.
    amount_wei = 10 * 10**18
    fee_wei = amount_wei * qh.PLATFORM_FEE_BPS // 10_000
    treasury_wei = fee_wei * qh.AXM_TREASURY_BPS // 10_000
    burn_wei = fee_wei * qh.AXM_BURN_BPS // 10_000
    assert fee_wei == 5 * 10**17
    assert treasury_wei == fee_wei
    assert burn_wei == 0
