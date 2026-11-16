"""Sealed-bid Vickrey reverse auction.

Lowest valid price wins. The winner is paid the second-lowest valid price
(or their own price if they were the only valid bid). No iterative
counter-bids: one signed envelope per agent.

Fee routing (Path 3): on a cleared award, the platform fee — 5 %
(``PLATFORM_FEE_BPS`` = 500, the locked fee canon) — is taken out of the
clearing-price proceeds and routed 100 % to the treasury, with NO burn,
mirroring ``marketplace/settlement.py`` (``PLATFORM_FEE_BPS`` /
``SettlementCoordinator.confirm_payment``) and
``sincor2.a2a_integration.A2A_PLATFORM_FEE_BPS``. The winner (payee)
receives the remainder. The fee does not alter the clearing price, the
winning bid, or winner selection, so bid truthfulness is preserved:
every winner's payoff is scaled by the same constant factor.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from .types import Award, AuctionPhase, Invite, SealedBid

# Platform fee in basis points (500 = 5 %). Locked fee canon 2026-09-26:
# 100 % to treasury, no burn. Kept local (mirroring
# sincor2.activity_engine.PLATFORM_FEE_BPS) so this module stays
# dependency-free pure clearing logic.
PLATFORM_FEE_BPS = 500
_BPS_DENOM = 10_000


def platform_fee_for(amount: int) -> int:
    """Return the 5 % platform fee for a settlement amount (micro-AXM).

    Integer floor, matching ``a2a_integration._compute_platform_fee_wei``:
    the fee never rounds up and never exceeds the amount.
    """
    if amount < 0:
        raise ValueError("Settlement amount cannot be negative")
    return (amount * PLATFORM_FEE_BPS) // _BPS_DENOM


def split_clearing_proceeds(clearing_price: int) -> Tuple[int, int]:
    """Split clearing proceeds into (platform_fee, winner_net_amount)."""
    fee = platform_fee_for(clearing_price)
    return fee, clearing_price - fee


def sort_valid_bids(bids: Sequence[SealedBid]) -> List[SealedBid]:
    valid = [bid for bid in bids if bid.valid]
    valid.sort(key=lambda bid: (bid.price, bid.agent_id))
    return valid


def clear_vickrey(
    *,
    auction_id: str,
    task_id: str,
    bids: Sequence[SealedBid],
    invites: Sequence[Invite],
    junior_reserved: bool,
    llm_calls_avoided: int,
    tokens_saved: int,
    invited_ids: Optional[Iterable[str]] = None,
) -> Award:
    invited = set(invited_ids) if invited_ids is not None else {row.agent_id for row in invites}
    checked: List[SealedBid] = []
    seen: set[str] = set()
    for bid in bids:
        if bid.agent_id in seen:
            bid.valid = False
            bid.reject_reason = bid.reject_reason or "duplicate_bid"
        seen.add(bid.agent_id)
        if bid.agent_id not in invited:
            bid.valid = False
            bid.reject_reason = bid.reject_reason or "not_invited"
        checked.append(bid)

    valid = sort_valid_bids(checked)
    rejected = sum(1 for bid in checked if not bid.valid)

    if not valid:
        return Award(
            auction_id=auction_id,
            task_id=task_id,
            winner_id="",
            winner_wallet="",
            winner_bid_price=0,
            clearing_price=0,
            savings_vs_first_price=0,
            mechanism="vickrey-second-price",
            junior_reserved=junior_reserved,
            junior_winner=False,
            invites=list(invites),
            bids=list(checked),
            llm_calls_avoided=llm_calls_avoided,
            tokens_saved=tokens_saved,
            valid_bid_count=0,
            rejected_bid_count=rejected,
            phase=AuctionPhase.FAILED.value,
            error="no_valid_bids",
        )

    winner = valid[0]
    clearing = valid[1].price if len(valid) > 1 else winner.price
    rent = clearing - winner.price
    platform_fee, winner_net = split_clearing_proceeds(clearing)
    junior_ids = {row.agent_id for row in invites if row.junior}

    return Award(
        auction_id=auction_id,
        task_id=task_id,
        winner_id=winner.agent_id,
        winner_wallet=winner.agent_wallet,
        winner_bid_price=winner.price,
        clearing_price=clearing,
        savings_vs_first_price=rent,
        platform_fee_bps=PLATFORM_FEE_BPS,
        platform_fee=platform_fee,
        winner_net_amount=winner_net,
        mechanism="vickrey-second-price",
        junior_reserved=junior_reserved,
        junior_winner=winner.agent_id in junior_ids,
        invites=list(invites),
        bids=list(checked),
        llm_calls_avoided=llm_calls_avoided,
        tokens_saved=tokens_saved,
        valid_bid_count=len(valid),
        rejected_bid_count=rejected,
        phase=AuctionPhase.CLEARED.value,
    )


def vickrey_truthful_prices(prices: Sequence[int]) -> Tuple[int, int, int]:
    """Return (winner_index_in_sorted, winner_price, clearing_price)."""
    ordered = sorted(prices)
    if not ordered:
        raise ValueError("no prices")
    winner_price = ordered[0]
    clearing = ordered[1] if len(ordered) > 1 else ordered[0]
    return 0, winner_price, clearing
