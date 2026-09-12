# Pricing logic

OBS-01 is the funnel entry at impulse-buy price. AUD-01 is the one-time wedge that converts. OBS-02 and OBS-03 are the margin. AUD-02 and OBS-ENT are where real revenue lives.

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

OBS-ENT includes OBS-01, OBS-02, OBS-03, and AUD-02. Do not double-charge those SKUs on an ENT contract.

AUD-01 stays a one-time engagement. ENT accounts still buy forensic audits when they want a deep-dive on a specific agent.

## Quote formula

```
monthly = 0
setup = 0
one_time = 0

if ENT:
  monthly += 2500
else:
  monthly += 49 * agent_count            # OBS-01
  monthly += 199 * deployment_count      # OBS-02
  if OBS-03: monthly += 399
  if AUD-02:
    monthly += 999
    setup += 2500

if AUD-01: one_time += 499 * engagement_count

year_one = monthly * 12 + setup + one_time
```

OBS-01 is listed in quotes today with a `needs_polish` flag. Do not put it in front of a paying client until the dashboard is a customer-facing view.

## Compounding

Every OBS customer generates behavioral data — cost curves, drift, quality scores, failure modes — that trains the underwriting engine (product line 01). Observability revenue is cash. Observability telemetry is underwriting edge.
