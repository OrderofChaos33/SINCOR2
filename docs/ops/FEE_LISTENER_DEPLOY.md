# Fee-Event Listener — Deploy Runbook (PRE-5)

The fee-event listener watches the AXM ERC-20 contract on Base for
`Transfer` events into the platform fee-recipient address and records the
locked 5 % platform fee of each **confirmed** (12 blocks deep) inflow as a
**pending** obligation in the conversion ledger.

**Hard rules — do not change at deploy time:**
- The listener never signs, never broadcasts, never holds keys.
- The conversion executor stays **DISARMED** (`FeeConversionConfig.armed`
  is `False`; `execute_plan` refuses non-dry-run execution). No deploy step
  arms it — arming is the separate item-32 ceremony
  (`docs/ops/FEE_EXECUTOR_RUNBOOK.md`).
- Obligations queue as **pending only**. Nothing converts, nothing moves funds.
- Do not edit treasury config to deploy this. `SINCOR_FEE_RECIPIENT`
  defaults to the canonical treasury address already in code.

## 1. Entry point

`scripts/run_fee_listener.py` — two modes:

```bash
# Persistent service (systemd): polls forever, 12 confirmations deep
python scripts/run_fee_listener.py

# Scheduled job (cron): exactly one poll cycle, JSON summary on stdout
python scripts/run_fee_listener.py --once
```

## 2. Required env vars

| Var | Required | Purpose |
|---|---|---|
| `BASE_RPC_URL` | **yes** (or `FEE_LISTENER_RPC_URL`) | Base RPC endpoint. Fail-closed: the service refuses to start without one. |
| `SINCOR_FEE_RECIPIENT` | no | Fee-recipient address. Default: canonical treasury. |
| `SINCOR_DATA_DIR` | recommended | Data dir for cursor + ledger (Railway: `/data` volume). |
| `FEE_LISTENER_CURSOR_PATH` | no | Cursor file; default `<data_dir>/fee_event_cursor.json`. |
| `SINCOR_FEE_LEDGER_PATH` | no | Conversion-ledger file; default `<data_dir>/fee_conversion_ledger.json`. |

Tuning (all optional; defaults shown):

| Var | Default | Notes |
|---|---|---|
| `FEE_LISTENER_CONFIRMATIONS` | `12` | Confirmations before an event is recorded. Do not lower. |
| `FEE_LISTENER_POLL_SECS` | `30` | Delay between poll cycles (forever mode). |
| `FEE_LISTENER_FEE_BPS` | `500` | Locked 5 % policy. Non-500 values log a warning. |
| `FEE_LISTENER_MIN_WEI` | `0` | Inflows below this are observed but not recorded. |
| `FEE_LISTENER_BACKFILL_BLOCKS` | `0` | First-run backfill (0 = start at tip). |
| `FEE_LISTENER_START_BLOCK` | — | Explicit first-run start block (overrides backfill). |
| `FEE_LISTENER_TOKEN` | AXM | Token contract watched. |
| `FEE_LISTENER_LOG_LEVEL` | `INFO` | |

Run exactly **one** instance per (cursor, ledger) pair. Two instances sharing
a ledger are idempotent-safe (same obligation ids), but two instances with
different ledgers record divergent journals.

## 3. systemd (persistent service)

`/etc/systemd/system/sincor-fee-listener.service`:

```ini
[Unit]
Description=SINCOR fee-event listener (read-only; executor disarmed)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sincor
WorkingDirectory=/opt/sincor2
Environment=BASE_RPC_URL=https://<your-base-rpc>
Environment=SINCOR_DATA_DIR=/var/lib/sincor
# SINCOR_FEE_RECIPIENT defaults to the canonical treasury; set only to override.
ExecStart=/opt/sincor2/.venv/bin/python /opt/sincor2/scripts/run_fee_listener.py
Restart=on-failure
RestartSec=10
# systemd sends SIGTERM first; the service translates it into a clean,
# cursor-preserving stop.
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sincor-fee-listener.service
sudo systemctl status sincor-fee-listener.service
```

## 4. cron (scheduled job)

For hosts without systemd, run one cycle every 5 minutes. `flock` prevents
overlapping cycles if a run ever exceeds the interval:

```cron
*/5 * * * * sincor BASE_RPC_URL=https://<your-base-rpc> SINCOR_DATA_DIR=/var/lib/sincor /usr/bin/flock -n /var/lib/sincor/fee-listener.lock /opt/sincor2/.venv/bin/python /opt/sincor2/scripts/run_fee_listener.py --once >> /var/log/sincor/fee-listener.log 2>&1
```

Exit codes: `0` = cycle ok, `1` = cycle failed (cursor not advanced; next run
retries the same range — fail-closed), `2` = config error (no RPC URL).

## 5. Verify it is observing

1. **Process alive:** `systemctl is-active sincor-fee-listener.service`
   (or a recent `--once` timestamp in the cron log).
2. **Cursor advancing:** the cursor file's `block` should track
   `(latest Base block − 12)`:
   ```bash
   cat /var/lib/sincor/fee_event_cursor.json
   # {"block": 34123456, "hash": "0x..."}
   ```
3. **Ledger pending-only:** obligations appear only as `pending`; nothing
   transitions to `quoted`/`executing` from this service:
   ```bash
   python -c "
   import json
   obs = json.load(open('/var/lib/sincor/fee_conversion_ledger.json'))['obligations']
   print(len(obs), 'obligations;', {o['status'] for o in obs})
   "
   ```
4. **Log signal:** `fee-listener cycle` lines; `obligation <id> recorded
   (fee … wei …)` when an inflow confirms.
5. **Disarmed check:** the service logs never contain a conversion plan or
   broadcast — the executor module refuses non-dry-run execution by design.

## 6. Reorg / failure behavior (what to expect)

- **RPC outage:** cycle fails closed (`ok=false`, exit 1 in `--once` mode);
  the cursor is never advanced; the next run retries the same block range.
- **Reorg at cursor:** the listener rewinds 64 blocks and rescans.
  Re-recording is idempotent — obligation ids derive from
  `(tx_hash, fee amount)` — so a rescan never duplicates.
- **Restart:** resumes exactly at the cursor — no gaps, no replays.
- **Dust:** inflows below `FEE_LISTENER_MIN_WEI` are observed, not recorded.

## 7. Before any future arming

Every obligation carries `tx_hash` / `block_number` / `log_index` in its
`source`. Reconcile the ledger against real receipts before the item-32
arming ceremony. The listener over-records direct transfers conservatively
while disarmed — that is intended; reconcile, do not edit the listener.

## 8. Troubleshooting

| Symptom | Cause → fix |
|---|---|
| Exit 2 immediately | `BASE_RPC_URL` unset — set it. |
| Cursor file never advances | RPC unreachable or rate-limited; check logs for `fail-closed`; consider a paid RPC endpoint. |
| `reorg detected … rewinding` warnings | Normal on Base; idempotent rescan, no action needed. |
| `FEE_LISTENER_FEE_BPS=… differs from the locked 5% policy` | Env set to non-500 — revert to 500 unless the fee policy itself changed. |
