# x402 Paid-Surface Checklist

**Origin:** dev-watch item 262 — the aiskills402 audit of 10 CDP Bazaar
storefronts found the same five defects repeating across sellers:

| # | Finding | Share |
|---|---------|-------|
| 1 | 402s served **without** `cache-control: no-store` | 7/9 |
| 2 | `PAYMENT-REQUIRED` header **differs** from the body envelope | 5/9 |
| 3 | header **not exposed** via CORS (`Access-Control-Expose-Headers`) | 4/9 |
| 4 | **CORS preflight fails** on paid routes | 4/9 |
| 5 | undeclared methods answered with **HTML** (or 404) | 2/9 |

A cached 402, a header/body mismatch, or a preflight failure each turns a
paying buyer into a failed sale — or worse, a buyer who pays against a stale
challenge. Run this checklist **before ANY Bazaar listing** and on every
change to a paid route.

## The checklist

### R1 — 402 MUST carry `Cache-Control: no-store`
A 402 is a per-challenge, per-payer, expiring offer. If any cache (CDN,
browser, proxy) replays it, the buyer signs against a dead challenge and the
payment fails or lands on the wrong terms. Every 402 response MUST include
the `no-store` directive. `no-cache` alone is not enough.

### R2 — `PAYMENT-REQUIRED` header, decoded, MUST deep-equal body `accepts`
Buyers read the challenge from the header OR the body (our own buyer,
`src/sincor2/x402_buyer.py`, tries body JSON then the header). If the two
disagree, the buyer may authorize different terms than the server enforces.
The 402 MUST emit a `PAYMENT-REQUIRED` header (raw JSON, base64 JSON, or
base64url JSON) whose decoded value deep-equals the body's `accepts`
envelope — same scheme, network, asset, amount, payTo, and challenge binding.

### R3 — CORS MUST expose `PAYMENT-REQUIRED` and allow payment headers
Browser buyers are cross-origin by definition. The 402 response MUST list
`PAYMENT-REQUIRED` in `Access-Control-Expose-Headers`, and an `OPTIONS`
preflight against every paid route MUST return 2xx with
`Access-Control-Allow-Origin` plus `Access-Control-Allow-Headers` covering
the payment headers the client sends (`X-Payment-Token`, `X-Payment`).

### R4 — undeclared methods return 405, never 404/HTML
An undeclared method on a paid route MUST return `405 Method Not Allowed`
with a machine-readable JSON body. Never 404 (which tells scanners the
route doesn't exist and invites method-guessing) and never an HTML error
page (which breaks JSON buyers and leaks server fingerprinting).

### R5 — run x402-doctor or the CDP validate API before listing
Wire rules R1–R4 are necessary, not sufficient. Before a listing goes live,
run the external validator (x402-doctor or the CDP facilitator validate
endpoint) against the public URL and fix everything it flags. Keep the
validator output with the listing record.

## Pre-listing runbook

1. `pytest tests/pytest/test_x402_paid_surface_probe.py -q` — all green,
   no unexpected xfails.
2. `curl -sSI` the 402 for each paid route; confirm `Cache-Control: no-store`,
   `PAYMENT-REQUIRED`, and `Access-Control-Expose-Headers` by hand.
3. `curl -s -X OPTIONS` each paid route with `Origin` +
   `Access-Control-Request-Headers: X-Payment-Token`; confirm 2xx + allow headers.
4. `curl -s -X DELETE` each paid route; confirm 405 + JSON body.
5. Run x402-doctor / CDP validate against the public URL; attach output.

## Our paid surface (as of this writing)

| Method | Route | 402 source |
|--------|-------|-----------|
| GET | `/x402/<resource_id>` | `create_challenge()` — unbound draft (`ownership_proof_required`) |
| POST | `/api/x402/challenge-bind` | `bind_challenge_payer()` — after valid ownership signature (`payment_required`) |
| POST | `/api/x402/verify` | `verify_challenge()` — while the treasury transfer is unobserved (`tx_pending`, …) |
| GET | `/api/paid/<resource_id>` | `create_challenge()` — no access token presented |
| POST | `/api/paid/<resource_id>` | `create_challenge()` — no access token presented |

Route code: `src/sincor2/mvp_blueprints/billing.py`. Challenge logic:
`src/sincor2/x402_payments.py`. Standing probe:
`tests/pytest/test_x402_paid_surface_probe.py` — runs every rule against
every declared method above, plus negative controls.

## Current findings (probe-reported, not yet fixed)

Scope of the probe commit is probe + this doc (no route changes), so these
are recorded as `xfail` markers in the probe, not fixed:

- **FINDING-1 (R1):** 402s carry no `Cache-Control` header —
  `jsonify(ch), 402` in `billing.py` sets none.
- **FINDING-2 (R2):** no `PAYMENT-REQUIRED` header is emitted on 402s, and
  the body envelope lives under `payment`, not the x402-v2 `accepts` key.
- **FINDING-3 (R3):** no CORS headers on the billing blueprint — 402s don't
  expose `PAYMENT-REQUIRED` and `OPTIONS` preflight returns no
  `Access-Control-Allow-*` headers.
- **FINDING-4 (R4):** undeclared methods get Werkzeug's default HTML 405
  page — there is no 405 errorhandler returning JSON on the blueprint.

Fixing any finding = a route/middleware change (out of scope here); when
fixed, delete the corresponding `xfail` marker in the probe — the assertion
itself must not be weakened.

## Adjacent notes

- `GET /admin/training-vault` can return a **402 with an HTML body**
  (`_vault_denied_response` in `billing.py`) for pending orders. That is the
  legacy training-vault flow, not the x402 protocol surface — but it is an
  HTML-402 in the codebase and should be JSON or moved off 402 when the
  vault flow is next touched.
- `GET /api/x402/resources` is informational (200/503), not a 402 issuer;
  it is outside this checklist.
