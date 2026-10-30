# EIP-7702 Delegation Lifecycle — Design Constraint

Status: **standing constraint** (dev-watch item 312, recorded 2026-10-30).
Applies to: any future EIP-7702 delegation SINCOR issues or accepts, in
any module. Enforcement: `tests/pytest/test_devwatch312_delegation_guard.py`
(fails CI if a 7702 execution surface appears in a money-path module, or
if this rule loses any element). The same rule is recorded in code as the
`EIP7702_DELEGATION_LIFECYCLE` design note in `src/sincor2/x402_payments.py`,
next to `MANDATES_API_WATCH`, where wallet-delegation primitives are tracked.

## Why this exists

A signed EIP-7702 authorization is a **standing capability**, not a
one-shot approval. Whoever holds the signed authorization tuple can
attach the delegation later, and the delegating EOA keeps executing the
delegate's code until the delegation is explicitly cleared. Incident
reference: the sven.eth drain (2026-10-14), where a signed delegation
was reused as a standing capability to drain the account.

Current state (grep-verified 2026-10-30 over `src/`, `contracts/`,
`onchain/`, `tests/`): **there is no EIP-7702 surface anywhere in this
codebase** — no 7702 reference in any `.py`/`.sol`/`.md` file before
this document, no code-setting transaction building, no authorization
tuples or lists, no authorization-signing API use, no DELEGATECALL.
The only delegation-adjacent surface is offchain CDP wallet delegation
(Mandates API), tracked as a design note, not code. This constraint is
therefore a gate on future work, not a fix to a live surface.

## The rule

Any EIP-7702 delegation we ever issue must be:

1. **Short-lived and scoped.** Every authorization carries an explicit
   expiry (block or timestamp) and names one specific implementation
   address. No open-ended delegations; no wildcard implementation.
2. **Revocable by a single clearing transaction.** The delegating
   account can revoke the delegation with one clearing transaction that
   restores it to a plain account. The clearing path must be tested
   before the delegation is ever used.
3. **Inventoried.** Every active delegation is recorded in a delegation
   inventory at issuance time (account, implementation, expiry,
   purpose). An active delegation that is **not** in the inventory is
   treated as a **compromise until cleared** — clear first, investigate
   second.

And on the accepting side:

4. **Re-verified at use time.** No money-path code may accept an action
   from a 7702-delegated account on the strength of a previously signed
   authorization. The current delegate address must be re-verified
   onchain at use time, and the action refused if the onchain
   delegation does not match the inventoried, unexpired scope.

## What changing this takes

Introducing a 7702 surface into a money-path module fails the guard
test by design. The change that introduces one must, in the same PR:
implement all four rule elements above, and deliberately update the
guard to name the sanctioned surface. Weakening the guard without the
lifecycle implementation is not an acceptable change.
