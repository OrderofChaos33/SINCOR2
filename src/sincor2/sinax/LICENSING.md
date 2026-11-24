# SINAX — License Pack (P14)

**Product:** SINAX Geometric Proof Navigator — SINCOR's 26-module geometric proof-navigation library (axiom solving, search, retrieval, graph store).
**Price:** **$1,149 per developer seat per year** (catalog list price, 2026-11-16 research). **Payment rail:** the live PayPal link for P14 — `https://www.paypal.com/ncp/payment/FSLMYAAVJ84QC`.
**Status:** This pack is the delivery machinery built 2026-11-23 on `xioix/autonomous-p14-sinax-licensing`. It becomes sellable when a licensee signs; the terms below are the operational pack — final legal review of the terms text is a founder/legal step before the first sale, and nothing here is legal advice.

## What a seat includes

One developer seat = one named developer's right, for the license term, to use the SINAX library modules in the licensee's own products and internal systems:

`axiom_solver` · `async_dispute_resolution` · `config` · `curvature` · `dispute_resolution` · `encoder` · `epoch_attestation` · `epoch_governor` · `geodesic_flow` · `graph_store` · `homology_detector` · `integration` · `lemma_discovery` · `memory_engine_contracts` · `morse_filter` · `node_package` · `operational_validation` · `production_telemetry` · `proof_manifold` · `ptn` · `retrieval` · `search` · `self_healing` · `swarm_protocol_lock` · `vector_retrieval_engine` · `visualization`

(`licensing.py` itself — the issuance/verification module — ships with the pack so the licensee can verify seats offline; it is tooling, not one of the 26.)

## Terms summary (operational)

- **Term:** 365 days from issuance. Renewal is a new issuance at the then-current list price.
- **Seats:** the licensed seat count is in the signed license record; activation beyond it is refused by the registry (`SeatLimitExceeded`). Seat ids are named per developer; a seat can be deactivated and reassigned by the licensee's admin through the registry.
- **Verification:** the license is a signed record (content-addressed ID + HMAC key). Verification is offline — no phone-home, no telemetry requirement.
- **Restrictions:** no redistribution or resale of the library or the license; no sublicensing; no use of SINCOR/SINAX marks to imply endorsement. Per SINCOR canon, the library and its outputs are never described as "certified."
- **Support shape:** delivery support at issuance (integration questions on the delivered modules) inside the engagement; no SLA is promised until one is written into a signed order.
- **Expiry:** at term end the license activates nothing new; existing activations recorded before expiry are a renewal conversation, not an automatic cutoff of the licensee's own built products — the exact post-expiry posture is one of the items for the founder/legal terms review.

## Delivery process (end to end)

1. **Order:** buyer pays per-seat via the P14 PayPal link ($1,149 × seats) and provides the licensee legal name + seat count + admin contact.
2. **Issuance (same business day as confirmed payment):** the operator issues the license with `licensing.issue_license(licensee, seats, secret)` — the issuer secret comes from the operator's secret store, never from the repo — and records the `license_id` against the order.
3. **Delivery:** the buyer receives (a) the library package at the issued commit, (b) the license payload (`license_id` + `key` + record fields), (c) this pack. Delivery is recorded against the order with the license_id as the receipt key.
4. **Activation:** each developer activates a seat id against the payload via `SeatRegistry.activate(...)`; the registry enforces the seat cap and expiry.
5. **Renewal/expiry:** 30 days before `expires_at`, the operator offers renewal at list price; renewal = a fresh issuance (new `issued_at`, new `license_id`). Lapsed licenses verify as expired and activate nothing.

## What is still owed before the first sale (named, not hidden)

- Founder/legal review of the terms text above (post-expiry posture, liability, governing law).
- The issuer secret's home in the operator secret store (a deploy/ops step, not code).
- A licensee. The rail, the pack, the issuance code, and the delivery process are ready; the buyer is the missing piece.
