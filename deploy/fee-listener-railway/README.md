# Fee listener → Railway: the smooth-out package

Why: on this VM the listener keeps getting reaped (6 watchdog restarts
in ~30 hours, each caught and restarted, none losing data — the cursor
only advances at completed scan boundaries and re-recording is
idempotent). Railway gives it a supervised home that doesn't die when
the VM cycles. Executor stays **DISARMED** throughout — this move
changes where the watcher runs, nothing else.

## What's in this package

- `railway.toml` — start command + restart-on-failure policy.
- `start.sh` — seeds the data volume from `seed/` on first boot only;
  logs the RPC **host only** (the old VM supervisor printed the full
  keyed URL into its log — that leak is fixed here, not carried over).
- `seed/fee_event_cursor.json` + `seed/fee_conversion_ledger.json` —
  snapshot of the live VM state at packaging time (cursor block
  53,288,882; ledger holds exactly 1 pending obligation: the 0.761 AXM
  fee on the founder's own 2026-10-25 transfer). A stale seed is safe:
  the listener rescans forward from the cursor and obligation ids are
  derived from (tx_hash, fee), so nothing double-records.

## Prerequisite

Railway deploys from GitHub, and the listener code does not exist on
`main` yet — this package rides branch `xioix/fee-listener-durable`.
That branch must be pushed first (it is in the one-shot PAT push
queue). Point the Railway service at that branch, or at `main` after
the branch merges.

## Founder taps (Railway UI)

1. **New service** in the existing SINCOR Railway project → deploy from
   the `OrderofChaos33/SINCOR2` repo, branch per the prerequisite.
2. **Settings:** config file path `deploy/fee-listener-railway/railway.toml`
   (if the UI won't take a path, set the start command manually to
   `bash deploy/fee-listener-railway/start.sh` and restart policy to
   on-failure). Root directory: repo root.
3. **Volume:** attach a volume mounted at `/data`.
4. **Variables** (paste values directly into Railway — never via chat):
   - `BASE_RPC_URL` — the Alchemy Base endpoint the VM service already
     uses (copy it from the VM env file or your Alchemy dashboard).
   - `SINCOR_DATA_DIR` = `/data`
   - `FEE_LISTENER_TOKEN`, `FEE_LISTENER_MIN_WEI`,
     `FEE_LISTENER_POLL_SECS`, `FEE_LISTENER_LOG_LEVEL` — copy as-is
     from the VM service env.
   - `FEE_LISTENER_CONFIRMATIONS` = `12` (do not lower)
   - `FEE_LISTENER_FEE_BPS` = `500` (locked 5% policy)
5. **Deploy.** Verify in the service logs:
   - `fee-listener starting (rpc host: base-mainnet.g.alchemy.com …)`
   - first boot only: the two `seeded …` lines
   - recurring `fee-listener cycle` lines, and the volume's
     `fee_event_cursor.json` block tracking (latest Base block − 12).

## Cutover (after the Railway copy is verified polling)

Order matters — the VM watchdog restarts the VM copy within 5 minutes
if it finds it stopped, and two live instances on divergent ledgers
are the one thing the runbook forbids:

1. Main agent pauses the `fee-listener-watchdog` cron.
2. Stop the VM service: `~/workspace/ops/fee-listener-service/stop.sh`.
3. Confirm the Railway cursor keeps advancing for ~15 minutes.
4. Main agent retires/repoints the watchdog and updates STATUS.md.

## Rollback

Stop the Railway service, restart the VM copy
(`~/workspace/ops/fee-listener-service/start.sh`), re-enable the
watchdog. The VM cursor/ledger were never modified by the Railway
instance (separate volume, seeded copy), so rollback loses nothing.

## Cost note

Runs inside the existing Railway project; a small always-on service
(~$5/mo class). The Alchemy PAYG balance the founder loaded covers the
RPC side separately.
