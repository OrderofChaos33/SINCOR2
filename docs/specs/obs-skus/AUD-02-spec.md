# AUD-02 — Compliance Pack (Spec)

**Catalog line:** sku_id=AUD-02 · $999/mo + $2,500 setup ·
billing=subscription · stage_label=SERVICE · lifecycle=engagement ·
lifecycle_stage=spec · gates=(engagement_playbook, evidence_templates,
monthly_cadence) · module=`src/sincor2/obs_skus/compliance_pack.py`
(sibling builder B5) · draft=`templates/products/drafts/aud-02-compliance-pack.html`
(unrouted)

**Standing rule (founder, 2026-09-29):** this SKU's page stays a draft until
the publish gate in §7 passes on verified end-to-end evidence. For a
service SKU, "working end-to-end" means: the onboarding + one full monthly
review cycle have been rehearsed and delivered to the playbook, with
evidence templates the founder would hand to a real reviewer.

**Grounding (verified on disk 2026-09-29):**
- `src/sincor2/compliance_guardrails.py` and
  `src/sincor2/compliance_monitor.py` exist — the platform's own compliance
  machinery, which the pack's evidence templates map to (not replace).
- PR #261's adjudicator-gated slashing and PR #266's earned-only
  reputation are the control points a compliance reviewer will ask about;
  the pack documents them as they are.
- No compliance-pack playbook, evidence templates, or tooling exist yet —
  sibling builder B5's build. Nothing below claims they do.

---

## 1. What/How

Compliance Pack is an ongoing service engagement for regulated
deployments: SINCOR maps your controls to platform evidence, builds you a
standing evidence pack, and runs a monthly review cadence with a reviewer
who knows your stack.

### Step-by-step flow (the engagement)

1. **Setup ($2,500).** Scoping workshop: which regulations/controls apply,
   which deployments are in scope, who the customer's reviewer is. Output:
   a control→evidence map — each control points at the platform record
   that satisfies it (audit trail exports, KYA records, dispute rulings,
   stake-ledger entries).
2. **Evidence templates.** For each control, a template showing exactly
   what the monthly evidence looks like and where it comes from. Gaps —
   controls with no platform evidence — are named explicitly with a
   manual-collection procedure, never papered over.
3. **Monthly cadence ($999/mo).** Each month the reviewer assembles the
   evidence pack, walks it with the customer, and logs control status:
   satisfied / gap-open / gap-closed. The pack is cumulative — month N
   includes the delta from month N−1.
4. **Change watch.** Platform changes that affect mapped controls (new
   slashing rule, new KYA field) are flagged to the customer before they
   take effect, with the evidence-map updated.
5. **Standing reviewer.** One named SINCOR reviewer owns the account;
   handoff notes exist so a substitute can run the cadence without loss.

### Key artifacts

| Artifact | Location | Role |
|---|---|---|
| engagement playbook | `docs/specs/obs-skus/AUD-02-playbook.md` (new, B5) | setup + monthly method |
| `compliance_pack.py` | `src/sincor2/obs_skus/` (new, B5) | evidence-map tooling, pack assembly |
| evidence templates | with the playbook (new, B5) | per-control evidence format |

### Numeric parameter table

| Parameter | Value |
|---|---|
| Setup | $2,500 one-time |
| Monthly | $999/mo |
| Cadence | monthly review, pack delivered by the 10th |
| Change-watch notice | before the affecting change takes effect |
| Gap SLA | named gaps get a manual-collection procedure at setup |

---

## 2. Why

**Who pays:** teams deploying agents under regulatory scrutiny who need a
standing answer to "show me your controls." **Why they pay:** mapping
controls to evidence and re-assembling the pack every month is specialist
work that rots when it's nobody's job; the pack makes it somebody's job.
**Revenue path:** $2,500 setup + $999/mo recurring.

---

## 3. Build Stack

- The playbook (markdown) + evidence-map tooling (Python) + templates.
- Evidence is pulled from platform records (audit trail, KYA,
  stake ledger, dispute log) — the pack references records, it does not
  duplicate them into a second source of truth.

---

## 4. Acceptance criteria

1. The engagement playbook exists at `docs/specs/obs-skus/AUD-02-playbook.md`
   and covers scoping workshop, control→evidence mapping, template
   creation, monthly cadence, change watch, and reviewer handoff.
2. A rehearsal setup was completed for a staging deployment: a
   control→evidence map with ≥ 10 controls, each pointing at a real
   platform record or an explicitly named gap — recorded in the proof
   ledger (`kind: rehearsal`, passed).
3. Gap honesty: every control with no platform evidence has a named gap
   and a manual-collection procedure in the rehearsal map (test: parse the
   map, assert zero controls silently unmapped).
4. Evidence templates render for all mapped controls; a template test
   asserts each template names its source record and its refresh cadence.
5. Monthly pack assembly: the rehearsal produced two consecutive monthly
   packs; pack N+1 contains the delta from pack N (test asserts the delta
   section lists exactly the changed control statuses).
6. Change watch: a simulated platform change (new KYA field) was flagged
   to the rehearsal customer with the evidence-map update before the
   change's effective date (ledger timestamps prove ordering).
7. Delivery receipt: the rehearsal customer (founder as stand-in) signed
   the monthly-pack receipt; the ledger holds `kind: delivery_receipt`
   with `delivered: true`.

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

- Not a certification, not a legal opinion, not an auditor's sign-off.
  The pack is evidence assembly and review support — the customer's
  auditor still audits.
- No claim about which regulations the pack "covers" in the abstract;
  coverage is per-engagement, written in the control map.
- Gaps are named, never hidden; a pack with open gaps is still a complete
  pack.

---

## 7. Publish gate — end-to-end verification + live walkthrough

The draft page may be linked from nav/pricing/sitemap and the SKU sold
**only** after ALL of the following are recorded in the proof ledger and
`gates.evaluate_publish` returns ok:

### End-to-end verification

- Criteria 1–7 above each have a passing `acceptance_run` ledger entry
  (`details: {criterion: N, passed: true}`).
- The rehearsal control map and two monthly packs are attached (or hashed)
  to the ledger; the founder has read them and would hand them to a real
  reviewer.
- A second reviewer ran the playbook's setup flow on a different staging
  deployment and produced a coherent control map (repeatability proof).

### Live walkthrough checklist (founder-conducted, every box ticked)

- [ ] Walk the rehearsal control map: 10+ controls, each traced to a
      platform record or a named gap with a manual procedure.
- [ ] Open both monthly packs; confirm pack 2's delta section matches the
      actual changed statuses.
- [ ] Confirm the change-watch flag arrived before the simulated change's
      effective date.
- [ ] Confirm the delivery receipt is signed.
- [ ] Click every CTA on the draft page; each resolves to a working
      destination (`/contact` with the engagement pre-identified).
- [ ] Read the draft page copy aloud; confirm zero banned claims and
      positive framing — especially: no certification claims, no legal-opinion
      claims, no abstract regulation-coverage claims.
- [ ] Record the walkthrough in the proof ledger
      (`kind: walkthrough`, `details: {all_boxes_ticked: true,
      conducted_by: <name>, date: <iso>}`).

### Promotion ceremony (after the gate is green)

1. Move `templates/products/drafts/aud-02-compliance-pack.html` →
   `templates/products/aud-02-compliance-pack.html`.
2. Add the slug to `_PRODUCT_SLUGS` in `mvp_blueprints/pages.py`.
3. Add the pricing-page entry (service label, contact CTA — never a
   fake "buy now") and sitemap link.
4. Re-run the registry gate tests for the new state; record a
   `pricing_live` ledger entry.
