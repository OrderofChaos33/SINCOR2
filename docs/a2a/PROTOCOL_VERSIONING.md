# A2A protocol versioning policy

**Status:** policy (Stream 4). Ratify by merging; enforce in code review.

## Current state

- Single constant: `A2A_PROTOCOL_VERSION = "1.0.1"` in
  `src/sincor2/a2a_integration.py:118`.
- Advertised in three places: the `AgentCard.protocol_version` field
  (served at `/.well-known/agent-card.json` by `agent_card_v1()`),
  `to_legacy_dict()` at `/.well-known/agent.json`, and the machine-readable
  `/docs/a2a` surface.
- There is no documented meaning to the version number, no deprecation
  process, and no way for a client to learn that a new version exists
  without diffing the card. This policy fixes all three.

## Version scheme

`MAJOR.MINOR.PATCH`, compared numerically per segment:

- **PATCH** (1.0.1 → 1.0.2): no wire change. Doc fixes, new optional
  response fields that old clients ignore, tightening of validation that
  only rejects previously-undefined input. Safe to deploy unannounced
  (still note it in the changelog).
- **MINOR** (1.0.x → 1.1.0): backward-compatible wire change. New optional
  query params, new endpoints, new response fields, new sort keys.
  Old clients keep working byte-for-byte.
- **MAJOR** (1.x → 2.0.0): breaking wire change. Removing/renaming a
  field, changing a field's type or semantics, changing auth requirements,
  changing default behavior of an existing parameter.

### What counts as breaking (non-exhaustive, judged conservatively)

Breaking:

- Removing `quoteUrl` from card skills, or renaming any card field.
- Changing `axm_price_wei` from string to number.
- Requiring auth on a currently-unauthenticated endpoint.
- Changing the default of `live_only` (Discovery v2 proposes exactly this —
  it ships as a **minor** with a deprecation notice, then the default flips
  in the next **major**; see the graduated path below).
- Changing error response shape (`{"success": false, "error_code": …}`).

Not breaking:

- Adding optional query params or response fields.
- Adding endpoints.
- Adding values to an enum-like field where clients are told to ignore
  unknowns (e.g. new agent `status` values — document "ignore unknown").

Rule of thumb: **if a client written against the old card can break without
changing its code, it's major.** When in doubt, it's major.

## Client discovery of the current version

Three surfaces, cheapest first:

1. **Agent card** (existing): `protocolVersion` top-level field at
   `/.well-known/agent-card.json`. Clients MUST read this, not hardcode.
2. **Response header** (new, additive): every `/v1/a2a/*` and `/api/a2a/*`
   response carries `X-SINCOR-Protocol: 1.0.1`. One header, no body parsing.
   (Implement as an after-request hook on the A2A blueprints — ~10 lines.)
3. **Discovery envelope** (new, per `docs/a2a/DISCOVERY_V2.md`): a
   `protocol` block in `/v1/a2a/agents` and `/v1/a2a/directory` responses:

```json
"protocol": {
  "version": "1.0.1",
  "deprecated": false,
  "sunset_at_ms": null,
  "changelog": "https://getsincor.com/docs/a2a/changelog"
}
```

When a version is deprecated, `deprecated: true` and `sunset_at_ms` is set.
Clients polling discovery see the deprecation without any out-of-band
announcement.

## Deprecation and grace periods

| Change | Notice | Grace period | Mechanism |
|---|---|---|---|
| PATCH | changelog entry | none | — |
| MINOR (additive) | changelog + card `protocolVersion` bump | none required (old clients unaffected) | — |
| MINOR with future breaking default (graduated path) | `deprecated: true` + `Sunset` HTTP header on affected endpoints + changelog | **≥ 30 days** | old default kept; new behavior opt-in via param |
| MAJOR | all of the above + direct notice to registered external agents (they gave us `caller_id`s — use them) | **≥ 90 days** | old major served in parallel during grace |

The **graduated path** is the normal way breaking-ish changes ship:

1. v1.1.0: new behavior available opt-in (e.g. `?live_only=false` explicitly
   still works; deprecation notice emitted for callers relying on the old
   default — detectable because they don't send the param).
2. After ≥30 days: v2.0.0 flips the default; v1.x served for 90 more days.
3. v1.x sunset: `sunset_at_ms` passes; old major returns `410 Gone` with a
   JSON body pointing at the changelog. Never a silent 404.

`Sunset` header format (RFC 8594): `Sunset: <HTTP-date>`.

## Changelog

New file: `docs/a2a/CHANGELOG.md`. Every version bump gets an entry:

```markdown
## 1.1.0 — 2026-10-15
### Added
- `GET /v1/a2a/agents`: `q`, `skill`, `min_reputation`, `sort`, `cursor` params.
### Deprecated
- `live_only` default `false` → will default `true` in 2.0.0 (see #<issue>).
```

No version ships without a changelog entry. Reviewers: check this like a
test.

## Rollout checklist for v1.1 (the next version)

1. Bump `A2A_PROTOCOL_VERSION` to `"1.1.0"` in `a2a_integration.py:118`.
2. Add `X-SINCOR-Protocol` after-request header on the A2A blueprints
   (`a2a_integration.A2ARouter`, `a2a_inbound_ext.mount`,
   `a2a_inbound_market.attach_market_routes`).
3. Add the `protocol` block to `/v1/a2a/agents` and `/v1/a2a/directory`
   (per `docs/a2a/DISCOVERY_V2.md` §2).
4. Create `docs/a2a/CHANGELOG.md` with the 1.1.0 entry.
5. If the release carries a graduated breaking change (e.g. the
   `live_only` default flip): set `deprecated: true`, emit `Sunset`
   headers, start the 30-day clock, log which callers still rely on the
   old default (they're the migration outreach list).
6. Update `docs/a2a/BIDDER_WALLET_FLOW.md` and the onboarding quickstart:
   "read `protocolVersion`, don't hardcode it."

## Non-goals

- Content negotiation (`Accept: application/vnd.sincor.v2+json`) — header
  + card field is enough at this scale; revisit if we ever serve two
  majors in parallel past grace.
- A versioned URL namespace (`/v2/a2a/...`) — same reason. One URL tree,
  versioned behavior via params and defaults, majors separated by grace
  periods, not paths.
