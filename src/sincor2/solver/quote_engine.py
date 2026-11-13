"""
Solver quoting interface — PAPER ONLY.

Given an intent (tokenIn, tokenOut, amountIn, deadline), returns a
quote object in the shape a solver would respond with: route, expected
output, gas, full fee breakdown, expiry. The object carries a
``signature`` field that is always None and ``signed`` that is always
False in this build — no keys exist here and nothing can be submitted
from a quote. It is a measurement artifact, not an order.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from .pathfinder import PriceBook, RouteQuote, find_routes
from .pools import PoolSnapshot, TOKEN_SYMBOLS, checksum

SOLVER_ID = "sincor-paper-solver/0.1"
QUOTE_TTL_SECONDS = 30
DEFAULT_SLIPPAGE_BPS = 50


class NoRouteError(Exception):
    """No quotable route exists for the intent at this snapshot."""


class IntentExpiredError(Exception):
    """The intent deadline has already passed."""


class SolverQuoter:
    def __init__(self, snapshot: PoolSnapshot,
                 price_book: Optional[PriceBook] = None):
        self.snapshot = snapshot
        self.price_book = price_book or PriceBook.from_snapshot(snapshot)

    def quote(self, token_in: str, token_out: str, amount_in: int,
              deadline: int, requester: Optional[str] = None,
              slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
              now: Optional[int] = None) -> dict:
        """Produce an unsigned solver quote for one intent."""
        now = int(time.time()) if now is None else now
        if deadline <= now:
            raise IntentExpiredError("intent deadline has passed")
        if amount_in <= 0:
            raise ValueError("amount_in must be positive")
        routes = find_routes(self.snapshot, token_in, token_out, amount_in,
                             self.price_book)
        if not routes:
            raise NoRouteError(
                f"no quotable route {token_in} -> {token_out} "
                f"at block {self.snapshot.block_number}")
        best: RouteQuote = routes[0]
        expires_at = min(deadline, now + QUOTE_TTL_SECONDS)
        min_amount_out = best.amount_out_net * (10_000 - slippage_bps) // 10_000
        return {
            "quote_id": uuid.uuid4().hex,
            "solver": SOLVER_ID,
            "mode": "PAPER",
            "signed": False,
            "signature": None,
            "intent": {
                "token_in": token_in,
                "token_out": token_out,
                "amount_in": amount_in,
                "deadline": deadline,
                "requester": requester,
            },
            "route": [
                {
                    "venue": hop.venue,
                    "pool": hop.pool_address,
                    "token_in": hop.token_in,
                    "token_out": hop.token_out,
                    "fee_bps": hop.fee_bps,
                    "amount_in": hop.amount_in,
                    "amount_out": hop.amount_out,
                    "lp_fee_amount": hop.lp_fee_amount,
                }
                for hop in best.hops
            ],
            "amount_in": best.amount_in,
            "amount_out_gross": best.amount_out_gross,
            "fees": {
                "lp_fees_in_hop_terms": [h.lp_fee_amount for h in best.hops],
                "platform_fee_bps": 500,
                "platform_fee_amount": best.platform_fee_amount,
                "platform_fee_token": token_out,
            },
            "amount_out_net": best.amount_out_net,
            "slippage_bps": slippage_bps,
            "min_amount_out": min_amount_out,
            "gas": {
                "estimated_units": best.gas_units,
                "gas_price_wei": self.snapshot.gas_price_wei,
                "cost_usd": (round(float(best.gas_cost_usd), 6)
                             if best.gas_cost_usd is not None else None),
            },
            "block_number": self.snapshot.block_number,
            "quoted_at": now,
            "expires_at": expires_at,
        }


def describe_quote(quote: dict) -> str:
    """One-line human summary of a quote (symbols when known)."""
    intent = quote["intent"]
    sym_in = TOKEN_SYMBOLS.get(_match(intent["token_in"]), intent["token_in"][:8])
    sym_out = TOKEN_SYMBOLS.get(_match(intent["token_out"]),
                                intent["token_out"][:8])
    venues = " -> ".join(h["venue"] for h in quote["route"])
    return (f"{sym_in}->{sym_out} via {venues}: "
            f"out_net={quote['amount_out_net']} "
            f"(platform fee {quote['fees']['platform_fee_amount']}), "
            f"block {quote['block_number']}, expires {quote['expires_at']}")


def _match(address: str) -> str:
    for known in TOKEN_SYMBOLS:
        if checksum(known) == checksum(address):
            return known
    return address
