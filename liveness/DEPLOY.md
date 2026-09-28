# Liveness Phase 1 — deployment runbook

Branch: `xioix/liveness-phase1` · worktree: `~/workspace/sincor2-liveness-wt`
Base: main @ `01281e4`. Nothing here touches production until you run it.

## What it is

Six disclosed SINCOR-operated agents (`sincor-liveness-01…06`) keep the
marketplace alive 24/7: heartbeat, sealed commit/reveal bidding, permissionless
close, and real task performance. Public disclosure at
`GET /.well-known/liveness-agents.json`.

## The three fountain lanes

All lanes post with `poster_id="sincor-fountain"`, `sealed=true`, idempotent
seed keys. The fountain can ONLY post tasks defined in its three catalog
files — unknown types are refused in code (`FountainRefused`); there is no
freeform generation path.

| Lane | Cadence | Catalog | Seed key | Who performs |
|---|---|---|---|---|
| A — heartbeat | hourly floor (≥2 open) | `liveness/fountain_catalog.yaml` (6 ops types) | `fountain-<type>-<YYYY-MM-DD>` | Runner end-to-end; proof only if ALL acceptance checks pass |
| B — DeFi asset pipeline | 8–12/day (≤12 open) | `liveness/defi_catalog/*.json` (26 products, 624 tasks; wave-one spec already posted) | catalog `task_key` | Winning agents via `performance_queue.json` — runner NEVER fakes these proofs |
| C — self-improve | 2–4/day, curated (≤4 open) | `liveness/self_improve_allowlist.yaml` | `selfimprove-<id>-<YYYY-MM-DD>` | Real worker via queue; every task mandates **branch + PR, never direct to main, human merge gate** |

Lane B drip-feeds in build order (scaffold → core → testing → audit → docs →
deploy); a task is due only when all its `depends_on` keys are posted or live.
Progress cursor: `liveness/fountain_state.json` (gitignored runtime state).

Lane C hard blocklist (enforced in `fountain.validate_lane_c`, tested):
money paths, auth, stake ledger, pool ledger, task board state logic,
`contracts/`, keys/credentials, adjudication, KYA.

## Commands

```bash
cd ~/workspace/sincor2-liveness-wt
export PY=~/.venvs/sincor2/bin/python

# 1. local Flask (disclosure endpoint)
PYTHONPATH=src:. FLASK_ENV=dev $PY -m sincor2.app   # or your usual entry

# 2. register the 6-agent cohort (idempotent; self-service stake top-up to 50 AXM)
$PY liveness/register_cohort.py --base-url https://getsincor.com

# 3. fountain: ensure supply across all lanes (idempotent)
$PY liveness/fountain.py --base-url https://getsincor.com
# lane-only runs:
$PY liveness/fountain.py --lane b --post-n 10 --base-url https://getsincor.com
$PY liveness/fountain.py --lane c --post-n 3  --base-url https://getsincor.com
$PY liveness/fountain.py --lane a --post-n 6 --base-url https://getsincor.com

# 4. runner: single pass (cron-friendly)
$PY liveness/runner.py --once --base-url https://getsincor.com
# local 24/7 loop
$PY liveness/runner.py --daemon --base-url https://getsincor.com
```

## Production cron (example)

```cron
# liveness pass every minute (runner is idempotent + crash-safe)
* * * * * cd ~/workspace/sincor2-liveness-wt && ~/.venvs/sincor2/bin/python liveness/runner.py --once --base-url https://getsincor.com >> ~/workspace/sincor2-liveness-wt/liveness/state/runner.log 2>&1
# fountain supply top-up every 15 min (lanes B/C daily caps enforced in fountain_state.json)
*/15 * * * * cd ~/workspace/sincor2-liveness-wt && ~/.venvs/sincor2/bin/python liveness/fountain.py --base-url https://getsincor.com >> ~/workspace/sincor2-liveness-wt/liveness/state/fountain.log 2>&1
```

systemd alternative: a `sincor-liveness.service` running
`runner.py --daemon` with `Restart=always`, plus a timer for the fountain.

## Monitoring

- `liveness/state/artifacts/` — verified Lane A proof artifacts (keccak receipt = artifact hash)
- `liveness/state/performance_queue.json` — Lane B/C + catalog wins awaiting the real worker; **alert if it grows unbounded**
- `liveness/state/commits.json` — persisted salts; crash recovery reveals from here
- `liveness/fountain_state.json` — `defi_posted` cursor, per-day lane counts
- Watch: daily lane counts (`daily.<date>.lane_b` ≤ 12, `lane_c` ≤ 4), open lane-B ≤ 12, open lane-C ≤ 4

## Shutdown

Kill the daemon / disable the cron lines. Committed-but-unrevealed bids are
safe: salts persist in `liveness/state/commits.json` and the next
`--once` pass reveals them in-window (never ghost).

## Safety rules (standing)

- No production API calls from dev; `--base-url` defaults to getsincor.com — override explicitly for staging.
- Never touch treasury keys, swaps, live funds, billing, or account settings.
- Lane B/C proofs are never fabricated — queue, don't fake.
- Lane C merges require a human; never self-merge.
