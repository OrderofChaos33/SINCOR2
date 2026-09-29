# OBS-02 — Agent Audit Trail (Spec)

**Catalog line:** sku_id=OBS-02 · $199/mo per deployment ·
billing=subscription · stage_label=SELLABLE · lifecycle=software ·
lifecycle_stage=spec · gates=(append_only, per_deployment_scoping,
exportable_evidence) · module=`src/sincor2/obs_skus/audit_trail.py`
(sibling builder B3) · draft=`templates/products/drafts/obs-02-agent-audit-trail.html`
(unrouted)

**Standing rule (founder, 2026-09-29):** this SKU's page stays a draft until
the publish gate in §7 passes on verified end-to-end evidence.

**Grounding (verified on disk 2026-09-29):**
- `src/sincor2/defi/proof_ledger.py` — the append-only evidence pattern
  this SKU mirrors: entries are never edited or deleted; a correction is a
  new entry; atomic tmp+replace writes.
- The agent-audit module (`audit_trail.py`) does **not** exist yet — it is
  sibling builder B3's build. The Ed25519/hash-chained audit stack is also
  in sibling hands. Nothing below claims either exists.
- Dispute/adjudication evidence exists on-platform (auction close
  tombstones ghosts; disputes halve reputation — PR #266), which is the
  kind of event stream an audit trail must record faithfully.

---

## 1. What/How

Agent Audit Trail keeps a tamper-evident, append-only record of what a
deployment's agents did and when — searchable by agent, action, and time
window, with exportable evidence for reviews and disputes.

### Step-by-step flow

1. **Capture.** `audit_trail.py` records lifecycle events for a
   deployment: task posted/completed, bid committed/revealed, auction
   closed, stake slashed, dispute opened/ruled, KYA status changed. Each
   entry carries: deployment id, agent id, event type, timestamp, and an
   event payload hash.
2. **Chain.** Each entry commits to the previous entry's hash (hash chain),
   so deletion or reordering is detectable by re-verification. (The
   Ed25519 signing layer lands with the sibling stack; v1 ships the chain
   with verification tooling.)
3. **Query.** `query(deployment_id, agent_id=None, event_type=None,
   since=None, until=None)` returns matching entries in chain order with
   pagination. Reads are scoped to the requesting customer's deployments.
4. **Verify.** `verify_chain(deployment_id)` replays the chain and reports
   intact / broken-at-entry-N. A broken chain is surfaced, never silently
   repaired.
5. **Export.** `export(deployment_id, format)` produces a portable evidence
   bundle (JSON + verification report) a customer can hand to a reviewer.

### Key modules

| Component | Location | Role |
|---|---|---|
| `audit_trail.py` | `src/sincor2/obs_skus/` (new, B3) | capture, chain, query, verify, export |
| proof ledger pattern | `src/sincor2/defi/proof_ledger.py` (exists) | append-only precedent |
| signing layer | sibling stack (not yet) | Ed25519 entry signatures |

### Numeric parameter table

| Parameter | Value |
|---|---|
| Price | $199/mo per deployment |
| Retention | 13 months, then export-or-purge with customer notice |
| Query page size | 100 entries (cursor pagination) |
| Export formats | JSON + verification report |

---

## 2. Why

**Who pays:** teams running agent deployments who answer for what their
agents did — to customers, partners, or adjudicators. **Why they pay:**
reconstructing "what happened" from scattered logs during a dispute is
slow and untrustworthy; an append-only, verifiable trail makes the answer
a query instead of an investigation. **Revenue path:** $199/mo per
deployment under audit.

---

## 3. Build Stack

- Python; JSON-lines or SQLite-backed chain store under the durable data
  dir (`sincor2.data_paths.data_dir()`, per PR #295).
- Hash chain: SHA-256 over canonical entry encoding (stdlib `hashlib`).
- Tests in `tests/pytest/` (B3's suite) + this registry's gate tests.

---

## 4. Acceptance criteria

1. Append-only: after 1,000 recorded events, an attempt to modify or
   delete entry #500 is either refused by the API or detected by
   `verify_chain` (test asserts detection, not prevention, at the store
   layer; prevention at the API layer).
2. Chain integrity: `verify_chain` returns intact on an unmodified chain
   and `broken-at-entry-N` (exact N) after a single-byte tamper at entry N.
3. Query correctness: a query filtered by agent + event_type + time window
   returns exactly the matching entries in chain order (fixture with 500
   mixed events).
4. Per-deployment scoping: `query()` for another customer's deployment
   returns empty; a cross-customer fixture test asserts zero leakage.
5. Every recorded entry carries deployment id, agent id, event type,
   timestamp, payload hash, and prev-hash; a schema test rejects entries
   missing any field.
6. Export: `export()` produces a bundle whose verification report
   independently re-verifies (replay the exported JSON through
   `verify_chain` logic → intact).
7. Pagination: querying 250 entries with page size 100 returns 3 pages
   with no duplicates and no gaps (cursor test).
8. Retention: entries older than 13 months are flagged export-or-purge and
   never silently deleted (test asserts the purge path requires an explicit
   customer-confirmed export receipt).

---

## 5. Lifecycle gates

Software lifecycle: spec → build → test → audit → product → catalog.
Gate checks (see `src/sincor2/obs_skus/gates.py`):

- **spec → build:** spec on file, draft page present, no public-surface
  wiring (`find_public_wiring` empty).
- **build → test:** `audit_trail.py` present; unit tests passing in proof
  ledger.
- **test → audit:** audit report recorded, 0 open criticals. (The audit
  must cover the chain-verification math and the scoping logic.)
- **audit → product / product → catalog:** still no public wiring until the
  publish gate passes.

---

## 6. What we do not claim

- The Ed25519/hash-chained audit stack is being built by sibling builders;
  until it lands, entries are hash-chained but not signed — the spec and
  the draft page say so plainly.
- No real-time streaming export, no SIEM integration in v1.
- No legal evidentiary guarantees; the trail is evidence, not a ruling.

---

## 7. Publish gate — end-to-end verification + live walkthrough

The draft page may be linked from nav/pricing/sitemap and the SKU sold
**only** after ALL of the following are recorded in the proof ledger and
`gates.evaluate_publish` returns ok:

### End-to-end verification (tests)

- Criteria 1–8 above each have a passing `acceptance_run` ledger entry
  (`details: {criterion: N, passed: true}`), recorded against the built
  `audit_trail.py`. Criteria 1, 2, 4, 6 run against the staging deployment
  with a live deployment's event stream (not fixtures alone).
- Tamper drill on staging: flip one byte in the staging chain store;
  `verify_chain` reports the exact entry; the incident is logged.
- The full existing pytest suite stays green (no regressions).

### Live walkthrough checklist (founder-conducted, every box ticked)

- [ ] On staging, run a real auction cycle (post → bid → close) and confirm
      each step appears in the trail within one query refresh.
- [ ] Tamper with one staging entry; confirm `verify_chain` reports
      broken-at-entry-N and the UI surfaces it (not silent).
- [ ] Export a deployment's trail; hand the JSON to a second machine and
      re-verify intact there.
- [ ] As a second customer, query the first deployment; confirm empty.
- [ ] Click every CTA on the draft page; each resolves to a working
      destination (`/signup?plan=obs-02-agent-audit-trail` pre-fills).
- [ ] Read the draft page copy aloud; confirm zero banned claims and
      positive framing throughout — especially: no claim that entries are
      signed until the sibling signing stack lands.
- [ ] Record the walkthrough in the proof ledger
      (`kind: walkthrough`, `details: {all_boxes_ticked: true,
      conducted_by: <name>, date: <iso>}`).

### Promotion ceremony (after the gate is green)

1. Move `templates/products/drafts/obs-02-agent-audit-trail.html` →
   `templates/products/obs-02-agent-audit-trail.html`.
2. Add the slug to `_PRODUCT_SLUGS` in `mvp_blueprints/pages.py`.
3. Add the pricing-page entry and sitemap link.
4. Re-run the registry gate tests for the new state; record a
   `pricing_live` ledger entry.
