# P24 Content-Policy Screener — selection decision

Two different "screener" things exist for P24. This doc keeps them apart and
states the exact founder decision needed for each.

## 1. Python issuance path (this wave)

`src/sincor2/defi/p24/screener.py` defines the pluggable `ContentScreener`
protocol: `screen(name, symbol, description, bio) -> ScreenDecision`
(allow / deny / reason / ruleset version). Enforcement lives in
`OnboardingAgent.register` — the single choke point the issuance route
(wave 8, `POST /v1/a2a/socialfi/issue`) calls. `ScreenerDenied` subclasses
`policy.PolicyViolation`, so existing route error mapping (→ 400 with
ruleset version) keeps working.

Shipped screeners:

| Screener | Behavior |
|---|---|
| `DenyListScreener` | Standing screen: the no_price_talk phrase deny-list, ruleset 1.0.0. This is the onboarding default. |
| `DeferredScreener` | Fail-closed stub. Always denies with `"deferred: no screener configured"`. Never a fake allow. |

Posture:

- `get_screener()` (process registry) is fail-closed: nothing configured →
  `DeferredScreener`. Any new consumer that asks for "the screener" without
  one configured is denied.
- `OnboardingAgent` defaults to the standing deny-list screen; set
  `P24_SCREENER=deferred` to halt all issuance through the onboarding path.
- Every decision is logged with screener id + ruleset version. Denial logs
  cite field + reason — the submitted text is never logged or stored
  (the existing `RejectionLog` invariant is preserved).

Merge note (wave 8): the route pre-screens with `policy.require_clean`
directly before calling `agent.register`. At merge time, keep both
(defense in depth) or route the pre-screen through `require_screened` so
there is one enforcement path; either way `onboarding.register` is the
authority and cannot be bypassed.

## 2. Onchain `ContentPolicyGuard.screener` role

The Solidity `ContentPolicyGuard` (onchain/src/p24/) takes the screener
address as a constructor arg and only that address may call `markScreened`.
No key material exists in this repo. The founder must decide the screener
identity before the Sepolia deploy ceremony:

| Option | What it is | Implies |
|---|---|---|
| A. Founder-held EOA | Founder key signs `markScreened` per approved token | Simplest; single-key trust, same custody question as the adjudicator |
| B. 2-of-3 multisig | Screeners vote offchain, multisig submits | Slower; no single key; matches mainnet posture |
| C. Automated screener service | Backend holding a screener key calls `markScreened` after the Python `ContentScreener` allows | Machine-speed; the key becomes infra to protect (Secure Vault) |
| D. Third-party moderation API | API verdict feeds a `ContentScreener` implementation; onchain screener = option A/B/C | Best coverage; vendor cost + dependency |

Recommendation: **A for the Sepolia rehearsal** (founder-held, disclosed as
single-key), **B for mainnet**. Whichever is chosen, the Python side is
ready: implement `ContentScreener` around the chosen verdict source and
pass it to `OnboardingAgent` (or `configure_screener` process-wide).

## Exact founder decisions needed

1. **Screener identity for Sepolia**: A, B, C, or D above (recommending A
   for rehearsal).
2. **Key custody**: where the screener key lives (founder-held vs Secure
   Vault service key).
3. **`P24_SCREENER` production posture**: `deferred` (halt issuance until
   the screener is pinned) vs `deny-list` (standing screen active).
4. Whether a third-party moderation API should augment the deny-list
   (which vendor, who pays, latency budget).

None of these are made by this wave. Issuance stays fail-closed until they are.
