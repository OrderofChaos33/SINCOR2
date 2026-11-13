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

## Current findings — all FIXED (branch `xioix/x402-paid-surface`)

FINDING-1 through FINDING-4 were fixed in the R1–R4 fix commit on branch
`xioix/x402-paid-surface` (one `after_request` hook + one app-wide 405
handler in `billing.py`); the probe's `xfail` markers were deleted and the
probe runs 39/39 green with its assertions unchanged:

- **FINDING-1 (R1) — FIXED:** every 402 from the billing blueprint now
  carries `Cache-Control: no-store`.
- **FINDING-2 (R2) — FIXED:** challenge 402s mirror the payment envelope
  into the body `accepts` key and emit a `PAYMENT-REQUIRED` header
  (base64 JSON) that deep-equals it; verify 402s (no envelope of their
  own) have theirs rebuilt read-only from the pending challenge.
- **FINDING-3 (R3) — FIXED:** 402s expose `PAYMENT-REQUIRED` via
  `Access-Control-Expose-Headers`, and `OPTIONS` preflight echoes the
  caller's `Origin` and allows the payment headers. **Adversarial-review
  follow-ups (same branch):** the origin reflection was initially added
  blueprint-wide; review finding **F2** narrowed the preflight
  reflection to the paid routes only (`/x402/`, `/api/x402/`,
  `/api/paid/`) — non-paid billing routes (payment/paypal webhooks,
  orders, platform checkout) now get NO CORS reflection — and review
  finding **F4b** added `Access-Control-Allow-Origin` echo plus
  `Vary: Origin` on paid-route 402 responses themselves, since
  Expose-Headers alone still left a cross-origin browser buyer unable
  to read the 402. No `Access-Control-Allow-Credentials` is ever set.
- **FINDING-4 (R4) — FIXED:** undeclared methods return a JSON 405 from
  an app-wide errorhandler. Review finding **F1** completed this fix:
  the JSON handler initially dropped the `Allow` header that
  RFC 9110 §15.5.6 requires on a 405 (stock Flask sends one); it now
  restores `Allow` from the exception's `valid_methods`.

The review-round fixes are pinned by
`tests/pytest/test_x402_paid_surface_fixes.py`.

## Adjacent notes

- `GET /admin/training-vault` can return a **402 with an HTML body**
  (`_vault_denied_response` in `billing.py`) for pending orders. That is the
  legacy training-vault flow, not the x402 protocol surface — but it is an
  HTML-402 in the codebase and should be JSON or moved off 402 when the
  vault flow is next touched.
- `GET /api/x402/resources` is informational (200/503), not a 402 issuer;
  it is outside this checklist.
