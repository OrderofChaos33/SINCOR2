# SINCOR Product Catalog — Observability & Audit

This folder is the commercial SKU catalog for **product line 02**.

Nothing here is new infrastructure. It packages four existing surfaces as sellable SKUs:

| Component | What it already is | What it sells as |
| --- | --- | --- |
| `enterprise_infrastructure` | Immutable hash-chained audit trail, Ed25519 signing | OBS-02, AUD-01, AUD-02, OBS-ENT |
| `monitoring_dashboard` | Agent health, uptime, cost | OBS-01, OBS-ENT |
| Quality scoring (9-dimension) | Trend and score pipeline | OBS-03, AUD-01, OBS-ENT |
| Drift detection | Persona / vector drift | OBS-03, AUD-01, OBS-ENT |

Product line 01 is the underwriting engine. Every OBS customer generates behavioral data that makes underwriting smarter. The two lines compound.

## SKUs

| Code | Name | Price | Role | Sellable today |
| --- | --- | --- | --- | --- |
| OBS-01 | Agent Vitals | $49/mo per agent | Funnel entry | No — dashboard needs customer-facing polish |
| OBS-02 | Agent Audit Trail | $199/mo per deployment | Margin | Yes |
| OBS-03 | Drift & Quality Watch | $399/mo per workspace | Margin | Yes |
| AUD-01 | Agent Forensic Audit | $499 one-time | Wedge | Yes — **this week's play** |
| AUD-02 | Compliance Pack | $999/mo + $2,500 setup | Revenue | Yes |
| OBS-ENT | Enterprise Mesh | $2,500/mo | Revenue | Yes |

## Pricing logic

- **OBS-01** is the impulse-buy funnel. Lowest friction, easiest first sale — after the dashboard is polished.
- **AUD-01** is the one-time wedge. It converts into OBS subscriptions and feeds underwriting.
- **OBS-02 / OBS-03** are the margin.
- **AUD-02** and **OBS-ENT** are where real revenue lives.

## Honest caveat

SKUs 02 and 03 are sellable today because the components exist. OBS-01 still needs the dashboard polished into a customer-facing view before it sits in front of a paying client. **The wedge play this week is AUD-01.**

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
```

JSON is canonical. Markdown is the sales one-pager.

## Bundle rule

OBS-ENT subsumes OBS-01, OBS-02, OBS-03, and AUD-02. AUD-01 remains a one-time engagement even for ENT accounts.
