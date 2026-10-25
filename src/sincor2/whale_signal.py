"""Polymarket Whale Signal — single-read x402 paid resource (G3).

One paid read returns the CURRENT fresh entries from the tracked
Polyclaw whale registry: wallets that qualified under
``whale_flow.score_wallet``'s resolved-market scoring (``>=20``
resolved trades and ``>=+$100`` realized P&L at scoring time), together
with each wallet's track record. Priced as the
``polymarket-whale-signal`` resource in ``config/x402_pricing.yaml``
($0.02 USDC on Base).

Honesty contract (Mission 1A, 2026-10-22 — mandatory):

- Mission 1A tested *trading* this signal on resolved historical
  episodes and returned a NO-GO verdict: the signal fired on exactly
  1 of 60 episodes. This resource sells the *data read* — what tracked
  wallets just traded, with provenance — and makes NO claim that
  following the signals is, or will be, profitable.
- Every served signal carries full provenance: whale wallet,
  market/question, side, the whale's trade size, and ``detected_at``.
- The wallet statistics served alongside each signal are the registry
  values exactly as scored by ``score_wallet`` over already-resolved
  markets under the fixed settlement math (a winning share pays 1,
  a losing share pays 0 — PR #515). Statistics are historical facts
  about that wallet; they are not a forecast, a rating of the signal,
  or a promise about any future trade. No derived "confidence" or
  expected-return figure is computed or served anywhere.
- Empty registry or no fresh entries is reported honestly as an
  empty signal list with a status — a signal is never fabricated.

Payment is NOT handled here: the serve path sits behind the standard
x402 flow (bound challenge -> verify with require_bound_challenge=True
-> single-use access token consumed by ``access_granted`` in the
billing blueprint). This module only does the data work served after
settlement.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

RESOURCE_ID = "polymarket-whale-signal"

_EVM_RE = re.compile(r"^0[xX][a-fA-F0-9]{40}$")

NOTICE = (
    "Historical statistics only: realized_pnl_usd and win_rate are "
    "this wallet's results on already-resolved Polymarket markets, "
    "computed with resolved-market settlement (a winning share pays "
    "1, a losing share pays 0). They describe that wallet's past "
    "trades. This read makes no claim about future results and no "
    "claim about following these signals."
)


def _default_window_min() -> int:
    try:
        return int(os.getenv("WHALE_SIGNAL_WINDOW_MIN", "60"))
    except ValueError:
        return 60


def load_tracked_registry() -> Dict[str, Dict[str, Any]]:
    """Load the tracked-whale registry (wallet -> scored stats).

    The registry path is read from ``WHALE_REGISTRY_PATH`` at call
    time (falling back to ``whale_flow``'s configured path). Seam for
    tests to inject a fixture registry. A missing/unreadable registry
    is an empty registry — never an invented one.
    """
    raw = os.getenv("WHALE_REGISTRY_PATH", "")
    if raw:
        path = Path(raw)
    else:
        from sincor2 import whale_flow
        path = Path(whale_flow.WHALE_REGISTRY_PATH)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def _fresh_entries(window_min: int):
    """Current fresh whale entries. Seam over ``whale_flow`` so tests
    can stub the trade feed hermetically (no live network)."""
    from sincor2 import whale_flow
    return whale_flow.fresh_entries(window_min=window_min)


def _iso(epoch: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(
            float(epoch), datetime.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _wallet_stats(wallet: str, registry: Dict[str, Dict[str, Any]],
                  signal: Any = None) -> Dict[str, Any]:
    """Resolved-market stats for one wallet, exactly as scored.

    Registry values win (they are ``score_wallet``'s output persisted
    at qualification time). When a signal arrives for a wallet with no
    registry entry, fall back to the signal's own scored fields and
    mark the resolved counts as unknown (``None``) rather than
    inventing them.
    """
    meta = None
    for key, value in registry.items():
        if key.lower() == wallet.lower():
            meta = value
            break
    if meta is not None:
        return {
            "realized_pnl_usd": meta.get("realized_pnl_usd"),
            "win_rate": meta.get("win_rate"),
            "resolved_trades": meta.get("resolved_trades"),
            "resolved_markets": meta.get("resolved_markets"),
            "scored_at": meta.get("scored_at"),
        }
    return {
        "realized_pnl_usd": getattr(signal, "whale_pnl_usd", None),
        "win_rate": getattr(signal, "whale_win_rate", None),
        "resolved_trades": None,
        "resolved_markets": None,
        "scored_at": None,
    }


def track_record(registry: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The tracked wallets' track record: scored resolved-market stats
    for every registry wallet, best realized P&L first. This is the
    free-preview element of the product shape — the same historical
    stats are embedded in the paid response so a buyer can see exactly
    whose flow the signals come from."""
    rows = []
    for wallet in registry:
        rows.append({"wallet": wallet, **_wallet_stats(wallet, registry)})
    rows.sort(key=lambda r: (r["realized_pnl_usd"] is not None,
                             r["realized_pnl_usd"] or 0), reverse=True)
    return rows


def _serialize_signal(signal: Any,
                      registry: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    detected = float(getattr(signal, "detected_at", 0) or 0)
    return {
        "wallet": signal.wallet,
        "market_id": signal.market_id,
        "question": signal.question,
        "side": signal.side,
        "token_id": signal.token_id,
        "whale_trade_size_usdc": signal.whale_trade_usdc,
        "detected_at": detected,
        "detected_at_iso": _iso(detected),
        "whale_stats": _wallet_stats(signal.wallet, registry, signal),
    }


def collect_whale_signals(
    payload: Optional[Dict[str, Any]] = None,
) -> tuple[int, Dict[str, Any]]:
    """Build the whale-signal read. Returns ``(status_code, body)``.

    Optional payload filters: ``wallet`` (one tracked wallet),
    ``market_id``, ``window_minutes`` (freshness window, 1..1440).
    """
    payload = payload or {}
    wallet_filter = str(payload.get("wallet") or "").strip()
    if wallet_filter and not _EVM_RE.match(wallet_filter):
        return 400, {"ok": False, "error": "invalid_wallet"}
    market_filter = str(payload.get("market_id") or "").strip()
    window_raw = payload.get("window_minutes")
    if window_raw is None or window_raw == "":
        window_min = _default_window_min()
    else:
        try:
            window_min = int(window_raw)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "invalid_window"}
        if not 1 <= window_min <= 1440:
            return 400, {"ok": False, "error": "invalid_window"}

    registry = load_tracked_registry()
    record = track_record(registry)
    base: Dict[str, Any] = {
        "ok": True,
        "resource": RESOURCE_ID,
        "tracked_wallet_count": len(registry),
        "track_record": record,
        "window_minutes": window_min,
        "data_delay_note": (
            f"Signals are tracked-whale entries detected within the "
            f"last {window_min} minutes; detected_at is when the "
            f"tracker observed the entry."),
        "notice": NOTICE,
    }
    if not registry:
        return 200, {**base, "status": "registry_empty", "signals": []}
    try:
        fresh = _fresh_entries(window_min)
    except Exception:
        return 503, {"ok": False, "error": "signal_feed_unavailable",
                     "resource": RESOURCE_ID}
    signals = []
    for sig in fresh or []:
        if wallet_filter and sig.wallet.lower() != wallet_filter.lower():
            continue
        if market_filter and str(sig.market_id) != market_filter:
            continue
        signals.append(_serialize_signal(sig, registry))
    if not signals:
        return 200, {**base, "status": "no_fresh_signals", "signals": []}
    return 200, {**base, "status": "signals_available",
                 "signals": signals}


def serve_whale_signal(
    payload: Optional[Dict[str, Any]],
) -> tuple[int, Dict[str, Any]]:
    """Serve-path handler for the paid resource (called by the billing
    blueprint ONLY after the x402 access token has been consumed)."""
    return collect_whale_signals(payload or {})
