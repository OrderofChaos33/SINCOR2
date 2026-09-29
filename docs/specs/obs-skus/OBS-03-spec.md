# OBS-03 — Drift & Quality Watch (Spec)

**Catalog line:** sku_id=OBS-03 · $399/mo · billing=subscription ·
stage_label=SELLABLE · lifecycle=software · lifecycle_stage=spec ·
gates=(baseline_drift, outcome_tied_quality, alert_routing) ·
module=`src/sincor2/obs_skus/drift_quality.py` (sibling builder B4) ·
draft=`templates/products/drafts/obs-03-drift-quality-watch.html` (unrouted)

**Standing rule (founder, 2026-09-29):** this SKU's page stays a draft until
the publish gate in §7 passes on verified end-to-end evidence.

**Grounding (verified on disk 2026-09-29):**
- `src/sincor2/onchain/stake_ledger.py` (exists, PR #261): ghosting → 100% slash,
  quality miss → 50% slash; all slash proceeds go to the poster re-auction
  fund. These are real, on-platform outcome signals.
- Reputation is earned-only (PR #266, merged): ghosting zeroes earned
  reputation (`src/sincor2/a2a_inbound_market.py:432`), an upheld quality
  dispute halves it (`:1126`).
- `GET /v1/a2a/registration-velocity` (exists): volume-over-vanity
  objective weights.
- The drift/quality module (`drift_quality.py`) does **not** exist yet — it
  is sibling builder B4's build. Nothing below claims it does.

---

## 1. What/How

Drift & Quality Watch keeps a continuous watch over agent behavior: drift
detection against each agent's established baseline, and quality signals
tied to real on-platform outcomes (ghosting, disputes, reputation) — with
alerts routed to the customer when thresholds trip.

### Step-by-step flow

1. **Baseline.** `drift_quality.py` builds a per-agent baseline from
   observed behavior: bid cadence, task completion rate, dispute rate,
   stake events. Baselines are computed from history, never asserted.
2. **Watch.** On each evaluation tick, current-window behavior is compared
   against the baseline. Drift beyond the configured band raises a drift
   flag with the offending dimensions named.
3. **Quality.** A quality signal combines outcome events the platform
   already records: ghosting slashes, upheld disputes, reputation changes.
   Quality moves only on verified outcomes — never on vibes, never on
   unproven telemetry.
4. **Alert.** When a drift flag or quality threshold trips, an alert is
   routed to the customer's configured channel with: agent id, what
   changed, the baseline vs. current numbers, and a link to the agent's
   audit trail (OBS-02) for the underlying events.
5. **Quiet by design.** No alert fires without a named cause and numbers.
   Alert fatigue is treated as a defect: duplicate flags for the same
   cause are coalesced.

### Key modules

| Component | Location | Role |
|---|---|---|
| `drift_quality.py` | `src/sincor2/obs_skus/` (new, B4) | baselines, drift bands, quality signal, alerting |
| stake ledger | `src/sincor2/stake_ledger.py` (exists) | ghosting/quality outcome events |
| reputation | PR #266 logic (exists) | earned-only reputation signal |
| audit trail | OBS-02 (sibling) | underlying event evidence |

### Numeric parameter table

| Parameter | Value |
|---|---|
| Price | $399/mo |
| Baseline window | 30 days of history (min 50 observations) |
| Evaluation tick | 15 minutes |
| Drift band | ±2σ per dimension (configurable per customer) |
| Alert coalescing | same cause re-fires at most once per 24h |

---

## 2. Why

**Who pays:** operators whose agents act in the world — where a quiet
behavior change today becomes a slashed stake or a lost customer tomorrow.
**Why they pay:** nobody watches baselines manually; by the time drift is
visible in outcomes, the damage is priced in. **Revenue path:** $399/mo
flat, covering the customer's whole deployment.

---

## 3. Build Stack

- Python; baselines stored under the durable data dir
  (`sincor2.data_paths.data_dir()`, per PR #295).
- Read-only over stake ledger, reputation, and task history; alerting via
  the platform's existing notification paths (no new infra in v1).
- Tests in `tests/pytest/` (B4's suite) + this registry's gate tests.

---

## 4. Acceptance criteria

1. Baseline requires minimum history: with fewer than 50 observations the
   module reports `insufficient_history` and raises no drift flags (test
   asserts: 49 observations → no flags, ever).
2. Drift detection: a fixture agent whose bid cadence shifts +3σ from its
   30-day baseline raises a drift flag naming the cadence dimension; the
   flag carries baseline vs. current numbers.
3. No false positives on steady state: 30 days of flat fixture behavior
   produce zero drift flags.
4. Quality signal moves only on verified outcomes: a ghosting slash event
   lowers the agent's quality; the passage of time alone never does (test
   asserts quality unchanged across 100 ticks with no outcome events).
5. Reputation tie-in: an upheld dispute halves the reputation input the
   quality signal reads (matches PR #266 semantics — the signal reflects
   the platform's ruling, not its own judgment).
6. Alert content: every alert names the agent, the cause, baseline vs.
   current numbers, and links the OBS-02 trail query for the window (test
   asserts all four fields present).
7. Coalescing: the same cause re-firing within 24h produces one alert, not
   N (test asserts exactly one alert for 10 identical ticks).
8. Per-customer scoping: drift flags and alerts for customer A's agents
   are never visible to customer B (fixture test, zero leakage).

---

## 5. Lifecycle gates

Software lifecycle: spec → build → test → audit → product → catalog.
Gate checks (see `src/sincor2/obs_skus/gates.py`):

- **spec → build:** spec on file, draft page present, no public-surface
  wiring (`find_public_wiring` empty).
- **build → test:** `drift_quality.py` present; unit tests passing in proof
  ledger.
- **test → audit:** audit report recorded, 0 open criticals. (The audit
  must cover the statistics: baseline math, σ bands, and the no-vibes rule
  that quality moves only on verified outcomes.)
- **audit → product / product → catalog:** still no public wiring until the
  publish gate passes.

---

## 6. What we do not claim

- No prediction of future agent behavior; drift is observed deviation from
  baseline, not a forecast.
- No automated remediation (no pausing or slashing agents) — this SKU
  watches and alerts; action stays with the customer.
- No cross-deployment benchmarking ("your agents vs. the network") in v1.

---

## 7. Publish gate — end-to-end verification + live walkthrough

The draft page may be linked from nav/pricing/sitemap and the SKU sold
**only** after ALL of the following are recorded in the proof ledger and
`gates.evaluate_publish` returns ok:

### End-to-end verification (tests)

- Criteria 1–8 above each have a passing `acceptance_run` ledger entry
  (`details: {criterion: N, passed: true}`), recorded against the built
  `drift_quality.py`. Criteria 2, 4, 5, 7 run against staging with real
  stake-ledger and reputation events (not fixtures alone).
- False-positive soak: 7 days of staging data with no injected drift →
  zero customer-facing alerts (logged as an acceptance entry).
- The full existing pytest suite stays green (no regressions).

### Live walkthrough checklist (founder-conducted, every box ticked)

- [ ] On staging, inject a +3σ cadence shift on a test agent; confirm a
      drift alert arrives naming the dimension with baseline vs. current.
- [ ] Trigger a real ghosting slash on a staging agent; confirm the
      quality signal drops and the alert cites the slash event.
- [ ] Let the same cause re-fire 5 times in an hour; confirm exactly one
      alert (coalescing holds).
- [ ] Confirm a 10-observation agent shows `insufficient_history`, never a
      flag.
- [ ] As a second customer, confirm zero visibility into the first
      customer's flags.
- [ ] Click every CTA on the draft page; each resolves to a working
      destination (`/signup?plan=obs-03-drift-quality-watch` pre-fills).
- [ ] Read the draft page copy aloud; confirm zero banned claims and
      positive framing — especially: no prediction claims, no automated
      remediation claims.
- [ ] Record the walkthrough in the proof ledger
      (`kind: walkthrough`, `details: {all_boxes_ticked: true,
      conducted_by: <name>, date: <iso>}`).

### Promotion ceremony (after the gate is green)

1. Move `templates/products/drafts/obs-03-drift-quality-watch.html` →
   `templates/products/obs-03-drift-quality-watch.html`.
2. Add the slug to `_PRODUCT_SLUGS` in `mvp_blueprints/pages.py`.
3. Add the pricing-page entry and sitemap link.
4. Re-run the registry gate tests for the new state; record a
   `pricing_live` ledger entry.
