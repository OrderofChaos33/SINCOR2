# ERC-8004 registration — operator steps (you on the laptop)

Registry (Base mainnet, CREATE2): `0x8004A169FB4a3325136EB29fA0ceB6D2e539a432`

Agent URI: `https://getsincor.com/.well-known/agent-card.json`

## Preferred: cast

```bash
export BASE_PRIVATE_KEY=0x...   # operator key that should own the agent NFT
cast send 0x8004A169FB4a3325136EB29fA0ceB6D2e539a432 \
  "register(string)" "https://getsincor.com/.well-known/agent-card.json" \
  --rpc-url https://mainnet.base.org \
  --private-key $BASE_PRIVATE_KEY
```

Read `agentId` from the receipt (Registered / Transfer tokenId). Confirm:

```bash
cast call 0x8004A169FB4a3325136EB29fA0ceB6D2e539a432 \
  "tokenURI(uint256)(string)" $AGENT_ID \
  --rpc-url https://mainnet.base.org
```

Explorer: `https://basescan.org/address/0x8004A169FB4a3325136EB29fA0ceB6D2e539a432`

## Python (same thing)

```bash
python scripts/erc8004_register.py                 # prints plan, no tx
python scripts/erc8004_register.py --broadcast     # needs BASE_PRIVATE_KEY + web3
```

## After the tx

1. Put `erc8004_id` and `repository: https://github.com/OrderofChaos33/SINCOR2` on the live Agent Card.
2. Paste agentId into the #233 issue comment.
3. Do not treat registration as a paid mandate. Next probe is `scripts/probe_paid_mandate.py`.
