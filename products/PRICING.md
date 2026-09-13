# Pricing logic

OBS-01 is the funnel entry once the dashboard is customer-facing. AUD-01 is the one-time wedge that converts. OBS-02 is the sellable recurring margin today. AUD-02 is current revenue. OBS-03 and OBS-ENT remain gated until their blockers clear.

## Units

| SKU | Amount | Unit | Cadence |
| --- | --- | --- | --- |
| OBS-01 | $49 | per agent | monthly |
| OBS-02 | $199 | per deployment | monthly |
| OBS-03 | $399 | per workspace | monthly |
| AUD-01 | $499 | per engagement (one agent, deep-dive) | one-time |
| AUD-02 | $999 + $2,500 setup | per organization | monthly + setup |
| OBS-ENT | $2,500 | per organization (fleet) | monthly |

## Bundle

When OBS-ENT is sellable, its current foundation is OBS-02 and AUD-02. Do not double-charge bundled SKUs on that contract. OBS-01 and OBS-03 stay opt-in only after their own readiness blockers clear.

AUD-01 stays a one-time engagement. Enterprise accounts still buy forensic audits when they want a deep-dive on a specific agent.

## Quote formula

```
monthly = 0
setup = 0
one_time = 0

if OBS_ENT and OBS_ENT_ready:
  monthly += 2500
else:
  if OBS_01 and OBS_01_ready:
    monthly += 49 * agent_count          # OBS-01
  monthly += 199 * deployment_count      # OBS-02
  if OBS_03 and OBS_03_ready:
    monthly += 399                       # OBS-03
  if AUD_02 and AUD_02_ready:
    monthly += 999
    setup += 2500

if AUD_01:
  one_time += 499 * engagement_count

year_one = monthly * 12 + setup + one_time
```

OBS-01 and OBS-03 should not appear in a live quote until their readiness blockers clear. OBS-ENT should not be quoted until the bundled fleet surface is productized.

## Compounding

Every OBS/AUD customer generates behavioral data — quality signals, failure evidence, operator needs — that trains the underwriting engine (product line 01). Observability revenue is cash. Observability telemetry is underwriting edge.
