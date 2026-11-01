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
- Stale snapshot (age > 48h): the screening function reports EVERY
  address as ``unknown`` with ``stale: true`` — never ``clean``, never
  ``flagged``. A stale list cannot prove either, and this module will
  not fake-fresh. This mirrors the AML adapter, which returns
  Verdict.STALE before it ever looks at the sanctions set.
- Every response carries the snapshot provenance (publication date,
  fetched_at, entry count) so the buyer can judge the evidence.

Payment is NOT handled here: the serve path sits behind the standard
x402 flow (bound challenge -> verify with require_bound_challenge=True
-> single-use access token). Since P1-3a the paid serve REFUSES a
stale snapshot with ``503 snapshot_stale`` instead of charging $0.005
for an unscreened ``unknown`` — and since P1-1 the billing blueprint
consumes the token only on a 200, so the 503 never burns it. The
snapshot is refreshed by ``python -m sincor2.refresh_ofac_sdn``
(boot/daily): a refreshed copy under ``SINCOR_DATA_DIR`` wins over
the vendored bundled file when present.

Escalation tap (additive, opt-in): when ``SINCOR_ESCALATION_SINK``
names an EscalationEnvelope store directory, a non-clean verdict
(match or stale-unknown) is also mirrored into the Phase 1
escalation pipeline (``_emit_escalation_envelope``). With the flag
unset the screen behaves exactly as before, and a broken sink can
never change or block a verdict.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from sincor2.defi.compliance_automation import (
    LIST_FRESHNESS_ALERT_SECONDS,
    LIST_FRESHNESS_FAIL_SECONDS,
)
from sincor2.defi.ofac_sdn import SNAPSHOT_FILENAME, load_snapshot

RESOURCE_ID = "wallet-sanctions-screen"

_log = logging.getLogger(__name__)

#: Env flag for the Phase 1 escalation tap (see
#: ``_emit_escalation_envelope``). The value is the directory holding
#: the EscalationEnvelope store. Unset/empty = tap fully off.
ESCALATION_SINK_ENV = "SINCOR_ESCALATION_SINK"

_EVM_RE = re.compile(r"^0[xX][a-fA-F0-9]{40}$")

# address (lowercased) -> matched snapshot entry metadata. Built lazily
# per snapshot object id so tests can inject synthetic snapshots without
# cache cross-talk.
_INDEX_CACHE: Dict[int, Dict[str, Dict[str, Any]]] = {}


def load_screen_snapshot() -> Dict[str, Any]:
    """Load the active OFAC snapshot. Seam for tests to inject a
    snapshot with a controlled ``fetched_at`` (freshness fixtures).

    Read order: a refreshed snapshot under ``SINCOR_DATA_DIR``
    (written by ``python -m sincor2.refresh_ofac_sdn``) wins when
    present; otherwise the vendored bundled file is the fallback.
    """
    data_dir = os.environ.get("SINCOR_DATA_DIR", "")
    if data_dir:
        candidate = Path(data_dir) / SNAPSHOT_FILENAME
        if candidate.exists():
            return load_snapshot(str(candidate))
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


def _emit_escalation_envelope(result: Dict[str, Any]) -> None:
    """Additive reporting tap into the Phase 1 escalation pipeline.

    Mirrors a non-clean verdict — an OFAC match (``flagged``) or a
    stale-snapshot ``unknown`` — into an EscalationEnvelope store so
    the verdict lands in the unified [Approve | Override | Escalate]
    operator queue (``sincor2.escalation``). Strictly opt-in and
    strictly best-effort:

    - Active only when ``SINCOR_ESCALATION_SINK`` names an envelope
      store directory. Unset/empty = the screen behaves exactly as it
      did before this tap existed; the escalation layer is not even
      imported.
    - Any failure (unwritable sink, corrupt store, core-layer error)
      is logged and swallowed. Emission NEVER changes the verdict,
      never raises into the caller, and never blocks the screen: the
      fail-closed verdicts are the compliance gate, and this is a
      reporting tap on the gate, not part of it.
    - Risk honesty: the OFAC matcher is exact set membership — it
      produces no confidence score, so the violation confidence is
      None (never fabricated). The screen prices no value transfer,
      so exposure is None: the screened action's parameters carry no
      amount to report.
    """
    verdict = result.get("verdict")
    if verdict == "flagged":
        match = result.get("match") or {}
        rule = "ofac-match"
        recommended_action = "deny"
        severity = "high"
        detail = (
            "address on OFAC SDN list "
            f"(uid {match.get('uid', '')}; "
            f"programs {', '.join(match.get('programs') or [])})"
        )
        summary = (
            f"Sanctions screen: OFAC SDN match for {result.get('address')}"
        )
    elif verdict == "unknown" and result.get("stale"):
        rule = "snapshot-stale"
        recommended_action = "escalate"
        severity = "medium"
        detail = str(result.get("detail") or "OFAC snapshot stale")
        summary = (
            "Sanctions screen: snapshot stale, verdict unknown for "
            f"{result.get('address')}"
        )
    else:
        return
    sink = os.environ.get(ESCALATION_SINK_ENV, "").strip()
    if not sink:
        return
    try:
        from sincor2.escalation import EscalationStore

        store = EscalationStore(sink)
        store.create_package(
            source_surface="sanctions-screen",
            actor="sincor2.sanctions_screen",
            source_agent_id="sincor2.sanctions_screen",
            action_category="legal_compliance",
            summary=summary,
            severity=severity,
            context={
                "rule_hit": {
                    "policy_id": "sanctions-screen",
                    "rule": rule,
                    "detail": detail,
                }
            },
            violations=[
                {
                    "policy_id": "sanctions-screen",
                    "detail": detail,
                    "confidence": None,
                }
            ],
            exposure=None,
            recommended_action=recommended_action,
            input_parameters={
                "address": result.get("address"),
                "resource": RESOURCE_ID,
            },
            dry_run_state={
                "verdict": verdict,
                "screened": result.get("screened"),
                "stale": result.get("stale"),
                "match": result.get("match"),
                "snapshot_age_seconds": result.get("snapshot_age_seconds"),
                "snapshot_fetched_at": result.get("snapshot_fetched_at"),
                "snapshot_publication_date": result.get(
                    "snapshot_publication_date"),
            },
        )
    except Exception:
        _log.warning(
            "sanctions-screen escalation emission failed (verdict %s); "
            "verdict unaffected", verdict, exc_info=True)


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
        result = {
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
        _emit_escalation_envelope(result)
        return result
    match = _address_index(snapshot).get(addr.lower())
    freshness_note = ""
    if age > LIST_FRESHNESS_ALERT_SECONDS:
        freshness_note = (
            f"freshness alert: snapshot {age / 3600:.1f}h old "
            "(48h fail-closed window)"
        )
    if match is not None:
        result = {
            **base,
            "verdict": "flagged",
            "status": "flagged",
            "screened": True,
            "stale": False,
            "match": match,
            "detail": "address on OFAC SDN list" + (
                f"; {freshness_note}" if freshness_note else ""),
        }
        _emit_escalation_envelope(result)
        return result
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
    blueprint after the x402 access token has been PEEK-verified; the
    token is consumed only when this returns a 200 — P1-1).

    The address to screen arrives in the request payload as ``address``
    (aliases: ``wallet_address``, ``wallet``). A missing/invalid address
    is a 400 — the screening itself never guesses an address. A stale
    snapshot is a 503 ``snapshot_stale`` (P1-3a): the buyer is never
    charged for an unscreened ``unknown``.
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
    if result.get("stale"):
        # Fail closed BEFORE charging: a stale list proves nothing, so
        # this is an availability refusal, not a screening verdict.
        return 503, {**result, "ok": False, "error": "snapshot_stale"}
    return 200, result
