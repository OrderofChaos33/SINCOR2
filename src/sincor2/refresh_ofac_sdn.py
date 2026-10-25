"""OFAC SDN snapshot refresh — boot/scheduled entry point (P1-3a).

The ``wallet-sanctions-screen`` paid resource fails closed with
``503 snapshot_stale`` once its snapshot ages past the P20 48h
freshness window (``LIST_FRESHNESS_FAIL_SECONDS``), so something has
to actually refresh the snapshot. This CLI is that something — run
it at boot and at least daily:

    python -m sincor2.refresh_ofac_sdn [--out PATH] [--url URL]

It reuses the verified machinery in
:mod:`sincor2.defi.ofac_sdn_updater` (download -> size/parse/record
sanity -> atomic replace); nothing is written unless the download
verifies. The snapshot lands at:

- ``$SINCOR_DATA_DIR/p20_ofac_sdn.json`` when ``SINCOR_DATA_DIR`` is
  set (the sanctions screen prefers that copy when present), else
- the vendored bundled file (:func:`ofac_sdn.snapshot_path`), which
  is also the read fallback on first boot, before any refresh.

``fetched_at`` is stamped with the true time of the verified
re-download — never back- or forward-dated. When OFAC has not
published a new list since the installed snapshot, the entries are
identical and the status is ``refreshed``: the verified re-download
still truthfully advances ``fetched_at``, which is exactly what the
freshness guards measure. Any failure raises :class:`UpdateError`
(installed snapshot untouched) and the CLI exits 1.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from sincor2.defi.ofac_sdn import (
    SNAPSHOT_FILENAME,
    load_snapshot,
    snapshot_path,
    write_snapshot_atomic,
)
from sincor2.defi.ofac_sdn_updater import (
    SDN_SOURCE_URL,
    UpdateError,
    fetch_sdn_xml,
    verify_and_parse,
)

__all__ = ["UpdateError", "active_snapshot_path", "refresh", "main"]


def active_snapshot_path() -> str:
    """Where the refreshed snapshot lives (see module docstring)."""
    data_dir = os.environ.get("SINCOR_DATA_DIR", "")
    if data_dir:
        return str(Path(data_dir) / SNAPSHOT_FILENAME)
    return snapshot_path()


def refresh(out_path: Optional[str] = None,
            url: str = SDN_SOURCE_URL) -> Dict[str, Any]:
    """Fetch, verify, and atomically install the latest SDN snapshot.

    Returns a status dict (``updated`` when the publication changed
    or no snapshot was installed, ``refreshed`` when only
    ``fetched_at`` advanced). Raises :class:`UpdateError` — snapshot
    untouched — on any download/verification failure.
    """
    path = out_path or active_snapshot_path()
    fetched_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    raw = fetch_sdn_xml(url)
    snapshot = verify_and_parse(raw, fetched_at)

    try:
        current = load_snapshot(path)
    except (OSError, ValueError):
        current = None
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_snapshot_atomic(snapshot, path)
    if current is not None and (
            current.get("publication_date")
            == snapshot["publication_date"]):
        status = "refreshed"
    else:
        status = "updated"
    return {"status": status,
            "publication_date": snapshot["publication_date"],
            "previous_publication_date": (current or {}).get(
                "publication_date"),
            "entry_count": snapshot["entry_count"],
            "fetched_at": fetched_at,
            "path": path}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None,
                        help="snapshot path (default: $SINCOR_DATA_DIR "
                             "copy when set, else the bundled file)")
    parser.add_argument("--url", default=SDN_SOURCE_URL,
                        help="OFAC SDN XML URL")
    args = parser.parse_args(argv)
    try:
        result = refresh(out_path=args.out, url=args.url)
    except UpdateError as e:
        print(json.dumps({"status": "failed", "error": str(e)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
