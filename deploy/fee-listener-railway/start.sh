#!/usr/bin/env bash
# Fee-listener start script for the Railway service.
#
# - Seeds the data dir from the bundled seed files on FIRST boot only
#   (cursor + ledger snapshot from the VM service). Re-scans are
#   idempotent by design (obligation ids derive from tx_hash + fee), so
#   a stale seed cursor costs a rescan, never a duplicate.
# - Logs only the RPC HOST, never the full URL (the URL carries the API
#   key; the old VM supervisor printed it into the log — fixed here).
# - Execs the listener in forever mode; Railway's restart policy is the
#   supervisor. The executor stays DISARMED — nothing in this script
#   arms anything, signs, or broadcasts.
set -eu

DATA_DIR="${SINCOR_DATA_DIR:-/data}"
mkdir -p "$DATA_DIR"

SEED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/seed"
if [ ! -f "$DATA_DIR/fee_event_cursor.json" ] && [ -f "$SEED_DIR/fee_event_cursor.json" ]; then
  cp "$SEED_DIR/fee_event_cursor.json" "$DATA_DIR/fee_event_cursor.json"
  echo "fee-listener: seeded cursor from package snapshot"
fi
if [ ! -f "$DATA_DIR/fee_conversion_ledger.json" ] && [ -f "$SEED_DIR/fee_conversion_ledger.json" ]; then
  cp "$SEED_DIR/fee_conversion_ledger.json" "$DATA_DIR/fee_conversion_ledger.json"
  echo "fee-listener: seeded ledger from package snapshot"
fi

RPC_HOST="$(printf '%s' "${BASE_RPC_URL:-}" | sed -E 's#^[a-zA-Z]+://([^/]+).*#\1#')"
echo "fee-listener starting (rpc host: ${RPC_HOST:-UNSET}, data dir: $DATA_DIR)"

export PYTHONPATH="src:."
exec python scripts/run_fee_listener.py
