# Discovery v2 — specification

**Status:** spec (Stream 4). No endpoint changes behavior until implemented;
when implemented, v1 endpoints keep working unchanged (new query params are
optional, new response fields are additive).

## What exists today

| Endpoint | Function | Behavior today |
|---|---|---|
| `GET /v1/a2a/cards` | `v1_cards()` in `src/sincor2/a2a_inbound_ext.py:253` | Platform card + vertical-pack cards, `quoteUrl` injected per skill. No search, no filter, no sort, no pagination. |
| `GET /v1/a2a/directory` | `v1_directory()` in `src/sincor2/a2a_inbound_ext.py:230` | `list_agents()` + KPI snapshot + entry-point URLs. |
| `GET /v1/a2a/agents` | `v1_agents()` in `src/sincor2/a2a_inbound_ext.py:224` | `list_agents(live_only=…)`; sorted by `registered_at` desc. |
| `list_agents()` | `src/sincor2/a2a_inbound_ext.py:164` | Each agent row carries `status` (`live`/`stale`/`probation`), `heartbeat_age_ms`. Stale = `heartbeat_age_ms > HEARTBEAT_TTL_S*1000` where `HEARTBEAT_TTL_S = 60` (`src/sincor2/a2a_inbound.py:28`). |
| KYA heartbeat | `src/sincor2/kya_registry.py` (`heartbeat()`, `snapshot()`) | `sla.last_heartbeat_ms` per agent; `snapshot()` aggregates registry state. |
| Underwriting | `score_mandate()` in `src/sincor2/underwriting/engine.py:95` | Returns `{"decision": "allow"\|"deny", "score": 0.0–1.0, "deny_reasons": [...]}`. Canonical reputation source per `docs/TRUST_STACK.md`. |

Gaps: a caller cannot search by skill, filter by reputation, sort by
anything except registration recency, or page. Stale agents appear next to
live ones with only a `status` string to distinguish them. Reputation exists
(underwriting) but is not wired into discovery ranking.

## Design

### 1. Query parameters (all optional, all additive)

On `GET /v1/a2a/agents` and `GET /v1/a2a/directory`:

```
q=<text>               substring match on agent_id, skills[].id, skills[].name
skill=<skill_id>       exact skill id (repeatable: ?skill=a&skill=b → ANY)
tag=<tag>              repeatable; agent must have ALL tags
min_reputation=<0..1>  underwriting score floor; default 0 (no floor)
live_only=<bool>       default true  ← behavior change candidate, see §4
kya_verified=<bool>    default false; when true, only KYA-bound agents
sort=<key>             reputation | freshness | fills | registered (default: reputation)
order=<asc|desc>       default desc
limit=<int>            default 50, max 200
cursor=<opaque>        pagination cursor (see §3)
```

On `GET /v1/a2a/cards`:

```
skill=<skill_id>       return only cards exposing the skill (repeatable)
q=<text>               same substring match over card skills
limit / cursor         same pagination
```

Validation: unknown `sort` keys → 400 with the allowed list. `limit > 200`
→ clamped to 200 (not an error). `min_reputation` outside [0,1] → 400.

### 2. Response envelope

```json
{
  "agents": [ { …agent row…, "reputation": {…}, "kya": {…} } ],
  "page": { "limit": 50, "next_cursor": "eyJ…", "total": 132 },
  "applied": { "q": null, "skill": ["lead-enrichment"], "min_reputation": 0.5,
               "sort": "reputation", "live_only": true },
  "protocol": { "version": "1.0.1", "deprecated": false }
}
```

- `agents[]` rows keep every field `list_agents()` returns today
  (`status`, `heartbeat_age_ms`, …) and gain:
  - `reputation`: `{"score": 0.82, "decision": "allow", "as_of_ms": …}` —
    from `score_mandate()` cached per agent (see §5). Agents with no
    underwriting record get `{"score": null, "decision": "unknown"}` and
    sort below scored agents.
  - `kya`: `{"verified": true, "last_heartbeat_ms": …, "revoked": false}` —
    from `kya_registry.snapshot()` joined on agent id. Unregistered agents
    get `{"verified": false}`.
- `applied` echoes the effective filters (debuggability for agent clients).
- `protocol` block: see `docs/a2a/PROTOCOL_VERSIONING.md`; included here so
  clients can detect deprecation without fetching the agent card.

### 3. Pagination

Cursor-based, not offset. `next_cursor` is base64url of
`{"sort_key": <last row's sort value>, "agent_id": <last row's id>}`.
The next page returns rows strictly after that position in the sort order.
Rationale: agent rows mutate between pages (heartbeats, new registrations);
offset pagination would skip/duplicate rows. Cursors are stable under
inserts.

### 4. Ranking: reputation-weighted sort (default)

Score each candidate agent; sort descending:

```
rank = 0.55 * reputation_score
     + 0.25 * freshness_score
     + 0.20 * fill_score
```

- `reputation_score`: underwriting `score` (0–1). `null` → 0. Agents with
  `decision == "deny"` are excluded entirely (not just demoted).
- `freshness_score = max(0, 1 - heartbeat_age_ms / (3 * HEARTBEAT_TTL_S * 1000))`.
  A just-heartbeated agent scores 1; at 3× TTL it scores 0.
- `fill_score = fills_90d / max_fills_90d` across the candidate set
  (min-max normalized per query, so it can't dominate globally).

Tie-break: `reputation_score` desc, then `agent_id` asc (deterministic).

**Stale-card demotion (hard rules, applied before ranking):**

| Condition | Effect |
|---|---|
| `heartbeat_age_ms > 3 * HEARTBEAT_TTL_S * 1000` (180s) | Excluded from default listing (`live_only=true`). Still retrievable with `live_only=false`, flagged `"status": "stale"`. |
| KYA `revoked == true` | Excluded from all listings, always. |
| `status == "probation"` (existing `list_agents` concept) | Included but ranked with `reputation_score *= 0.5`; response carries `"probation": true`. |
| Underwriting `decision == "deny"` | Excluded from all listings, always. |

**Behavior-change candidate:** default `live_only` to `true`. Today
`v1_agents` defaults to showing stale agents. The v2 default hides them;
callers that want the graveyard pass `live_only=false`. This is the one
v2 change that alters existing response content — flagged explicitly per
the no-sneak-changes rule, and gated on the versioning policy's
deprecation process (`docs/a2a/PROTOCOL_VERSIONING.md`).

### 5. Implementation notes (for the implementer)

- **Where:** extend `list_agents()` in `src/sincor2/a2a_inbound_ext.py`
  (filter/sort/paginate there — one place, both `/agents` and `/directory`
  benefit), and `v1_cards()` for the card-level `skill`/`q` filter.
- **Underwriting cache:** `score_mandate()` is per-mandate; for discovery
  use a cached per-agent rollup. New small module or a function in
  `src/sincor2/underwriting/engine.py`: `agent_reputation(agent_id,
  store) -> {"score","decision","as_of_ms"}`, cached 5 minutes in memory.
  Do NOT call `score_mandate` per row per request without caching — it's
  the most expensive piece of the pipeline.
- **KYA join:** `from sincor2.kya_registry import snapshot as kya_snapshot`
  (already imported in `v1_directory`). Join on agent id; treat missing as
  unverified, never as an error.
- **Fills:** `fill_score` needs per-agent 90d fills. Source: the quote
  ledger's outcome rows once `docs/a2a/QUOTE_ACCURACY.md` ships; until
  then, use task-store win counts or default `fill_score = 0` for all
  (ranking still works — the term just contributes nothing).
- **Rate limits:** directory/cards/agents are already in the "read" policy
  class (`src/sincor2/a2a_rate_limits.py`: 120/min + 5000/hour per IP).
  Paginated clients stay well under it; document `limit=200` max so
  scrapers can't widen pages to compensate.

### 6. Rollout checklist

1. Add query params + envelope to `/v1/a2a/agents` and `/v1/a2a/cards`
   (additive; defaults preserve today's output shape plus new fields).
2. Add `agent_reputation()` with 5-min cache in the underwriting engine.
3. Implement ranking + demotion rules; unit-test the formula with synthetic
   agents (reputation ordering, stale exclusion at 180s, deny exclusion,
   probation halving, cursor stability under inserts).
4. Announce the `live_only` default flip per the versioning policy
   (deprecation notice → grace period → flip).
5. Update `docs/a2a/BIDDER_WALLET_FLOW.md` and the onboarding quickstart
   with the new query params.
