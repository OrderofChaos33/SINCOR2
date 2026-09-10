# Axiom 2.0 launch checklist

Bonding curve is dead. `SincBondingCurve.sol` and its deploy scripts are gone. This is the replacement path. Old addresses are landmines.

**Path:** mint → pool → liquidity → quest

Locked 2026-09-10. Runtime source: `src/sincor2/onchain/constants.py`. One source of truth — do not paste a guessed pool or a DEAD address into copy, env, or Agent Cards.

## Live, locked (Base 8453)

| Role | Address | Status |
|---|---|---|
| AXM | `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a` | LIVE — sole A2A settlement |
| SINC | `0xe1D836087F6573b665d25CE088793E916D7892f8` | LIVE — 8 decimals, $0.15 floor. 1 holder / 0 transfers: do not market float |
| Treasury | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` | LIVE |
| USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | LIVE |
| Uniswap v4 PoolManager | `0x498581fF718922c3f8e6A244956aF099B2652b2b` | Infra |
| Uniswap v4 PositionManager | `0x7C5f5A4bBd8fD63184577525326123B519429bDc` | Infra |

## Not locked — do not invent

- Official AXM/USDC pool address
- Official LP NFT / position id
- Bonding-curve buy path (retired)

## Landmines — DEAD, never paste

| Address | Label | Why |
|---|---|---|
| `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | DEAD | Retired SINC v1 |
| `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` | DEAD | Dead PumpClawToken labeled AXM |
| `0x75dE341a2BC81806198364F125d4Cde36527619C` | DEAD | Retired bonding curve |
| `0xb627F53E08AD7d455e787d052C18D6877020E2BF` | DEAD | Old bonding curve |
| `0x25cA41Dac29f892c72A53500853eC45a5FfF90aa` | DEAD | Superseded bonding curve |
| `0x85372932f9b151a076815d92cf71a97980ffd667` | DEAD | Rogue Uniswap V2 SINC/USDC |

`$1.50` is a ceiling wall, not a floor.

## Quest

KYA airdrop quest verifies `keccak256(address20)` merkle proofs against the **committed production root**, bound by address. Set `KYA_PRODUCTION_ROOT` on Railway after:

```
python scripts/kya_build_merkle.py --from-file recipients.txt --source axm-disperse
```

Do not publish a replica root as the drop root. Claims against `source=replica` raise `REPLICA_ROOT_FORBIDDEN`. A replica proof will not verify against the disperse root even if that replica is accidentally deployed.

## Surfaces

- Official buy: https://getsincor.com/buy
- Agent Card: https://getsincor.com/.well-known/agent-card.json (`supportedInterfaces[0].protocolVersion` 1.0.1, JSONRPC)
- Genesis: 2026-09-26 16:00 UTC
