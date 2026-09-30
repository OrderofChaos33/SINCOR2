#!/usr/bin/env python3
"""Liveness runner — keeps the marketplace visibly alive, honestly.

One pass per invocation (cron-friendly); ``--daemon`` loops locally.

Per pass:
  1. heartbeat every cohort agent (commit requires a fresh heartbeat)
  2. crash recovery: reveal any committed-but-unrevealed bids whose
     reveal window is open  <-- NEVER GHOST, this is the hard requirement
  3. sweep open tasks: a random 3-4 eligible agents commit per task
     (salt persisted to disk BEFORE the commit POST)
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
import json
import os
import random
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from net import HttpError, get_all_pages, get_json, post_json  # noqa: E402
import fountain  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
COMMITS_PATH = os.path.join(STATE_DIR, "commits.json")
QUEUE_PATH = os.path.join(STATE_DIR, "performance_queue.json")

BID_FRACTION_LO, BID_FRACTION_HI = 0.85, 0.98
COMMIT_SAFETY_MS = 30_000
REVEAL_SAFETY_MS = 10_000
# The server enforces a 60s heartbeat TTL on every bid action
# (commit/reveal): ts - last_heartbeat > 60_000 -> 403 "agent heartbeat
# expired".  Refresh sooner than that so clock skew between runner and
# server can never turn a fresh heartbeat stale at the gate.
HEARTBEAT_FRESH_MS = 45_000


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
    """Offchain sealed-bid commitment:
    keccak256(abi.encodePacked(bytes32(price), salt, keccak256(agent_id))).

    Byte-identical to the server's offchain ``sealed_commitment``; this is
    for the OFFCHAIN API flow only. It is NOT the onchain scheme (which
    additionally binds auctionId and chainId) and must never be submitted
    to ``CommitRevealAuction.commit()``.
    """
    if price_wei <= 0:
        raise ValueError("price_wei must be positive")
    if len(salt) != 32:
        raise ValueError("salt must be 32 bytes")
    agent_id_hash = _keccak256(str(agent_id).encode("utf-8"))
    return _keccak256(int(price_wei).to_bytes(32, "big") + bytes(salt) + agent_id_hash)


def receipt_hash_for(artifact: bytes) -> str:
    return "0x" + _keccak256(artifact).hex()


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
        self.now_ms = int(time.time() * 1000)
        # agent_id -> wall-clock ms of the last heartbeat POST this
        # process made.  In-pass dedupe only; the server is the source
        # of truth for TTL.  See ensure_heartbeat().
        self._hb_at = {}
        # Flips to False on any partial failure (e.g. one agent's
        # heartbeat dies).  one_pass()/main() turn this into a nonzero
        # process exit code so cron can detect a degraded pass.
        self.pass_ok = True

    def note_failure(self, msg):
        """Log a partial failure and mark the pass as failed."""
        log(msg)
        self.pass_ok = False

    # -- step 1: heartbeats -------------------------------------------------
    def ensure_heartbeat(self, agent) -> bool:
        """Refresh this agent's heartbeat so the next bid action passes
        the server's 60s TTL check.

        A pass heartbeats at step 1 but can run for many minutes
        (paginated task fetches under truncation retries), so those
        heartbeats are expired by the time the commit/reveal sweep runs.
        Calling this immediately before each commit/reveal closes the
        TTL-vs-pass-duration skew.  Heartbeats are cheap, idempotent,
        and not rate-limited; an in-pass 45s dedupe guard avoids
        spamming one per task when an agent bids often.

        404 (unknown agent — e.g. the registry was wiped by a restart)
        triggers the idempotent re-register path, then one heartbeat
        retry.  Any other failure returns False; callers treat the
        refresh as best-effort and proceed with the bid action unchanged
        so a heartbeat transport failure never changes bidding semantics.
        """
        aid = agent["agent_id"]
        now = int(time.time() * 1000)
        if now - self._hb_at.get(aid, 0) < HEARTBEAT_FRESH_MS:
            return True
        try:
            post_json(self.base_url, "/v1/a2a/heartbeat", {"agent_id": aid})
        except HttpError as e:
            if e.status == 404:
                # self-heal: re-register a missing agent (idempotent:
                # reputation/registered_at/origin are preserved server-side)
                log(f"heartbeat 404 for {aid}: re-registering")
                try:
                    post_json(self.base_url, "/v1/a2a/register",
                              register_agent_body(agent))
                    post_json(self.base_url, "/v1/a2a/heartbeat",
                              {"agent_id": aid})
                except HttpError as e2:
                    log(f"  re-register failed for {aid}: {e2}")
                    return False
                except Exception as e2:
                    log(f"  re-register transport failure for {aid}: {e2}")
                    return False
            else:
                log(f"heartbeat refresh failed for {aid}: {e}")
                return False
        except Exception as e:
            # Transport failure AFTER net.py exhausted its retries
            # (truncated read, connection reset, ...).
            log(f"heartbeat refresh transport failure for {aid}: {e}")
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
        # Same 60s TTL gate as commit: refresh best-effort before the
        # reveal POST; existing error handling covers a failed refresh.
        agent = self.by_id.get(aid)
        if agent is not None:
            self.ensure_heartbeat(agent)
        try:
            post_json(self.base_url, "/v1/a2a/bids/reveal", body)
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
        """Commit a random 3-4 eligible agents per open task."""
        for task_id, task in tasks.items():
            if task.get("state") not in ("open", "auction"):
                continue
            if not task.get("sealed"):
                continue
            cd = int(task.get("commit_deadline") or 0)
            if self.now_ms > cd - COMMIT_SAFETY_MS:
                continue
            bounty = float(task.get("bounty_axm") or 0)
            if bounty <= 0:
                continue
            tags = set(task.get("tags") or [])
            per_agent = self.commits.setdefault(task_id, {})
            eligible = [a for a in self.cohort
                        if a["agent_id"] not in per_agent
                        and set(a["capability_tags"]) & tags]
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
        # time; the pass-start heartbeat is long expired by the time the
        # sweep runs.  Refresh best-effort — a failed refresh must not
        # change bidding semantics, so the commit attempt proceeds
        # regardless and existing error handling applies.
        self.ensure_heartbeat(agent)
        try:
            post_json(self.base_url, "/v1/a2a/bids/commit",
                      {"task_id": task_id, "agent_id": aid,
                       "commitment": commitment})
            log(f"  commit {aid} on {task_id} ({bid_axm} AXM sealed)")
        except HttpError as e:
            if "already committed" in e.body:
                log(f"  commit {aid} on {task_id}: already committed (idempotent)")
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
    def perform_assigned(self, tasks):
        queue = load_queue()
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
        tasks = self._task_index()
        self.recover_and_reveal(tasks)
        self.sweep_commits(tasks)
        tasks = self._task_index()  # refresh states after commits
        self.reveal_due(tasks)
        self.close_due(tasks)
        tasks = self._task_index()
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
    """
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
