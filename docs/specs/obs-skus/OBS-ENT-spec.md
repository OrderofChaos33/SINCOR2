# OBS-ENT — Enterprise Mesh (Spec)

**Catalog line:** sku_id=OBS-ENT · $2,500/mo · billing=subscription ·
stage_label=SERVICE · lifecycle=engagement · lifecycle_stage=spec ·
gates=(engagement_playbook, onboarding_runbook, response_sla) ·
module=`src/sincor2/obs_skus/enterprise_mesh.py` (sibling builder B6) ·
draft=`templates/products/drafts/obs-ent-enterprise-mesh.html` (unrouted)

**Standing rule (founder, 2026-09-29):** this SKU's page stays a draft until
the publish gate in §7 passes on verified end-to-end evidence. For a
service SKU, "working end-to-end" means: the onboarding runbook has been
rehearsed on a real multi-deployment setup, one full operating cycle has
been delivered to the playbook, and the response SLAs have been met on the
record.

**Grounding (verified on disk 2026-09-29):**
- The fleet primitives exist and are verified: agent cards are surfaced
  (`marketplace/agent_cards.json` registry exists; PR #256), the MCP
  discovery server advertises `/.well-known/sincor-marketplace.json`
  (`src/sincor2/mcp_server.py:10`), and the login-gated dashboards menu is
  live (`src/sincor2/mvp_blueprints/pages.py:452`).
- The operational primitives the mesh coordinates are real: KYA
  `live_statuses()`, stake ledger, registration-velocity, dispute flow.
- No enterprise-mesh runbook, tooling, or operator role exists yet —
  sibling builder B6's build. Nothing below claims they do.

---

## 1. What/How

Enterprise Mesh is fleet-grade operations for customers running many
deployments: a unified onboarding runbook, cross-deployment visibility,
and a named SINCOR operator with defined response times.

### Step-by-step flow (the engagement)

1. **Onboarding.** The runbook onboards each deployment the same way:
   KYA verification confirmed, stake posture reviewed, audit trail
   enabled, drift watch configured, alert routing tested end-to-end
   (an alert is fired and received before sign-off).
2. **Mesh view.** The operator maintains one operating picture across the
   customer's deployments: per-deployment status rollups, open alerts,
   open disputes, upcoming stake timelocks. Built from the same signals
   as OBS-01/02/03 — the mesh is the human operating layer over them.
3. **Operating cycle.** Weekly: operator reviews the mesh view with the
   customer (30 min), confirms alert routing still works, and logs
   deployment health notes. Monthly: written operations summary.
4. **Incident response.** Defined response times (see table). The operator
   triages: acknowledges, pulls the audit trail, opens a forensic audit
   (AUD-01) when warranted, and stays on the incident through resolution.
5. **Change coordination.** Platform changes affecting the customer's
   deployments are communicated before they take effect, with action
   items where the customer must do something.

### Key artifacts

| Artifact | Location | Role |
|---|---|---|
| engagement playbook | `docs/specs/obs-skus/OBS-ENT-playbook.md` (new, B6) | the operating method |
| onboarding runbook | with the playbook (new, B6) | repeatable per-deployment onboarding |
| `enterprise_mesh.py` | `src/sincor2/obs_skus/` (new, B6) | mesh-view tooling, SLA tracking |

### Numeric parameter table

| Parameter | Value |
|---|---|
| Price | $2,500/mo |
| Onboarding | per deployment, runbook-driven, alert-routing proven before sign-off |
| Operating cycle | weekly 30-min review + monthly written summary |
| Response SLA | critical (funds/safety at risk): 1h · standard: 1 business day |
| Named operator | one accountable SINCOR operator per account |

---

## 2. Why

**Who pays:** enterprises running agent fleets where "someone's watching"
needs to be a named person with a runbook, not a hope. **Why they pay:**
multi-deployment operations rot into tribal knowledge and missed alerts;
the mesh makes it a service with SLAs. **Revenue path:** $2,500/mo
recurring, per customer account.

---

## 3. Build Stack

- The playbook + onboarding runbook (markdown) + mesh-view/SLA tooling
  (Python).
- Read-only over the same platform signals as the OBS software SKUs; the
  mesh adds the human operating layer, not a parallel data pipeline.

---

## 4. Acceptance criteria

1. The engagement playbook exists at
   `docs/specs/obs-skus/OBS-ENT-playbook.md` and covers onboarding,
   mesh view, operating cycle, incident response, and change coordination.
2. The onboarding runbook exists and is ordered: KYA → stake posture →
   audit trail → drift watch → alert-routing proof. A runbook test
   asserts no step is skippable (each step's sign-off requires the
   previous step's evidence).
3. A rehearsal onboarding was completed on a staging account with ≥ 3
   deployments: every deployment reached runbook sign-off, including the
   fired-and-received alert-routing proof — recorded in the proof ledger
   (`kind: rehearsal`, passed).
4. Mesh view: the rehearsal's operating picture showed all 3 deployments'
   status rollups, open alerts, and open disputes on one screen (screenshot
   or render test attached to the ledger entry).
5. Response SLA: two rehearsal incidents (one critical, one standard)
   were acknowledged within 1h and 1 business day respectively (ledger
   timestamps prove it).
6. Operating cycle: one weekly review and one monthly summary were
   delivered in the rehearsal (both attached or hashed in the ledger).
7. Delivery receipt: the rehearsal customer (founder as stand-in) signed
   the monthly operations summary; the ledger holds
   `kind: delivery_receipt` with `delivered: true`.

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

- Not a 24/7 NOC; response times are as tabled, and the SLA covers
  acknowledgment + triage, not instant resolution.
- The operator advises and coordinates; the customer still owns their
  deployments and keys.
- No exclusivity claims about operator capacity; accounts are staffed as
  sold.

---

## 7. Publish gate — end-to-end verification + live walkthrough

The draft page may be linked from nav/pricing/sitemap and the SKU sold
**only** after ALL of the following are recorded in the proof ledger and
`gates.evaluate_publish` returns ok:

### End-to-end verification

- Criteria 1–7 above each have a passing `acceptance_run` ledger entry
  (`details: {criterion: N, passed: true}`).
- The rehearsal onboarding sign-offs (all 3 deployments), the mesh-view
  render, and the monthly summary are attached (or hashed) to the ledger;
  the founder has read them.
- A second operator ran the onboarding runbook on a different staging
  account and reached sign-off on all deployments (repeatability proof).

### Live walkthrough checklist (founder-conducted, every box ticked)

- [ ] Walk the rehearsal onboarding: 3 deployments, each step signed off
      in order, alert-routing proof fired and received for each.
- [ ] Open the mesh view: confirm all deployments, open alerts, and open
      disputes on one screen.
- [ ] Confirm both rehearsal incidents met their SLA timestamps.
- [ ] Confirm the monthly summary is signed.
- [ ] Click every CTA on the draft page; each resolves to a working
      destination (`/contact` with the engagement pre-identified).
- [ ] Read the draft page copy aloud; confirm zero banned claims and
      positive framing — especially: no 24/7 claims, no instant-resolution
      claims.
- [ ] Record the walkthrough in the proof ledger
      (`kind: walkthrough`, `details: {all_boxes_ticked: true,
      conducted_by: <name>, date: <iso>}`).

### Promotion ceremony (after the gate is green)

1. Move `templates/products/drafts/obs-ent-enterprise-mesh.html` →
   `templates/products/obs-ent-enterprise-mesh.html`.
2. Add the slug to `_PRODUCT_SLUGS` in `mvp_blueprints/pages.py`.
3. Add the pricing-page entry (service label, contact CTA — never a
   fake "buy now") and sitemap link.
4. Re-run the registry gate tests for the new state; record a
   `pricing_live` ledger entry.
