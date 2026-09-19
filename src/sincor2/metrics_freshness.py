"""Fail if public metrics are older than 24h."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sincor2.onchain.live_snapshot import fetch_live_onchain, load_canon

MAX_AGE_S = 24 * 3600


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def freshness_report() -> dict[str, Any]:
    canon = load_canon()
    snap = fetch_live_onchain()
    now = datetime.now(timezone.utc)
    verified = _parse_ts(snap.get("verified_at")) or _parse_ts(canon.get("verified_at"))
    age_s = None if verified is None else max(0, int((now - verified).total_seconds()))
    stale = verified is None or age_s > MAX_AGE_S
    sinc = (snap.get("sinc") or {})
    axiom = (snap.get("axiom") or {})
    return {
        "ok": not stale and int(sinc.get("holders") or 0) >= 1000,
        "stale": stale,
        "max_age_s": MAX_AGE_S,
        "age_s": age_s,
        "verified_at": snap.get("verified_at") or canon.get("verified_at"),
        "lock_version": canon.get("lock_version"),
        "sinc_holders": sinc.get("holders"),
        "axiom_holders": axiom.get("holders"),
        "axiom_source_verified_basescan": bool((canon.get("axiom") or {}).get("source_verified_basescan")),
    }
