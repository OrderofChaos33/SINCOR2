# Li.FI — verify SINC on Base (fix rogue V2 price)

Li.Fi already returns SINC but status is **unverified** with price **$0.0000000001** (rogue V2 dust pool).
Canonical venue: bonding curve `0x75dE341a2BC81806198364F125d4Cde36527619C` (~$0.00009/SINC spot).

Open issue (do not fork PR):
https://github.com/lifinance/customized-token-list/issues/new?template=add-token.yml

## Form fields

| Field | Value |
|-------|-------|
| Chain ID | 8453 |
| Token address | 0x9C8cd8d3961F445D653713dE65C6578bE11668e7 |
| Symbol | SINC |
| Name | SINC |
| Decimals | 8 |
| Logo URI | https://getsincor.com/static/tokenlists/assets/logo-256.png |
| Website | https://getsincor.com |
| Description | SINC is the utility token of SINCOR — production AI agent swarm on Base. Sourcify-verified, CertiK Skynet 97/100, fixed 100M supply, no mint/admin keys. Official buy: getsincor.com/sinc (bonding curve). |

## Notes for reviewer

- Do **not** use rogue Uniswap V2 pair `0x85372932f9b151a076815d92cf71a97980ffd667` for pricing.
- Uniswap v4 hook pool exists but hook sell walls are not spot; curve `0x75dE…` is the official sale.

Optional bulk JSON (bottom of form):

```json
{"address":"0x9C8cd8d3961F445D653713dE65C6578bE11668e7","chainId":8453,"symbol":"SINC","name":"SINC","decimals":8,"logoURI":"https://getsincor.com/static/tokenlists/assets/logo-256.png"}
```