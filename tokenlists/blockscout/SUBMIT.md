# Blockscout — clear unverified / suspicious UI for SINC

Checked: 2026-06-15T07:32:12.203580+00:00

## Current API status
- reputation: `ok`
- is_scam: `False`
- contract verified: `True`
- certified: `None`
- icon_url: `None`

Blockscout shows **suspicious / unverified** when `certified` is false and `icon_url` is null.
Contract is verified on-chain; you need **explorer certification** (steps below).

## Fix (in order)

1. Deploy latest getsincor.com (token list + metadata routes must return 200).
2. Blockscout → My Account → Verified addresses → Add address → sign as deployer.
3. Update token icon URL to https://getsincor.com/static/tokenlists/assets/logo-256.png
4. Submit public tag: https://base.blockscout.com/public-tags/submit
5. Register on TKN: https://tkn.xyz/token/base/0x9C8cd8d3961F445D653713dE65C6578bE11668e7
6. Open Superchain PR: python scripts/whitelist_token.py launch (or submit_superchain_pr.ps1)

## Deployer wallet (verify ownership in Blockscout)
`0xC184EcEfFaf6392951e4C7b042d61774497B5dC5`

## Paste into Blockscout token update
- Logo: `https://getsincor.com/static/tokenlists/assets/logo-256.png`
- Website: `https://getsincor.com`
- Token list: `https://getsincor.com/tokenlists/sincor.tokenlist.json`