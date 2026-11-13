"""Launch bounty pool — offchain AXM reservation ledger for external-agent bounties.

Tracks AXM the operator has *reserved* for launch bounties (e.g. to fund
seeded task bounties that attract external agents). This is ledger-only
accounting in the same class as the offchain stake ledger: it does not move
funds on any chain. Onchain settlement of bounties, when it arrives, reads
this ledger as the reservation source of truth.

Funding
-------
The pool's reserve comes from the environment::

    SINCOR_LAUNCH_BOUNTY_AXM   total AXM the operator earmarks (default 0)

DEFAULT ZERO: with the variable unset the pool is unconfigured and
``fund()`` refuses — no real money moves unless the operator configures it.

Admin surface (see attach_market_routes in a2a_inbound_market.py)
-----------------------------------------------------------------
* ``POST /v1/a2a/pool/fund``      move reserve into the spendable pool
* ``POST /v1/a2a/pool/allocate``  reserve pool funds for a task (creates an
  allocation record; the task's bounty is then pool-backed)
* ``POST /v1/a2a/pool/release``   return an allocation to the pool unspent
* ``GET  /v1/a2a/pool``           public status (reserve/funded/allocated/
  available + allocation records) — intentionally unauthenticated.
  Carries ``ledger_verification`` (verify_ledger() aggregate: ok flag +
  discrepancy list; no secrets, no per-agent data) and the W-119
  ``rehydrate_blocked`` flag with its live obligations.

Admin credential (G2.13 — single unified surface)
-------------------------------------------------
Every A2A admin-gated route (pool writes, task-board admin delete,
sponsored-stake, recovery) uses ONE credential: the operator's
``ADMIN_PASSWORD`` environment variable, presented ONLY via the
``X-Admin-Key`` request header. Credentials are never accepted in request
bodies (secret-persistence risk in logs) and the expected value is never
logged. An unset ``ADMIN_PASSWORD`` disables the admin surface
(deny-by-default: 503). The legacy ``SINCOR_BOUNTY_POOL_ADMIN_KEY`` is
retired and no longer read.

Persistence: JSON file with atomic writes (tmp + os.replace), following the
fee_conversion_executor.py ledger pattern. ``SINCOR_BOUNTY_POOL_PATH``
overrides the file location (tests). Every save is ALSO mirrored into the
workflow checkpoint store (sincor2.workflow_state) as a hash-bound
snapshot: if the JSON ledger file is missing on boot (the 2026-11-02
redeploy wipe), the exact last-saved state is restored from the verified
checkpoint and the JSON file is rewritten. A present JSON file always
wins (W-118/W-119 semantics are unchanged); a corrupt checkpoint on the
missing-file path fails the boot loudly instead of booting empty. The
checkpoint DB location is ``SINCOR_MARKETPLACE_CHECKPOINT_PATH`` or,
by default, ``marketplace_checkpoints.db`` beside the ledger file.

The mirror is part of the save's durability, NOT best-effort: a save
whose mirror fails raises PoolError, poisons the in-memory pool (every
further money operation refuses until restart), and leaves durable
degraded markers in TWO independent failure domains — beside the
checkpoint DB and beside the ledger JSON itself. (A marker only beside
the checkpoint DB shares that store's failure domain: one event that
breaks the checkpoint store — e.g. its directory replaced by a failed
mount's placeholder file — also prevents the marker write, and a
later checkpoint restore resurrects spent allocations. The ledger's
directory has just proven writable by accepting the JSON write, so it
is an independent witness.) A boot that finds the ledger JSON missing
while EITHER marker exists fails closed instead of restoring a
checkpoint it knows may be one save behind — the crash-window
resurrection that would let one allocation be spent twice. The
markers are cleared only by the next successful mirror, and a clear
that fails also fails closed (poison + raise): a stale marker an
operator has not reconciled must never be silently forgotten.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.a2a.pool")

RESERVE_ENV = "SINCOR_LAUNCH_BOUNTY_AXM"
PATH_ENV = "SINCOR_BOUNTY_POOL_PATH"
# Unified A2A admin credential (G2.13): the single admin surface for every
# A2A admin-gated route is the operator's ADMIN_PASSWORD, presented only
# via the X-Admin-Key header. SINCOR_BOUNTY_POOL_ADMIN_KEY is retired and
# no longer read. Never accept admin credentials in request bodies.
ADMIN_KEY_ENV = "ADMIN_PASSWORD"

# Task states in which a pool-backed task's allocation is provably
# consumed (spent on settlement, released on expiry/rejection/delete).
# Every other state still holds a live claim on the pool: open, auction,
# assigned, proof_submitted, held — and "failed", because settlement
# spends ONLY on receipt.ok, so a failed task is adjudication-pending,
# not done. Unknown/missing states count as live (fail-loud direction).
_TERMINAL_TASK_STATES = {"settled", "expired", "rejected", "canceled"}

# Every key _save() writes, in a single payload, on every save. A
# persisted file carrying NONE of these keys was therefore never written
# by the pool — e.g. a ledger truncated to "{}" — and holds no pool state
# at all. _load() treats such a file as a failed (corrupt) load so the
# W-119 rehydrate guard engages instead of trusting the empty state.
_SAVE_STATE_KEYS = frozenset({
    "reserve_wei", "funded_wei", "spent_wei", "allocations",
    "history", "history_writes", "saved_at",
})

# W-141: the journal anchor. _save() persists only the last
# _JOURNAL_RETAIN entries, but verify_ledger() must reconcile the
# pool's FULL lifetime. Entries trimmed out of the retained window are
# folded into a persisted anchor — their replay totals plus a SHA-256
# hash chain over their exact contents — so verification is
# anchor (folded prefix) + retained-suffix replay, with exact coverage
# (anchor writes + retained entries == total writes) and a chain check
# (anchor digest extended over the suffix must land on the persisted
# head digest). Trimming therefore no longer blinds verification.
_JOURNAL_RETAIN = 500
_JOURNAL_GENESIS_DIGEST = hashlib.sha256(
    b"sincor2-bounty-pool-journal-v1").hexdigest()
_JOURNAL_ACTIONS = ("fund", "allocate", "release", "spend")


def _journal_chain_extend(digest_hex: str, entry: Dict[str, Any]) -> str:
    """Extend the journal hash chain by one entry (canonical JSON)."""
    canon = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        bytes.fromhex(digest_hex) + canon.encode("utf-8")).hexdigest()


def _replay_totals(entries: List[Dict[str, Any]]):
    """(funded, allocated, spent) replay totals for journal entries.

    Mirrors verify_ledger()'s action arithmetic exactly. Returns None
    when an entry carries an unknown action (replay impossible).
    """
    funded = allocated = spent = 0
    for e in entries:
        action = e.get("action")
        try:
            amt = int(e.get("amount_wei") or 0)
        except (TypeError, ValueError):
            amt = 0
        if action == "fund":
            funded += amt
        elif action == "allocate":
            allocated += amt
        elif action == "release":
            allocated -= amt
        elif action == "spend":
            allocated -= amt
            spent += amt
        else:
            return None
    return funded, allocated, spent


def _obligation_from_task(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A live pool-backed task record -> one rehydrate obligation."""
    if not task.get("pool_backed"):
        return []
    state = str(task.get("state") or task.get("status") or "")
    if state in _TERMINAL_TASK_STATES:
        return []
    try:
        bounty = float(task.get("bounty_axm") or 0)
    except (TypeError, ValueError):
        bounty = 0.0
    return [{"task_id": str(task.get("task_id") or task.get("id") or ""),
             "bounty_axm": bounty, "state": state}]


def _board_tasks_path() -> Optional[Path]:
    """Marketplace board file: SINCOR_A2A_TASKS_PATH override, else
    data_dir()/a2a_inbound_tasks.json. Mirrors
    a2a_inbound._tasks_persist_path without importing that module (the
    pool must stay import-cycle-free)."""
    override = os.environ.get("SINCOR_A2A_TASKS_PATH", "").strip()
    if override:
        return Path(override)
    try:
        from sincor2.data_paths import data_dir
        return data_dir() / "a2a_inbound_tasks.json"
    except Exception:
        return None


class PoolError(Exception):
    """Base pool error (maps to 400)."""


class PoolNotConfigured(PoolError):
    """Reserve is zero / operator has not configured the pool (maps to 503)."""


class InsufficientPoolFunds(PoolError):
    """Allocation exceeds available pool balance (maps to 400)."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _axm_to_wei(value: Any) -> int:
    return int(round(float(value) * 1e18))


def _wei_to_axm(wei: int) -> float:
    return int(wei) / 1e18


def _default_path() -> Path:
    override = os.environ.get(PATH_ENV, "").strip()
    if override:
        return Path(override)
    try:
        from sincor2.data_paths import data_dir
        return data_dir() / "a2a_bounty_pool.json"
    except Exception:
        return Path("a2a_bounty_pool.json")


def pool_admin_configured() -> bool:
    return bool(os.environ.get(ADMIN_KEY_ENV, "").strip())


class BountyPool:
    """JSON-persisted launch-bounty reservation ledger."""

    def __init__(self, path: Optional[str] = None, reserve_axm: Optional[float] = None) -> None:
        self.path = Path(path) if path else _default_path()
        if reserve_axm is None:
            try:
                reserve_axm = float(os.environ.get(RESERVE_ENV, "0") or 0)
            except (TypeError, ValueError):
                reserve_axm = 0.0
        self.reserve_wei = max(0, _axm_to_wei(reserve_axm))
        self._lock = threading.Lock()
        self._funded_wei = 0
        self._spent_wei = 0
        self._allocations: Dict[str, Dict[str, Any]] = {}
        self._history: List[Dict[str, Any]] = []
        # Total history records ever written (persists across restarts).
        # _save() retains only the last _JOURNAL_RETAIN entries; the
        # trimmed prefix is folded into the W-141 anchor below, so
        # verify_ledger() still reconciles the full lifetime (anchor +
        # retained suffix) instead of going inconclusive on any pool
        # that has ever passed the retention window.
        self._history_writes = 0
        self._history_maybe_trimmed = False
        # W-141 anchor: replay totals + hash-chain digest over the
        # leading journal entries folded out of the retained window.
        self._anchor_writes = 0
        self._anchor_funded_wei = 0
        self._anchor_allocated_wei = 0
        self._anchor_spent_wei = 0
        self._anchor_digest = _JOURNAL_GENESIS_DIGEST
        # True only when the anchor was synthesized from record state
        # at load (pre-anchor ledger whose trimmed entries are gone);
        # surfaced in verify_ledger()'s result so the checkpoint's
        # provenance is never misrepresented as journal-folded.
        self._anchor_bootstrapped = False
        # Hash-chain head over EVERY journal entry ever recorded
        # (anchor prefix + retained suffix); persisted on every save.
        self._journal_head_digest = _JOURNAL_GENESIS_DIGEST
        # Set by _load() when the persisted file exists but yields no
        # usable state (corrupt JSON / unreadable, or a stateless file
        # carrying none of the _save() keys): the in-memory state is
        # empty, but that emptiness is NOT a clean boot.
        self._load_failed = False
        # W-119: set when the ledger file was missing or corrupt on boot
        # while live pool-backed tasks still hold claims. While set, the
        # pool refuses to auto-fund AND refuses manual fund(): re-funding
        # the full reserve would promise the same AXM twice. Recovery is
        # by restart after the live tasks resolve (settlement/expiry
        # release or spend their backing, fail-safe) or after the ledger
        # file is restored from backup — a fresh boot then re-checks.
        self._rehydrate_blocked = False
        self._rehydrate_obligations: List[Dict[str, Any]] = []
        # Set by _load() when the JSON ledger was missing on boot and the
        # exact state came back from the workflow checkpoint store.
        self._checkpoint_restored = False
        # Set when a checkpoint mirror fails mid-save: the on-disk
        # checkpoint may now lag the JSON ledger, so this instance must
        # not move more money (a restart re-boots from the JSON, which
        # is current, or fails closed if the JSON is gone — see the
        # degraded marker in _mirror_to_checkpoint).
        self._persistence_degraded = False
        self._load()
        file_missing = not self.path.is_file()
        if self.reserve_wei > 0 and (file_missing or self._load_failed):
            obligations, inconclusive = self._live_pool_obligations()
            if obligations or inconclusive:
                self._rehydrate_blocked = True
                self._rehydrate_obligations = obligations
                logger.error(
                    "bounty pool ledger %s on boot with live pool-backed "
                    "tasks still holding claims; REFUSING to re-fund the "
                    "reserve (would double-promise %s AXM). Resolve the "
                    "live tasks or restore the ledger file, then restart. "
                    "Obligations: %s%s",
                    "missing" if file_missing else "corrupt",
                    _wei_to_axm(sum(
                        int(round(float(o.get("bounty_axm") or 0) * 1e18))
                        for o in obligations)),
                    obligations,
                    f" (check inconclusive: {inconclusive})"
                    if inconclusive else "")
                # Deliberately no _save(): a corrupt file is evidence for
                # the operator, and creating a warm empty file would make
                # the next boot trust the wiped state as-is.
            elif file_missing:
                # Fresh disk (first boot, or a redeploy wiped the ephemeral
                # ledger) with NO live obligations: the operator configured
                # a reserve via SINCOR_LAUNCH_BOUNTY_AXM, so rehydrate it
                # instead of silently refusing allocations until someone
                # re-funds by hand. A warm ledger file is always trusted
                # as-is; a corrupt file is never auto-funded (see above).
                self._funded_wei = self.reserve_wei
                self._record("fund", self.reserve_wei,
                             {"auto_rehydrate": True,
                              "reason": "fresh disk; reserve configured"})
                self._save()
                logger.warning(
                    "bounty pool ledger missing on boot; auto-funded %s AXM "
                    "from configured reserve", _wei_to_axm(self.reserve_wei))

    # -- persistence ------------------------------------------------------
    def _checkpoint_db_path(self) -> Path:
        from sincor2.workflow_state import marketplace_checkpoint_path
        return Path(marketplace_checkpoint_path(self.path))

    def _apply_payload(self, raw: Dict[str, Any]) -> None:
        """Apply a persisted ledger payload (the _save() shape) in memory."""
        self._funded_wei = int(raw.get("funded_wei") or 0)
        self._spent_wei = int(raw.get("spent_wei") or 0)
        allocs = raw.get("allocations") or {}
        if isinstance(allocs, dict):
            for aid, alloc in allocs.items():
                if isinstance(alloc, dict):
                    alloc = dict(alloc)
                    alloc["amount_wei"] = int(alloc.get("amount_wei") or 0)
                    self._allocations[aid] = alloc
        hist = raw.get("history")
        if isinstance(hist, list):
            self._history = hist
        # Total-write counter lets verify_ledger() detect a trimmed
        # journal. Files written before the counter existed carry a
        # history but no counter -> treat as maybe-trimmed.
        hw = raw.get("history_writes")
        if hw is None:
            self._history_maybe_trimmed = bool(self._history)
        else:
            try:
                self._history_writes = max(0, int(hw))
            except (TypeError, ValueError):
                self._history_maybe_trimmed = bool(self._history)
        # W-141 anchor restore. Files written before the anchor existed
        # (counter present, no anchor keys) go through the bootstrap in
        # _bootstrap_journal_anchor(); pre-counter files stay
        # maybe-trimmed (their gap cannot even be sized).
        anchor = raw.get("journal_anchor")
        if isinstance(anchor, dict):
            try:
                self._anchor_writes = max(0, int(anchor.get("writes") or 0))
                self._anchor_funded_wei = int(anchor.get("funded_wei") or 0)
                self._anchor_allocated_wei = int(
                    anchor.get("allocated_wei") or 0)
                self._anchor_spent_wei = int(anchor.get("spent_wei") or 0)
            except (TypeError, ValueError):
                self._history_maybe_trimmed = True
            digest = anchor.get("digest")
            if isinstance(digest, str) and digest:
                self._anchor_digest = digest
            self._anchor_bootstrapped = bool(anchor.get("bootstrapped"))
            head = raw.get("journal_head_digest")
            if isinstance(head, str) and head:
                self._journal_head_digest = head
            else:
                # Anchor without a persisted head: rebuild the head by
                # chaining the retained suffix onto the anchor digest.
                head = self._anchor_digest
                for e in self._history:
                    head = _journal_chain_extend(head, e)
                self._journal_head_digest = head
        elif not self._history_maybe_trimmed:
            self._bootstrap_journal_anchor()

    def _bootstrap_journal_anchor(self) -> None:
        """W-141 migration for pre-anchor ledger payloads.

        Called from _apply_payload() when the write counter exists but
        no anchor does (a file written before the anchor shipped):

        * Journal never actually trimmed (len == writes): the full
          history is in hand — chain it from genesis. Full strength,
          nothing synthesized.
        * Journal already trimmed (len < writes) — the live W-141
          shape: the folded entries are gone, so the anchor is
          synthesized as records minus retained-suffix replay and
          marked bootstrapped. Guarded: it engages only when the
          records are internally consistent (spent counter == sum of
          spent-state allocation records) and every synthesized anchor
          total is non-negative. Anything else leaves the journal
          maybe-trimmed (inconclusive), exactly as before the fix.
        """
        writes = self._history_writes
        retained = self._history
        if len(retained) == writes:
            head = _JOURNAL_GENESIS_DIGEST
            for e in retained:
                head = _journal_chain_extend(head, e)
            self._journal_head_digest = head
            return
        if len(retained) > writes:
            self._history_maybe_trimmed = True
            return
        suffix = _replay_totals(retained)
        if suffix is None:
            self._history_maybe_trimmed = True
            return
        record_spent = sum(
            int(a.get("amount_wei") or 0)
            for a in self._allocations.values()
            if a.get("state") == "spent")
        if record_spent != self._spent_wei:
            self._history_maybe_trimmed = True
            return
        s_funded, s_allocated, s_spent = suffix
        a_funded = self._funded_wei - s_funded
        a_allocated = self._allocated_wei() - s_allocated
        a_spent = self._spent_wei - s_spent
        if a_funded < 0 or a_allocated < 0 or a_spent < 0:
            self._history_maybe_trimmed = True
            return
        self._anchor_writes = writes - len(retained)
        self._anchor_funded_wei = a_funded
        self._anchor_allocated_wei = a_allocated
        self._anchor_spent_wei = a_spent
        self._anchor_digest = hashlib.sha256(
            ("sincor2-bounty-pool-journal-bootstrap-v1|"
             f"{self._anchor_writes}|{a_funded}|{a_allocated}|{a_spent}"
             ).encode("utf-8")).hexdigest()
        self._anchor_bootstrapped = True
        head = self._anchor_digest
        for e in retained:
            head = _journal_chain_extend(head, e)
        self._journal_head_digest = head
        logger.warning(
            "bounty pool ledger %s: pre-anchor journal was trimmed "
            "(%d of %d entries retained); bootstrapped a W-141 anchor "
            "from record state covering the first %d writes",
            self.path, len(retained), writes, self._anchor_writes)

    def _restore_from_checkpoint(self) -> bool:
        """Missing-JSON boot path: restore the exact last-saved state
        from the workflow checkpoint store and rewrite the JSON ledger.

        Returns False when no checkpoint snapshot exists (the caller
        then runs the historical W-119 missing-file logic). Raises
        PoolError when a checkpoint DB exists but is corrupt or fails
        verification — fail closed, never boot empty over checkpointed
        state.
        """
        db_path = self._checkpoint_db_path()
        if not db_path.is_file():
            return False
        try:
            from sincor2.workflow_state import (
                POOL_CHECKPOINT_NAME,
                POOL_RUN_ID,
                WorkflowStateStore,
                load_state_snapshot,
            )
            store = WorkflowStateStore.open(str(db_path))
            try:
                state = load_state_snapshot(
                    store, POOL_RUN_ID, POOL_CHECKPOINT_NAME)
            finally:
                store.close()
        except PoolError:
            raise
        except Exception as exc:
            raise PoolError(
                f"bounty pool checkpoint store {db_path} is corrupt or "
                f"failed verification ({exc}); refusing to boot empty "
                "over checkpointed pool state") from exc
        if state is None:
            return False
        self._apply_payload(state)
        self._checkpoint_restored = True
        logger.warning(
            "bounty pool ledger %s missing on boot; restored exact "
            "state from checkpoint store %s (funded=%s spent=%s "
            "allocations=%d)", self.path, db_path,
            _wei_to_axm(self._funded_wei), _wei_to_axm(self._spent_wei),
            len(self._allocations))
        # Rewrite the JSON ledger so legacy readers and the next warm
        # boot see the restored state. (_save also re-mirrors, which is
        # a harmless duplicate snapshot of identical state.)
        self._save()
        return True

    def _load(self) -> None:
        if not self.path.is_file():
            # A degraded marker means a previous save's checkpoint
            # mirror FAILED after its JSON write: the checkpoint may be
            # one save behind the state the operator already acted on.
            # With the JSON also gone there is no trustworthy source —
            # fail closed (operator reconciles) rather than resurrect
            # spent allocations for a second spend. Markers live in two
            # independent failure domains (beside the checkpoint DB and
            # beside this ledger file); EITHER one present — as any
            # filesystem object, not just a regular file — blocks the
            # checkpoint restore.
            markers = [p for p in self._degraded_marker_paths()
                       if p.exists()]
            if markers:
                raise PoolError(
                    "bounty pool persistence degraded marker(s) "
                    f"{', '.join(str(m) for m in markers)} "
                    "present and ledger file "
                    f"{self.path} missing: a previous save's checkpoint "
                    "mirror failed, so the checkpoint store may lag the "
                    "last committed state; refusing to boot from a "
                    "possibly stale checkpoint (double-spend risk). "
                    "Restore the ledger JSON from backup or reconcile "
                    "manually, then remove the marker(s) and restart.")
            # A missing ledger with a verified checkpoint snapshot is a
            # wiped disk, not a first boot: restore exactly. Without a
            # snapshot, fall through to the historical W-119 logic in
            # __init__ (auto-fund only when no live obligations exist).
            self._restore_from_checkpoint()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and not (_SAVE_STATE_KEYS & raw.keys()):
                # Parses, but carries no pool state at all (e.g. "{}"):
                # not a warm empty ledger — a wiped one. Route it through
                # the corrupt-file path so __init__ runs the W-119
                # rehydrate guard instead of trusting the empty state
                # (a manual fund() would otherwise re-fund the full
                # reserve over live claims).
                self._load_failed = True
                logger.warning(
                    "bounty pool file %s contains no pool state; "
                    "treating as corrupt (load failed)", self.path)
                return
            self._apply_payload(raw)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            self._load_failed = True
            logger.warning("bounty pool load failed (%s); starting empty", exc)

    def _live_pool_obligations(self):
        """Live pool-backed tasks visible in durable task sources.

        Returns (obligations, inconclusive). ``obligations`` is a list of
        {task_id, bounty_axm, state}. ``inconclusive`` is a string when a
        source could not be read at all — fail-closed: the caller must
        not auto-fund on an unproven empty answer.

        Sources (both read, either can block):
        1. The marketplace board file — the actual home of pool_backed
           task records (create_task persists pool_backed there).
        2. The pluggable task store (lazy import, no import cycle) as a
           second source, in case pool-backed records land there.
        Runs ONLY on the missing/corrupt-file boot path, never on a warm
        boot, so it adds no cost or side effects to normal operation.
        """
        obligations: List[Dict[str, Any]] = []
        tasks_path = _board_tasks_path()
        if tasks_path is None:
            return obligations, "board tasks path could not be determined"
        try:
            if tasks_path.is_file():
                raw = json.loads(tasks_path.read_text(encoding="utf-8"))
                tasks = raw.get("tasks") if isinstance(raw, dict) else raw
                items = (tasks.values() if isinstance(tasks, dict)
                         else (tasks or []))
                for task in items:
                    if isinstance(task, dict):
                        obligations.extend(_obligation_from_task(task))
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            return obligations, f"board tasks file {tasks_path} unreadable ({exc})"
        try:
            from sincor2.a2a_task_store import get_task_store
            for task in get_task_store().list_all():
                if isinstance(task, dict):
                    obligations.extend(_obligation_from_task(task))
        except Exception as exc:  # store backend failure must not read as "no tasks"
            return obligations, f"task store unreadable ({exc})"
        return obligations, None

    def _fold_journal_anchor(self) -> None:
        """Fold journal entries beyond the retained window into the
        W-141 anchor. Runs at save time (callers hold the pool lock, or
        it is boot) BEFORE the payload is built: the folded entries'
        replay totals and hash-chain segment move into the anchor and
        the entries leave the retained window, in memory and on disk
        alike, so len(_history) stays bounded by _JOURNAL_RETAIN and
        anchor writes + retained entries == total writes, exactly.

        An entry with an unknown action is never folded: the window
        grows past retention instead, so verify_ledger() reports the
        unknown action as inconclusive rather than laundering an
        unreplayable prefix into a trusted anchor.
        """
        excess = len(self._history) - _JOURNAL_RETAIN
        if excess <= 0:
            return
        folding = self._history[:excess]
        totals = _replay_totals(folding)
        if totals is None:
            return
        funded, allocated, spent = totals
        digest = self._anchor_digest
        for entry in folding:
            digest = _journal_chain_extend(digest, entry)
        self._anchor_funded_wei += funded
        self._anchor_allocated_wei += allocated
        self._anchor_spent_wei += spent
        self._anchor_digest = digest
        self._anchor_writes += excess
        del self._history[:excess]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fold_journal_anchor()
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = {
            "reserve_wei": str(self.reserve_wei),
            "funded_wei": str(self._funded_wei),
            "spent_wei": str(self._spent_wei),
            "allocations": {
                aid: {**a, "amount_wei": str(int(a.get("amount_wei") or 0))}
                for aid, a in self._allocations.items()
            },
            "history": self._history[-_JOURNAL_RETAIN:],
            "history_writes": self._history_writes,
            "journal_anchor": {
                "writes": self._anchor_writes,
                "funded_wei": str(self._anchor_funded_wei),
                "allocated_wei": str(self._anchor_allocated_wei),
                "spent_wei": str(self._anchor_spent_wei),
                "digest": self._anchor_digest,
                "bootstrapped": self._anchor_bootstrapped,
            },
            "journal_head_digest": self._journal_head_digest,
            "saved_at": _now_ms(),
        }
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        self._mirror_to_checkpoint(payload)

    def _degraded_marker_path(self) -> Path:
        """Checkpoint-side degraded marker (first location).

        Lives beside the checkpoint DB (independent of the checkpoint
        machinery itself, so it can be written even when that machinery
        is what failed) and survives deletion of the ledger JSON.
        """
        return Path(str(self._checkpoint_db_path()) + ".pool-degraded")

    def _ledger_degraded_marker_path(self) -> Path:
        """Ledger-side degraded marker (second location).

        Lives beside the ledger JSON — a directory whose writability
        ``_save()`` has just proven by writing the JSON itself, and an
        independent failure domain from the checkpoint store (the two
        may sit on different volumes: the checkpoint path is env-
        configurable separately from the ledger path). A file-level
        wipe of the ledger leaves this marker in place, still guarding
        the boot.
        """
        return Path(str(self.path) + ".pool-degraded")

    def _degraded_marker_paths(self) -> List[Path]:
        """Every degraded-marker location, deduplicated.

        The two locations coincide when the checkpoint DB and the
        ledger JSON live in the same directory under the same stem
        rules; callers must not write/check/clear the same file twice.
        """
        paths: List[Path] = []
        for candidate in (self._degraded_marker_path(),
                          self._ledger_degraded_marker_path()):
            if candidate not in paths:
                paths.append(candidate)
        return paths

    def _write_degraded_marker(self, exc: Exception) -> List[Path]:
        """Record a mirror failure durably in BOTH failure domains.

        Each location is attempted independently: the signal counts as
        durably recorded when AT LEAST ONE write lands (the boot path
        fails closed on either marker). Returns the locations actually
        written; when neither lands, both failures are error-logged —
        the in-memory poison still protects this process, but no
        durable signal survives it (two independent domains failing at
        once is outside this guard's envelope and needs an operator).
        """
        body = json.dumps({
            "at": _now_ms(),
            "reason": f"checkpoint mirror failed: {exc}",
            "funded_wei": str(self._funded_wei),
            "spent_wei": str(self._spent_wei),
            "ledger_path": str(self.path),
        })
        written: List[Path] = []
        for marker in self._degraded_marker_paths():
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(body, encoding="utf-8")
                written.append(marker)
            except Exception as marker_exc:
                logger.error(
                    "bounty pool degraded marker %s could not be "
                    "written (%s)", marker, marker_exc)
        if not written:
            logger.error(
                "bounty pool degraded marker could not be written in "
                "ANY location; the in-memory pool is poisoned, but no "
                "durable degraded signal survives this process")
        return written

    def _clear_degraded_markers(self) -> List[Path]:
        """Remove every degraded marker after a successful mirror.

        Returns the locations that could NOT be removed (empty list =
        fully cleared). A failed clear is never silently swallowed by
        the caller: the stale marker is a durable "durability
        unproven" signal, and forgetting it quietly would unguard the
        next JSON-wipe boot.
        """
        failed: List[Path] = []
        for marker in self._degraded_marker_paths():
            try:
                marker.unlink(missing_ok=True)
            except OSError as exc:
                logger.error(
                    "bounty pool degraded marker %s could not be "
                    "cleared (%s)", marker, exc)
                failed.append(marker)
        return failed

    def _mirror_to_checkpoint(self, payload: Dict[str, Any]) -> None:
        """Mirror a just-saved ledger payload into the workflow
        checkpoint store (hash-bound snapshot).

        The mirror is part of the save's durability contract, NOT
        best-effort. If it fails, the checkpoint may lag the JSON
        ledger by this save; restoring from it after a JSON wipe would
        resurrect spent allocations (a double-spend). So a failure:
        (1) writes the degraded markers in both failure domains (the
        boot path fails closed if it finds EITHER marker with the JSON
        missing), (2) poisons this instance so no further money moves
        on a diverged ledger, and (3) raises PoolError so the calling
        operation reports failure instead of silently committing. The
        next successful mirror clears both markers and the poison —
        and if a marker cannot be cleared, that fails closed too
        (poison + raise) rather than silently unguarding the next
        boot.
        """
        try:
            from sincor2.workflow_state import (
                POOL_CHECKPOINT_NAME,
                POOL_RUN_ID,
                WorkflowStateStore,
                save_state_snapshot,
            )
            store = WorkflowStateStore.open(str(self._checkpoint_db_path()))
            try:
                save_state_snapshot(store, POOL_RUN_ID,
                                    POOL_CHECKPOINT_NAME, payload)
            finally:
                store.close()
        except Exception as exc:
            self._persistence_degraded = True
            self._write_degraded_marker(exc)
            raise PoolError(
                f"bounty pool checkpoint mirror failed ({exc}); the "
                "save is NOT durably committed and this pool instance "
                "is poisoned until restart (ledger JSON at "
                f"{self.path} holds the newest state)") from exc
        if self._persistence_degraded:
            self._persistence_degraded = False
            logger.warning(
                "bounty pool checkpoint mirror recovered; degraded "
                "state cleared")
        # The mirror succeeded, so the checkpoint now holds exactly
        # this state and the degraded signal is stale. Clearing happens
        # ONLY here — after the successful mirror — and a marker that
        # cannot be removed fails closed: the pool re-poisons and the
        # save raises, because booting later over a forgotten-but-real
        # marker must stay an operator decision, never a silent one.
        clear_failed = self._clear_degraded_markers()
        if clear_failed:
            self._persistence_degraded = True
            raise PoolError(
                "bounty pool degraded marker(s) "
                f"{', '.join(str(m) for m in clear_failed)} could not "
                "be cleared after a successful checkpoint mirror; "
                "failing closed (pool poisoned) rather than silently "
                "unguarding a later boot — remove the marker(s) "
                "manually after reconciling and restart")

    def _raise_if_degraded(self) -> None:
        if self._persistence_degraded:
            raise PoolError(
                "bounty pool persistence is degraded: a previous "
                "save's checkpoint mirror failed, so this instance's "
                "durability is unproven; refusing money operations "
                "until restart (the ledger JSON holds the newest "
                "state)")

    def _record(self, action: str, amount_wei: int, detail: Dict[str, Any]) -> None:
        entry = {"action": action, "amount_wei": str(amount_wei),
                 "at": _now_ms(), "detail": detail}
        self._history.append(entry)
        self._history_writes += 1
        self._journal_head_digest = _journal_chain_extend(
            self._journal_head_digest, entry)

    def verify_ledger(self) -> Dict[str, Any]:
        """Reconcile the append-only history journal against record state.

        Genuine double-entry check (W-101 fix): the journal is replayed to
        compute expected funded/allocated totals, which must match the
        record-derived state. Catches record drift, phantom allocations,
        and journal/record disagreement — the UNCX bug class — which the
        derived identity ``available = funded - allocated`` cannot see
        (it holds for corrupted books by construction).

        W-118 extension: the replay ALSO covers the spent counter — the
        exact field allocate() gates on via
        ``available = funded - spent - allocated``. Journal spend totals
        must match both the _spent_wei counter and the sum of spent-state
        allocation records; a drift mutant zeroing spent_wei previously
        passed verify green while inflating available.

        W-141 extension: the replay no longer needs the full journal in
        hand. Entries trimmed out of the retained window were folded
        into a persisted anchor (replay totals + hash chain) at save
        time, so verification is anchor totals + retained-suffix
        replay, gated on exact coverage (anchor writes + retained
        entries == total writes) and on the hash chain (anchor digest
        extended over the suffix must land on the recorded head
        digest). A pre-anchor ledger file whose journal was already
        trimmed gets a record-bootstrapped anchor at load (flagged in
        the result); without one, trimming is no longer a reason to
        report inconclusive.

        Returns {"ok": bool, "discrepancies": [...], "inconclusive": str|None}.
        ``inconclusive`` is set (instead of a false pass) only when the
        journal genuinely cannot cover the pool's lifetime — a
        pre-counter journal, a coverage gap, a failed anchor bootstrap
        — or when an unknown action appears in the journal.
        """
        with self._lock:
            discrepancies = []
            inconclusive = None
            if self._history_maybe_trimmed:
                inconclusive = (
                    "journal predates the write counter (or failed "
                    "anchor bootstrap); replay cannot cover full "
                    "lifetime")
            elif (self._anchor_writes + len(self._history)
                    != self._history_writes):
                inconclusive = (
                    "journal coverage gap: anchor writes "
                    f"({self._anchor_writes}) + retained entries "
                    f"({len(self._history)}) != total writes "
                    f"({self._history_writes}); replay cannot cover "
                    "full lifetime")
            else:
                # Hash chain: the anchor digest extended over the
                # retained suffix must land on the recorded head
                # digest — a spliced or edited retained entry breaks
                # it even when the totals were edited to match.
                head = self._anchor_digest
                for e in self._history:
                    head = _journal_chain_extend(head, e)
                if head != self._journal_head_digest:
                    discrepancies.append(
                        "journal chain: retained entries do not chain "
                        "from the anchor digest to the recorded head "
                        "digest (journal edited or spliced)")
                suffix = _replay_totals(self._history)
                if suffix is None:
                    journal_funded = journal_allocated = journal_spent = 0
                    for e in self._history:
                        if e.get("action") not in _JOURNAL_ACTIONS:
                            inconclusive = (
                                f"unknown journal action "
                                f"{e.get('action')!r}; replay incomplete")
                            break
                else:
                    journal_funded = self._anchor_funded_wei + suffix[0]
                    journal_allocated = (self._anchor_allocated_wei
                                         + suffix[1])
                    journal_spent = self._anchor_spent_wei + suffix[2]
                if inconclusive is None:
                    if journal_funded != self._funded_wei:
                        discrepancies.append(
                            f"funded: journal={journal_funded} "
                            f"records={self._funded_wei}")
                    record_allocated = self._allocated_wei()
                    if journal_allocated != record_allocated:
                        discrepancies.append(
                            f"allocated: journal={journal_allocated} "
                            f"records={record_allocated}")
                    if journal_spent != self._spent_wei:
                        discrepancies.append(
                            f"spent: journal={journal_spent} "
                            f"counter={self._spent_wei}")
                    record_spent = sum(
                        int(a.get("amount_wei") or 0)
                        for a in self._allocations.values()
                        if a.get("state") == "spent")
                    if record_spent != self._spent_wei:
                        discrepancies.append(
                            f"spent: spent_records={record_spent} "
                            f"counter={self._spent_wei}")
            return {"ok": not discrepancies and inconclusive is None,
                    "discrepancies": discrepancies,
                    "inconclusive": inconclusive,
                    "anchor": {"writes": self._anchor_writes,
                               "bootstrapped": self._anchor_bootstrapped}}

    def _allocated_wei(self) -> int:
        return sum(int(a.get("amount_wei") or 0)
                   for a in self._allocations.values()
                   if a.get("state") == "allocated")

    def _available_wei(self) -> int:
        """Spendable right now: funded minus spent minus live allocations."""
        return self._funded_wei - self._spent_wei - self._allocated_wei()

    # -- API --------------------------------------------------------------
    def fund(self, amount_axm: Optional[float] = None) -> Dict[str, Any]:
        """Move reserve into the spendable pool.

        ``amount_axm=None`` funds the full remaining reserve, and is
        idempotent: when nothing remains it returns the current status
        instead of raising. Raises ``PoolNotConfigured`` when the operator
        has not set a reserve (``SINCOR_LAUNCH_BOUNTY_AXM`` defaults to 0).
        """
        with self._lock:
            self._raise_if_degraded()
            if self._rehydrate_blocked:
                raise PoolError(
                    "bounty pool rehydrate blocked: the pool ledger file "
                    "was missing or corrupt on boot while live pool-backed "
                    "tasks still hold claims; refusing to re-fund the full "
                    "reserve until those tasks resolve or the ledger file "
                    "is restored, then restart. Live obligations: "
                    f"{self._rehydrate_obligations}")
            if self.reserve_wei <= 0:
                raise PoolNotConfigured(
                    f"launch bounty pool not configured: set {RESERVE_ENV} "
                    "to earmark AXM for launch bounties")
            remaining = self.reserve_wei - self._funded_wei
            if amount_axm is None:
                if remaining <= 0:
                    return self._status_locked()
                amount_wei = remaining
            else:
                amount_wei = _axm_to_wei(amount_axm)
                if amount_wei <= 0:
                    raise PoolError("amount_axm must be positive")
                if amount_wei > remaining:
                    raise PoolError(
                        f"amount exceeds remaining reserve "
                        f"({_wei_to_axm(remaining):g} AXM)")
            self._funded_wei += amount_wei
            self._record("fund", amount_wei, {})
            self._save()
            return self._status_locked()

    def allocate(self, task_id: str, amount_axm: float, reason: str = "") -> Dict[str, Any]:
        """Reserve pool funds for a task. Returns the allocation record."""
        task_id = str(task_id or "").strip()
        if not task_id:
            raise PoolError("task_id is required")
        amount_wei = _axm_to_wei(amount_axm)
        if amount_wei <= 0:
            raise PoolError("amount_axm must be positive")
        with self._lock:
            self._raise_if_degraded()
            available = self._available_wei()
            if amount_wei > available:
                raise InsufficientPoolFunds(
                    f"allocation of {_wei_to_axm(amount_wei):g} AXM exceeds "
                    f"available pool balance ({_wei_to_axm(available):g} AXM)")
            alloc_id = "alloc_" + uuid.uuid4().hex[:10]
            alloc = {
                "allocation_id": alloc_id,
                "task_id": task_id,
                "amount_wei": amount_wei,
                "amount_axm": _wei_to_axm(amount_wei),
                "state": "allocated",
                "reason": str(reason or ""),
                "created_at": _now_ms(),
                "history": [{"state": "allocated", "at": _now_ms()}],
            }
            self._allocations[alloc_id] = alloc
            self._record("allocate", amount_wei,
                         {"allocation_id": alloc_id, "task_id": task_id,
                          "reason": str(reason or "")})
            self._save()
            return self._public_alloc(alloc)

    def release(self, allocation_id: str) -> Dict[str, Any]:
        """Return an allocation to the pool unspent."""
        allocation_id = str(allocation_id or "").strip()
        with self._lock:
            self._raise_if_degraded()
            alloc = self._allocations.get(allocation_id)
            if alloc is None:
                raise KeyError("unknown allocation")
            if alloc.get("state") != "allocated":
                raise PoolError(
                    f"allocation already {alloc.get('state')}")
            alloc["state"] = "released"
            alloc["history"].append({"state": "released", "at": _now_ms()})
            self._record("release", int(alloc.get("amount_wei") or 0),
                         {"allocation_id": allocation_id,
                          "task_id": alloc.get("task_id")})
            self._save()
            return self._public_alloc(alloc)

    def spend(self, allocation_id: str) -> Dict[str, Any]:
        """Consume an allocation: the backed bounty was paid out on settlement.

        Terminal transition allocated -> spent. Unlike ``release`` (which
        returns funds to available unspent), spent funds left the pool:
        they are no longer counted as allocated *or* available, and the
        cumulative ``spent_wei`` counter is bumped so ``available`` stays
        exact. Raises ``KeyError`` for an unknown allocation and
        ``PoolError`` unless the allocation is currently allocated.
        """
        allocation_id = str(allocation_id or "").strip()
        with self._lock:
            self._raise_if_degraded()
            alloc = self._allocations.get(allocation_id)
            if alloc is None:
                raise KeyError("unknown allocation")
            if alloc.get("state") != "allocated":
                raise PoolError(
                    f"allocation already {alloc.get('state')}")
            amount_wei = int(alloc.get("amount_wei") or 0)
            alloc["state"] = "spent"
            alloc["history"].append({"state": "spent", "at": _now_ms()})
            self._spent_wei += amount_wei
            self._record("spend", amount_wei,
                         {"allocation_id": allocation_id,
                          "task_id": alloc.get("task_id")})
            self._save()
            return self._public_alloc(alloc)

    def allocations_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        """Live allocations attached to one task (for admin delist)."""
        task_id = str(task_id or "").strip()
        with self._lock:
            return [self._public_alloc(a) for a in self._allocations.values()
                    if a.get("task_id") == task_id and a.get("state") == "allocated"]

    def _public_alloc(self, alloc: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(alloc)
        out["amount_wei"] = str(int(alloc.get("amount_wei") or 0))
        return out

    def _status_locked(self) -> Dict[str, Any]:
        allocated = self._allocated_wei()
        return {
            "reserve_axm": _wei_to_axm(self.reserve_wei),
            "funded_axm": _wei_to_axm(self._funded_wei),
            "spent_axm": _wei_to_axm(self._spent_wei),
            "allocated_axm": _wei_to_axm(allocated),
            "available_axm": _wei_to_axm(self._available_wei()),
            "allocation_count": sum(1 for a in self._allocations.values()
                                  if a.get("state") == "allocated"),
            "allocations": [self._public_alloc(a)
                            for a in sorted(self._allocations.values(),
                                            key=lambda x: x.get("created_at") or 0)],
            # W-119: True when boot found a missing/corrupt ledger file
            # with live pool-backed tasks still holding claims; while
            # True the pool refuses to (re-)fund. Surfaced by
            # GET /v1/a2a/pool alongside the ledger verification.
            "rehydrate_blocked": self._rehydrate_blocked,
            "rehydrate_blocked_obligations": [
                dict(o) for o in self._rehydrate_obligations],
            # Currency note: offchain AXM-denominated reservation ledger;
            # it does not move funds on any chain.
            "ledger": "offchain-axm",
        }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return self._status_locked()


_POOL: Optional[BountyPool] = None
_POOL_LOCK = threading.Lock()


def bounty_pool(path: Optional[str] = None) -> BountyPool:
    """Process-wide pool singleton (mirrors stake_ledger())."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None or path is not None:
            _POOL = BountyPool(path=path)
        return _POOL


def reset_bounty_pool(path: Optional[str] = None,
                      reserve_axm: Optional[float] = None) -> BountyPool:
    """Test isolation hook — rebuild the singleton."""
    global _POOL
    with _POOL_LOCK:
        _POOL = BountyPool(path=path, reserve_axm=reserve_axm)
        return _POOL
