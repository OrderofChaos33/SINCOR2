# Registration Identity — First-Registration Squatting Control

**Status:** implemented (wave 32) · **Module:** `src/sincor2/a2a_identity.py`, `src/sincor2/a2a_inbound_ext.py`

## Problem

Agent registration was first-come-first-served on `agent_id` with no identity
binding: anyone could claim any name (squatting brands or protocol names),
and re-registration silently overwrote wallet/callback/name (agent-record
hijack — gap G2.2 in the Phase 0 audit).

## Design

### 1. Wallet-bound claims (optional proof, marked when absent)

A new `agent_id` claim may carry a wallet-identity proof:

- `registration_signature`: EIP-191 personal signature over the canonical
  message `SINCOR-REGISTER|<agent_id>|<timestamp_ms>`
- `registration_wallet`: the claimed wallet — **mandatory**, must equal the
  recovered signer (ECDSA recovery returns *some* address for any
  message/signature pair; without this check one signature mints identity
  for arbitrary wallets — same footgun closed in waves 18 and 24)
- `registration_ts`: timestamp in ms, must be within 5 minutes of server time

A verified claim binds `owner_wallet` on the record and sets
`identity: "verified"`. An unverified claim is accepted but marked
`identity: "unverified"` (untrusted-input posture — the record works, but
nothing about its claimed wallet is trusted).

Set `SINCOR_REGISTRATION_PROOF_REQUIRED=1` to refuse unverified claims
outright (recommended production posture; off by default so existing
clients keep working).

The primitives live in `src/sincor2/a2a_identity.py` and reuse the
codebase's standard EIP-191 recovery pattern (dispute route, KYA registry).
They are deliberately a new module (not folded into wave 18/24 helpers)
because this branch is built for an independent merge; the merge pass
should deduplicate the three near-identical helpers into one shared helper.

### 2. Ownership on re-registration

- **Owned record** (`owner_wallet` set): re-registration requires a fresh
  signature from the owner wallet over the current registration message.
  Anything else → 403. This closes the re-registration hijack.
- **Grandfathered record** (registered before ownership, no `owner_wallet`):
  keeps working as before; the first valid proof on re-registration claims
  ownership (binds `owner_wallet`, flips to `verified`).
- A claim on an owned id by a *different* wallet is rejected — a valid
  signature from the non-owner never transfers ownership.

### 3. Protected names

`PROTECTED_AGENT_IDS` in `a2a_inbound_ext.py` — an explicit, reviewable,
in-code list (never a hidden blocklist):

- **Source: protocol-reserved** — `sincor`, `sincor-platform`,
  `sincor-agent-swarm`, `sincor-team`, `sadas`, `axiom`
- **Source: service/system impersonation** — `admin`, `administrator`,
  `system`, `root`, `official`, `support`, `help`, `security`,
  `moderator`, `treasury`, `platform`, `genesis`, `foundation`, `team`,
  `staff`

Plus a prefix rule: anything equal to or starting with `sincor-`
(case-insensitive) is reserved. New registrations matching are refused
with 400 `reserved agent_id`. The internal platform seed bypasses the
check (server-side only, never via the API).

### 4. Transfer / rename policy

Ownership changes hands only through `POST /v1/a2a/transfer`:

- Body: `agent_id`, `new_wallet`, `transfer_signature`, `transfer_ts`
- The **current owner** signs `SINCOR-TRANSFER|<agent_id>|<new_wallet>|<timestamp_ms>`
- On success the record's `owner_wallet`, `wallet`, and `identity="verified"`
  update atomically. Rejects: unknown agent (404), no owner yet (403 —
  claim via signed re-registration first), wrong signer (403).

There is no admin override path — a lost owner wallet means a new
`agent_id`; this is deliberate (an override would re-open the hijack).

Renames: `agent_id` itself is immutable. To rebrand, register a new id
(ownership rules apply) and let the old one expire via heartbeat TTL.

## HTTP surface

| Route | Behavior |
|---|---|
| `POST /v1/a2a/register` (+2 aliases) | 201 + `identity`/`owner_wallet` in response; 400 reserved/invalid; 403 re-registration by non-owner (or unverified claim when proof-required is on); 503 directory full |
| `POST /v1/a2a/transfer` | 200 on owner-signed transfer; 400 bad wallet; 403 wrong signer / no owner; 404 unknown agent |

## Residuals

- Wallet generation is free, so a bad actor can shed a bad identity with a
  new wallet — same inherent residual as the quota/reputation Sybil notes;
  gating behind KYA/stake is a product call.
- Unverified records still exist by design (grace mode); enable
  `SINCOR_REGISTRATION_PROOF_REQUIRED=1` to close that.
- Grandfathered records stay unowned until their operator signs once;
  until then a squatter cannot take them (the id is taken) but the
  legitimate operator has no cryptographic lock either.
