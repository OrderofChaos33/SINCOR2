"""Market-Data Single Read — x402 paid resource (skills gap G6).

One pair in, one Chainlink price read out, priced as the
``market-data-read`` resource in ``config/x402_pricing.yaml``
($0.0075 USDC on Base — the AsterPay published price point for a
market-data price read, skills-gap-map-2026-10-24 §2.4 / §4 item 5).

The read is served from the repo's REAL Chainlink machinery, reused
unchanged:

- ``sincor2.defi.price_oracle.BASE_MAINNET_FEEDS`` — the repo-pinned
  Base-mainnet aggregator map. It pins exactly two pairs today:
  ``ETH/USD`` and ``USDC/USD``. BTC/USD is deliberately NOT pinned
  (independent sources disagree on its Base address), so it is an
  unknown pair here too — feed addresses are never invented.
- ``sincor2.defi.price_oracle.make_eth_call_reader`` — the read-only
  ``latestRoundData()`` eth_call reader (PR #508-era live-read work).
  Its injected ``read_round(asset)`` seam returns a
  :class:`~sincor2.defi.price_oracle.ChainlinkRound`
  (round_id, answer, decimals, started_at, updated_at,
  answered_in_round) or ``None``. Tests stub exactly this seam with
  fixture rounds — no live network in tests.
- ``sincor2.contract_net.resolve_base_rpc`` — the repo's Base RPC
  resolver for the production reader.

Modes — what shipped and what honestly did not:

- ``price`` ($0.0075, this resource): SHIPPED. The latest round's
  value with full round provenance.
- OHLCV / trending ($0.015 in the gap map): NOT SHIPPED. The repo
  has no OHLCV source — no candle/trades store, no historical-round
  series reader wired for serving (the PR #508 reader fetches ONE
  pinned historical round for options settlement, not a series).
  Inventing candles from single rounds would be fabrication, so
  there is deliberately NO second resource and NO ohlcv/trending
  mode: a payload asking for one is rejected 400
  (``mode_not_available``) rather than served invented data.

Staleness is this product's honesty core:

- Heartbeat threshold: the existing code defines NO per-feed
  heartbeat constant. ``price_oracle.AssetConfig.max_age_s``
  defaults to 300s, but that is the multi-source oracle's
  publication guard, not a Chainlink feed heartbeat; the PR #508
  round reader defaults ``max_age_s`` to 2h for historical rounds.
  CHOSEN THRESHOLD (stated, per the build brief): **3600s** for
  these majors (ETH/USD, USDC/USD), exposed as
  :data:`STALE_AFTER_S` and returned in every response as
  ``stale_after_s`` so buyers can judge for themselves.
- A round whose ``updated_at`` is older than the threshold is still
  served (it is real data) but flagged ``stale: true`` with its
  ``age_s`` — never dressed as live.
- A round with ``answered_in_round < round_id`` is INCOMPLETE
  (superseded): flagged ``incomplete: true`` and ``stale: true``,
  never served as fresh. (The production eth_call reader drops such
  rounds entirely and returns ``None`` -> 503 here; the flagging
  path is what a round that reaches this layer receives.)
- ``answer <= 0`` or ``updated_at <= 0`` is not data at all: 502.
  A feed read failure / no round: 503. Fail closed, never a guess.

Value representation: ``price_fp`` is the round answer normalized
to the oracle's integer fixed-point scale (``PRICE_FP = 10**8``,
the exact conversion ``ChainlinkFeedAdapter`` performs:
``answer * PRICE_FP // 10**decimals``), and ``price`` is its exact
decimal string rendering — no float math anywhere on the value.

Statelessness: the module keeps NO state between calls. Every paid
call re-reads the feed seam; two sequential paid calls are two
independent reads (and two independent payments upstream).

Payment is NOT handled here: the read sits behind the standard x402
flow (bound challenge -> verify with require_bound_challenge=True ->
single-use access token consumed by ``access_granted`` in the
billing blueprint). Malformed payloads are rejected with 400 AFTER
payment, per the platform's token-consumption pattern (same as the
sibling paid resources).
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

from sincor2.defi.price_oracle import (
    BASE_MAINNET_FEEDS,
    PRICE_FP,
    ChainlinkRound,
)

RESOURCE_ID = "market-data-read"

#: Pairs this resource can serve — exactly the repo-pinned feed map.
#: Derived from the real config, never hand-listed, so a future pin
#: (or unpin) changes the served set and the 400 error body with it.
SUPPORTED_PAIRS: Tuple[str, ...] = tuple(sorted(BASE_MAINNET_FEEDS))

#: Staleness threshold in seconds (see module docstring for why this
#: value was chosen: no per-feed heartbeat exists in the repo's code;
#: 3600s for majors, stated here and echoed in every response).
STALE_AFTER_S = 3600

_ALLOWED_TOP_KEYS = {"pair", "mode"}
_MAX_PAIR_LEN = 32


# ---------------------------------------------------------------------------
# Feed seam (tests stub this with fixture ChainlinkRounds — no network)
# ---------------------------------------------------------------------------

def load_latest_round(pair: str) -> Optional[ChainlinkRound]:
    """Production loader: latest Chainlink round for ``pair``.

    Builds the repo's read-only eth_call reader over the pinned
    BASE_MAINNET_FEEDS map and calls its ``read_round(asset)`` seam.
    Returns ``None`` when the reader has no round (unpinned pair,
    dropped stale/invalid round, transport failure — the reader
    fails closed and records the reason in its stats).
    """
    from sincor2.contract_net import resolve_base_rpc
    from sincor2.defi.price_oracle import make_eth_call_reader

    reader = make_eth_call_reader(resolve_base_rpc(), BASE_MAINNET_FEEDS)
    return reader(pair)


# ---------------------------------------------------------------------------
# Payload validation (strict, fail closed — runs AFTER payment)
# ---------------------------------------------------------------------------

def _err(code: str, **extra: Any) -> Tuple[int, Dict[str, Any]]:
    body: Dict[str, Any] = {"ok": False, "error": code,
                            "resource": RESOURCE_ID}
    body.update(extra)
    return 400, body


def validate_payload(
    payload: Any,
) -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[int, Dict[str, Any]]]]:
    """Validate a buyer payload. Returns ``(parsed, None)`` or
    ``(None, (status, body))``. ``parsed`` has ``pair`` and ``mode``."""
    if not isinstance(payload, dict):
        return None, _err("payload_must_be_object")
    if set(payload) - _ALLOWED_TOP_KEYS:
        return None, _err("unknown_payload_fields")

    pair = payload.get("pair")
    if not isinstance(pair, str) or not pair.strip():
        return None, _err("pair_required")
    pair = pair.strip().upper()
    if len(pair) > _MAX_PAIR_LEN:
        return None, _err("pair_too_long")
    if pair not in SUPPORTED_PAIRS:
        return None, _err("unknown_pair",
                          supported_pairs=list(SUPPORTED_PAIRS))

    mode = payload.get("mode", "price")
    if not isinstance(mode, str) or not mode.strip():
        return None, _err("mode_invalid")
    mode = mode.strip().lower()
    if mode != "price":
        # OHLCV/trending honestly not available: no OHLCV source
        # exists in the repo (see module docstring). Never invent.
        return None, _err("mode_not_available",
                          available_modes=["price"])

    return {"pair": pair, "mode": mode}, None


# ---------------------------------------------------------------------------
# Serve path (called by the billing blueprint ONLY after the x402
# access token has been consumed)
# ---------------------------------------------------------------------------

def _price_str(price_fp: int) -> str:
    """Exact decimal rendering of a PRICE_FP-scaled integer."""
    whole, frac = divmod(price_fp, PRICE_FP)
    if frac == 0:
        return str(whole)
    return f"{whole}.{frac:08d}".rstrip("0")


def serve_market_data_read(
    payload: Optional[Dict[str, Any]],
    *,
    read_round: Optional[Callable[[str], Optional[ChainlinkRound]]] = None,
    now: Optional[float] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Run one paid market-data price read.

    ``read_round`` is the feed seam (defaults to
    :func:`load_latest_round`); tests inject fixture rounds through
    it. ``now`` is injectable so staleness is deterministic in tests.

    Returns the round-matched value with provenance on 200 (flagged
    ``stale`` / ``incomplete`` when the round says so), 400 on a
    malformed payload, 502 on a non-positive answer, 503 when the
    feed yields no round.
    """
    parsed, error = validate_payload(payload)
    if error is not None:
        return error
    assert parsed is not None
    pair = parsed["pair"]
    now = time.time() if now is None else now
    fetch = read_round if read_round is not None else load_latest_round

    try:
        rnd = fetch(pair)
    except Exception:
        return 503, {"ok": False, "error": "feed_unavailable",
                     "resource": RESOURCE_ID, "pair": pair}
    if rnd is None:
        return 503, {"ok": False, "error": "feed_unavailable",
                     "resource": RESOURCE_ID, "pair": pair}
    if rnd.answer <= 0 or rnd.updated_at <= 0:
        return 502, {"ok": False, "error": "invalid_round",
                     "resource": RESOURCE_ID, "pair": pair}

    # The exact conversion ChainlinkFeedAdapter performs.
    price_fp = rnd.answer * PRICE_FP // (10 ** rnd.decimals)
    if price_fp <= 0:
        return 502, {"ok": False, "error": "invalid_round",
                     "resource": RESOURCE_ID, "pair": pair}

    age_s = max(0, int(now - rnd.updated_at))
    incomplete = rnd.answered_in_round < rnd.round_id
    stale = incomplete or (now - rnd.updated_at) > STALE_AFTER_S

    return 200, {
        "ok": True,
        "resource": RESOURCE_ID,
        "pair": pair,
        "mode": "price",
        "source": "chainlink",
        "feed_address": BASE_MAINNET_FEEDS[pair],
        "price": _price_str(price_fp),
        "price_fp": price_fp,
        "price_fp_scale": PRICE_FP,
        "answer": rnd.answer,
        "decimals": rnd.decimals,
        "round_id": rnd.round_id,
        "answered_in_round": rnd.answered_in_round,
        "started_at": rnd.started_at,
        "updated_at": rnd.updated_at,
        "age_s": age_s,
        "stale": stale,
        "incomplete": incomplete,
        "stale_after_s": STALE_AFTER_S,
    }
