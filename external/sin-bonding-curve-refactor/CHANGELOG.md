Changelog

v0.1.0 - 2026-01-10
- Fix: Use token.decimals() dynamically when converting between smallest units and whole tokens (prevents 9-decimal bugs).
- Fix: Cache `decimalsFactor` in constructor to improve gas efficiency and ensure consistent math.
- Feature: Added `priceForTokenAmountRoundedUp` to perform ceil rounding for tiny amounts.
- Tests: Added comprehensive unit tests for 9-decimal and 18-decimal tokens, and JS-side helper tests.
- CI: Added GitHub Actions workflow to run tests on push/pull_request.
- Linting: Added `solhint` configuration and lint script for contracts.

Notes:
- This is a reference implementation and patch. If you want I can apply these changes directly to your repo (create branch, run tests, and open a PR) when you copy the bonding-curve repo into the workspace or give me permission to edit it.
