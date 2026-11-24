"""SINCOR KYA v0 — in-process registry.

Persist: PersistentStore.kv (same SQLite DB /health already uses).
Redis only if a URL exists. File is last-resort and dies on Railway.

W-205: save() reloads the persisted registry under a cross-process
flock and merges per record instead of blindly overwriting the whole
blob from a possibly stale in-memory snapshot (the lost-update defect
that erased peer registrations and resurrected revocations).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform
    fcntl = None  # type: ignore[assignment]

from sincor2.card_sanitize import (
    NAME_MAX_LEN,
    VERSION_MAX_LEN,
    sanitize_card_text,
    sanitize_skills,
    safe_url,
)

WALLET_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
AXM = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
CHAIN_ID = 8453
MIN_STAKE_WEI = 10 * 10**18
VERIFY_FEE_WEI = 2 * 10**18
HEARTBEAT_TTL_MS = 60_000
LIVE_GRACE = 3
HEARTBEAT_SAVE_MIN_INTERVAL_MS = 5_000
STORE_KEY = "sincor:kya:records"
REDIS_KEY = STORE_KEY

# Identity-persistence policy (anti-whitewash).
# A wallet may back at most this many live (non-revoked) agent identities.
# Binding beyond the cap requires revoking one first — and revocation writes
# a tombstone, so shedding is visible rather than free.
MAX_IDENTITIES_PER_WALLET = 3
# Clean-exit timelock: an unstake request finalizes only after this long with
# no revocation and no open dispute hold. Rage-quitting to dodge a dispute
# does not work; honest quitting does.
UNSTAKE_TIMELOCK_MS = 7 * 24 * 60 * 60 * 1000

class KyaPersistenceError(RuntimeError):
    """Persisted KYA registry state is corrupt or cannot be trusted.

    Raised by save() (fail closed) instead of overwriting a blob this
    process could not parse — a corrupt registry is never silently
    wiped by a stale or blind save.
    """


# RLock (W-205): callers may hold the lock while calling back into
# lock-taking helpers (the family pattern — cf. proof_events W-212).
_LOCK = threading.RLock()
_STORE: Dict[str, Dict[str, Any]] = {}
_BY_AGENT: Dict[str, str] = {}
_BY_CARD: Dict[str, set] = {}
_TOMBSTONES: Dict[str, Dict[str, Any]] = {}
_LAST_HEARTBEAT_SAVE_MS = 0
# W-205 flock reentrancy depth: >0 means this process already holds the
# sidecar flock; nested save() calls join the outer transaction.
_FLOCK_DEPTH = 0
# W-205: True once this instance has loaded/ingested the persisted
# registry (or successfully saved), i.e. its in-memory state is based
# on the shared persisted registry and save() must merge against it.
# reset() clears it: a freshly reset registry is a new authoritative
# registry whose first save replaces, exactly as before W-205.
_SYNCED_WITH_PERSISTENCE = False


def _now_ms() -> int:
    return int(time.time() * 1000)


def reset() -> None:
    with _LOCK:
        _STORE.clear()
        _BY_AGENT.clear()
        _BY_CARD.clear()
        _TOMBSTONES.clear()
    global _LAST_HEARTBEAT_SAVE_MS, _SYNCED_WITH_PERSISTENCE
    _LAST_HEARTBEAT_SAVE_MS = 0
    _SYNCED_WITH_PERSISTENCE = False


def _redis_url() -> str:
    import os
    return (
        os.environ.get("KYA_REDIS_URL")
        or os.environ.get("REDIS_PRIVATE_URL")
        or os.environ.get("REDIS_URL")
        or ""
    ).strip()


def _redis():
    url = _redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore
        return redis.Redis.from_url(url, decode_responses=True, socket_timeout=3)
    except Exception:
        return None


def _sqlite():
    try:
        from sincor2.persistent_store import get_store
        return get_store()
    except Exception:
        return None


def _ingest(records) -> None:
    if not isinstance(records, list):
        return
    global _SYNCED_WITH_PERSISTENCE
    _SYNCED_WITH_PERSISTENCE = True
    with _LOCK:
        for rec in records:
            if isinstance(rec, dict) and rec.get("kya_id") and rec.get("agent_id"):
                _STORE[rec["kya_id"]] = rec
                _BY_AGENT[rec["agent_id"]] = rec["kya_id"]
                ch = rec.get("card_hash")
                if ch:
                    _BY_CARD.setdefault(ch, set()).add(rec["agent_id"])


def _ingest_tombstones(tombstones) -> None:
    if not isinstance(tombstones, list):
        return
    global _SYNCED_WITH_PERSISTENCE
    _SYNCED_WITH_PERSISTENCE = True
    with _LOCK:
        for t in tombstones:
            if isinstance(t, dict) and t.get("wallet"):
                _TOMBSTONES[str(t["wallet"]).lower()] = t


def _persist_path() -> Optional[Path]:
    import os
    override = os.environ.get("KYA_STORE_PATH")
    if override:
        return Path(override)
    try:
        from sincor2.data_paths import data_dir
        return data_dir() / "kya_records.json"
    except Exception:
        return Path("/tmp/kya_records.json")


def _parse_blob(raw) -> None:
    if not raw:
        return
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(parsed, dict):
        _ingest(parsed.get("records"))
        _ingest_tombstones(parsed.get("tombstones"))
    else:
        _ingest(parsed)


def load() -> None:
    store = _sqlite()
    if store is not None:
        try:
            raw = store.kv_get(STORE_KEY)
            if raw:
                _parse_blob(raw)
                return
        except Exception:
            pass
    client = _redis()
    if client is not None:
        try:
            raw = client.get(REDIS_KEY)
            if raw:
                _parse_blob(raw)
                return
        except Exception:
            pass
    path = _persist_path()
    if path is None or not path.is_file():
        return
    try:
        _parse_blob(path.read_text(encoding="utf-8"))
    except Exception:
        pass


@contextmanager
def _flock():
    """Cross-process exclusive flock on the persist path's sidecar
    ``.lock`` file (W-205, mirroring LockedJsonStore / proof_events
    W-212). Reentrant: nested entry by the flock holder joins the outer
    transaction. Caller must hold ``_LOCK``. When fcntl is unavailable
    the instance RLock alone guards the save (graceful fallback)."""
    global _FLOCK_DEPTH
    if _FLOCK_DEPTH > 0:
        _FLOCK_DEPTH += 1
        try:
            yield
        finally:
            _FLOCK_DEPTH -= 1
        return
    path = _persist_path()
    if fcntl is None or path is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(Path(str(path) + ".lock"), "a+b")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        _FLOCK_DEPTH = 1
        try:
            yield
        finally:
            _FLOCK_DEPTH = 0
    finally:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        finally:
            lock_fh.close()


def _read_persisted_raw():
    """Raw persisted blob from the first backend that has one, in
    load()'s priority order (PersistentStore kv -> Redis -> file).
    Backend operational errors fall through, mirroring load(); a
    backend that answers is authoritative for what is persisted."""
    store = _sqlite()
    if store is not None:
        try:
            raw = store.kv_get(STORE_KEY)
            if raw:
                return raw
        except Exception:
            pass
    client = _redis()
    if client is not None:
        try:
            raw = client.get(REDIS_KEY)
            if raw:
                return raw
        except Exception:
            pass
    path = _persist_path()
    if path is not None and path.is_file():
        try:
            raw = path.read_text(encoding="utf-8")
            if raw:
                return raw
        except OSError:
            pass
    return None


def _parse_blob_strict(raw):
    """Parse a persisted blob for save()'s merge. Fail closed: any
    malformed content raises KyaPersistenceError so save() refuses
    instead of silently wiping state it could not read."""
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError) as exc:
        raise KyaPersistenceError(
            f"persisted KYA registry is corrupt (invalid JSON): {exc}"
        ) from exc
    if isinstance(parsed, dict):
        records = parsed.get("records")
        tombstones = parsed.get("tombstones")
    elif isinstance(parsed, list):
        records, tombstones = parsed, None
    else:
        raise KyaPersistenceError(
            "persisted KYA registry is corrupt (not an object/array)"
        )
    if records is not None and not isinstance(records, list):
        raise KyaPersistenceError(
            "persisted KYA registry is corrupt (records not a list)")
    if tombstones is not None and not isinstance(tombstones, list):
        raise KyaPersistenceError(
            "persisted KYA registry is corrupt (tombstones not a list)")
    return records or [], tombstones or []


def _record_wins(candidate, incumbent):
    """Per-record merge verdict (W-205): revocation is sticky — a
    revoked version always beats a live one, so a stale save can never
    resurrect a revoked agent. Otherwise the newest updated_at wins;
    ties go to the candidate (the version being merged in last)."""
    cand_revoked = bool(candidate.get("revoked"))
    inc_revoked = bool(incumbent.get("revoked"))
    if cand_revoked != inc_revoked:
        return cand_revoked

    def _ts(rec) -> int:
        try:
            return int(rec.get("updated_at") or 0)
        except (TypeError, ValueError):
            return 0

    return _ts(candidate) >= _ts(incumbent)


def _merge_registry(persisted_records, persisted_tombstones,
                    memory_records, memory_tombstones):
    """Merge the persisted registry with the in-memory one, per record.

    Records are keyed by kya_id, collapsing any agent_id duplicates to
    the winning version; tombstones merge as a union keyed by wallet
    (sticky — a tombstone is never dropped by a peer that never saw
    it). Returns (records, tombstones) lists in the on-disk blob shape.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    by_agent: Dict[str, str] = {}

    def _offer(rec) -> None:
        if not isinstance(rec, dict) or not rec.get("kya_id") or not rec.get("agent_id"):
            return
        kya_id = rec["kya_id"]
        agent_id = rec["agent_id"]
        rival_id = by_agent.get(agent_id)
        if rival_id is not None and rival_id != kya_id:
            # Same agent under a different kya_id: one logical record.
            if _record_wins(rec, merged[rival_id]):
                del merged[rival_id]
            else:
                return
        incumbent = merged.get(kya_id)
        if incumbent is None or _record_wins(rec, incumbent):
            merged[kya_id] = rec
            by_agent[agent_id] = kya_id

    for rec in persisted_records:
        _offer(rec)
    for rec in memory_records:
        _offer(rec)

    tombs: Dict[str, Dict[str, Any]] = {}
    for t in list(persisted_tombstones) + list(memory_tombstones):
        if isinstance(t, dict) and t.get("wallet"):
            key = str(t["wallet"]).lower()
            incumbent = tombs.get(key)
            if incumbent is None or int(t.get("tombstoned_at") or 0) >= int(
                    incumbent.get("tombstoned_at") or 0):
                tombs[key] = t
    return list(merged.values()), list(tombs.values())


def _resync_memory(records, tombstones) -> None:
    """Rebuild the in-memory indexes from a merged registry. Caller
    holds ``_LOCK`` and the flock. Winning memory-side records keep
    their object identity; persisted-only records are adopted as-is."""
    with _LOCK:
        _STORE.clear()
        _BY_AGENT.clear()
        _BY_CARD.clear()
        _TOMBSTONES.clear()
        for rec in records:
            _STORE[rec["kya_id"]] = rec
            _BY_AGENT[rec["agent_id"]] = rec["kya_id"]
            ch = rec.get("card_hash")
            if ch:
                _BY_CARD.setdefault(ch, set()).add(rec["agent_id"])
        for t in tombstones:
            _TOMBSTONES[str(t["wallet"]).lower()] = t


def _write_payload(payload: str) -> bool:
    """Write via the legacy backend chain (first success wins).
    Returns True when some backend durably accepted the payload."""
    store = _sqlite()
    if store is not None:
        try:
            store.kv_set(STORE_KEY, payload)
            return True
        except Exception:
            pass
    client = _redis()
    if client is not None:
        try:
            client.set(REDIS_KEY, payload)
            return True
        except Exception:
            pass
    path = _persist_path()
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass  # already replaced (or never created)
        return True
    except Exception:
        return False


def save() -> None:
    """Persist the registry without losing peer writes (W-205).

    Under the instance RLock and the cross-process flock, reload the
    latest persisted blob and merge it per record with the in-memory
    state (revocations and tombstones sticky; per-record newest
    updated_at wins), then write the merged blob and re-sync memory to
    it. A corrupt persisted blob raises KyaPersistenceError — save()
    refuses rather than silently wiping the registry. An instance that
    has never loaded the persisted registry (fresh after reset()) is a
    new authoritative registry: its save replaces, as before W-205.
    """
    global _SYNCED_WITH_PERSISTENCE
    with _LOCK:
        with _flock():
            raw = _read_persisted_raw()
            persisted_records: List[Dict[str, Any]] = []
            persisted_tombstones: List[Dict[str, Any]] = []
            if raw:
                persisted_records, persisted_tombstones = _parse_blob_strict(raw)
            memory_records = list(_STORE.values())
            memory_tombstones = list(_TOMBSTONES.values())
            if _SYNCED_WITH_PERSISTENCE and raw:
                records, tombstones = _merge_registry(
                    persisted_records, persisted_tombstones,
                    memory_records, memory_tombstones,
                )
            else:
                records, tombstones = memory_records, memory_tombstones
            payload = json.dumps({
                "records": records,
                "tombstones": tombstones,
                "saved_at": _now_ms(),
            })
            if _write_payload(payload):
                _resync_memory(records, tombstones)
                _SYNCED_WITH_PERSISTENCE = True


def _write_tombstone(wallet: str, agent_id: str, card_hash: str, reason: str) -> Dict[str, Any]:
    """Record a dead identity. Tombstones are append-mostly history: revoking
    or ghost-flagging an identity marks its wallet+card so a reborn identity
    behind the same wallet or card is flagged, not silently fresh."""
    t = {
        "wallet": wallet.lower(),
        "agent_id": agent_id,
        "card_hash": card_hash,
        "reason": reason,
        "tombstoned_at": _now_ms(),
    }
    with _LOCK:
        _TOMBSTONES[wallet.lower()] = t
    return t


def _identity_risk(agent_id: str, wallet: str, card_hash: str) -> Dict[str, Any]:
    """Whitewash detection: is this (new) identity a rebirth of a dead one?"""
    risk: Dict[str, Any] = {}
    with _LOCK:
        tomb = _TOMBSTONES.get(wallet.lower()) if wallet else None
        others = sorted(a for a in _BY_CARD.get(card_hash, set()) if a != agent_id) if card_hash else []
    if tomb:
        risk["tombstoned_wallet"] = {
            "prior_agent_id": tomb.get("agent_id"),
            "reason": tomb.get("reason"),
            "tombstoned_at": tomb.get("tombstoned_at"),
        }
    if others:
        risk["card_reuse"] = {
            "note": "same agent card previously listed under different agent_id(s)",
            "other_agent_ids": others,
        }
    return risk


def check_identity_risk(agent_id: str, wallet: str, card_hash: str = "") -> Dict[str, Any]:
    """Public whitewash detector for the registration path.

    Wraps :func:`_identity_risk`: is this (new) identity a rebirth of a dead
    one? Returns the risk map — empty when nothing looks risky. Key of
    interest to callers is ``tombstoned_wallet``: when present, the wallet
    is dead and the registration must be refused (fail closed). Never
    raises; a broken lookup reports {} rather than bricking registration.
    """
    try:
        return _identity_risk(agent_id or "", wallet or "", card_hash or "")
    except Exception:
        return {}


def _live_identities_for_wallet(wallet: str, exclude_agent_id: Optional[str] = None) -> int:
    wallet_l = wallet.lower()
    with _LOCK:
        return sum(
            1 for r in _STORE.values()
            if not r.get("revoked")
            and r.get("agent_id") != exclude_agent_id
            and (r.get("principal", "").lower() == wallet_l or r.get("agent_wallet", "").lower() == wallet_l)
        )


def tombstones_snapshot() -> Dict[str, Any]:
    with _LOCK:
        return {"count": len(_TOMBSTONES), "tombstones": list(_TOMBSTONES.values())}


def is_wallet_tombstoned(wallet: str) -> bool:
    """Public read: has this wallet been tombstoned (revoked or ghost-flagged)?

    Used by the recovery track's tombstone gate. Any tombstone — any reason —
    disqualifies the wallet from sponsored recovery: the platform does not
    front money to wallets it has killed.
    """
    if not wallet:
        return False
    with _LOCK:
        return wallet.lower() in _TOMBSTONES


def _canonical(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


def card_hash(card: Dict[str, Any]) -> str:
    return "0x" + hashlib.sha256(_canonical(card).encode()).hexdigest()


def make_kya_id(agent_id: str, principal: str, c_hash: str) -> str:
    raw = f"{agent_id}:{principal.lower()}:{c_hash}".encode()
    return "kya_" + hashlib.sha256(raw).hexdigest()[:16]


def _safe_url(value: Any) -> str:
    # W-82: hardened SSRF policy lives in card_sanitize (shared with
    # a2a_inbound). This wrapper preserves the historical call sites.
    return safe_url(value)


def _recover_eip191(message: str, signature: str) -> str:
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from sincor2.sig_canonical import require_low_s
    except Exception as exc:
        raise ValueError("signature verification unavailable") from exc
    try:
        require_low_s(signature)
        return str(Account.recover_message(encode_defunct(text=message), signature=signature))
    except Exception as exc:
        raise ValueError("bad signature") from exc


def _save_heartbeat_debounced() -> None:
    global _LAST_HEARTBEAT_SAVE_MS
    now_ms = _now_ms()
    if (now_ms - _LAST_HEARTBEAT_SAVE_MS) < HEARTBEAT_SAVE_MIN_INTERVAL_MS:
        return
    save()
    _LAST_HEARTBEAT_SAVE_MS = now_ms


def score(rec: Dict[str, Any]) -> int:
    sla = rec.get("sla") or {}
    jobs = rec.get("jobs") or {}
    live = 1 if rec.get("status") in ("verified", "staked", "bound") and _is_live(rec) else 0
    bound = 1 if rec.get("status") in ("bound", "staked", "verified") else 0
    staked = 1 if int(rec.get("stake_axm_wei") or 0) >= MIN_STAKE_WEI else 0
    uptime = float(sla.get("uptime_30d") or 0)
    completed = int(jobs.get("completed") or 0)
    failed = int(jobs.get("failed") or 0)
    total = completed + failed
    completion = (completed / total) if total else 0.0
    slashed = int(jobs.get("slashed") or 0)
    disputed = int(jobs.get("disputed") or 0)
    raw = 200 * live + 200 * bound + 200 * staked + 200 * uptime + 200 * completion - 50 * slashed - 100 * disputed
    return max(0, min(1000, int(raw)))


def _is_live(rec: Dict[str, Any]) -> bool:
    last = int((rec.get("sla") or {}).get("last_heartbeat_ms") or 0)
    return (_now_ms() - last) <= HEARTBEAT_TTL_MS * LIVE_GRACE if last else False


def refresh_status(rec: Dict[str, Any]) -> Dict[str, Any]:
    if rec.get("revoked"):
        rec["status"] = "revoked"
        rec["score"] = score(rec)
        return rec
    valid_until = ((rec.get("scope") or {}).get("valid_until") or "")
    expired = False
    if valid_until:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            expired = dt.timestamp() * 1000 < _now_ms()
        except Exception:
            expired = False
    if expired or (rec.get("status") == "verified" and not _is_live(rec)):
        rec["status"] = "expired"
    rec["score"] = score(rec)
    rec["updated_at"] = _now_ms()
    return rec


def list_from_inbound(agent: Dict[str, Any], card: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    agent_id = str(agent.get("agent_id") or "")
    if not AGENT_ID_RE.match(agent_id):
        raise ValueError("bad agent_id")
    wallet = str(agent.get("wallet") or "")
    if wallet and not WALLET_RE.match(wallet):
        raise ValueError("bad wallet")
    card = card or {
        "id": agent_id,
        "name": agent.get("name"),
        "description": agent.get("description"),
        "version": agent.get("version"),
        "skills": agent.get("skills") or [],
    }
    c_hash = card_hash(card)
    principal = wallet or "0x0000000000000000000000000000000000000000"
    kya_id = make_kya_id(agent_id, principal, c_hash)
    clean_skills = sanitize_skills(agent.get("skills"))
    rec = {
        "kya_id": kya_id,
        "agent_id": agent_id,
        "principal": principal,
        "agent_wallet": wallet or principal,
        "card_url": _safe_url(agent.get("rpc_callback")),
        "card_hash": c_hash,
        "name": sanitize_card_text(agent.get("name") or agent_id, NAME_MAX_LEN),
        "version": sanitize_card_text(agent.get("version") or "0.0.0", VERSION_MAX_LEN),
        "skills": clean_skills,
        "status": "listed",
        "scope": {
            "max_axm_per_tx": str(5 * 10**18),
            "daily_axm_limit": str(50 * 10**18),
            "allowed_skills": [s.get("id") for s in clean_skills if isinstance(s, dict)],
            "allowed_counterparties": [],
            "valid_until": "2027-01-01T00:00:00Z",
        },
        "stake_axm_wei": "0",
        "stake_tx": None,
        "score": 0,
        "sla": {
            "last_ok": False,
            "last_heartbeat_ms": int(agent.get("last_heartbeat") or 0),
            "uptime_30d": 0.0,
            "last_proof_tx": None,
            "latency_ms": None,
        },
        "jobs": {"completed": 0, "failed": 0, "disputed": 0, "slashed": 0},
        "attestation": None,
        "revoked": False,
        "revoke_reason": None,
        "created_at": _now_ms(),
        "updated_at": _now_ms(),
        "chain_id": CHAIN_ID,
        "identity_risk": _identity_risk(agent_id, principal, c_hash),
        "unstake_requested_at": None,
        "dispute_hold": False,
    }
    rec["score"] = score(rec)
    with _LOCK:
        _STORE[kya_id] = rec
        _BY_AGENT[agent_id] = kya_id
        _BY_CARD.setdefault(c_hash, set()).add(agent_id)
    save()
    return rec


def bind(agent_id: str, principal: str, signature: str, message: str, recovered: Optional[str] = None) -> Dict[str, Any]:
    if not WALLET_RE.match(principal):
        raise ValueError("bad principal")
    rec = get_by_agent(agent_id)
    if rec is None:
        raise KeyError("unknown agent")
    if rec.get("revoked"):
        raise ValueError("revoked")
    # Fail closed: a caller-supplied `recovered` address is only an
    # assertion. The signature must cryptographically verify, and the
    # recovered signer must match the assertion; a malformed signature
    # rejects the bind outright instead of trusting the voucher.
    verified_addr = _recover_eip191(message=message, signature=signature)
    if recovered is not None and str(recovered).lower() != verified_addr.lower():
        raise ValueError("signer mismatch")
    recovered_addr = verified_addr
    signer = recovered_addr.lower()
    if signer != principal.lower():
        raise ValueError("signer mismatch")
    # Wallet cardinality: one wallet backs at most MAX_IDENTITIES_PER_WALLET
    # live identities. Binding beyond the cap requires revoking one first,
    # which writes a tombstone — shedding stays visible.
    if _live_identities_for_wallet(principal, exclude_agent_id=agent_id) >= MAX_IDENTITIES_PER_WALLET:
        raise ValueError("wallet identity cap reached; revoke an existing identity first")
    rec["principal"] = principal
    rec["attestation"] = {"scheme": "eip191", "message": message, "signature": signature, "recovered": recovered_addr}
    rec["status"] = "bound"
    rec["identity_risk"] = _identity_risk(agent_id, principal, rec.get("card_hash") or "")
    refresh_status(rec)
    save()
    return rec


def apply_stake(kya_id: str, stake_wei: str, stake_tx: str) -> Dict[str, Any]:
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    if rec.get("revoked"):
        raise ValueError("revoked")
    rec["stake_axm_wei"] = str(int(stake_wei))
    rec["stake_tx"] = stake_tx
    rec["status"] = "staked" if int(stake_wei) >= MIN_STAKE_WEI else rec.get("status")
    refresh_status(rec)
    save()
    return rec


def verify(kya_id: str) -> Dict[str, Any]:
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    if rec.get("revoked"):
        raise ValueError("revoked")
    if not rec.get("attestation"):
        raise ValueError("not bound")
    if int(rec.get("stake_axm_wei") or 0) < MIN_STAKE_WEI:
        raise ValueError("stake below minimum")
    if not _is_live(rec):
        raise ValueError("heartbeat stale")
    rec["status"] = "verified"
    refresh_status(rec)
    save()
    return rec


def heartbeat(agent_id: str, ok: bool = True, latency_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    rec = get_by_agent(agent_id)
    if rec is None:
        return None
    sla = rec.setdefault("sla", {})
    sla["last_heartbeat_ms"] = _now_ms()
    sla["last_ok"] = bool(ok)
    if latency_ms is not None:
        try:
            sla["latency_ms"] = int(latency_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError("bad latency_ms") from exc
    if rec.get("status") == "expired" and rec.get("attestation") and int(rec.get("stake_axm_wei") or 0) >= MIN_STAKE_WEI:
        rec["status"] = "verified"
    refresh_status(rec)
    _save_heartbeat_debounced()
    return rec


def post_sla(kya_id: str, ok: bool, latency_ms: int, proof_tx: Optional[str] = None) -> Dict[str, Any]:
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    sla = rec.setdefault("sla", {})
    sla["last_ok"] = bool(ok)
    sla["latency_ms"] = int(latency_ms)
    sla["last_heartbeat_ms"] = _now_ms()
    if proof_tx:
        sla["last_proof_tx"] = proof_tx
    prev = float(sla.get("uptime_30d") or 0)
    sla["uptime_30d"] = min(1.0, prev * 0.99 + (1.0 if ok else 0.0) * 0.01)
    refresh_status(rec)
    save()
    return rec


def revoke(kya_id: str, reason: str = "") -> Dict[str, Any]:
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    rec["revoked"] = True
    rec["revoke_reason"] = reason
    rec["status"] = "revoked"
    _write_tombstone(
        rec.get("agent_wallet") or rec.get("principal") or "",
        rec.get("agent_id") or "",
        rec.get("card_hash") or "",
        reason or "revoked",
    )
    refresh_status(rec)
    save()
    return rec


def flag_ghost(agent_id: str) -> Optional[Dict[str, Any]]:
    """Mark the wallet+card behind a ghosted identity. The KYA record itself
    is left for the market layer's reputation reset; the tombstone makes the
    shed visible so the next identity behind the same wallet or card is
    flagged by _identity_risk(). Never raises — shedding detection must not
    break auction close."""
    try:
        rec = get_by_agent(agent_id)
        if rec is None:
            return None
        tomb = _write_tombstone(
            rec.get("agent_wallet") or rec.get("principal") or "",
            rec.get("agent_id") or "",
            rec.get("card_hash") or "",
            "ghosting",
        )
        jobs = rec.setdefault("jobs", {})
        jobs["slashed"] = int(jobs.get("slashed") or 0) + 1
        refresh_status(rec)
        save()
        return tomb
    except Exception:
        return None


def request_unstake(kya_id: str) -> Dict[str, Any]:
    """Begin a clean exit: starts the timelock. The agent keeps its status
    until finalize; it may still heartbeat and work during the wait."""
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    if rec.get("revoked"):
        raise ValueError("revoked")
    if int(rec.get("stake_axm_wei") or 0) <= 0:
        raise ValueError("no stake locked")
    rec["unstake_requested_at"] = _now_ms()
    refresh_status(rec)
    save()
    return rec


def set_dispute_hold(kya_id: str, held: bool) -> Optional[Dict[str, Any]]:
    """Adjudication hook: an open dispute blocks unstake finalization so an
    agent cannot rage-quit to dodge a ruling."""
    rec = get(kya_id)
    if rec is None:
        return None
    rec["dispute_hold"] = bool(held)
    save()
    return rec


def finalize_unstake(kya_id: str) -> Dict[str, Any]:
    """Complete a clean exit after the timelock. Returns the released amount;
    the caller (custody layer) moves the funds — the registry only clears
    the accounting so the stake cannot be double-counted."""
    rec = get(kya_id)
    if rec is None:
        raise KeyError("unknown kya")
    if rec.get("revoked"):
        raise ValueError("revoked")
    requested = rec.get("unstake_requested_at")
    if not requested:
        raise ValueError("no unstake requested")
    if _now_ms() - int(requested) < UNSTAKE_TIMELOCK_MS:
        raise ValueError("timelock active")
    if rec.get("dispute_hold"):
        raise ValueError("dispute hold active")
    released = str(int(rec.get("stake_axm_wei") or 0))
    rec["stake_axm_wei"] = "0"
    rec["stake_tx"] = None
    rec["unstake_requested_at"] = None
    rec["status"] = "bound"
    refresh_status(rec)
    save()
    return {"kya_id": kya_id, "released_axm_wei": released}


def get(kya_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _STORE.get(kya_id)


def get_by_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        kid = _BY_AGENT.get(agent_id)
        return _STORE.get(kid) if kid else None


def lookup_wallet(wallet: str) -> List[Dict[str, Any]]:
    wallet_l = wallet.lower()
    with _LOCK:
        return [
            dict(r)
            for r in _STORE.values()
            if r.get("principal", "").lower() == wallet_l or r.get("agent_wallet", "").lower() == wallet_l
        ]


def backend_name() -> str:
    if _sqlite() is not None:
        return "sqlite"
    if _redis() is not None:
        return "redis"
    return "ephemeral-file"


def snapshot() -> Dict[str, Any]:
    with _LOCK:
        recs = list(_STORE.values())
    return {
        "token": AXM,
        "chain_id": CHAIN_ID,
        "backend": backend_name(),
        "store_key": STORE_KEY,
        "min_stake_wei": str(MIN_STAKE_WEI),
        "verify_fee_wei": str(VERIFY_FEE_WEI),
        "listed": len(recs),
        "verified": sum(1 for r in recs if r.get("status") == "verified"),
        "revoked": sum(1 for r in recs if r.get("revoked")),
        "tombstoned": len(_TOMBSTONES),
        "max_identities_per_wallet": MAX_IDENTITIES_PER_WALLET,
        "unstake_timelock_ms": UNSTAKE_TIMELOCK_MS,
    }


def live_statuses(kya_ids: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """Refresh and return {kya_id: status} for KYA records.

    Single lock acquisition — read paths (directory listings) use this
    instead of N get_by_agent()/refresh_status() round-trips. Refresh is
    in-memory only (no save()); revocation always wins via refresh_status().
    """
    with _LOCK:
        if kya_ids is None:
            recs = list(_STORE.values())
        else:
            recs = [_STORE[k] for k in kya_ids if k in _STORE]
        out: Dict[str, str] = {}
        for rec in recs:
            refresh_status(rec)
            out[rec["kya_id"]] = rec.get("status")
        return out


def hook_listed(agent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        if not agent or not agent.get("agent_id"):
            return None
        return list_from_inbound(agent)
    except Exception:
        return None


def hook_heartbeat(agent_id: str, ok: bool = True) -> Optional[Dict[str, Any]]:
    try:
        return heartbeat(agent_id, ok=ok)
    except Exception:
        return None


load()
