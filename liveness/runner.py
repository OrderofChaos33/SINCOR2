#!/usr/bin/env python3
"""Liveness runner — keeps the marketplace visibly alive, honestly.

One pass per invocation (cron-friendly); ``--daemon`` loops locally.

Per pass:
  1. heartbeat every cohort agent (commit requires a fresh heartbeat)
  2. crash recovery: reveal any committed-but-unrevealed bids whose
     reveal window is open  <-- NEVER GHOST, this is the hard requirement
  3. sweep open tasks: a random 3-4 eligible agents commit per task
     (salt persisted to disk BEFORE the commit POST), earliest
     commit deadline first, and only while the deadline still has
     COMMIT_WINDOW_SAFETY_MS of headroom against the live clock;
     merit-gated tasks are selected only for agents whose current
     server-side reputation clears the 0.15 floor — never attempted
     from below it
  4. reveal bids whose window is open
  5. close any auction past its reveal deadline (permissionless timeout)
  6. perform: Lane A (heartbeat) fountain tasks assigned to us are
     executed, verified, and proven; Lane B (DeFi pipeline) and Lane C
     (self-improve) wins go to performance_queue.json for the real worker
  7. ensure fountain supply across all three lanes (A: heartbeat floor,
     B: DeFi drip 8-12/day, C: curated self-improve 2-4/day)

Honesty rules: bids are randomized 85-98% of bounty with realistic time
estimates; agents lose regularly; proofs are submitted ONLY for Lane A
tasks whose acceptance checks all pass.  Lane B/C wins are NEVER faked —
they queue for real work.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import secrets
import sys
import time

try:
    import fcntl
except ImportError:  # non-POSIX platforms: no flock, guard degrades
    fcntl = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from net import HttpError, get_all_pages, get_json, post_json  # noqa: E402
import fountain  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
COMMITS_PATH = os.path.join(STATE_DIR, "commits.json")
QUEUE_PATH = os.path.join(STATE_DIR, "performance_queue.json")

BID_FRACTION_LO, BID_FRACTION_HI = 0.85, 0.98
# Commit-window safety margin: the sweep never attempts a commit
# whose deadline is closer than this — a commit the runner already
# knows would land late is pure noise (and a salt-persisted entry to
# clean up).  Judged against the LIVE clock at each task's selection
# moment, never the pass-start now_ms: a pass spends ~42s fetching
# the task index (measured 2026-11-14: 3,107 tasks, 32 pages,
# ~1.34s/page) plus heartbeats and earlier sweep work, so a frozen
# clock waves through commits whose windows close mid-sweep
# (2026-11-14: 34/34 attempts rejected "commit window closed" under
# the old 30s margin + frozen clock).  Sizing: from selection to the
# last of a task's 4 commit POSTs landing is ~8 requests ≈ 11s
# typical, ~60s if every request burns one retry; 120s is 2x that
# worst case, with room for runner<->server clock skew.
COMMIT_WINDOW_SAFETY_MS = 120_000
REVEAL_SAFETY_MS = 10_000
# The server enforces a 60s heartbeat TTL on every bid action
# (commit/reveal): ts - last_heartbeat > 60_000 -> 403 "agent heartbeat
# expired".  Refresh sooner than that so clock skew between runner and
# server can never turn a fresh heartbeat stale at the gate.
HEARTBEAT_FRESH_MS = 45_000
# The server merit-gates tasks stamped requires_merit (bounty >= 5.0
# AXM): _sealed_agent_checks rejects commit AND reveal with 403
# "merit required" when the agent's server-side reputation is below
# this floor (REPUTATION_MERIT_THRESHOLD in sincor2.a2a_inbound).
# Reputation is earned only (+0.2 per settled proof, reset to 0.0 on
# ghosting).  The sweep must therefore SELECT merit tasks only for
# agents currently at/above the floor — attempting them from below it
# is a guaranteed 403 and pure noise (2026-11-14: 41% of the live
# board merit-gated, every cohort agent at 0.0, 35x 403 per pass).
REPUTATION_MERIT_THRESHOLD = 0.15
# A performance-queue entry whose task is ABSENT from a complete task
# index (the server forgot it — old tasks 404 on read) is dead, but
# only once it is older than this grace: an entry queued moments ago
# for a task the index snapshot predates must not be pruned on sight.
QUEUE_MISSING_GRACE_MS = 3_600_000


# ---------------------------------------------------------------------------
# commitment helper (byte-identical to the server's sealed_commitment)
# ---------------------------------------------------------------------------

def _keccak256(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak
        return keccak(data)
    except Exception:
        from sha3 import keccak_256  # type: ignore
        return keccak_256(data).digest()


def sealed_commitment(price_wei: int, salt: bytes, agent_id: str) -> bytes:
    """keccak256(abi.encodePacked(bytes32(price), salt, keccak256(agent_id)))."""
    if price_wei <= 0:
        raise ValueError("price_wei must be positive")
    if len(salt) != 32:
        raise ValueError("salt must be 32 bytes")
    agent_id_hash = _keccak256(str(agent_id).encode("utf-8"))
    return _keccak256(int(price_wei).to_bytes(32, "big") + bytes(salt) + agent_id_hash)


def receipt_hash_for(artifact: bytes) -> str:
    return "0x" + _keccak256(artifact).hex()


# ---------------------------------------------------------------------------
# bid auth (W-77): EIP-191 signatures by each agent's registered wallet
#
# Canonical message, byte-identical to the server's
# sincor2.a2a_integration._auth_bid_message (origin/main):
#
#   SINCOR-A2A-MARKET bid-{purpose}\n
#   task_id:{task_id}\n
#   agent_id:{agent_id}\n
#   payload:{payload}\n
#   timestamp:{timestamp_seconds}\n
#   nonce:{nonce}
#
# payload is the commitment hex for purpose="commit" and
# f"{bid_axm}:{nonce_salt}" for purpose="reveal" — exactly how the
# server routes construct it in a2a_inbound_market (commit passes
# str(commitment); reveal passes f"{float(bid_axm)}:{body nonce}").
# timestamp is unix SECONDS (server freshness window is 600s) and the
# nonce must be unique per call (signatures are single-use server-side).
# ---------------------------------------------------------------------------

COHORT_KEYS_PATH = os.path.join(HERE, "cohort_keys.json")
_AUTH_BID_DOMAIN_FMT = "SINCOR-A2A-MARKET bid-{purpose}"


def load_cohort_keys(path=None):
    """Load cohort signing keys: {agent_id: raw_key_hex}. Never log values.

    The on-disk file maps agent_id -> {"key": "<hex>"} (a bare hex string
    per agent is also accepted). Returns {} when the file is absent or
    unreadable — callers then fall back to the operator-token path.
    """
    keys = {}
    try:
        with open(path or COHORT_KEYS_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return keys
    if not isinstance(raw, dict):
        return keys
    for aid, entry in raw.items():
        if isinstance(entry, dict):
            key = entry.get("key") or entry.get("private_key") or ""
        else:
            key = entry
        if isinstance(key, str) and key.strip():
            keys[str(aid)] = key.strip()
    return keys


def auth_bid_message(purpose: str, task_id: str, agent_id: str,
                     payload: str, timestamp: int, nonce: str) -> str:
    """Canonical bid message — must stay byte-identical to the server's."""
    return (
        f"{_AUTH_BID_DOMAIN_FMT.format(purpose=purpose)}\n"
        f"task_id:{task_id}\n"
        f"agent_id:{agent_id}\n"
        f"payload:{payload}\n"
        f"timestamp:{int(timestamp)}\n"
        f"nonce:{nonce}"
    )


def build_bid_auth(keys, agent_id: str, *, purpose: str, task_id: str,
                   payload: str, timestamp=None, nonce=None):
    """Build {authSignature, authTimestamp, authNonce} for one bid call.

    Signs the canonical bid message with the agent's own key (EIP-191
    personal_sign, same machinery as the heartbeat signer). Returns
    None when no key is held for the agent — the caller then falls back
    to the operator heartbeat token instead of sending a bare commit.
    """
    key = (keys or {}).get(agent_id)
    if not key:
        return None
    from eth_account import Account
    from eth_account.messages import encode_defunct

    ts = int(time.time()) if timestamp is None else int(timestamp)
    nonce = nonce or secrets.token_hex(16)
    message = auth_bid_message(purpose, task_id, agent_id, payload, ts, nonce)
    acct = Account.from_key(key)
    sig_hex = acct.sign_message(encode_defunct(text=message)).signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex
    return {
        "authSignature": sig_hex,
        "authTimestamp": ts,
        "authNonce": nonce,
    }


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _pass_lock_path():
    return os.path.join(STATE_DIR, ".runner.lock")


@contextlib.contextmanager
def pass_lock():
    """Exclusive non-blocking single-instance guard for one pass.

    Yields True when this process holds the lock for the whole pass,
    False when another pass already holds it (the caller must then
    skip without touching state — a skipped pass is harmless on a
    2-minute cron). Falls back to unlocked, with a warning, when
    flock is unavailable; the lock is a guard, never a reason to
    crash a pass. Closing the handle releases the lock on every path,
    including exceptions.
    """
    if fcntl is None:
        log("  WARNING: fcntl unavailable — pass lock disabled, "
            "concurrent passes may race on state")
        yield True
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    fh = open(_pass_lock_path(), "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        yield False
        return
    except OSError:
        log("  WARNING: pass lock failed — proceeding unlocked")
        fh.close()
        yield True
        return
    try:
        yield True
    finally:
        fh.close()


def load_commits():
    return _load_json(COMMITS_PATH, {})


def save_commits(commits):
    _save_json(COMMITS_PATH, commits)


def load_queue():
    return _load_json(QUEUE_PATH, [])


def save_queue(queue):
    _save_json(QUEUE_PATH, queue)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# cohort
# ---------------------------------------------------------------------------

def load_cohort():
    with open(os.path.join(HERE, "cohort.json"), encoding="utf-8") as fh:
        return json.load(fh)["agents"]


def register_agent_body(agent):
    return {
        "agent_id": agent["agent_id"],
        "name": agent["name"],
        "description": agent["description"],
        "version": agent.get("version", "1.0.0"),
        "capability_tags": agent["capability_tags"],
        "skills": agent.get("skills", []),
        "wallet": agent["wallet"],
        "chain_id": agent.get("chain_id", 8453),
    }


# ---------------------------------------------------------------------------
# one pass
# ---------------------------------------------------------------------------

class Runner:
    def __init__(self, base_url, cohort):
        self.base_url = base_url
        self.cohort = cohort
        self.by_id = {a["agent_id"]: a for a in cohort}
        self.commits = load_commits()
        self.cohort_keys = load_cohort_keys()
        self.now_ms = int(time.time() * 1000)
        # agent_id -> wall-clock ms of the last heartbeat POST this
        # process made.  In-pass dedupe only; the server is the source
        # of truth for TTL.  See ensure_heartbeat().
        self._hb_at = {}
        # agent_id -> server-side reputation, fetched lazily ONCE per
        # pass (see reputations()).  None = not fetched yet.
        self._reputations = None
        # Flips to False on any partial failure (e.g. one agent's
        # heartbeat dies).  one_pass()/main() turn this into a nonzero
        # process exit code so cron can detect a degraded pass.
        self.pass_ok = True

    def note_failure(self, msg):
        """Log a partial failure and mark the pass as failed."""
        log(msg)
        self.pass_ok = False

    @staticmethod
    def _ops_headers():
        """Operator auth headers — FALLBACK for first-party bid actions.

        Preferred bid auth is a per-agent EIP-191 signature (see
        ``_bid_auth``); this operator heartbeat token is only used for
        agents whose key the runner does not hold.
        """
        token = (os.environ.get("SINCOR_HEARTBEAT_TOKEN") or "").strip()
        return {"X-Sincor-Heartbeat": token} if token else None

    def _bid_auth(self, aid, *, purpose, task_id, payload):
        """Per-agent EIP-191 bid auth fields, or None if no key is held."""
        try:
            return build_bid_auth(self.cohort_keys, aid, purpose=purpose,
                                  task_id=task_id, payload=payload)
        except Exception as e:  # signing must never crash the pass
            log(f"  bid signing failed for {aid} ({purpose} on {task_id}): {e}")
            return None

    # -- merit gate: server-side reputation (selection only) ----------------
    def reputations(self):
        """agent_id -> server-side reputation, fetched ONCE per pass.

        The server is the only source of truth for reputation (local
        state stores none).  Fetched lazily on first use and cached
        for the rest of the pass.

        FAIL-SAFE: any fetch failure yields {} — callers treat a
        missing reputation as 0.0, so merit tasks are SKIPPED rather
        than attempted blind.  Never fail open into merit tasks: a
        sub-floor commit is a guaranteed 403, and selection is the
        only legitimate lever (reputation itself is earned solely by
        settling proofs server-side).
        """
        if self._reputations is None:
            try:
                payload = get_json(self.base_url, "/v1/a2a/agents")
                agents = (payload.get("agents", [])
                          if isinstance(payload, dict) else [])
                self._reputations = {
                    a["agent_id"]: float(a.get("reputation") or 0.0)
                    for a in agents if a.get("agent_id")
                }
            except Exception as e:
                log(f"  reputation fetch failed: {e} — treating every "
                    f"reputation as 0.0 (merit tasks skipped this pass)")
                self._reputations = {}
        return self._reputations

    def reputation_of(self, agent_id):
        """One agent's reputation; unknown/fetch-failed reads as 0.0."""
        return self.reputations().get(agent_id, 0.0)

    # -- step 1: heartbeats -------------------------------------------------
    def _post_heartbeat(self, agent) -> bool:
        """POST one heartbeat for one agent. Returns True on success.

        EIP-191 signing (primary): production requires an EIP-191
        signature or the operator heartbeat token. We use EIP-191 with
        the cohort keys. Operator token (fallback):
        SINCOR_HEARTBEAT_TOKEN env -> X-Sincor-Heartbeat header.

        Failures are logged, never raised, and do NOT mark the pass
        failed — callers decide that (heartbeat_all does;
        ensure_heartbeat is best-effort and does not).
        """
        from eth_account import Account
        from eth_account.messages import encode_defunct
        import time as _time

        # Load cohort keys for EIP-191 signing (absolute path, cwd-safe)
        keys = load_cohort_keys()

        token = (os.environ.get("SINCOR_HEARTBEAT_TOKEN") or "").strip()
        hb_headers = {"X-Sincor-Heartbeat": token} if token else None

        aid = agent["agent_id"]
        # Build EIP-191 signed heartbeat if we have the key
        payload = {"agent_id": aid}
        if aid in keys:
            try:
                acct = Account.from_key(keys[aid])
                ts = int(_time.time() * 1000)
                # Canonical message: SINCOR-HEARTBEAT\nagent_id\ntimestamp (from a2a_inbound_ext.py)
                msg = f"SINCOR-HEARTBEAT\n{aid}\n{ts}"
                sig = acct.sign_message(encode_defunct(text=msg)).signature.hex()
                payload["signature"] = sig
                payload["ts"] = ts
            except Exception as e:
                log(f"  signing failed for {aid}: {e}")
                return False
        try:
            post_json(self.base_url, "/v1/a2a/heartbeat",
                      payload, headers=hb_headers)
        except HttpError as e:
            if e.status == 404:
                # self-heal: re-register a missing agent
                log(f"heartbeat 404 for {aid}: re-registering")
                try:
                    post_json(self.base_url, "/v1/a2a/register",
                              register_agent_body(agent))
                    post_json(self.base_url, "/v1/a2a/heartbeat",
                              {"agent_id": aid}, headers=hb_headers)
                except HttpError as e2:
                    log(f"  re-register failed for {aid}: {e2}")
                    return False
                except Exception as e2:
                    log(f"  re-register transport failure for {aid}: {e2}")
                    return False
            else:
                log(f"heartbeat failed for {aid}: {e}")
                return False
        except Exception as e:
            # Transport failure AFTER net.py exhausted its retries
            # (truncated read, connection reset, ...).  Isolate it to
            # this agent: log it, keep the pass going.
            log(f"heartbeat transport failure for {aid}: {e}")
            return False
        return True

    def ensure_heartbeat(self, agent) -> bool:
        """Refresh this agent's heartbeat so the next bid action passes
        the server's 60s TTL check.

        A pass heartbeats at step 1 but runs ~84-90s (paginated task
        fetch + a long sequential commit sweep), so those heartbeats
        are expired by the time late commits/reveals run — production
        logged "agent heartbeat expired" rejections clustered in the
        final ~15s of every pass.  Calling this immediately before
        each commit/reveal closes the TTL-vs-pass-duration skew.  An
        in-pass 45s dedupe guard avoids spamming one heartbeat per
        task when an agent bids often.  Best-effort: a failed refresh
        returns False and the caller proceeds with the bid unchanged,
        so a heartbeat transport failure never changes bidding
        semantics.
        """
        aid = agent["agent_id"]
        now = int(time.time() * 1000)
        if now - self._hb_at.get(aid, 0) < HEARTBEAT_FRESH_MS:
            return True
        if not self._post_heartbeat(agent):
            return False
        self._hb_at[aid] = int(time.time() * 1000)
        return True

    def heartbeat_all(self):
        for agent in self.cohort:
            if not self.ensure_heartbeat(agent):
                self.note_failure(
                    f"heartbeat failed for {agent['agent_id']}")

    # -- steps 2-5: auction lifecycle ---------------------------------------
    def _task_index(self):
        # Paginated: a single page is NEVER treated as the full inventory
        # (a truncated page would silently drop tasks from the pass).
        tasks = get_all_pages(self.base_url, "/v1/a2a/tasks", per_page=100)
        return {t["task_id"]: t for t in tasks}

    def recover_and_reveal(self, tasks):
        """Reveal every committed bid whose window is open. Never ghost."""
        for task_id, per_agent in list(self.commits.items()):
            task = tasks.get(task_id)
            for aid, entry in list(per_agent.items()):
                if entry.get("status") != "committed":
                    continue
                if task is None or task.get("state") not in ("open", "auction"):
                    # task gone/closed: nothing to reveal; drop the entry
                    log(f"  drop stale commit entry {aid} on {task_id}")
                    del per_agent[aid]
                    continue
                cd = int(task.get("commit_deadline") or 0)
                rd = int(task.get("reveal_deadline") or 0)
                if self.now_ms <= cd:
                    continue  # window not open yet
                if self.now_ms > rd:
                    log(f"  !!! MISSED reveal window for {aid} on {task_id} "
                        f"(ghost risk — slash likely)")
                    del per_agent[aid]
                    continue
                self._reveal(task_id, aid, entry)
        save_commits(self.commits)

    def _reveal(self, task_id, aid, entry):
        body = {
            "task_id": task_id,
            "agent_id": aid,
            "bid_axm": entry["bid_axm"],
            "nonce": entry["salt_hex"],
            "estimated_seconds": entry["estimated_seconds"],
        }
        # W-77: sign with the agent's registered wallet. The server
        # builds the reveal payload as f"{float(bid_axm)}:{nonce}".
        auth = self._bid_auth(
            aid, purpose="reveal", task_id=task_id,
            payload=f"{float(entry['bid_axm'])}:{entry['salt_hex']}")
        headers = self._ops_headers()
        if auth:
            body.update(auth)
            headers = None  # prove the EIP-191 path, not the ops token
        # Same 60s TTL gate as commit: refresh best-effort before the
        # reveal POST; existing error handling covers a failed refresh.
        agent = self.by_id.get(aid)
        if agent is not None:
            self.ensure_heartbeat(agent)
        try:
            post_json(self.base_url, "/v1/a2a/bids/reveal", body,
                      headers=headers)
            entry["status"] = "revealed"
            log(f"  reveal {aid} on {task_id}: {entry['bid_axm']} AXM")
        except HttpError as e:
            if "already revealed" in e.body:
                entry["status"] = "revealed"
            elif "unknown commitment" in e.body:
                log(f"  reveal failed (no server commit) {aid}/{task_id}: dropping")
                self.commits.get(task_id, {}).pop(aid, None)
            elif e.status >= 500:
                log(f"  reveal failed {aid} on {task_id}: {e} — will retry")
            else:
                # permanent (window closed, commitment mismatch): drop loudly
                log(f"  reveal REJECTED {aid} on {task_id}: {e} — entry dropped")
                self.commits.get(task_id, {}).pop(aid, None)
        except Exception as e:
            log(f"  reveal transport failure {aid} on {task_id}: {e} — will retry")
        save_commits(self.commits)

    def sweep_commits(self, tasks):
        """Commit a random 3-4 eligible agents per open task.

        Earliest-deadline-first, and headroom is judged against the
        LIVE clock at each task's own selection moment (see
        COMMIT_WINDOW_SAFETY_MS): the index snapshot ages while the
        sweep works, but deadlines are absolute timestamps, so a
        live-clock comparison stays exact no matter how stale the
        snapshot is.  No pre-commit single-task re-check — a fresh
        GET would return the same (or, if the fountain refreshed the
        listing, a LATER) deadline, so it buys nothing and costs
        ~1.3s per task across the whole board.
        """
        by_deadline = sorted(
            tasks.items(),
            key=lambda kv: int(kv[1].get("commit_deadline") or 0))
        for task_id, task in by_deadline:
            if task.get("state") not in ("open", "auction"):
                continue
            if not task.get("sealed"):
                continue
            cd = int(task.get("commit_deadline") or 0)
            if int(time.time() * 1000) > cd - COMMIT_WINDOW_SAFETY_MS:
                continue
            bounty = float(task.get("bounty_axm") or 0)
            if bounty <= 0:
                continue
            poster_id = task.get("poster_id")
            if not poster_id:
                # Server self-dealing guard (axm_antigaming
                # .check_self_bid, enforced via check_bid_eligibility
                # on commit_bid/place_bid and re-checked at reveal):
                # an anonymous task (poster_id absent) carrying a
                # bounty is un-biddable for EVERY agent — the server
                # cannot prove the bidder is not the poster, so the
                # commit is rejected 403 fail-closed.  The sweep only
                # reaches this point with bounty > 0 established,
                # which is exactly the guard's firing condition (a
                # zero-bounty anonymous task is NOT rejected by the
                # server and is not claimed here — the bounty check
                # above already skipped it for the sweep's own
                # reasons).  Skip at selection instead of burning
                # commit attempts on guaranteed 403s.
                log(f"  skipping anonymous-poster task {task_id}: "
                    f"self-dealing guard (poster_id absent, "
                    f"bounty > 0)")
                continue
            tags = set(task.get("tags") or [])
            per_agent = self.commits.setdefault(task_id, {})
            eligible = [a for a in self.cohort
                        if a["agent_id"] not in per_agent
                        and set(a["capability_tags"]) & tags]
            # Self-bid guard (same server check): an agent can never
            # commit on a task it posted itself — poster_id is
            # server-bound at creation and the server compares it
            # against the bidder's agent_id as an exact string.
            # Exclude only the poster; the rest of the cohort may
            # still bid, exactly as the server allows.
            non_posters = [a for a in eligible
                           if str(poster_id) != str(a["agent_id"])]
            if eligible and not non_posters:
                log(f"  skipping task {task_id}: every eligible "
                    f"cohort agent is the poster (self-bid guard)")
            eligible = non_posters
            if task.get("requires_merit"):
                # Merit gate (selection, never bypass): only agents
                # whose CURRENT server-side reputation clears the
                # floor may commit.  Below-floor attempts are
                # guaranteed 403s — skip them and keep the fleet on
                # the probation-open board it can lawfully take.
                # Reveals of EXISTING commits are never filtered
                # (recover_and_reveal): not revealing is ghosting,
                # which resets reputation to 0.0.
                kept = [a for a in eligible
                        if self.reputation_of(a["agent_id"])
                        >= REPUTATION_MERIT_THRESHOLD]
                if eligible and not kept:
                    log(f"  skipping merit task {task_id}: no eligible "
                        f"cohort agent at reputation >= "
                        f"{REPUTATION_MERIT_THRESHOLD}")
                eligible = kept
            random.shuffle(eligible)
            n = min(len(eligible), random.choice([3, 4]))
            for agent in eligible[:n]:
                self._commit(task, agent, bounty)

    def _commit(self, task, agent, bounty):
        aid = agent["agent_id"]
        task_id = task["task_id"]
        bid_axm = round(bounty * random.uniform(BID_FRACTION_LO, BID_FRACTION_HI), 6)
        price_wei = int(round(bid_axm * 1e18))
        salt = secrets.token_bytes(32)
        commitment = "0x" + sealed_commitment(price_wei, salt, aid).hex()
        est = self._estimate_seconds(task)
        entry = {
            "status": "committed",
            "salt_hex": salt.hex(),
            "price_wei": str(price_wei),
            "bid_axm": bid_axm,
            "estimated_seconds": est,
            "committed_at": self.now_ms,
        }
        # CRASH SAFETY: persist BEFORE the POST so a crash can never
        # leave a commit the runner doesn't know how to reveal.
        self.commits.setdefault(task_id, {})[aid] = entry
        save_commits(self.commits)
        # The server requires a heartbeat fresher than 60s at commit
        # time; the pass-start heartbeat is expired by the time the
        # sweep gets here.  Refresh best-effort — a failed refresh
        # must not change bidding semantics, so the commit attempt
        # proceeds regardless and existing error handling applies.
        self.ensure_heartbeat(agent)
        body = {"task_id": task_id, "agent_id": aid,
                "commitment": commitment}
        # W-77: sign with the agent's registered wallet. The server
        # passes str(commitment) as the commit payload verbatim.
        auth = self._bid_auth(aid, purpose="commit", task_id=task_id,
                              payload=commitment)
        headers = self._ops_headers()
        if auth:
            body.update(auth)
            headers = None  # prove the EIP-191 path, not the ops token
        try:
            resp = post_json(self.base_url, "/v1/a2a/bids/commit",
                             body, headers=headers)
            # TRUE IDEMPOTENCY CHECK: a duplicate commit returns the
            # PRE-EXISTING record as success (201 + old record) — the
            # "already committed" error string never comes back. If the
            # server's commitment differs from the one just sent, our
            # fresh entry's salt can never reveal against it; keeping
            # it guarantees a later "commitment mismatch" rejection.
            # Drop the fresh entry immediately. A byte-identical
            # re-POST returns the SAME commitment and the entry is
            # correctly kept.
            server_commitment = resp.get("commitment") if isinstance(resp, dict) else None
            if server_commitment is not None and \
                    str(server_commitment).lower() != commitment.lower():
                log(f"  commit {aid} on {task_id}: server returned a "
                    f"pre-existing commitment — fresh entry dropped "
                    f"(its salt could never reveal)")
                self.commits.get(task_id, {}).pop(aid, None)
                save_commits(self.commits)
            elif server_commitment is None:
                log(f"  commit {aid} on {task_id}: server response carried "
                    f"no commitment field — entry kept (ambiguous)")
            else:
                log(f"  commit {aid} on {task_id} ({bid_axm} AXM sealed)")
        except HttpError as e:
            if "already committed" in e.body:
                # The server keeps the FIRST commitment for (task, agent).
                # This entry was created fresh in this call (sweep only
                # commits agents with no local entry), so the server's
                # commitment predates it and its salt can never reveal —
                # keeping it guarantees a later "commitment mismatch"
                # reveal rejection. Drop it; the original bid (if any)
                # belongs to a lost local entry and is unrecoverable.
                log(f"  commit {aid} on {task_id}: already committed by an "
                    f"earlier entry — fresh entry dropped (salt mismatch "
                    f"would fail reveal)")
                self.commits.get(task_id, {}).pop(aid, None)
                save_commits(self.commits)
            elif e.status >= 500:
                # server-side failure: keep the entry, retry is idempotent
                log(f"  commit failed {aid} on {task_id}: {e} — entry kept for retry")
            else:
                # permanent rejection (capability mismatch, insufficient
                # stake, window closed, ...): drop, don't retry forever
                log(f"  commit REJECTED {aid} on {task_id}: {e} — entry dropped")
                self.commits.get(task_id, {}).pop(aid, None)
                save_commits(self.commits)
        except Exception as e:
            # transport failure (response possibly lost): keep the entry;
            # a lost-success retry hits "already committed"
            log(f"  commit transport failure {aid} on {task_id}: {e} — entry kept")

    def _estimate_seconds(self, task):
        sk = task.get("seed_key") or ""
        lane = fountain_lane_of(sk)
        if lane == "a":
            ftype = fountain_type_of(sk)
            spec = fountain.FOUNTAIN_TYPES.get(ftype)
            if spec:
                return int(spec["estimated_seconds"])
        elif lane == "b":
            dtask = fountain.defi_task(sk)
            if dtask:
                return fountain.defi_estimated_seconds(dtask)
        elif lane == "c":
            allow = {e["id"]: e for e in fountain.allowlist_entries()}
            entry = allow.get(fountain_lane_c_id(sk))
            if entry:
                return int(entry["estimated_seconds"])
        bounty = float(task.get("bounty_axm") or 1)
        return max(600, min(7200, int(bounty * 3600)))

    def reveal_due(self, tasks):
        for task_id, per_agent in list(self.commits.items()):
            task = tasks.get(task_id)
            if task is None:
                continue
            for aid, entry in list(per_agent.items()):
                if entry.get("status") != "committed":
                    continue
                cd = int(task.get("commit_deadline") or 0)
                rd = int(task.get("reveal_deadline") or 0)
                if cd < self.now_ms <= rd - REVEAL_SAFETY_MS:
                    self._reveal(task_id, aid, entry)

    def close_due(self, tasks):
        for task_id, task in tasks.items():
            if task.get("state") not in ("open", "auction"):
                continue
            rd = int(task.get("reveal_deadline") or 0)
            if rd and self.now_ms > rd:
                try:
                    post_json(self.base_url, f"/v1/a2a/tasks/{task_id}/close", {})
                    log(f"  closed {task_id}")
                except HttpError as e:
                    log(f"  close failed {task_id}: {e}")

    # -- step 6: perform ------------------------------------------------------
    def _prune_queue(self, queue, tasks):
        """Drop performance-queue entries that can never be performed.

        An entry is performable only while its task is still
        ``assigned`` server-side — submit_proof rejects anything else
        ("task is not assigned").  Entries whose task reached a
        terminal state (settled by the real worker, expired
        unperformed, failed) or that the server has forgotten entirely
        (absent from a complete index; old ones 404 on read) are dead
        weight.  The queue grew past 5,000 entries, nearly all dead
        (DEPLOY.md: "alert if it grows unbounded"), burying the live
        work list the real worker reads.  A grace window covers
        entries queued moments ago for tasks an index snapshot may
        predate.  Pruning loses nothing performable: a pruned task
        could not accept a proof anyway.
        """
        kept = []
        dropped = 0
        for entry in queue:
            task = tasks.get(entry.get("task_id"))
            if task is not None:
                if task.get("state") == "assigned":
                    kept.append(entry)
                else:
                    dropped += 1
            elif self.now_ms - int(entry.get("assigned_at") or 0) \
                    <= QUEUE_MISSING_GRACE_MS:
                kept.append(entry)
            else:
                dropped += 1
        if dropped:
            log(f"  performance queue: pruned {dropped} dead entries, "
                f"{len(kept)} live")
        return kept

    def perform_assigned(self, tasks):
        queue = self._prune_queue(load_queue(), tasks)
        queued_ids = {q["task_id"] for q in queue}
        for task_id, task in tasks.items():
            if task.get("state") != "assigned":
                continue
            aid = task.get("assigned_to")
            if aid not in self.by_id:
                continue
            sk = task.get("seed_key") or ""
            lane = fountain_lane_of(sk)
            if lane == "a":
                # Lane A only: the runner executes these end-to-end.
                # Lanes B/C and external catalog tasks are queued for real
                # performance — NEVER faked.
                ftype = fountain_type_of(sk)
                if ftype and ftype in fountain.EXECUTORS:
                    self._perform_fountain(task, aid, ftype)
                    continue
            if task_id not in queued_ids:
                queue.append({
                    "task_id": task_id,
                    "agent_id": aid,
                    "seed_key": sk,
                    "lane": lane,
                    "title": task.get("title"),
                    "bounty_axm": task.get("bounty_axm"),
                    "assigned_at": self.now_ms,
                    "note": "won by liveness agent; needs real LLM worker — never fake a proof",
                })
                log(f"  queued {'lane-' + lane if lane else 'catalog'} task "
                    f"{task_id} for real performance")
        save_queue(queue)

    def _perform_fountain(self, task, aid, ftype):
        task_id = task["task_id"]
        log(f"  performing fountain task {task_id} ({ftype}) as {aid}")
        try:
            artifact, checks = fountain.EXECUTORS[ftype](self.base_url)
        except Exception as e:
            log(f"    executor failed: {e} — no proof submitted")
            return
        failed = [name for name, ok in checks if not ok]
        if failed:
            log(f"    acceptance FAILED {failed} — no proof submitted")
            return
        rh = receipt_hash_for(artifact)
        # persist the artifact for audit
        art_dir = os.path.join(STATE_DIR, "artifacts")
        os.makedirs(art_dir, exist_ok=True)
        with open(os.path.join(art_dir, f"{task_id}.json"), "wb") as fh:
            fh.write(artifact)
        try:
            post_json(self.base_url, "/v1/a2a/proofs",
                      {"task_id": task_id, "agent_id": aid,
                       "receipt_hash": rh})
            log(f"    proof submitted for {task_id} (receipt {rh[:18]}...)")
        except HttpError as e:
            log(f"    proof submission failed for {task_id}: {e}")

    # -- main pass ------------------------------------------------------------
    def run_once(self):
        self.heartbeat_all()
        # ONE index fetch per pass, shared by every phase.  This used
        # to fetch three times (~42s per fetch of 3,100+ tasks —
        # ~128s of pure fetching per pass), and the sweep's snapshot
        # went stale against short commit windows.  A shared snapshot
        # is safe here: deadlines are absolute timestamps (window
        # math stays exact), the sweep judges headroom against the
        # live clock, and a task this pass closes is simply seen as
        # assigned on the NEXT pass's fetch (<= one cron interval
        # later) instead of this one.
        tasks = self._task_index()
        self.recover_and_reveal(tasks)
        self.sweep_commits(tasks)
        self.reveal_due(tasks)
        self.close_due(tasks)
        self.perform_assigned(tasks)
        try:
            posted = fountain.ensure_supply(self.base_url)
            n = sum(len(v) for v in posted.values())
            if n:
                log(f"  fountain posted {n}: "
                    + ", ".join(f"{k}={len(v)}" for k, v in posted.items()))
        except Exception as e:
            log(f"  fountain supply check failed: {e}")


def fountain_lane_of(seed_key: str):
    """Which fountain lane owns this seed_key: 'a', 'b', 'c', or None."""
    if seed_key.startswith("fountain-"):
        return "a"
    if seed_key.startswith("selfimprove-"):
        return "c"
    if seed_key in fountain.defi_task_keys():
        return "b"
    return None


def fountain_lane_c_id(seed_key: str):
    """selfimprove-<id>-<YYYY-MM-DD> -> <id> (None if unparseable)."""
    rest = seed_key[len("selfimprove-"):]
    parts = rest.split("-")
    if len(parts) < 4:
        return None
    return "-".join(parts[:-3])


def fountain_type_of(seed_key: str):
    """fountain-<type>-<YYYY-MM-DD> -> <type> (None if unparseable)."""
    rest = seed_key[len("fountain-"):]
    parts = rest.split("-")
    if len(parts) < 4:
        return None
    return "-".join(parts[:-3])


def one_pass(base_url, cohort) -> bool:
    """Run a single pass. Returns True iff the pass fully succeeded.

    A raised exception OR any partial failure recorded on the Runner
    (r.pass_ok == False) both yield False, which main() turns into a
    nonzero process exit code.  A failed pass never exits 0.

    The whole pass runs under the single-instance lock (state load to
    final save). If another pass already holds it, this pass skips
    cleanly and returns True — a skipped pass exits 0, never waits,
    and never touches state.
    """
    with pass_lock() as acquired:
        if not acquired:
            log("another liveness pass is running, skipping")
            return True
        r = Runner(base_url, cohort)
        try:
            r.run_once()
        except Exception as e:
            log(f"pass failed: {e}")
            return False
        if not r.pass_ok:
            log("pass completed with partial failures")
            return False
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="https://getsincor.com")
    ap.add_argument("--once", action="store_true",
                    help="single pass (default; cron-friendly)")
    ap.add_argument("--daemon", action="store_true",
                    help="loop forever with ~50s sleep")
    args = ap.parse_args()
    os.makedirs(STATE_DIR, exist_ok=True)
    cohort = load_cohort()

    if args.daemon:
        log(f"liveness daemon starting against {args.base_url}")
        while True:
            one_pass(args.base_url, cohort)
            time.sleep(50)
    else:
        return 0 if one_pass(args.base_url, cohort) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
