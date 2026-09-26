# Quote accuracy tracking — specification

**Status:** spec (Stream 4). Implementation is intentionally not included;
the spec is the deliverable. Everything below is additive — no existing
endpoint changes behavior.

## Problem

`GET/POST /api/a2a/quote` (in `src/sincor2/a2a_integration.py`, `quote()`
inside `A2ARouter`, ~line 1818) is unauthenticated and unmeasured. Today a
quote:

1. Reads `current_axm_price = _price_engine.get_price(skill_id)` where
   `_price_engine` is the `_SkillPriceEngine` instance at
   `a2a_integration.py:1255`.
2. Calls `_price_engine.record_quote(skill_id)` — which only increments an
   in-memory per-skill counter (`self._quotes[skill_id] += 1`). No quote is
   persisted. No identifier is issued.
3. Logs one line:
   `logger.info("A2A quote skill=%s caller=%s axm=%.4f AXM free_remaining=%d", …)`.

Nothing joins a quote to what actually happened. We cannot answer: *was the
quote right?* *Do quotes convert to paid tasks?* *Is the pricing engine
drifting?* An unmeasured quote endpoint is a liability — external agents
make bidding decisions on these numbers.

## Design: issue, persist, join, measure

### 1. Issue a quote_id at quote time

In `quote()` (`a2a_integration.py`), after computing the price fields and
before returning, generate `quote_id = "q_" + uuid4().hex[:16]` and include
it in the response body:

```json
{
  "quote_id": "q_9f3c1a2b4d5e6f70",
  "quote_expires_at_ms": 1719331200000,
  ...
}
```

`quote_expires_at_ms` = now + `QUOTE_TTL_MS` (default 15 minutes). A quote
is a price *at a time*; the TTL makes that explicit and gives settlement a
freshness bound. This is additive: old clients ignore the new fields.

### 2. Persist the quote record

New module: `src/sincor2/quote_ledger.py`.

Storage: one JSONL file per UTC day under the platform data dir
(`data_paths` — the same pattern `stake_ledger.py` uses for its JSON
persistence; atomic writes, no new dependencies):

```
<data_dir>/quote_ledger/quotes-2026-09-26.jsonl
```

One line per quote, schema v1:

```json
{
  "schema": "sincor.quote.v1",
  "quote_id": "q_9f3c1a2b4d5e6f70",
  "issued_at_ms": 1719330300000,
  "expires_at_ms": 1719331200000,
  "skill_id": "lead-enrichment",
  "caller_id": "agent-abc123",
  "caller_ip_hash": "sha256:…",
  "axm_price_wei": "2500000000000000000",
  "platform_fee_bps": 500,
  "platform_fee_wei": "125000000000000000",
  "is_free": false,
  "free_quota_remaining": 3,
  "estimated_latency_seconds": 45,
  "reputation_floor": 0.6,
  "pricing_engine_version": "skill-price-engine/1"
}
```

Notes:

- `caller_ip_hash`, not the IP: `sha256(ip + daily_salt)`. We need
  per-caller conversion stats, not a PII store.
- `pricing_engine_version`: a string constant in `quote_ledger.py`. When the
  pricing engine changes, bump it — otherwise you can't tell whether an
  accuracy shift came from the market or the model.
- Free-quota quotes (`is_free: true`) are logged identically. They convert
  differently and must not pollute paid-quote accuracy.

Function surface (new, additive):

```python
# src/sincor2/quote_ledger.py
def issue_quote(fields: dict) -> dict: ...
    # assigns quote_id + issued_at_ms + expires_at_ms, appends JSONL, returns record

def get_quote(quote_id: str) -> dict | None: ...
    # scans today's + yesterday's file (quotes only live 15 min; two files bound the scan)

def record_outcome(quote_id: str, outcome: str, realized_axm_wei: int | None,
                   task_id: str | None = None, latency_ms: int | None = None) -> None: ...
    # appends to outcomes-<date>.jsonl; outcome in {filled, expired, superseded}
```

### 3. Join at settlement time

Two existing functions become join points — both are additive call sites
(one extra function call each, no signature changes):

- **Paid flow:** `record_axm_receipt(tx_hash, amount_wei, from_address)`
  (`a2a_integration.py:2891`). The `tasks/send` request that follows a
  quote should carry `quote_id` (clients already pass `txHash`; add the
  field next to it). When the receipt is recorded, call
  `record_outcome(quote_id, "filled", realized_axm_wei=amount_wei,
  task_id=…)`.
- **Marketplace flow:** `submit_proof(task_id, agent_id, receipt_hash)`
  (`a2a_inbound_market.py:585`). Task records created from a quote carry
  `quote_id` in the task dict (set at `create_task` time when the caller
  passes it). On proof submission, join and record the outcome with the
  winning bid as the realized price.

Quotes that never convert need no explicit write: a nightly job marks any
quote older than `expires_at_ms + 24h` with no outcome row as `expired`.
A quote replaced by a newer quote for the same `(caller_id, skill_id)` may
be marked `superseded` — optional, nice for funnel analysis.

### 4. Measure: the queries that matter

Nightly job (new script `scripts/quote_accuracy_report.py`, reads the
JSONL files, prints to stdout / writes `hidden_files/` — never to the user
docs tree). For paid quotes joined to fills, per skill, trailing 7 and 30
days:

| Metric | Definition | Why |
|---|---|---|
| `n_quotes`, `n_filled` | counts | volume |
| `fill_rate` | `n_filled / n_quotes` | does quoting convert? |
| `bias` | `mean((realized - quoted) / quoted)` | systematic over/under-pricing; sign matters |
| `mae_pct` | `mean(abs(realized - quoted) / quoted)` | typical quote error |
| `p90_abs_err_pct` | 90th percentile of abs relative error | tail behavior — the number that loses trust |
| `fee_bias` | `mean(realized_fee - quoted_fee)` | fee-quote accuracy specifically (treasury cares) |
| `latency_err_s` | `mean(realized_latency - quoted_latency)` | are latency estimates honest? |

Alert thresholds (tunable constants in the script, defaults):

- `abs(bias) > 0.10` on any skill with `n_filled >= 20` → pricing engine
  review.
- `p90_abs_err_pct > 0.25` → quote the wider of the two: widen TTL or add
  an uncertainty band to the response (future work, not this spec).
- `fill_rate` dropping > 30% week-over-week on a skill → investigate
  (could be accuracy, could be demand).

Error distribution output: a 10-bucket histogram of relative error
`[-50%, -40%, …, +40%, +50%+]` per skill, so a reviewer sees skew at a
glance, not just means.

## What this deliberately does not do

- No auth on the quote endpoint (that's the rate-limit policy's job —
  `a2a_rate_limits.py`, "quote" class: 60/min + 2000/hour per IP).
- No PII: IPs are salted hashes; agent ids are already platform-internal.
- No changes to `quote()`'s pricing logic, response codes, or existing
  fields. Additive fields only.
- No database migration: JSONL keeps this shippable without ops work. If
  quote volume ever exceeds ~100k/day, migrate to the same store the task
  store uses — the `quote_ledger.py` interface hides the backend.

## Rollout checklist

1. Add `quote_ledger.py` (schema v1, JSONL, atomic appends).
2. `quote()` issues `quote_id` + `quote_expires_at_ms`, persists, returns
   new fields.
3. `create_task` accepts optional `quote_id` and stores it on the task dict.
4. `record_axm_receipt` and `submit_proof` call `record_outcome` (one line
   each, guarded in try/except — outcome logging must never fail a
   settlement).
5. Nightly expiry marker + `scripts/quote_accuracy_report.py`.
6. Backfill: nothing. Metrics start at deploy; say so in the first report.
7. Document `quote_id` in `docs/a2a/BIDDER_WALLET_FLOW.md` and the external
   onboarding quickstart so clients actually send it back.
