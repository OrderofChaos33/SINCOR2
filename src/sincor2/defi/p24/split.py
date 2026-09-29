"""P24 fee-split distributor: conservation-exact three-way split.

Every trade pays trade_fee_bps (10%) total. Each leg is floored and the
residual dust goes to the treasury leg (the most conservative destination):

    creator  = floor(fee * 4940 / 10000)
    platform = floor(fee * 4940 / 10000)
    treasury = floor(fee * 120 / 10000) + dust

Invariant (structural, not remembered): creator + platform + treasury == fee
for every input. The creator_split gate: creator share of the fee can never
be set below creator_floor_bps (4000); parameter updates violating this are
rejected.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..catalog import TREASURY
from . import PARAMS


class SplitParamError(ValueError):
    """Split parameter update rejected."""


@dataclass(frozen=True)
class SplitResult:
    fee_wei: int
    creator_wei: int
    platform_wei: int
    treasury_wei: int
    treasury_to: str
    dust_wei: int


def validate_split_params(
    creator_bps: int,
    platform_bps: int,
    treasury_bps: int,
) -> None:
    """Gate: shares sum to 10000 and creator stays at/above the 40% floor."""
    if creator_bps + platform_bps + treasury_bps != 10_000:
        raise SplitParamError(
            f"split shares must sum to 10000, got "
            f"{creator_bps + platform_bps + treasury_bps}"
        )
    if creator_bps < PARAMS["creator_floor_bps"]:
        raise SplitParamError(
            f"creator share {creator_bps} bps below floor "
            f"{PARAMS['creator_floor_bps']} bps"
        )
    for name, v in (("creator", creator_bps), ("platform", platform_bps),
                    ("treasury", treasury_bps)):
        if v < 0:
            raise SplitParamError(f"{name} share negative")


def split_fee(
    fee_wei: int,
    creator_bps: int | None = None,
    platform_bps: int | None = None,
    treasury_bps: int | None = None,
) -> SplitResult:
    if fee_wei < 0:
        raise ValueError("fee cannot be negative")
    creator_bps = PARAMS["creator_share_bps"] if creator_bps is None else creator_bps
    platform_bps = PARAMS["platform_share_bps"] if platform_bps is None else platform_bps
    treasury_bps = PARAMS["treasury_share_bps"] if treasury_bps is None else treasury_bps
    validate_split_params(creator_bps, platform_bps, treasury_bps)

    creator = (fee_wei * creator_bps) // 10_000
    platform = (fee_wei * platform_bps) // 10_000
    treasury_base = (fee_wei * treasury_bps) // 10_000
    dust = fee_wei - (creator + platform + treasury_base)
    treasury = treasury_base + dust
    assert creator + platform + treasury == fee_wei  # structural invariant
    return SplitResult(
        fee_wei=fee_wei,
        creator_wei=creator,
        platform_wei=platform,
        treasury_wei=treasury,
        treasury_to=TREASURY,
        dust_wei=dust,
    )


def trade_fee(trade_value_wei: int) -> int:
    """The 10% trade fee in wei."""
    if trade_value_wei < 0:
        raise ValueError("trade value cannot be negative")
    return (trade_value_wei * PARAMS["trade_fee_bps"]) // 10_000
