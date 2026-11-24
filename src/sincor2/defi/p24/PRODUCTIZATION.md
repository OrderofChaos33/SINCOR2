# P44 — SocialFi Revenue Share: the offer, productized

**SKU:** P44 (module P24) — SocialFi Revenue Share. **Price form:** 5% platform revenue share (catalog list terms, 2026-11-16 research). **Status:** mechanics were built and tested; the revenue-share *accounting* was the missing product layer. Built 2026-11-23 on `xioix/autonomous-p44-revshare-productization`: `platform_book.py` (this package) + this document.

## The offer

A partner platform or creator community integrates the P24 SocialFi module (creator tokens on a bonding curve, DeFi primitives, content-policy screening). Every trade pays a 10% fee, split conservation-exact: **creator 49.4% / platform 49.4% / treasury 1.2%** of the fee. The **platform leg — 4.94% of trade value — is the revenue share** this SKU sells (marketed as the 5% platform revenue share). Of the platform leg, 10% is earmarked to staking rewards per the module parameters; the remaining 90% is the share due to the revenue-share beneficiary named in the integration agreement.

## How the money is accounted (the part that was missing)

- `split.py` already computed the three legs exactly; `fees.TreasuryFeeBook` already accrued the treasury leg. The platform leg accrued **nowhere** — no book, no statement, nothing to settle against.
- `platform_book.PlatformShareBook` now accrues the platform leg per token per 7-day epoch, carves the staking earmark exactly (Fraction arithmetic, no float drift), and tracks the share due.
- **Statements:** `statement(token, epoch_index)` is the counterparty-facing artifact — gross fees, platform leg, staking earmark, share due, settled, outstanding for the epoch. An integration partner can reconcile every figure against the split's published parameters.
- **Settlement:** outstanding share settles to the agreement's beneficiary in ≥ $10 batches (the module's standard floor), attributed oldest-epoch-first so statements reconcile to payments exactly. Settlements are records in the module's standing LIVE-BLOCKED posture — execution of any live transfer remains behind the module's live gate and the founder's deploy decisions, unchanged by this build.

## Contracting process

1. **Agreement:** partner + SINCOR sign an integration agreement naming the revenue-share beneficiary address, the epoch cadence (7 days, per module parameters), and the settlement floor. The split parameters and the staking earmark are stated in the agreement by reference to the module's published PARAMS — they are code, not promises.
2. **Integration:** the partner integrates the module (testnets/forks/local chains under the current LIVE-BLOCKED posture; live operation is a separate founder-gated step).
3. **Epoch close:** at each epoch end, the operator issues the epoch statement from `PlatformShareBook.statement(...)` to the partner.
4. **Settlement:** when the outstanding share reaches the $10 batch floor, settlement is recorded and executed through the live rail once the module's live gate is lifted by the founder.

## Still owed before the first partner (named, not hidden)

- The module's live gate (founder decision — the LIVE-BLOCKED posture is deliberate and untouched here).
- Founder/legal review of the integration-agreement terms text.
- A partner. The mechanics, the book, the statements, and the process are ready.
