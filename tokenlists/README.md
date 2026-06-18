# SINC whitelist package

Generated for `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` on Base (8453).

## Immediate (no approval needed)

- **Wallet import URL** (MetaMask → Settings → Token lists → Add):
  `https://getsincor.com/static/tokenlists/sincor.tokenlist.json`
  (Deploy `static/tokenlists/` to production first.)

## PR-ready folders

| Target | Folder | Action |
|--------|--------|--------|
| Superchain / Coinbase Wallet | `pr-packages/superchain/` | PR to ethereum-optimism/ethereum-optimism.github.io |
| Trust Wallet | `pr-packages/trustwallet/` | PR to trustwallet/assets (+ fee) |
| Balancer | `pr-packages/balancer/` | Check if already listed |
| CoW Swap | `pr-packages/cowswap/` | GitHub issue form |
| Forms (CG, CMC, DexScreener…) | `pr-packages/forms/` | Copy `FORM_PAYLOAD.txt` |

## Commands

```powershell
python scripts/whitelist_token.py check
python scripts/whitelist_token.py launch
python scripts/whitelist_token.py open-forms
```
