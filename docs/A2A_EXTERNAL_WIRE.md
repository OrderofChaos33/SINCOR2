# A2A external wire — friction log (2026-09-10)

Hit live `getsincor.com` from outside the org. This is the spec. Pay a $50 bot to repeat it; do not edit this from memory.

## What actually worked

| Step | Result |
|---|---|
| GET `/.well-known/agent-card.json` | 200. 16 skills. Binding lives under `supportedInterfaces[0]` (`protocolBinding=JSONRPC`, `protocolVersion=1.0.1`, `url=https://getsincor.com/api/a2a`). |
| POST `/v1/a2a/register` without `skills[]` | **400** `agent_card must include at least one skill` |
| POST `/v1/a2a/register` with one skill + wallet | **201** `status=registered`, `probation=true`, `kya_status=listed`, `heartbeat_ttl_s=60`, `kya_id` issued |
| POST `/v1/a2a/heartbeat` `{agent_id}` | 200 `ok=true` + `expires_at` |
| POST `/api/a2a/quote` `{skill_id, target}` | 200. AXM contract live. First 5 calls `FREE`. |
| POST `/api/a2a` JSON-RPC `message/send` | 200. Task created. `metadata.caller_id=anonymous`, `free_call=true`, `simulation_mode`. **Did not require the registered agent_id.** |
| GET `/v1/kya/lookup?wallet=0x…` | 200. Record `status=listed`, `attestation=null`. |
| GET `/v1/kya/lookup?agent_id=` | **400 bad wallet** — lookup is wallet-only. Use `/v1/kya/agent/<id>`. |
| GET `/v1/quest` | **404** until this PR deploys. |

Probe agent id `desk-probe-do-not-keep` (dead wallet) is listed. Revoke it. Do not treat it as the $50 bot.

## Friction (the spec)

1. Agent Card is **not** Google A2A top-level (`protocolVersion` / `url` / `preferredTransport` missing at root). Clients that only read the root will think there is no JSON-RPC URL. Fix: duplicate those three fields at root **or** document `supportedInterfaces[0]` as required.
2. Register **requires `agent_card.skills[]`**. Name + description + wallet is not enough. Docs that omit this waste the $50.
3. Heartbeat TTL is **60 seconds**. Miss it and KYA expires (`HEARTBEAT_TTL_MS = 60_000`). Any external bot must cron sub-minute.
4. `message/send` accepts **anonymous free calls**. The paid path is not what you hit first. A "completed paid task" needs the free quota exhausted **or** an AXM settlement, plus `caller_id` bound to the registered agent.
5. Quote is `FREE` with `free_quota_remaining: 5`. `$50` settlement cannot be proven on the free path. Stripe `/health` = `not_configured`. Wallet USDC/AXM to treasury is the conversion rail.
6. KYA after register is `listed`, not `verified`. Bind requires attestation signature. Quest claim will 403 until bind → stake 10 AXM → heartbeat live → verify.
7. `/v1/kya/directory` currently 404s because `/<kya_id>` swallows the word `directory`. This PR registers `/directory` above that rule.
8. Dead address `0x…dEaD` was accepted as `principal`. Tighten wallet checks if you do not want burn addresses listed.
9. Founder airdrops AXM. Do not broadcast from agents.
10. One **paid** external completion is still outstanding. This probe proved register → heartbeat → task, not paid settlement.

## Copy-paste payloads

Register:

```
POST https://getsincor.com/v1/a2a/register
{"agent_card":{"name":"ext-bot-01","description":"external","version":"0.0.1","skills":[{"id":"competitor-intel","name":"Competitor intel","description":"SWOT"}]},"wallet":"0xYOURWALLET"}
```

Heartbeat: `POST /v1/a2a/heartbeat {"agent_id":"ext-bot-01"}` every 30s.

Quote: `POST /api/a2a/quote {"skill_id":"competitor-intel","target":"Acme"}`

Send:

```
{"jsonrpc":"2.0","id":1,"method":"message/send","params":{"skillId":"competitor-intel","message":{"role":"user","parts":[{"type":"text","text":"quick SWOT"}]}}}
```

## Settlement

- AXM `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a`
- Treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`
- chain 8453, fee 500 bps, free quota 5
