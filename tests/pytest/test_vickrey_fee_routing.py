"""Path 3: Vickrey auction platform-fee routing to treasury.

Fee canon (locked 2026-09-26, mirrored from Path 1):
5 % platform fee (PLATFORM_FEE_BPS = 500, cf.
``marketplace/settlement.py`` PLATFORM_FEE_BPS and
``sincor2.a2a_integration.A2A_PLATFORM_FEE_BPS``), 100 % to treasury,
NO burn. The fee comes out of the settlement proceeds (the clearing
price the poster pays); it does NOT alter the clearing price, the
winner's bid, or who wins — the winner (payee) receives the remainder,
exactly as ``SettlementCoordinator.confirm_payment`` records
fee = 5 % of amount, payee = amount - fee.

This is a REVERSE auction: the poster/payer pays the winner the
second-lowest valid price. Prices are integer micro-AXM.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if not (ROOT / "marketplace" / "discovery.py").exists() and "marketplace" not in sys.modules:
    pkg = types.ModuleType("marketplace")
    pkg.__path__ = [str(ROOT / "marketplace")]
    sys.modules["marketplace"] = pkg

from marketplace.contract_net.engine import ContractNetEngine  # noqa: E402
from marketplace.contract_net.roster import demo_roster, demo_tasks  # noqa: E402
from marketplace.contract_net.types import (  # noqa: E402
    DEFAULT_VERIFYING_CONTRACT,
    ContractNetConfig,
    Invite,
    SealedBid,
)
from marketplace.contract_net.vickrey import (  # noqa: E402
    PLATFORM_FEE_BPS,
    clear_vickrey,
    platform_fee_for,
    split_clearing_proceeds,
)

TREASURY = DEFAULT_VERIFYING_CONTRACT  # types.py: canonical platform treasury


def _bid(agent_id: str, price: int) -> SealedBid:
    return SealedBid(
        auction_id="0x" + "11" * 32,
        task_id="t",
        agent_id=agent_id,
        agent_wallet="0x" + "22" * 20,
        price=price,
        estimated_tokens=100,
        nonce=1,
        deadline=9_999_999_999,
        digest="0x" + "33" * 32,
        signature="0x" + "44" * 32,
        sig_type="eip712-hmac-sha256",
    )


def _invite(agent_id: str) -> Invite:
    return Invite(
        agent_id=agent_id, name=agent_id, cosine=0.9, junior=False,
        llm_invited=True, reason="top-k",
    )


def _clear(prices):
    bids = [_bid(f"agent-{i}", p) for i, p in enumerate(prices)]
    return clear_vickrey(
        auction_id="0x" + "11" * 32,
        task_id="t",
        bids=bids,
        invites=[_invite(b.agent_id) for b in bids],
        junior_reserved=False,
        llm_calls_avoided=0,
        tokens_saved=0,
    )


def test_fee_canon_is_500_bps():
    assert PLATFORM_FEE_BPS == 500


def test_fee_amount_correctness_at_canon_rate():
    award = _clear([1_000_000, 2_000_000, 3_000_000])
    # Clearing is the second-lowest price; poster pays exactly that.
    assert award.clearing_price == 2_000_000
    assert award.platform_fee == 100_000  # 5 % of 2_000_000
    assert award.winner_net_amount == 1_900_000
    assert award.platform_fee + award.winner_net_amount == award.clearing_price


def test_treasury_receives_fee_and_winner_remainder():
    award = _clear([1_000_000, 2_000_000])
    assert award.platform_fee_bps == 500
    assert award.treasury_address == TREASURY
    assert award.platform_fee == 100_000
    # Remainder flows to the winner (payee), as before minus the fee.
    assert award.winner_net_amount == award.clearing_price - award.platform_fee


def test_winner_payment_terms_unchanged():
    """Gross settlement (what the poster pays) is still the second price."""
    award = _clear([800_000, 1_500_000, 2_500_000])
    assert award.winner_id == "agent-0"
    assert award.winner_bid_price == 800_000
    assert award.clearing_price == 1_500_000
    assert award.savings_vs_first_price == 700_000


def test_single_bid_fee_on_own_price():
    award = _clear([700_000])
    assert award.clearing_price == 700_000
    assert award.platform_fee == 35_000
    assert award.winner_net_amount == 665_000


def test_no_valid_bids_no_fee():
    award = clear_vickrey(
        auction_id="0x" + "11" * 32, task_id="t", bids=[], invites=[],
        junior_reserved=False, llm_calls_avoided=0, tokens_saved=0,
    )
    assert award.phase == "failed"
    assert award.clearing_price == 0
    assert award.platform_fee == 0
    assert award.winner_net_amount == 0


def test_fee_rounding_floors_and_never_exceeds_proceeds():
    # 19 micro: 5 % floors to 0 — winner keeps all, nothing invented.
    assert split_clearing_proceeds(19) == (0, 19)
    # 199 micro: fee 9 (floor of 9.95), remainder 190; sum is exact.
    assert split_clearing_proceeds(199) == (9, 190)
    for amount in (0, 1, 20, 10_001, 1_000_001, 123_456_789):
        fee, net = split_clearing_proceeds(amount)
        assert fee == amount * 500 // 10_000
        assert fee + net == amount
        assert 0 <= fee <= amount


def test_tiny_surplus_still_splits_on_clearing():
    # Surplus (clearing - winner bid) is 1 micro; fee is on the proceeds
    # (clearing price), not on the surplus, mirroring Path 1 settlement.
    award = _clear([1_000_000, 1_000_001])
    assert award.savings_vs_first_price == 1
    assert award.platform_fee == 1_000_001 * 500 // 10_000
    assert award.platform_fee + award.winner_net_amount == award.clearing_price


def test_platform_fee_for_rejects_negative():
    with pytest.raises(ValueError):
        platform_fee_for(-1)


def test_engine_wiring_carries_fee_routing():
    """The real caller (ContractNetEngine.run) exercises the routing."""
    engine = ContractNetEngine(
        ContractNetConfig(chain_id=84532, invite_k=4, epsilon=0.12,
                          allow_hmac_bids=True)
    )
    award = engine.run(demo_tasks()[1], demo_roster(), seed=11,
                       force_junior=False)
    assert award.phase == "cleared"
    assert award.platform_fee == award.clearing_price * PLATFORM_FEE_BPS // 10_000
    assert award.winner_net_amount == award.clearing_price - award.platform_fee
    payload = award.to_dict()
    assert payload["platform_fee"] == award.platform_fee
    assert payload["winner_net_amount"] == award.winner_net_amount
    assert payload["platform_fee_bps"] == 500
    assert payload["treasury_address"] == TREASURY
