# CAPITALIZATION.md — P25 DeFi Risk Mutual: Capitalization Requirements Spec

Sprint extra for P25 (suite P05). This spec states what capitalization an
engagement requires **before cover could be written**, derived from what
the packaged code (`insurance_mutual.py`) actually enforces. Where the
code is silent, this spec says so — **"not specified in code — engagement
term to be set"** — rather than inventing numbers.

**Starting fact: no mutual is capitalized or open today.** The platform
module holds no funds, the packaged pool starts at zero reserves, and
underwriting is blocked by default (`live_underwriting = False`).

## 1. Requirements the code enforces

### 1.1 Reserve-ratio floor — 130% of outstanding cover (enforced)

`MutualPool._reserve_ok_after` requires, on **every** underwrite and
every capital redemption:

```
reserves × 100  ≥  outstanding cover × 130
```

(`RESERVE_RATIO_NUM = 130`, `RESERVE_RATIO_DEN = 100`; violations raise
`ReserveBreachError`.)

Capitalization consequence: before a cover line of amount **C** can be
written, the pool must already hold reserves of at least **1.30 × C**
(measured before the new premium is added — `buy_cover` checks the floor
against pre-premium reserves). Equivalently, a capitalized pool with
reserves **R** can carry at most **R / 1.30** of outstanding cover. This
is the binding capitalization rule of the module, and an engagement's
capitalization plan must be sized from it, per cover schedule.

### 1.2 Premiums add to reserves; treasury fee is on top (enforced accounting)

On underwrite, the full premium is added to reserves and a 25 bps fee on
the premium (`FEE_BPS = 25`) is recorded as collected by the treasury.
Premiums therefore grow reserves over time but are **not** a substitute
for the up-front capitalization in §1.1: the floor is checked before the
premium lands.

### 1.3 Live gate — no cover before release (enforced)

`buy_cover` raises `LiveBlockedError` unless `live_underwriting` is set.
The code provides no setter or release workflow: the module docstring
describes the release as auditor + actuarial + founder release. The
engagement must define who flips this flag, on what evidence — the code
only enforces that it starts off.

## 2. Amounts the code names but does not enforce

### 2.1 `MIN_CAPITAL_WEI = 200_000_000` ($200 in 6-decimal stablecoin wei)

This constant is **defined and never referenced** anywhere else in the
module. `deposit_capital` enforces only `amount > 0`; there is no
minimum-deposit check and no minimum-pool-capitalization check.
Consequence: $200 is **not** a capitalization requirement of the code,
and no engagement may cite it as one. A minimum initial capitalization
is **not specified in code — engagement term to be set** (and, per §1.1,
must in any case be at least 1.30× the first cover schedule).

### 2.2 `MAX_ALLOC_PCT = 0.15`

Also defined and never referenced. No per-protocol or per-cover
allocation cap is enforced beyond the §1.1 floor. A single-cover or
single-protocol concentration limit is **not specified in code —
engagement term to be set**.

### 2.3 Assessor bonds — 1,000 units default (parameter, not capital)

`AssessorRegistry.appoint` defaults each assessor's bond to
`ASSESSOR_BOND_UNITS = 1_000` abstract units, slashable to zero on a
fraudulent approval. The unit is not denominated in any asset in code,
and claim approval additionally requires voting weight **> 5× the cover
amount** (`ASSESSOR_QUORUM_MULT = 5`) — again in abstract weight units.
What assessor bonds are posted in, and how weight is assigned, is
**not specified in code — engagement term to be set**.

## 3. Requirements the code does not address at all

Each of the following is **not specified in code — engagement term to be
set** before cover could be written:

- Absolute minimum pool capitalization (see §2.1).
- Asset and chain for capitalization (the code accounts in generic
  6-decimal stablecoin wei; it names no token and no chain).
- Who may provide capital, and any lockup / withdrawal notice beyond
  the §1.1 redemption floor.
- Reinsurance or excess-of-loss arrangements.
- Capital custody, wallet, or signing arrangements (the module holds
  no keys and moves no funds).
- Ongoing top-up triggers if reserves approach the 130% floor outside
  the underwrite/redeem checks (the code checks only at those moments;
  a claim payout can lawfully take reserves below 130% of the remaining
  outstanding cover — payouts are never blocked by the floor).
- Regulatory, licensing, or jurisdictional requirements for writing
  cover in the counterparty's jurisdiction.

## 4. Worked sizing examples (from the enforced rule only)

| Planned outstanding cover | Minimum reserves required at underwrite (§1.1) |
|---|---|
| $100,000 | $130,000 |
| $500,000 | $650,000 |
| $1,000,000 | $1,300,000 |

These follow mechanically from the enforced 130% floor. They are not a
capitalization commitment, a quote, or evidence that any pool holds
these amounts — today no pool holds anything.

## 5. Pre-cover checklist (engagement must complete all)

1. Capitalization amount fixed in the engagement, ≥ 1.30× the initial
   cover schedule (§1.1), with the §3 terms set in writing.
2. Capital actually delivered to the engagement's designated pool
   arrangement (outside this package — the package holds no funds).
3. Assessor panel appointed, with bond asset and voting weights set (§2.3).
4. Treasury address for the 25 bps premium fee confirmed (`TREASURY_ADDRESS`).
5. Live release given under the engagement's release procedure (§1.3).
Only then may `buy_cover` be exercised under the engagement.
