# P04 — Dual-Pool Re-Auction Fee Rule (design + build record)

**Status:** Built 2026-11-23 on `xioix/autonomous-p04-reauction-fee` (off main `904b55f`). The contract is **undeployed**; the deploy signature remains founder-gated. Nothing in this build charges anyone anything today.

## The gap

`SincorTaskEscrow` handled create → claim → complete / slash, but a task that **expired** — never claimed, or claimed by an agent who went silent — had no recycling path: the creator's escrowed reward sat locked forever, and no re-auction fee rule existed anywhere (catalog §3C: "P04 — re-auction fee rule (none designed; contract also undeployed)").

## The rule (as built in `SincorTaskEscrow.reauctionTask`)

1. **Who/when:** only the task's creator, only after `expiryTimestamp` has passed, only while the task is `Unassigned` or `Claimed`. Verified and Slashed tasks are terminal and cannot be re-auctioned.
2. **Fee:** `REAUCTION_FEE_BPS = 1000` — **10% of the escrowed reward**, paid to `treasury` at re-auction time. The reduced reward stays escrowed for the next attempt. The 10% figure is the researched P04 list price (2026-11-16 market research, catalog §2) — **unratified**: it is a named constant in an undeployed contract so the founder can see and change the exact number at the deploy gate; it is not a charged fee.
3. **Claimed tasks:** the silent agent's bond is returned **in full**. Re-auction is the recycling path; `slashTask` remains the punitive path. The two are mutually exclusive — both move the task out of `Claimed`, so a task can never be both slashed and re-auctioned.
4. **Repeat re-auctions:** each one charges 10% of the *already-reduced* reward, so the fee decays geometrically (1000 → 900 → 810 …) instead of draining the task at a flat rate. `timesReauctioned` is tracked on the task and emitted in the `TaskReauctioned` event.
5. **Conservation invariant:** the escrow balance falls by exactly `fee + returned bond` per re-auction; all other escrowed rewards and bonds are untouched. Verified by `test_conservation_across_mixed_reauctions`.

## Deliberately out of scope

- **No creator refund path** for expired tasks was added. Whether an abandoned task's remaining reward should ever be refundable is a policy call that rides with the founder's deploy decision, not this fee-rule build.
- **No rate setter.** The fee is a compile-time constant, not an owner-adjustable parameter — the contract has no owner role today, and adding one is a governance decision for the deploy gate.
- **Deploy** (and any Sepolia drill before it) remains founder-gated, unchanged.

## Verification

`tests/pytest/test_p04_reauction_fee.py` — 8 tests, eth-tester/PyEVM, solc 0.8.30, OpenZeppelin v5.5.0 imports resolved from a local checkout: fee charge on unclaimed expiry, bond return + fee on claimed expiry, reverts (before expiry, non-creator, verified task, zero duration), fee flooring + second re-auction on the reduced reward, and the mixed conservation check.
