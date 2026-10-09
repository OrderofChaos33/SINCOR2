# Owner Safety Decisions — 2026-10-09 (founder, via chat)

These decisions unblock WP2–WP4 of the scope-locked safety build.
Recorded verbatim from founder; builders must treat as frozen constraints.

## D1. Cold outreach: SCAFFOLD-READY, DEFAULT OFF
- Not enabled yet.
- Build the full scaffold (consent classification, suppression/opt-out,
  sender identity, rate limits, approval gates) as if it were going live,
  so enablement is a policy flip, not a build project.
- Default state: OFF. Fail closed. No autonomous sends without explicit
  founder enablement per campaign.

## D2. Email: TRANSACTIONAL ONLY
- Only transactional emails may flow (signup, onboarding, receipts, alerts).
- Marketing email stays OFF. Nothing bypasses the suppression list.
- Transactional vs marketing classification is enforced in code, not docs.

## D3. Payments: USDC + AXM ONLY
- Legacy Stripe/fiat surfaces are NOT supported.
- Disable or remove unauthenticated alternate Stripe routes found in audit.
- Supported: USDC settlement, AXM settlement, x402 flows.
- Server-priced intents bound to asset/chain/recipient/amount/payer/expiry.

## D4. On-chain auction anchoring: TOA DECISION — OFF-CHAIN ONLY FOR LAUNCH
- TOA analysis run 2026-10-09 (toa_auction_anchoring_decision.py).
- Option A (on-chain): 0.410 | Option B (off-chain): 0.707
- RECOMMENDATION: Off-chain only for 2026-11-09 launch.
- Rationale: 31 days to launch, contracts undeployed, no audit engaged,
  open red-team money-path items (M6 replay, W-34 paymaster, settlement
  reconciliation gaps, kill-switch replay bypass). On-chain bugs are
  irreversible; Python fixes deploy in minutes.
- On-chain anchoring deferred to post-launch v2 with proper audit runway.
- Build reconciliation scaffolding (winner/price/beneficiary mapping) now,
  keep broadcast flags OFF.

## D5. Agent financial ops: HEAD ORCHESTRATION UNDER OVERLAPPING SUPERVISION
- Financial operations run under a head orchestrator agent.
- Overlapping supervision: multiple supervisors watch the head; no single
  supervisor is a single point of failure or collusion.
- Per-action and daily limits enforced in code; approval/revocation paths
  require supervisor quorum, not single-signer.
