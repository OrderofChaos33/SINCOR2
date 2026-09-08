# SINCOR KYA v0

Drop-in trust layer on inbound fabric. Does not replace `/v1/a2a/register`.

- Chain: Base 8453
- AXM bond/fee: `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a`
- Do not use dead AXM `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822`
- Min stake: 10 AXM. Verify fee: 2 AXM.
- States: listed -> bound -> staked -> verified | revoked | expired
- Phase 1: lookup only. Do not gate message/send until 50 verified records.

## Hooks

`register_agent_record` calls `kya_registry.hook_listed`.
`heartbeat_agent` calls `kya_registry.hook_heartbeat`.
`mount()` registers `/v1/kya`.

## API

- POST /v1/kya/list
- POST /v1/kya/bind
- POST /v1/kya/verify
- POST /v1/kya/heartbeat
- POST /v1/kya/sla
- POST /v1/kya/revoke
- GET /v1/kya/{kya_id}
- GET /v1/kya/agent/{agent_id}
- GET /v1/kya/lookup?wallet=

On-chain `contracts/kya/KYARegistry.sol` is bond/revoke truth. Do not deploy until list+bind is live on Railway.
