Patch & Integration Instructions

Goal: Fix 9-decimal price bug and ensure bonding curve handles arbitrary token decimals robustly.

Summary of changes to apply to your repo:

1) Contract changes (Solidity):
- Add `uint256 public decimalsFactor;`
- Initialize `decimalsFactor = 10 ** uint256(tokenDecimals);` in the constructor after reading token.decimals().
- Replace any hardcoded use of `10 ** tokenDecimals` or `1e18` scaling with `decimalsFactor`.
- Add a helper `priceForTokenAmountRoundedUp(uint256 tokenAmount)` that performs ceil division to avoid zero prices for tiny amounts:

    function priceForTokenAmountRoundedUp(uint256 tokenAmount) public view returns (uint256) {
        if (tokenAmount == 0) return 0;
        uint256 numerator = (basePricePerWholeToken * tokenAmount) + (decimalsFactor - 1);
        return numerator / decimalsFactor;
    }

2) Tests (JS/Hardhat):
- Add tests for 9-decimal token (MockToken9) verifying:
  - 1 whole token => price equals basePricePerWholeToken
  - 1 smallest unit => price equals floor(basePricePerWholeToken / 1e9) or ceil depending on rounding function
  - rounding behavior when base price is tiny (e.g., 1 wei)
- Add tests for 18-decimal token (MockToken18) to ensure backward compatibility.
- Add JS-side tests demonstrating correct use of `ethers.utils.formatUnits` and BigNumber math.

3) JS/Frontend changes (if applicable):
- Replace manual divisions like `value / 1e9` with `ethers.utils.formatUnits(value, decimals)` or use dynamic `token.decimals()` to display human-readable numbers.
- When building price conversion helpers, use BigNumber math and explicitly pass token decimals.

4) CI & Linting:
- Add solhint to devDependencies and a lint script: `solhint 'contracts/**/*.sol'`.
- Add a GitHub Actions workflow that runs `npm ci` and `npm test` on push/pull requests.

Applying the patch:
- Create a branch: `git checkout -b fix/bonding-curve-9-decimals`
- Apply the contract changes and tests as described above (copy from the `external/sin-bonding-curve-refactor` files if convenient).
- Run `npm ci` and `npm test` to ensure tests pass.
- Commit changes and open a PR with a one-line summary and a short description referencing the bug and tests added.

If you prefer, I can apply these changes directly to your repo (create branch + commit + tests) if you copy the bonding-curve repo into the workspace or grant me access to edit it here. Otherwise, paste the relevant contract file(s) and I'll generate an exact patch content you can `git apply`.
