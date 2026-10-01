# Shared Durable-State Adoption Guide (wave 30)

**Status:** merge-time instruction. This branch is on base `fd96801` and
changes no production code. The canonical shared-state module lives on
`xioix/buildout-15-durable-state` (`src/sincor2/a2a_shared_state.py`) and
stays there — this guide tells the merge exactly how each parallel feature
adopts it.

**Why:** rate-limit counters, idempotency keys, and quota counters lived in
per-process memory. Under multi-worker gunicorn workers disagree; on
restart everything resets. Wave 15 built the one backend interface
(`SharedState` + memory/SQLite/Redis drivers, `get_shared_state()`
singleton, fail-closed `StateStoreUnavailable`). The three parallel waves
built against their own in-process stores from the same base. This guide
maps each onto the shared backend with exact before/after edits.

**Verified:** every adapter below was executed against wave-15's real
interface (32 checks green, incl. SQLite restart survival) — see
`_verification/w30_verify_adapters.py` run evidence in the wave-30 report.
Adapters are NOT copied into this branch to avoid a duplicate-source
situation with wave 15; they belong in `a2a_shared_state.py` (or a new
`a2a_shared_state_adapters.py`) when the merge lands.

---

## 1. Rate limits — wave 16 (`a2a_rate_limits.py`) onto the shared store

**Situation.** Wave 16 rewrote `a2a_rate_limits.py` with a pluggable
`RateLimitStore` Protocol:

```python
class RateLimitStore(Protocol):
    def check(self, policy, client_key, windows, now) -> (allowed, info): ...
    def clear(self) -> None: ...
```

with `MemoryRateLimitStore` as default, and `_ENFORCER = SlidingWindowLimiter()`
(wave 16, `a2a_rate_limits.py:510`). Its own docstring says "wave 15 can
swap in a durable store implementing the same interface with no other code
changes." The adapter below satisfies that Protocol on `SharedState`:

```python
class SharedRateLimitStore:
    """Satisfies wave-16's RateLimitStore Protocol on a SharedState."""
    def __init__(self, state, domain: str = "a2a-rl"):
        self._state = state
        self._domain = domain

    def check(self, policy, client_key, windows, now):
        tuples = [(float(w.seconds), float(w.max_hits)) for w in windows]
        result = self._state.claim_window_hit(
            self._domain, f"{policy}:{client_key}", now, tuples)
        info = {
            "retry_after": float(result.get("retry_after", 0.0)),
            "window_seconds": float(result.get("window_seconds", 0.0)),
            "window_max": float(result.get("window_max", 0.0)),
        }
        return bool(result["allowed"]), info

    def clear(self):
        self._state.clear()
```

`claim_window_hit` returns exactly wave-16's info shape (`allowed`,
`retry_after`, `window_seconds`, `window_max`), records the hit only on
allow, and is atomic on every driver — the Protocol's one hard
requirement. Bucket parts are URL-quoted inside `namespaced()`, so a
client key containing `:` can never alias another bucket (verified).

**Merge edits** (file: `src/sincor2/a2a_rate_limits.py`):

BEFORE (wave 16):
```python
_ENFORCER = SlidingWindowLimiter()
```

AFTER:
```python
from sincor2.a2a_shared_state import get_shared_state  # wave-15 module

_ENFORCER = SlidingWindowLimiter(store=SharedRateLimitStore(get_shared_state()))
```

**Two mandatory companions:**

1. **Wall clock, not monotonic.** Wave 16's `SlidingWindowLimiter`
   defaults to `time.monotonic`; wave 15's durable drivers require wall
   clock — monotonic resets on every boot, which makes pre-restart hits
   look fresh forever on the shared backend (this exact bug was caught in
   wave 15's self-review). Pass `clock=time.time`:
   ```python
   _ENFORCER = SlidingWindowLimiter(clock=time.time,
                                    store=SharedRateLimitStore(get_shared_state()))
   ```

2. **Fail-closed 503 on store failure.** Wave 16's `a2a_rate_limit_check`
   calls `_ENFORCER.check(...)` with no error handling. Wrap it (this is
   what wave 15 did to the same file on its own branch):
   ```python
   from sincor2.a2a_shared_state import StateStoreUnavailable
   try:
       allowed, info = _ENFORCER.check(policy, client_key)
   except StateStoreUnavailable:
       resp = jsonify({"error": "state_store_unavailable", "status": 503,
                       "policy": policy,
                       "detail": "shared rate-limit store unreachable; request denied"})
       resp.status_code = 503
       resp.headers["Retry-After"] = "5"
       return resp
   ```

**Merge-order note.** Waves 15 and 16 both edited `a2a_rate_limits.py`
from the same base. Keep BOTH halves: wave 15's shared-state wiring +
wave 16's policies/limits/SSE/wiring. Do not take either side wholesale.

---

## 2. Free quota — wave 18 (`a2a_integration.py`) onto the shared store

**Situation.** Wave 18 defined its own `QuotaStore` Protocol in
`a2a_integration.py:1463` with `_MemoryQuotaStore` behind it, and its
module docstring explicitly says: "The durable-state wave replaces this
with a shared backend — call sites only see the QuotaStore protocol."
The adapter:

```python
_QUOTA_WINDOW_SECONDS = 10 * 365 * 86400  # lifetime free quota, not rolling

class SharedQuotaStore:
    """Satisfies wave-18's QuotaStore Protocol on a SharedState."""
    def __init__(self, state, domain: str = "a2a-quota"):
        self._state = state
        self._domain = domain

    def get(self, identity_key, skill_id): ...
        # informational display only; reads a mirror counter

    def try_consume(self, identity_key, skill_id, limit):
        if limit <= 0:
            return False
        result = self._state.claim_window_hit(
            self._domain, f"{str(identity_key).lower()}:{skill_id}",
            time.time(), [(_QUOTA_WINDOW_SECONDS, float(limit))])
        if not result["allowed"]:
            return False
        self._state.incr(f"count:{str(identity_key).lower()}:{skill_id}")
        return True
```

`claim_window_hit` is the atomic check-and-consume the Protocol demands:
exactly one hit is recorded on allow, nothing on deny, so concurrent
requests cannot overshoot the quota — on any driver. Identity keys are
lowercased wallet addresses (wave 18's verified identity); bucket quoting
prevents aliasing. The `incr` mirror feeds the informational `get()`
("remaining" display); the atomic gate above is the source of truth, so a
store failure between them fails closed via `StateStoreUnavailable`
rather than desyncing.

**Merge edits** (file: `src/sincor2/a2a_integration.py`): replace the
`_MemoryQuotaStore()` instantiation feeding `_FreeQuotaTracker` with
`SharedQuotaStore(get_shared_state())`. Keep `_MemoryQuotaStore` for
tests (`A2A_STATE_STORE` unset in dev/test already selects the memory
driver, so behavior there is unchanged).

**Caveat — quota sweep.** The 10-year window raises the driver's
stale-row sweep bound (`_longest_window` is per driver instance). Quota
rows are one tiny entry per identity+skill, so suppressed sweeping is a
non-issue; do NOT "fix" it by shortening the window — that would turn a
lifetime quota into a rolling one.

---

## 3. Write idempotency — wave 14 (`a2a_idempotency.py`) onto the shared store

**Situation.** Wave 14 built a full Stripe-style protocol
(`begin` → fresh/replay/conflict/inflight, `complete`, `discard`) on its
own dedicated SQLite file (`a2a_idempotency.db`). **That store is already
durable and multi-process safe** (`UNIQUE(key)`, WAL). Shared-state
adoption for idempotency is therefore OPTIONAL — it buys cross-host
coordination (Redis), not restart survival. Do not regress the protocol
to wave 15's simpler `claim`-only `IdempotencyStore`; the fingerprint
binding (422 on different request), in-flight reclaim, and TTL expiry
are load-bearing. If/when adoption happens, preserve the full protocol:

- `begin`: `add_if_absent` → fresh; existing record → adjudicate
  (inflight → 409 / stale → reclaim via `set`; complete+scope+hash match
  → replay; else 422 conflict). Expiry via driver TTL (no purge sweep).
- `complete`: `set` the completed record with the idempotency TTL.
- `discard`: `delete`.
- Keep wave-14's strict key regex at the decorator level.

**Atomicity caveat (documented, not hidden):** the stale in-flight
reclaim (`set` after a stale read) is not atomic across hosts — two
workers can both observe a stale marker and both execute. Wave-14's
SQLite narrowed the same race with `UNIQUE` + `IntegrityError` retry;
on the shared backend the window is wider. Keep the in-flight TTL short
(120s) and route strict exactly-once deployments at single-primary
Redis.

The verified adapter (`SharedIdempotencyStore`, 9 behavior checks green)
is the reference implementation.

---

## 4. Settlement-idempotency residual — RESOLVED, no re-add needed

Driver-state deferred item: *"Settle route idempotency: wave 10 flagged
repeated /api/a2a/settle calls re-record reputation/fee entries —
covered by wave 14. Verify on w14 review."*

Verified: wave 14's branch decorates the settle route —
`src/sincor2/a2a_integration.py:1961`:
`@idempotent("settle", error=_a2a_idempotency_error)` on
`POST /api/a2a/settle`, plus `tasks.create`, `bids.place/commit/reveal`
scopes. The residual is covered; nothing to re-add. The merge must keep
that decorator on the settle route.

---

## 5. Global cautions for the merge

- **One canonical home.** `a2a_shared_state.py` stays on wave 15's
  branch. Adapters go in that module (or a sibling
  `a2a_shared_state_adapters.py`); never copy them into the feature
  modules — features depend on shared state, never the reverse.
- **`clear()` is blast-radius-wide.** `SharedState.clear()` wipes the
  whole fabric namespace (rate-limit buckets AND quota counters AND
  idempotency keys). The per-feature `clear()`/`reset_*` test hooks
  currently call it. In the merged tree, either scope `clear()` per
  domain or use separate driver instances per feature in tests.
- **Env selection.** `A2A_STATE_STORE=memory|sqlite|redis` (unset →
  sqlite in production, memory in dev/test). `A2A_STATE_SQLITE_PATH`
  overrides the file for tests. An explicitly configured but
  unreachable store raises `StateStoreUnavailable` at resolution —
  fail-closed by design; every call site above must map it to HTTP 503.
- **Test plan on the merged tree:** `test_a2a_rate_limits.py`,
  `test_a2a_rate_limit_wiring.py`, `test_wave16_rate_limits_stream.py`,
  `test_a2a_idempotency.py`, `test_a2a_quota_identity.py`,
  `test_a2a_shared_state.py` — all green, no test rewritten.
