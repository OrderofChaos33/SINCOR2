"""Wallet Sanctions Screen — single-read x402 paid resource (G1).

One EVM wallet address in, one sanctions verdict out, screened against
the vendored OFAC SDN snapshot that P20 already uses
(``src/sincor2/defi/data/p20_ofac_sdn.json`` — 19,471 entries,
publication 10/21/2026). Priced as the ``wallet-sanctions-screen``
resource in ``config/x402_pricing.yaml`` ($0.005 USDC on Base).

Honesty contract (standing observability rule — feeds report unknown,
never healthy):

- Fresh snapshot (age <= 48h, the P20 ``LIST_FRESHNESS_FAIL_SECONDS``
  window): a listed address returns ``flagged`` with the real snapshot
  entry metadata; an unlisted address returns ``clean``.
- Stale snapshot (age > 48h): EVERY address returns ``unknown`` with
  ``stale: true`` — never ``clean``, never ``flagged``. A stale list
  cannot prove either, and this module will not fake-fresh. This mirrors
  the AML adapter, which returns Verdict.STALE before it ever looks at
  the sanctions set.
- Every response carries the snapshot provenance (publication date,
  fetched_at, entry count) so the buyer can judge the evidence.

Payment is NOT handled here: the serve path sits behind the standard
x402 flow (bound challenge -> verify with require_bound_challenge=True
-> single-use access token consumed by ``access_granted`` in the billing
blueprint). This module only does the screening work that is served
after settlement.
"""

from __future__ import annotations

import datetime
import re
import time
from typing import Any, Dict, Optional

from sincor2.defi.compliance_automation import (
    LIST_FRESHNESS_ALERT_SECONDS,
    LIST_FRESHNESS_FAIL_SECONDS,
)
from sincor2.defi.ofac_sdn import load_snapshot

RESOURCE_ID = "wallet-sanctions-screen"

_EVM_RE = re.compile(r"^0[xX][a-fA-F0-9]{40}$")

# address (lowercased) -> matched snapshot entry metadata. Built lazily
# per snapshot object id so tests can inject synthetic snapshots without
# cache cross-talk.
_INDEX_CACHE: Dict[int, Dict[str, Dict[str, Any]]] = {}


def load_screen_snapshot() -> Dict[str, Any]:
    """Load the vendored OFAC snapshot. Seam for tests to inject a
    snapshot with a controlled ``fetched_at`` (freshness fixtures)."""
    return load_snapshot()


def _snapshot_epoch(fetched_at: str) -> float:
    try:
        return datetime.datetime.fromisoformat(
            str(fetched_at).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        # Unparseable provenance = cannot prove freshness = treat as
        # epoch 0, i.e. maximally stale (fail closed, never fake-fresh).
        return 0.0


def _address_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    key = id(snapshot)
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    index: Dict[str, Dict[str, Any]] = {}
    for entry in snapshot.get("entries", []):
        for dca in entry.get("digital_currency_addresses", []):
            addr = str(dca.get("address", "")).strip()
            if not addr.startswith("0x"):
                continue  # this resource screens EVM wallets only
            index.setdefault(addr.lower(), {
                "uid": entry.get("uid", ""),
                "name": entry.get("name", ""),
                "type": entry.get("type", ""),
                "programs": list(entry.get("programs", [])),
                "currency": dca.get("currency", ""),
                "address": addr,
            })
    _INDEX_CACHE[key] = index
    return index


def _provenance(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": snapshot.get("source", ""),
        "source_url": snapshot.get("source_url", ""),
        "snapshot_publication_date": snapshot.get("publication_date", ""),
        "snapshot_fetched_at": snapshot.get("fetched_at", ""),
        "snapshot_entry_count": snapshot.get(
            "entry_count", len(snapshot.get("entries", []))),
        "snapshot_record_count": snapshot.get("record_count"),
    }


def screen_wallet(
    address: Any,
    *,
    snapshot: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Screen one EVM wallet address against the OFAC snapshot.

    Returns a verdict dict. ``ok`` is False only for a malformed request
    (invalid address / unloadable snapshot) — a screening outcome,
    including ``unknown`` on a stale snapshot, is ``ok: True`` with the
    verdict in ``verdict``/``status``.
    """
    if not isinstance(address, str) or not _EVM_RE.match(address.strip()):
        return {"ok": False, "error": "invalid_address"}
    addr = address.strip()
    if snapshot is None:
        try:
            snapshot = load_screen_snapshot()
        except Exception:
            return {"ok": False, "error": "snapshot_unavailable"}
    now = time.time() if now is None else now
    age = now - _snapshot_epoch(snapshot.get("fetched_at", ""))
    base: Dict[str, Any] = {
        "ok": True,
        "resource": RESOURCE_ID,
        "address": addr,
        **_provenance(snapshot),
        "snapshot_age_seconds": max(0, int(age)),
    }
    if age > LIST_FRESHNESS_FAIL_SECONDS:
        # Fail-closed honesty: a stale list proves nothing either way.
        return {
            **base,
            "verdict": "unknown",
            "status": "unknown",
            "screened": False,
            "stale": True,
            "match": None,
            "detail": (
                "OFAC snapshot is stale (>48h old); refusing to report "
                "clean or flagged from stale data"
            ),
        }
    match = _address_index(snapshot).get(addr.lower())
    freshness_note = ""
    if age > LIST_FRESHNESS_ALERT_SECONDS:
        freshness_note = (
            f"freshness alert: snapshot {age / 3600:.1f}h old "
            "(48h fail-closed window)"
        )
    if match is not None:
        return {
            **base,
            "verdict": "flagged",
            "status": "flagged",
            "screened": True,
            "stale": False,
            "match": match,
            "detail": "address on OFAC SDN list" + (
                f"; {freshness_note}" if freshness_note else ""),
        }
    return {
        **base,
        "verdict": "clean",
        "status": "screened",
        "screened": True,
        "stale": False,
        "match": None,
        "detail": "address not on OFAC SDN list" + (
            f"; {freshness_note}" if freshness_note else ""),
    }


def serve_sanctions_screen(
    payload: Optional[Dict[str, Any]],
) -> tuple[int, Dict[str, Any]]:
    """Serve-path handler for the paid resource (called by the billing
    blueprint ONLY after the x402 access token has been consumed).

    The address to screen arrives in the request payload as ``address``
    (aliases: ``wallet_address``, ``wallet``). A missing/invalid address
    is a 400 — the screening itself never guesses an address.
    """
    payload = payload or {}
    address = (
        payload.get("address")
        or payload.get("wallet_address")
        or payload.get("wallet")
        or ""
    )
    result = screen_wallet(address)
    if not result.get("ok"):
        return 400, result
    return 200, result
