# OBS-01 — Agent Vitals (Spec)

**Catalog line:** sku_id=OBS-01 · $49/mo per agent · billing=subscription ·
stage_label=BETA · lifecycle=software · lifecycle_stage=spec ·
gates=(dashboard_customer_facing, unknown_not_healthy, per_agent_scoping) ·
module=`src/sincor2/obs_skus/vitals.py` (sibling builder B2) ·
draft=`templates/products/drafts/obs-01-agent-vitals.html` (unrouted)

**Standing rule (founder, 2026-09-29):** this SKU's page stays a draft until
the publish gate in §7 passes on verified end-to-end evidence.

**Grounding (verified on disk 2026-09-29):**
- `src/sincor2/kya_registry.py:656` — `live_statuses()` returns
  `{kya_id: status}`; `list_agents()` consults it so revoked agents are
  excluded immediately (PR #261).
- `GET /v1/a2a/registration-velocity` exists with origin stamping
  (internal/external).
- Dashboards exist but are operator-facing, not customer-facing:
  `templates/command_center.html`, `templates/operator_dashboard.html`,
  `templates/dashboards_menu.html`; route `/dashboards` in
  `src/sincor2/mvp_blueprints/pages.py:452` (login-gated).
- Observability rule (standing): feeds report **unknown**, never healthy.
- The per-agent vitals module (`vitals.py`) does **not** exist yet — it is
  sibling builder B2's build. Nothing below claims it does.

---

## 1. What/How

Agent Vitals gives a customer one honest health picture per agent they run:
current status, liveness signal freshness, and recent activity — rendered on
a customer-facing dashboard that does not exist yet.

### Step-by-step flow

1. **Collect.** `vitals.py` reads the signals the platform already keeps:
   KYA `live_statuses()`, registration-velocity origin stamps, and the
   agent's recent task/bid activity. No new instrumentation is invented;
   vitals are a read model over existing state.
2. **Snapshot.** `snapshot(agent_id)` returns a named record:
   `status` (active / idle / revoked / unknown), `last_seen_ts`,
   `signal_freshness_s`, and `activity_7d` (counts of tasks, bids, disputes).
   Any signal older than its freshness budget degrades the agent's display
   to **unknown** — never to healthy-by-default.
3. **Render.** A customer-facing dashboard panel lists the customer's
   agents with per-agent status chips, freshness ages, and 7-day activity
   bars. The panel refreshes on a stated cadence (target: 60s) and shows
   the data-as-of timestamp on every render.
4. **Scope.** Every read is filtered to the requesting customer's agents.
   Cross-customer leakage is a hard failure, tested explicitly.

### Key modules

| Component | Location | Role |
|---|---|---|
| `vitals.py` | `src/sincor2/obs_skus/` (new, B2) | snapshot(), freshness budgets, per-agent scoping |
| KYA registry | `src/sincor2/kya_registry.py` (exists) | status source of truth |
| registration-velocity | existing endpoint (exists) | origin/activity signals |
| dashboard panel | new template + route (B2) | customer-facing render |

### Numeric parameter table

| Parameter | Value |
|---|---|
| Price | $49/mo per agent |
| Panel refresh target | 60s |
| Stale-signal budget | 300s → status degrades to unknown |
| Activity window | 7 days |
| Freshness label | data-as-of timestamp on every render |

---

## 2. Why

**Who pays:** operators running fleets of agents who need to know, at a
glance, which agents are alive, idle, or revoked. **Why they pay:** the
platform's dashboards today are operator-facing and login-gated; a customer
cannot see their own agents' health without asking SINCOR. **Revenue path:**
$49/mo per agent, billed per active agent on the customer's account.

---

## 3. Build Stack

- Python, Flask/Jinja (pro-site design system for the panel).
- Read-only over existing platform state; no new daemons in v1.
- Tests in `tests/pytest/` (B2's suite) + this registry's gate tests.

---

## 4. Acceptance criteria

1. `snapshot(agent_id)` returns all five named fields for an active agent,
   and `signal_freshness_s` is never negative.
2. A signal older than the 300s freshness budget degrades the agent's
   status to **unknown** (test asserts: stale fixture → `status == "unknown"`).
3. A revoked agent (KYA `live_statuses()` → revoked) renders as revoked
   within one refresh cycle — never as active or healthy.
4. Per-agent scoping: `snapshot()` for another customer's agent raises /
   returns empty; a cross-customer fixture test asserts zero leakage.
5. The dashboard panel renders with a visible data-as-of timestamp; a
   template test asserts the timestamp element exists in the HTML.
6. Panel refresh: two consecutive renders 60s apart show a changed
   data-as-of timestamp (no frozen/stale page served as fresh).
7. Empty fleet: a customer with zero agents sees a clean empty state, not
   an error and not another customer's data.
8. Feed rule: no code path in `vitals.py` maps missing data to a healthy
   status (static test asserts the module never emits `status == "active"`
   for an agent with no signals).

---

## 5. Lifecycle gates

Software lifecycle: spec → build → test → audit → product → catalog.
Gate checks (see `src/sincor2/obs_skus/gates.py`):

- **spec → build:** spec on file (§ exists), draft page present, and the
  standing-rule tripwire — no public-surface wiring
  (`find_public_wiring` empty).
- **build → test:** `vitals.py` present; unit tests passing in proof ledger.
- **test → audit:** audit report recorded, 0 open criticals.
- **audit → product / product → catalog:** still no public wiring until the
  publish gate passes.

---

## 6. What we do not claim

- The customer-facing dashboard does not exist yet (BETA is honest: the
  operator dashboards are login-gated and internal).
- No uptime SLA, no alerting, no mobile app — none are in this SKU.
- No "AI health score" or predictive failure claims; vitals are observed
  signals with freshness budgets, nothing more.

---

## 7. Publish gate — end-to-end verification + live walkthrough

The draft page may be linked from nav/pricing/sitemap and the SKU sold
**only** after ALL of the following are recorded in the proof ledger and
`gates.evaluate_publish` returns ok:

### End-to-end verification (tests)

- Criteria 1–8 above each have a passing `acceptance_run` ledger entry
  (`details: {criterion: N, passed: true}`), recorded against the built
  `vitals.py` and the real dashboard route — not fixtures alone. At least
  criteria 2, 3, 4, 8 run against the staging deployment with real KYA data.
- The full existing pytest suite stays green (no regressions).

### Live walkthrough checklist (founder-conducted, every box ticked)

- [ ] Sign in as a test customer with 3 agents (1 active, 1 idle > 300s,
      1 revoked). The panel shows active / unknown / revoked respectively.
- [ ] Revoke the active agent mid-session; within one refresh cycle the
      panel shows revoked.
- [ ] Open the panel as a second customer; confirm zero cross-customer data.
- [ ] Disconnect the signal source; confirm the panel degrades to unknown,
      never to healthy, with the data-as-of timestamp visible.
- [ ] Click every CTA on the draft page; each resolves to a working
      destination (`/signup?plan=obs-01-agent-vitals` pre-fills the plan).
- [ ] Read the draft page copy aloud; confirm zero banned claims
      (see `gates.BANNED_DRAFT_PHRASES`) and positive framing throughout.
- [ ] Record the walkthrough in the proof ledger
      (`kind: walkthrough`, `details: {all_boxes_ticked: true,
      conducted_by: <name>, date: <iso>}`).

### Promotion ceremony (after the gate is green)

1. Move `templates/products/drafts/obs-01-agent-vitals.html` →
   `templates/products/obs-01-agent-vitals.html`.
2. Add the slug to `_PRODUCT_SLUGS` in `mvp_blueprints/pages.py`.
3. Add the pricing-page entry and sitemap link.
4. Re-run `test_obs_sku_registry.py::test_no_public_wiring_before_publish`
   expecting the new state, and record a `pricing_live` ledger entry.
