# AUD-01 — Agent Forensic Audit (Spec)

**Catalog line:** sku_id=AUD-01 · $499 one-time flat · billing=one_time ·
stage_label=SERVICE · lifecycle=engagement · lifecycle_stage=spec ·
gates=(engagement_playbook, written_findings, delivery_receipt) ·
module=`src/sincor2/obs_skus/forensic_audit.py` (sibling builder B5) ·
draft=`templates/products/drafts/aud-01-agent-forensic-audit.html` (unrouted)

**Standing rule (founder, 2026-09-29):** this SKU's page stays a draft until
the publish gate in §7 passes on verified end-to-end evidence. For a
service SKU, "working end-to-end" means: the engagement has been rehearsed
and delivered to the playbook at least once, with a real findings report
the founder would sign.

**Grounding (verified on disk 2026-09-29):**
- The platform records the event types a forensic review needs: auction
  commit/reveal/close, ghosting slashes, dispute rulings, KYA status
  changes (stake_ledger.py, PR #261, PR #266 — all merged).
- `src/sincor2/defi/proof_ledger.py` (exists): the append-only evidence
  pattern the audit's evidence handling mirrors.
- `docs/ops/AUCTION_SECURITY_DECISIONS.md` (exists): precedent for a
  written, ratified findings document as a work product.
- No forensic-audit tooling or playbook exists yet — sibling builder B5's
  build. Nothing below claims it does.

---

## 1. What/How

Agent Forensic Audit is a one-time service engagement: a SINCOR reviewer
reconstructs what an agent or deployment did, determines what went wrong
(or right), and delivers a written findings report with concrete
remediation steps.

### Step-by-step flow (the engagement)

1. **Intake.** Customer defines the scope: which agent/deployment, which
   time window, and the question to answer (e.g., "why did this agent get
   slashed twice in March?").
2. **Evidence pull.** The reviewer pulls the deployment's trail (OBS-02
   when available; platform records otherwise), stake events, dispute
   records, and KYA history into a case file. Every claim in the final
   report cites a case-file exhibit.
3. **Reconstruction.** Timeline of the incident: what the agent did, what
   the platform did in response, in order, with timestamps.
4. **Findings.** Root-cause analysis distinguishing agent behavior from
   platform behavior from external conditions. Each finding is graded
   (confirmed / likely / inconclusive) — inconclusive is an allowed,
   honest answer.
5. **Remediation.** Concrete, ordered steps: configuration changes,
   playbook changes, or monitoring to add (with OBS-01/OBS-03 as the
   standing recommendation where they fit).
6. **Delivery.** Written findings report + a 30-minute walkthrough call.
   Delivery is recorded with a receipt the customer signs.

### Key artifacts

| Artifact | Location | Role |
|---|---|---|
| engagement playbook | `docs/specs/obs-skus/AUD-01-playbook.md` (new, B5) | the repeatable method |
| `forensic_audit.py` | `src/sincor2/obs_skus/` (new, B5) | case-file tooling, exhibit index |
| findings report template | with the playbook (new, B5) | consistent deliverable |

### Numeric parameter table

| Parameter | Value |
|---|---|
| Price | $499 one-time flat |
| Scope | one agent or deployment, one time window (≤ 90 days) |
| Turnaround | 5 business days from complete intake |
| Deliverable | written findings report + 30-min walkthrough |
| Finding grades | confirmed / likely / inconclusive |

---

## 2. Why

**Who pays:** anyone who just lived through an agent incident — a slash, a
dispute loss, a revoked agent — and needs a straight answer about what
happened. **Why they pay:** reconstructing an incident from raw platform
records takes days and still leaves doubt; a structured forensic review
gives them a citable answer and a fix list. **Why it's the wedge:** a
$499 one-time engagement is the lowest-commitment way for a prospect to
experience SINCOR's rigor — it naturally leads to OBS-02/OBS-03. **Revenue
path:** $499 flat per engagement.

---

## 3. Build Stack

- The playbook (markdown) + case-file tooling (Python) + report template.
- Evidence handling mirrors the proof-ledger pattern: append-only case
  file, every exhibit hashed and indexed.
- No customer data leaves the case file without the customer's written
  direction.

---

## 4. Acceptance criteria

1. The engagement playbook exists at `docs/specs/obs-skus/AUD-01-playbook.md`
   and covers intake, evidence pull, reconstruction, findings grading,
   remediation, and delivery — a reviewer unfamiliar with the case can run
   it.
2. A rehearsal engagement was completed end-to-end against a real staging
   incident (not a synthetic fixture): case file built, timeline
   reconstructed, findings graded, report written — recorded in the proof
   ledger (`kind: rehearsal`, passed).
3. Every finding in the rehearsal report cites at least one case-file
   exhibit (test: parse the report, assert citation coverage = 100%).
4. The report template renders all required sections: scope, timeline,
   findings (graded), remediation (ordered), exhibits index. A template
   test asserts no section is droppable.
5. Turnaround: intake-to-delivery for the rehearsal met the 5-business-day
   SLA (ledger timestamps).
6. Delivery receipt: the rehearsal customer (founder as stand-in) signed
   the receipt; the ledger holds `kind: delivery_receipt` with
   `delivered: true`.
7. Confidentiality: the case file contains no other customer's data; a
   scoping test on the evidence-pull tooling asserts deployment-id
   filtering on every query it issues.

---

## 5. Lifecycle gates

Engagement lifecycle: spec → playbook → rehearsal → delivery → report.
Gate checks (see `src/sincor2/obs_skus/gates.py`):

- **spec → playbook:** spec on file, draft page present, no public-surface
  wiring (`find_public_wiring` empty).
- **playbook → rehearsal:** playbook present on disk.
- **rehearsal → delivery:** passing rehearsal in the proof ledger.
- **delivery → report:** delivery receipt recorded.

---

## 6. What we do not claim

- Not a legal investigation and not admissible-evidence preparation; it is
  an operational forensic review.
- "Inconclusive" is a valid finding — the engagement never manufactures
  certainty to fill a gap.
- One engagement covers one scope; it is not ongoing monitoring (that's
  OBS-03).

---

## 7. Publish gate — end-to-end verification + live walkthrough

The draft page may be linked from nav/pricing/sitemap and the SKU sold
**only** after ALL of the following are recorded in the proof ledger and
`gates.evaluate_publish` returns ok:

### End-to-end verification

- Criteria 1–7 above each have a passing `acceptance_run` ledger entry
  (`details: {criterion: N, passed: true}`).
- The rehearsal report is attached to the ledger entry (or its hash is);
  the founder has read it and would sign it.
- A second reviewer ran the playbook's intake-to-report flow on a
  *different* staging incident and produced a coherent report (proves the
  playbook is repeatable, not reviewer-magic).

### Live walkthrough checklist (founder-conducted, every box ticked)

- [ ] Walk the rehearsal case file: intake form → exhibits → timeline →
      graded findings → remediation list. Every finding traces to an exhibit.
- [ ] Confirm the report states its limits (what was inconclusive) in
      plain language.
- [ ] Confirm the 5-business-day SLA was met with timestamps.
- [ ] Confirm the delivery receipt is signed.
- [ ] Click every CTA on the draft page; each resolves to a working
      destination (`/contact` with the engagement pre-identified).
- [ ] Read the draft page copy aloud; confirm zero banned claims and
      positive framing — especially: no legal-evidence claims, no
      certainty claims beyond the finding grades.
- [ ] Record the walkthrough in the proof ledger
      (`kind: walkthrough`, `details: {all_boxes_ticked: true,
      conducted_by: <name>, date: <iso>}`).

### Promotion ceremony (after the gate is green)

1. Move `templates/products/drafts/aud-01-agent-forensic-audit.html` →
   `templates/products/aud-01-agent-forensic-audit.html`.
2. Add the slug to `_PRODUCT_SLUGS` in `mvp_blueprints/pages.py`.
3. Add the pricing-page entry (service label, contact CTA — never a
   fake "buy now") and sitemap link.
4. Re-run the registry gate tests for the new state; record a
   `pricing_live` ledger entry.
