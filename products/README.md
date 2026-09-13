# SINCOR Product Catalog — Observability & Audit

This folder is the commercial SKU catalog for **product line 02**.

It packages existing audit, monitoring, quality-scoring, and compliance surfaces, and it flags where customer-facing productization is still missing.

| Component | What it already is | What it sells as |
| --- | --- | --- |
| `enterprise_infrastructure` | Ed25519-signed, tamper-evident audit events, compliance reporting | OBS-02, AUD-01, AUD-02, OBS-ENT foundation |
| `monitoring_dashboard` | Internal status, metrics, security, and log-summary endpoints | OBS-01 foundation, OBS-ENT foundation |
| `src/sincor2/quality_scoring_engine.py` | Nine-dimension quality scoring, benchmarks, and agent profiles | OBS-03 foundation, AUD-01 |
| `verticals/healthcare` + `verticals/compliance` | Existing regulated workflows and guardrails | AUD-02, OBS-ENT foundation |

## SKUs

| Code | Name | Price | Role | Sellable today |
| --- | --- | --- | --- | --- |
| OBS-01 | Agent Vitals | $49/mo per agent | Funnel entry | No — dashboard needs customer-facing polish |
| OBS-02 | Agent Audit Trail | $199/mo per deployment | Margin | Yes |
| OBS-03 | Drift & Quality Watch | $399/mo per workspace | Margin | No — production drift-watch code is missing |
| AUD-01 | Agent Forensic Audit | $499 one-time | Wedge | Yes — **this week's play** |
| AUD-02 | Compliance Pack | $999/mo + $2,500 setup | Revenue | Yes |
| OBS-ENT | Enterprise Mesh | $2,500/mo | Revenue | No — bundled fleet surfaces are not productized yet |

## Pricing logic

- **OBS-01** is the impulse-buy funnel once the dashboard is polished.
- **AUD-01** is the one-time wedge. It converts into recurring audit/compliance work and feeds underwriting.
- **OBS-02** is the current sellable recurring margin.
- **OBS-03** is the planned quality/drift watch once a production drift module exists.
- **AUD-02** is current revenue.
- **OBS-ENT** is the planned enterprise bundle after the included surfaces are customer-ready.

## Honest caveat

**OBS-02, AUD-01, and AUD-02 are sellable today.** OBS-01 still needs the dashboard polished into a customer-facing view, OBS-03 still needs a real drift-watch implementation, and OBS-ENT should wait until those bundled surfaces are customer-ready. **The wedge play this week is AUD-01.**

## File layout

```
products/
  README.md              this file
  catalog.json           machine-readable catalog (source of truth)
  schema.json            JSON Schema for a SKU object
  PRICING.md             pricing logic, units, bundle rules
  FUNNEL.md              conversion map and this-week play
  skus/
    OBS-01.json / .md
    OBS-02.json / .md
    OBS-03.json / .md
    AUD-01.json / .md
    AUD-02.json / .md
    OBS-ENT.json / .md
PRODUCTS/
  <SKU>/
    SPEC.md              detailed product spec sheet
    MEDIA.md             marketing and messaging guardrails
    COMPLIANCE.md        claims, evidence, and constraints
    SALES.md             pricing, qualification, and sales notes
```

JSON is canonical. Markdown is the sales one-pager.
