"""OFAC snapshot freshness badge — provenance-only public readout.

  GET /api/compliance/ofac-status

Returns ONLY the provenance of the active OFAC SDN snapshot (publication
date, fetched_at, entry/record counts, age, fresh/stale) as computed by
``sanctions_screen.load_screen_snapshot()`` + ``_provenance()`` — the
same snapshot the paid wallet-sanctions-screen resource screens against.
No snapshot entries, no screening verdicts, no paid data: this is the
freshness signal an operator (or a buyer) can check *before* the paid
path 503s on a stale snapshot, which is how the 2026-10-27 freshness
cliff presented.

Honesty contract (same as sanctions_screen): freshness is never faked.
The endpoint fails soft — HTTP 200 with ``status: "unknown"`` — when the
snapshot cannot be loaded, and an unparseable ``fetched_at`` ages the
snapshot to epoch 0 (maximally stale) via ``_snapshot_epoch``. Fresh
means age <= the P20 48h fail-closed window
(``LIST_FRESHNESS_FAIL_SECONDS``), the exact window the paid serve path
enforces.

Purely additive: one read-only route + one payload builder. It never
touches screening semantics, money paths, or auth.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from sincor2 import sanctions_screen
from sincor2.defi.compliance_automation import LIST_FRESHNESS_FAIL_SECONDS


def ofac_status(now: Optional[float] = None) -> Dict[str, Any]:
    """Provenance + freshness of the active OFAC snapshot. Never raises."""
    now = time.time() if now is None else now
    try:
        snapshot = sanctions_screen.load_screen_snapshot()
    except Exception:
        snapshot = None
    if not isinstance(snapshot, dict) or not snapshot:
        return {
            "ok": False,
            "status": "unknown",
            "fresh": False,
            "stale": True,
            "source": "",
            "source_url": "",
            "snapshot_publication_date": "",
            "snapshot_fetched_at": "",
            "snapshot_entry_count": None,
            "snapshot_record_count": None,
            "snapshot_age_seconds": None,
            "freshness_window_seconds": LIST_FRESHNESS_FAIL_SECONDS,
            "detail": (
                "OFAC snapshot unavailable; freshness unknown — never "
                "reported fresh without a loadable snapshot"
            ),
        }
    provenance = sanctions_screen._provenance(snapshot)
    age = max(0, int(now - sanctions_screen._snapshot_epoch(
        snapshot.get("fetched_at", ""))))
    fresh = age <= LIST_FRESHNESS_FAIL_SECONDS
    return {
        "ok": True,
        **provenance,
        "snapshot_age_seconds": age,
        "freshness_window_seconds": LIST_FRESHNESS_FAIL_SECONDS,
        "fresh": fresh,
        "stale": not fresh,
        "status": "fresh" if fresh else "stale",
    }


def attach_ofac_status(app: Any) -> None:
    """Register the read-only freshness badge route on a Flask app."""

    @app.get("/api/compliance/ofac-status")
    def ofac_status_badge():  # type: ignore[no-redef]
        from flask import jsonify
        return jsonify(ofac_status()), 200
