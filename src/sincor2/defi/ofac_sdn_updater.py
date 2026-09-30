"""OFAC SDN snapshot updater.

Fetches the official SDN XML, verifies it, and atomically replaces the
vendored lean snapshot (:mod:`sincor2.defi.ofac_sdn`). Idempotent: when the
publication date is unchanged, the snapshot is left untouched.

Verification (all must pass before anything is written):
  1. HTTP 200 with a non-trivial body (>= MIN_BYTES).
  2. The body parses as an SDN export (correct root, publication info,
     non-zero entries) via :func:`parse_sdn_xml`.
  3. Record count sanity (>= MIN_RECORDS).

Atomicity: the new snapshot is written to a temp file, fsynced, and moved
into place with :func:`os.replace`. A crash or failed verification can
never leave a half-written list.

Cadence: run at least daily — the oracle's freshness guards alert at 24h
and fail closed beyond 48h (``LIST_FRESHNESS_*_SECONDS``), so a daily run
keeps the list meaningfully fresh. See docs/ops/OFAC_SDN_UPDATER.md.

Usage:
    python -m sincor2.defi.ofac_sdn_updater [--out PATH] [--url URL]
"""

import argparse
import datetime
import json
import sys
import urllib.request
from typing import Any, Dict

from sincor2.defi.ofac_sdn import (
    SDN_SOURCE_URL,
    load_snapshot,
    parse_sdn_xml,
    snapshot_path,
    write_snapshot_atomic,
)

MIN_BYTES = 1_000_000      # SDN XML is ~29MB; anything far smaller is suspect
MIN_RECORDS = 10_000       # SDN has ~19k entries; far fewer means truncation
FETCH_TIMEOUT_SECONDS = 180


class UpdateError(Exception):
    """The snapshot was NOT modified."""


def fetch_sdn_xml(url: str = SDN_SOURCE_URL,
                  timeout: int = FETCH_TIMEOUT_SECONDS) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "SINCOR-P20-OFAC-Updater/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise UpdateError(f"OFAC download HTTP {resp.status}")
            return resp.read()
    except UpdateError:
        raise
    except Exception as e:  # network/DNS/TLS — snapshot untouched
        raise UpdateError(f"OFAC download failed: {e}") from e


def verify_and_parse(raw: bytes, fetched_at: str) -> Dict[str, Any]:
    """Size sanity + structural parse. Raises UpdateError on any defect."""
    if len(raw) < MIN_BYTES:
        raise UpdateError(
            f"OFAC payload too small ({len(raw)} bytes < {MIN_BYTES}); "
            "refusing to replace the snapshot")
    try:
        snapshot = parse_sdn_xml(raw, fetched_at)
    except ValueError as e:
        raise UpdateError(f"OFAC payload failed parse verification: {e}") from e
    if snapshot["record_count"] < MIN_RECORDS:
        raise UpdateError(
            f"OFAC record count too low ({snapshot['record_count']} < "
            f"{MIN_RECORDS}); refusing to replace the snapshot")
    return snapshot


def update_snapshot(out_path: str = None,
                    url: str = SDN_SOURCE_URL,
                    now: str = None) -> Dict[str, Any]:
    """Fetch, verify, and atomically install the latest SDN snapshot.

    Returns a status dict. Raises UpdateError (snapshot untouched) on any
    failure. Idempotent: unchanged publication date => no rewrite.
    """
    path = out_path or snapshot_path()
    fetched_at = now or datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    raw = fetch_sdn_xml(url)
    snapshot = verify_and_parse(raw, fetched_at)

    try:
        current = load_snapshot(path)
    except (FileNotFoundError, ValueError):
        current = None
    if current is not None and (current.get("publication_date")
                                == snapshot["publication_date"]):
        return {"status": "unchanged",
                "publication_date": snapshot["publication_date"],
                "path": path}
    write_snapshot_atomic(snapshot, path)
    return {"status": "updated",
            "publication_date": snapshot["publication_date"],
            "previous_publication_date": (current or {}).get("publication_date"),
            "entry_count": snapshot["entry_count"],
            "path": path}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None,
                        help="snapshot path (default: vendored data dir)")
    parser.add_argument("--url", default=SDN_SOURCE_URL,
                        help="OFAC SDN XML URL")
    args = parser.parse_args(argv)
    try:
        result = update_snapshot(out_path=args.out, url=args.url)
    except UpdateError as e:
        print(json.dumps({"status": "failed", "error": str(e)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
