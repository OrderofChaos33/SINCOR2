# OBS-01 — Agent Vitals

**$49/mo per agent** · Funnel entry · Not sellable until the dashboard is customer-facing

> Is the service up, and what shape is it in.

The impulse-buy tier packages the existing `monitoring_dashboard` status, metrics, security, and log-summary endpoints. Lowest friction once that internal surface becomes a buyer-facing dashboard.

## What exists today

- Service status and uptime
- CPU, memory, disk, process, and platform metrics
- Security feature snapshot
- Log summary when logging is available

## What does **not** exist today

- Buyer-ready per-agent cost attribution
- Token or tool-call budget controls in this surface
- Customer alert webhooks

## Source

`src/sincor2/monitoring_dashboard.py`

## Converts to

OBS-02 · AUD-01

Feeds the underwriting engine.
