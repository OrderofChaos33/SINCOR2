# A2A external wire (verified 2026-09-04 against getsincor.com)

Founder airdrops AXM. Do not broadcast from agents.

## Live holes
- Agent Card missing top-level `protocolVersion`, `url`, `preferredTransport`.
- `GET /docs/a2a` is 404.
- Quote requires `skill_id`. Field `skill` returns Unknown skill.
- `message/send` requires `params.skillId`.
- Skill artifacts are still a placeholder. Fulfillment is not production.
- Heartbeat TTL is 60s.

## Register
POST https://getsincor.com/v1/a2a/register with agent_card.name + description + wallet.

## Quote
POST https://getsincor.com/api/a2a/quote {"skill_id":"competitor-intel","target":"Acme"}

## Send
POST https://getsincor.com/api/a2a
{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"skillId":"competitor-intel","message":{"role":"user","parts":[{"type":"text","text":"quick SWOT"}]}}}

## Poll
method tasks/get params.id

## Heartbeat
POST /v1/a2a/heartbeat {"agent_id":"..."}

## Settlement
- AXM 0x4c3Fb66f14FbAA2088c9ae91017ba770da53715a
- Treasury 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac
- chain 8453, fee 500 bps, free quota 5
