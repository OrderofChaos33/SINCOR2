# Sin Bonding Curve — Reference Fix for 9-decimal token bug

This small reference project demonstrates correct handling of tokens with arbitrary decimals (including 9 decimals).

Root cause for many 9-decimal bugs:
- Code assumed 18 decimals (or hardcoded 1e18) when converting between smallest units and human-readable values.
- Frontend or scripts used incorrect formatUnits(decimals) leading to display or arithmetic errors.

Fix implemented here:
- `SinBondingCurve` reads `token.decimals()` and uses it when converting token amounts to whole-token equivalents.
- Unit tests include a `MockToken9` (9 decimals) and `MockToken18` (18 decimals) and verify pricing for whole tokens, smallest units, and rounding behaviors.

Rounding behavior and gas notes:
- A new method `priceForTokenAmountRoundedUp` is provided to perform ceil rounding so tiny token amounts don't evaluate to a zero-price when you need a minimum payment.
- `decimalsFactor` is cached in the constructor for gas efficiency and consistent math.

How to run:
- npm ci
- npm test
- npm run lint:sol

If you want, I can:
- Integrate these tests and fixes into your existing bonding-curve repo and open a PR with a short changelog (recommended).
- Extend tests to cover more edge cases and add CI checks and coverage reports.
