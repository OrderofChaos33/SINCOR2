"""Sealed-bid Vickrey reverse auction.

Lowest valid price wins. The winner is paid the second-lowest valid price
(or their own price if they were the only valid bid). No iterative
counter-bids: one signed envelope per agent.

Fee routing (Path 3): on a cleared award, the platform fee is taken out
of the clearing-price proceeds and routed 100 % to the treasury, with NO
burn, mirroring ``marketplace/settlement.py`` (``PLATFORM_FEE_BPS`` /
``SettlementCoordinator.confirm_payment``) and
``sincor2.a2a_integration.A2A_PLATFORM_FEE_BPS``. The winner (payee)
receives the remainder. The fee does not alter the clearing price, the
winning bid, or winner selection, so bid truthfulness is preserved:
every winner's payoff is scaled by the same constant factor.

Fee rate (founder ratification 2026-11-16): the base rate is the locked
fee canon, 5 % (``PLATFORM_FEE_BPS`` = 500) — a deliberate 50 % discount
to the researched 10 % market median, held as penetration pricing. It
steps up to the market median (1000 bps) once monthly clearing volume
reaches $1,000,000 for 2 consecutive months; the resolver and the
clearing-volume ledger live in ``.volume_ledger``. Each clearing is
recorded to that ledger and the award carries the rate actually applied
(``Award.platform_fee_bps``).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from .types import Award, AuctionPhase, Invite, SealedBid
from .volume_ledger import record_clearing, resolve_fee_bps

# Platform fee in basis points (500 = 5 %). Locked fee canon 2026-09-26:
# 100 % to treasury, no burn. Kept local (mirroring
# sincor2.activity_engine.PLATFORM_FEE_BPS) so this module stays
# dependency-free pure clearing logic.
PLATFORM_FEE_BPS = 500
_BPS_DENOM = 10_000


def platform_fee_for(amount: int, fee_bps: int = PLATFORM_FEE_BPS) -> int:
    """Return the platform fee for a settlement amount (micro-AXM).

    ``fee_bps`` defaults to the 5 % canon rate. Integer floor, matching
    ``a2a_integration._compute_platform_fee_wei``: the fee never rounds
    up and never exceeds the amount.
    """
    if amount < 0:
        raise ValueError("Settlement amount cannot be negative")
    if fee_bps < 0:
        raise ValueError("fee_bps cannot be negative")
    return (amount * fee_bps) // _BPS_DENOM


def split_clearing_proceeds(
    clearing_price: int, fee_bps: int = PLATFORM_FEE_BPS
) -> Tuple[int, int]:
    """Split clearing proceeds into (platform_fee, winner_net_amount)."""
    fee = platform_fee_for(clearing_price, fee_bps)
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
    cleared_at: Optional[datetime] = None,
    ledger_path: Optional[Union[str, Path]] = None,
    record_volume: bool = True,
) -> Award:
    """Clear a sealed-bid Vickrey reverse auction.

    On a cleared award the platform fee rate is resolved from the
    clearing-volume history recorded before this clearing (base 500 bps;
    1000 bps once monthly clearing volume is >= $1M for 2 consecutive
    months — see ``.volume_ledger``), the fee is split out of the
    clearing-price proceeds to the treasury, and the clearing is
    appended to the volume ledger with the rate actually applied.
    ``cleared_at`` / ``ledger_path`` / ``record_volume`` exist for tests
    and deployments that keep volume state elsewhere; defaults preserve
    the recorded-ledger behavior.
    """
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
    # Volume step-up (founder ratification 2026-11-16): the rate comes
    # from the volume history recorded BEFORE this clearing; this
    # clearing is then recorded with the rate actually applied.
    fee_bps = resolve_fee_bps(ledger_path, now=cleared_at)
    platform_fee, winner_net = split_clearing_proceeds(clearing, fee_bps)
    if record_volume:
        record_clearing(
            auction_id=auction_id,
            task_id=task_id,
            clearing_price_micro_axm=clearing,
            fee_bps=fee_bps,
            cleared_at=cleared_at,
            ledger_path=ledger_path,
        )
    junior_ids = {row.agent_id for row in invites if row.junior}

    return Award(
        auction_id=auction_id,
        task_id=task_id,
        winner_id=winner.agent_id,
        winner_wallet=winner.agent_wallet,
        winner_bid_price=winner.price,
        clearing_price=clearing,
        savings_vs_first_price=rent,
        platform_fee_bps=fee_bps,
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
