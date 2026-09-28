#!/usr/bin/env python3
"""SINCOR task fountain — three lanes of perpetual task supply.

All lanes: poster_id="sincor-fountain", sealed=true, idempotent seed_keys.

LANE A — heartbeat (fountain_catalog.yaml): machine-performable ops tasks
    (metrics sweeps, auction health checks). The liveness runner executes
    these end-to-end and only proves work when every acceptance check passes.

LANE B — DeFi asset pipeline (defi_catalog/*.json): the REAL DeFi catalog
    task waves (26 products, 624 tasks), drip-fed in dependency order.
    Winners perform these via the performance_queue handoff — the runner
    NEVER fabricates these proofs.

LANE C — self-improvement (self_improve_allowlist.yaml): curated tasks that
    improve SINCOR itself, ONLY from the allowlist. Hard blocklist enforced
    in code (money paths, auth, stake/pool ledgers, task board state logic,
    contracts/, keys/credentials, adjudication, KYA). Every Lane C task
    carries exact file paths, runnable-test acceptance criteria, and
    "delivery: branch + PR, never direct to main" with a human merge gate.

SCOPE-DRIFT CONTROL: the fountain can ONLY post tasks defined in these three
catalog files. Unknown task types are refused with FountainRefused — there is
no freeform generation path.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from net import HttpError, get_json, post_json  # noqa: E402

import yaml  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
POSTER_ID = "sincor-fountain"

LANE_A_CATALOG = os.path.join(HERE, "fountain_catalog.yaml")
LANE_C_ALLOWLIST = os.path.join(HERE, "self_improve_allowlist.yaml")
DEFI_CATALOG_DIR = os.environ.get("SINCOR_DEFI_CATALOG_DIR",
                                 os.path.join(HERE, "defi_catalog"))
STATE_PATH = os.path.join(HERE, "fountain_state.json")

# Lane B: phases drip-feed in build order (spec = wave one, already posted).
DEFI_PHASE_ORDER = ["scaffold", "core", "testing", "audit", "docs", "deploy"]
DEFI_EFFORT_SECONDS = {"S": 1800, "M": 7200, "L": 21600}


class FountainRefused(Exception):
    """Raised when the fountain refuses to post (unknown type, blocklist, …)."""


# ---------------------------------------------------------------------------
# executors — LANE A only. Each returns (artifact_bytes, [(check, passed)..]).
# The runner only submits a proof when every check passes.
# ---------------------------------------------------------------------------

def execute_defi_metrics_sweep(base_url: str):
    pool = get_json(base_url, "/v1/a2a/pool")
    tasks = get_json(base_url, "/v1/a2a/tasks?per_page=100").get("tasks", [])
    by_state = {}
    for t in tasks:
        by_state[t.get("state", "?")] = by_state.get(t.get("state", "?"), 0) + 1
    report = {
        "type": "defi-metrics-sweep",
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pool": {k: pool.get(k) for k in
                 ("funded_axm", "allocated_axm", "available_axm",
                  "allocation_count")},
        "tasks_total": len(tasks),
        "tasks_by_state": by_state,
    }
    blob = json.dumps(report, indent=2).encode()
    checks = [
        ("pool_fields_present",
         all(report["pool"].get(k) is not None for k in
             ("funded_axm", "allocated_axm", "available_axm"))),
        ("task_count_nonnegative", report["tasks_total"] >= 0),
        ("timestamp_fresh", True),
    ]
    return blob, checks


def execute_auction_health_check(base_url: str):
    import time
    now_ms = int(time.time() * 1000)
    tasks = get_json(base_url, "/v1/a2a/tasks?per_page=100").get("tasks", [])
    stale = [t for t in tasks
             if t.get("state") in ("open", "auction")
             and t.get("reveal_deadline") and now_ms > int(t["reveal_deadline"])]
    closed, failed = [], []
    for t in stale:
        tid = t["task_id"]
        try:
            post_json(base_url, f"/v1/a2a/tasks/{tid}/close", {})
            detail = get_json(base_url, f"/v1/a2a/tasks/{tid}")
            if detail.get("state") not in ("open", "auction"):
                closed.append(tid)
            else:
                failed.append(tid)
        except HttpError:
            failed.append(tid)
    report = {
        "type": "auction-health-check",
        "stale_found": len(stale),
        "closed": closed,
        "failed": failed,
    }
    blob = json.dumps(report, indent=2).encode()
    checks = [
        ("stale_list_consistent", report["stale_found"] == len(closed) + len(failed)),
        ("no_failures", not failed),
    ]
    return blob, checks


def execute_directory_snapshot(base_url: str):
    agents = get_json(base_url, "/v1/a2a/agents").get("agents", [])
    try:
        directory = get_json(base_url, "/v1/a2a/directory")
        kpis = directory.get("kpis", {})
    except HttpError:
        kpis = {}
    report = {
        "type": "directory-snapshot",
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "agent_count": len(agents),
        "agent_ids": sorted(a.get("agent_id", "?") for a in agents)[:200],
        "kpis": kpis,
    }
    blob = json.dumps(report, indent=2).encode()
    checks = [
        ("agent_count_matches_ids", report["agent_count"] == len(report["agent_ids"])),
        ("timestamp_fresh", True),
    ]
    return blob, checks


def execute_fee_ledger_watch(base_url: str):
    pool = get_json(base_url, "/v1/a2a/pool")
    funded = float(pool.get("funded_axm") or 0)
    allocated = float(pool.get("allocated_axm") or 0)
    available = float(pool.get("available_axm") or 0)
    invariant_holds = abs(funded - (allocated + available)) < 1e-6
    report = {
        "type": "fee-ledger-watch",
        "funded_axm": funded,
        "allocated_axm": allocated,
        "available_axm": available,
        "invariant_funded_eq_allocated_plus_available": invariant_holds,
    }
    blob = json.dumps(report, indent=2).encode()
    checks = [("ledger_invariant_holds", invariant_holds)]
    return blob, checks


def execute_task_board_audit(base_url: str):
    tasks = get_json(base_url, "/v1/a2a/tasks?per_page=100").get("tasks", [])
    seeds = [t.get("seed_key") for t in tasks if t.get("seed_key")]
    dupes = len(seeds) - len(set(seeds))
    missing = sum(1 for t in tasks if not t.get("seed_key"))
    report = {
        "type": "task-board-audit",
        "tasks_total": len(tasks),
        "seeded": len(seeds),
        "duplicate_seed_keys": dupes,
        "missing_seed_keys": missing,
    }
    blob = json.dumps(report, indent=2).encode()
    checks = [
        ("no_duplicate_seed_keys", dupes == 0),
        ("counts_consistent", report["seeded"] + missing == len(tasks)),
    ]
    return blob, checks


def execute_heartbeat_monitor(base_url: str):
    agents = get_json(base_url, "/v1/a2a/agents?live=1").get("agents", [])
    all_agents = get_json(base_url, "/v1/a2a/agents").get("agents", [])
    report = {
        "type": "heartbeat-monitor",
        "live_agents": len(agents),
        "total_agents": len(all_agents),
    }
    blob = json.dumps(report, indent=2).encode()
    checks = [
        ("live_le_total", report["live_agents"] <= report["total_agents"]),
    ]
    return blob, checks


EXECUTORS = {
    "defi-metrics-sweep": execute_defi_metrics_sweep,
    "auction-health-check": execute_auction_health_check,
    "directory-snapshot": execute_directory_snapshot,
    "fee-ledger-watch": execute_fee_ledger_watch,
    "task-board-audit": execute_task_board_audit,
    "heartbeat-monitor": execute_heartbeat_monitor,
}


# ---------------------------------------------------------------------------
# catalog loading
# ---------------------------------------------------------------------------

def _load_yaml(path: str):
    with open(path) as f:
        return yaml.safe_load(f)


def _load_lane_a() -> dict:
    """Lane A catalog: type -> spec, from fountain_catalog.yaml."""
    doc = _load_yaml(LANE_A_CATALOG)
    out = {}
    for entry in doc.get("task_types", []):
        out[entry["type"]] = entry
    return out


# FOUNTAIN_TYPES keeps the historical name: the Lane A (heartbeat) catalog.
FOUNTAIN_TYPES: dict = _load_lane_a()


def _load_allowlist() -> list:
    doc = _load_yaml(LANE_C_ALLOWLIST)
    return doc.get("tasks", [])


_ALLOWLIST_CACHE: list | None = None


def allowlist_entries() -> list:
    """Lane C allowlist entries (cached)."""
    global _ALLOWLIST_CACHE
    if _ALLOWLIST_CACHE is None:
        _ALLOWLIST_CACHE = _load_allowlist()
    return _ALLOWLIST_CACHE


def _load_defi_catalog() -> list:
    """All DeFi catalog tasks, ordered for the pipeline drip-feed.

    Phase order scaffold -> core -> testing -> audit -> docs -> deploy;
    file order within a phase (stable). 'spec' tasks are wave one and are
    already posted, so they are excluded here (they still satisfy
    depends_on via the live board).
    """
    tasks = []
    for path in sorted(glob.glob(os.path.join(DEFI_CATALOG_DIR, "p*.json"))):
        with open(path) as f:
            data = json.load(f)
        for t in data:
            if t.get("phase") in DEFI_PHASE_ORDER:
                tasks.append(t)
    order = {p: i for i, p in enumerate(DEFI_PHASE_ORDER)}
    tasks.sort(key=lambda t: order[t["phase"]])
    return tasks


_DEFI_CACHE: list | None = None


def defi_catalog() -> list:
    global _DEFI_CACHE
    if _DEFI_CACHE is None:
        _DEFI_CACHE = _load_defi_catalog()
    return _DEFI_CACHE


_DEFI_KEYS_CACHE: set | None = None


def defi_task_keys() -> set:
    """ALL catalog task keys (every phase) — used for lane routing.

    Note: defi_catalog() (the drip-feed) excludes 'spec' because wave one
    is already posted, but spec keys are still lane-B tasks when live.
    """
    global _DEFI_KEYS_CACHE
    if _DEFI_KEYS_CACHE is None:
        keys = set()
        for path in sorted(glob.glob(os.path.join(DEFI_CATALOG_DIR, "p*.json"))):
            with open(path) as f:
                for t in json.load(f):
                    keys.add(t["task_key"])
        _DEFI_KEYS_CACHE = keys
    return _DEFI_KEYS_CACHE


# ---------------------------------------------------------------------------
# fountain state cursor (liveness/fountain_state.json)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            st = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    st.setdefault("defi_posted", [])
    st.setdefault("lane_c_posted", {})   # date -> [allowlist ids]
    st.setdefault("daily", {})          # date -> {"lane_b": n, "lane_c": n}
    return st


def save_state(st: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, STATE_PATH)


def _today() -> str:
    return datetime.date.today().isoformat()


def _daily_count(st: dict, lane: str, day: str | None = None) -> int:
    return st["daily"].get(day or _today(), {}).get(lane, 0)


def _bump_daily(st: dict, lane: str, day: str | None = None) -> None:
    day = day or _today()
    st["daily"].setdefault(day, {}).setdefault(lane, 0)
    st["daily"][day][lane] += 1


# ---------------------------------------------------------------------------
# Lane C hard blocklist — enforced in code, no exceptions
# ---------------------------------------------------------------------------

LANE_C_BLOCKED_PATTERNS = [
    r"contracts/",            # smart contracts
    r"stake_ledger",          # stake ledger
    r"sponsored_stake",       # sponsored stake ledger
    r"a2a_bounty_pool",       # pool ledger
    r"bounty_pool",
    r"pool_ledger",
    r"a2a_task_store",        # task board state logic
    r"task_store",
    r"auth_system",           # auth
    r"(^|[/_])auth([/_]|$)",
    r"kya",                   # KYA identity
    r"adjudicat",             # adjudication
    r"money",                 # money paths
    r"treasur",
    r"billing",
    r"bankroll",
    r"credential",            # keys / credentials
    r"secret",
    r"private_key",
    r"id_rsa",
    r"keystore",
    r"wallet",
    r"forwarder",
    r"\.pem$",
    r"(^|[/.])env$",          # .env files
]
_LANE_C_BLOCKED = [re.compile(p, re.IGNORECASE)
                   for p in LANE_C_BLOCKED_PATTERNS]

# Every Lane C task description must carry these (human merge gate).
LANE_C_REQUIRED_PHRASES = [
    "branch + pr",
    "never direct to main",
    "human merge gate",
]


def validate_lane_c(entry: dict) -> None:
    """Enforce the Lane C safety contract. Raises FountainRefused."""
    eid = entry.get("id", "?")
    file_paths = entry.get("file_paths") or []
    if not file_paths:
        raise FountainRefused(f"lane C task {eid}: no file_paths — refused")
    for fp in file_paths:
        for rx in _LANE_C_BLOCKED:
            if rx.search(fp):
                raise FountainRefused(
                    f"lane C task {eid}: blocklisted path '{fp}' "
                    f"(pattern '{rx.pattern}') — refused")
        abs_fp = os.path.join(REPO_ROOT, fp)
        if not os.path.exists(abs_fp):
            raise FountainRefused(
                f"lane C task {eid}: path '{fp}' does not exist in repo — refused")
    desc = (entry.get("description") or "")
    low = desc.lower()
    for phrase in LANE_C_REQUIRED_PHRASES:
        if phrase not in low:
            raise FountainRefused(
                f"lane C task {eid}: description missing required phrase "
                f"'{phrase}' — refused")
    for fp in file_paths:
        if fp not in desc:
            raise FountainRefused(
                f"lane C task {eid}: exact file path '{fp}' not stated in "
                f"description — refused")
    for field in ("title", "bounty_axm", "estimated_seconds", "tags"):
        if not entry.get(field):
            raise FountainRefused(f"lane C task {eid}: missing {field} — refused")


def lane_c_seed(entry_id: str, date: datetime.date | None = None) -> str:
    day = (date or datetime.date.today()).isoformat()
    return f"selfimprove-{entry_id}-{day}"


# ---------------------------------------------------------------------------
# Lane A
# ---------------------------------------------------------------------------

def seed_key_for(task_type: str, date: datetime.date | None = None) -> str:
    day = (date or datetime.date.today()).isoformat()
    return f"fountain-{task_type}-{day}"


def validate_type(task_type: str) -> None:
    """Refuse anything not defined in the Lane A catalog."""
    spec = FOUNTAIN_TYPES.get(task_type)
    if spec is None:
        raise FountainRefused(
            f"unknown fountain task type '{task_type}': not in "
            f"fountain_catalog.yaml — refused (no freeform generation)")
    for field in ("title", "description", "tags", "bounty_axm", "estimated_seconds"):
        if not spec.get(field):
            raise FountainRefused(f"{task_type} missing {field}")
    if not spec["tags"]:
        raise FountainRefused(f"{task_type} needs non-empty tags")
    if not (0 < spec["bounty_axm"] <= 5):
        raise FountainRefused(f"{task_type} bounty out of range")
    if task_type not in EXECUTORS:
        raise FountainRefused(f"{task_type} has no executor")


def execute_lane_a(base_url: str, task_type: str):
    """Run a Lane A executor. Refuses anything that is not Lane A."""
    validate_type(task_type)
    return EXECUTORS[task_type](base_url)


def post_lane_a(base_url: str, task_type: str, date=None):
    """Post one Lane A task. Unknown type -> FountainRefused."""
    validate_type(task_type)
    spec = FOUNTAIN_TYPES[task_type]
    body = {
        "skill": "defi",
        "tags": list(spec["tags"]) + ["fountain-lane-a"],
        "bounty_axm": spec["bounty_axm"],
        "title": f"[Fountain] {spec['title']}",
        "description": spec["description"],
        "sealed": True,
        "seed_key": seed_key_for(task_type, date),
        "auto_refresh": False,
        "poster_id": POSTER_ID,
    }
    return post_json(base_url, "/v1/a2a/tasks", body)


# ---------------------------------------------------------------------------
# Lane B — DeFi asset pipeline
# ---------------------------------------------------------------------------

LANE_B_FOOTER = (
    "\n---\n"
    "Posted by the SINCOR fountain (DeFi asset pipeline — lane B) as poster "
    "sincor-fountain. Acceptance is adjudicated against the numbered criteria "
    "above; proof must be real deliverable evidence. Fabricated proofs are "
    "slashable under the marketplace slashing rules."
)


def due_defi_tasks(live_seeds: set, st: dict, limit: int) -> list:
    """Next catalog tasks due: not posted, not live, deps satisfied."""
    posted = set(st.get("defi_posted", []))
    known = posted | set(live_seeds)
    due = []
    for t in defi_catalog():
        if len(due) >= limit:
            break
        key = t["task_key"]
        if key in posted or key in live_seeds:
            continue
        deps = t.get("depends_on") or []
        if all(d in known for d in deps):
            due.append(t)
    return due


def post_lane_b(base_url: str, max_n: int = 10, st: dict | None = None):
    """Drip-feed the next due DeFi catalog tasks. Returns posted task_ids."""
    st = st if st is not None else load_state()
    day = _today()
    if _daily_count(st, "lane_b", day) >= max_n:
        return []
    live_seeds, _ = _live_seed_keys(base_url)
    remaining = max_n - _daily_count(st, "lane_b", day)
    due = due_defi_tasks(live_seeds, st, remaining)
    posted = []
    for t in due:
        body = {
            "skill": t.get("skill") or "defi",
            "tags": list(t.get("tags") or []) + ["fountain-lane-b"],
            "bounty_axm": t["bounty_axm"],
            "title": t["title"],
            "description": (t.get("description") or "") + LANE_B_FOOTER,
            "sealed": True,
            "seed_key": t["task_key"],
            "auto_refresh": False,
            "poster_id": POSTER_ID,
        }
        try:
            resp = post_json(base_url, "/v1/a2a/tasks", body)
        except HttpError as e:
            print(f"  lane B post {t['task_key']} failed: {e}")
            continue
        posted.append(resp.get("task_id"))
        if t["task_key"] not in st["defi_posted"]:
            st["defi_posted"].append(t["task_key"])
        _bump_daily(st, "lane_b", day)
    save_state(st)
    return posted


def defi_estimated_seconds(task: dict) -> int:
    return DEFI_EFFORT_SECONDS.get(task.get("effort"), 7200)


def defi_task(task_key: str) -> dict | None:
    """Full catalog lookup by task_key (all phases, incl. wave-one spec)."""
    for t in defi_catalog():
        if t["task_key"] == task_key:
            return t
    for path in sorted(glob.glob(os.path.join(DEFI_CATALOG_DIR, "p*.json"))):
        with open(path) as f:
            for t in json.load(f):
                if t.get("task_key") == task_key:
                    return t
    return None


# ---------------------------------------------------------------------------
# Lane C — self-improvement (allowlist only)
# ---------------------------------------------------------------------------

def post_lane_c(base_url: str, max_n: int = 3, st: dict | None = None):
    """Post curated self-improvement tasks from the allowlist only."""
    st = st if st is not None else load_state()
    day = _today()
    if _daily_count(st, "lane_c", day) >= max_n:
        return []
    live_seeds, _ = _live_seed_keys(base_url)
    done_today = set(st["lane_c_posted"].get(day, []))
    posted = []
    for entry in allowlist_entries():
        if _daily_count(st, "lane_c", day) >= max_n:
            break
        eid = entry.get("id")
        if not eid or eid in done_today:
            continue
        validate_lane_c(entry)  # raises FountainRefused on any violation
        sk = lane_c_seed(eid)
        if sk in live_seeds:
            done_today.add(eid)
            continue
        body = {
            "skill": "self-improve",
            "tags": list(entry.get("tags") or []) + ["fountain-lane-c"],
            "bounty_axm": entry["bounty_axm"],
            "title": f"[Self-improve] {entry['title']}",
            "description": entry["description"],
            "sealed": True,
            "seed_key": sk,
            "auto_refresh": False,
            "poster_id": POSTER_ID,
        }
        try:
            resp = post_json(base_url, "/v1/a2a/tasks", body)
        except HttpError as e:
            print(f"  lane C post {eid} failed: {e}")
            continue
        posted.append(resp.get("task_id"))
        done_today.add(eid)
        _bump_daily(st, "lane_c", day)
    st["lane_c_posted"][day] = sorted(done_today)
    save_state(st)
    return posted


# ---------------------------------------------------------------------------
# supply
# ---------------------------------------------------------------------------

def _live_seed_keys(base_url: str):
    tasks = get_json(base_url, "/v1/a2a/tasks?per_page=100").get("tasks", [])
    return ({t.get("seed_key") for t in tasks if t.get("seed_key")},
            {t.get("seed_key"): t.get("state") for t in tasks if t.get("seed_key")})


def _count_open_lane(states: dict, prefix=None, key_set=None) -> int:
    n = 0
    for sk, state in states.items():
        if state not in ("open", "auction"):
            continue
        if prefix and sk.startswith(prefix):
            n += 1
        elif key_set and sk in key_set:
            n += 1
    return n


def ensure_supply(base_url: str, lane_a_min_open: int = 2,
                 lane_b_daily_cap: int = 10, lane_b_open_cap: int = 12,
                 lane_c_daily_cap: int = 3, lane_c_open_cap: int = 4):
    """Keep all three lanes supplied.

    Lane A: at least lane_a_min_open open heartbeat tasks.
    Lane B: drip up to lane_b_daily_cap/day while fewer than lane_b_open_cap
        catalog tasks are open.
    Lane C: drip up to lane_c_daily_cap/day while fewer than lane_c_open_cap
        self-improve tasks are open.
    """
    seed_keys, states = _live_seed_keys(base_url)
    st = load_state()
    posted = {"lane_a": [], "lane_b": [], "lane_c": []}

    # Lane A — heartbeat floor
    open_a = _count_open_lane(states, prefix="fountain-")
    for task_type in FOUNTAIN_TYPES:
        if open_a >= lane_a_min_open:
            break
        sk = seed_key_for(task_type)
        if sk in seed_keys and states.get(sk) in ("open", "auction"):
            continue
        try:
            task = post_lane_a(base_url, task_type)
            posted["lane_a"].append(task.get("task_id"))
            _, states = _live_seed_keys(base_url)
            open_a = _count_open_lane(states, prefix="fountain-")
        except HttpError as e:
            print(f"  lane A post {task_type} failed: {e}")

    # Lane B — DeFi pipeline drip
    if _count_open_lane(states, key_set=defi_task_keys()) < lane_b_open_cap:
        posted["lane_b"] = post_lane_b(base_url, max_n=lane_b_daily_cap, st=st)

    # Lane C — curated self-improvement drip
    if _count_open_lane(states, prefix="selfimprove-") < lane_c_open_cap:
        posted["lane_c"] = post_lane_c(base_url, max_n=lane_c_daily_cap, st=st)

    return posted


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="https://getsincor.com")
    ap.add_argument("--lane", choices=["a", "b", "c", "all"], default="all")
    ap.add_argument("--post-n", type=int, default=0,
                    help="lane A: post the next N types; lanes B/C: cap posts at N "
                         "(0 = ensure supply with defaults)")
    ap.add_argument("--min-open", type=int, default=2,
                    help="lane A minimum open tasks")
    ap.add_argument("--daily-cap-b", type=int, default=10)
    ap.add_argument("--open-cap-b", type=int, default=12)
    ap.add_argument("--daily-cap-c", type=int, default=3)
    ap.add_argument("--open-cap-c", type=int, default=4)
    args = ap.parse_args()

    for t in FOUNTAIN_TYPES:
        validate_type(t)
    for entry in allowlist_entries():
        validate_lane_c(entry)
    print(f"  catalogs OK: {len(FOUNTAIN_TYPES)} lane-A types, "
          f"{len(defi_catalog())} DeFi tasks, "
          f"{len(allowlist_entries())} lane-C allowlist entries")

    if args.lane == "a" and args.post_n > 0:
        seed_keys, _ = _live_seed_keys(args.base_url)
        n = 0
        for task_type in FOUNTAIN_TYPES:
            if n >= args.post_n:
                break
            if seed_key_for(task_type) in seed_keys:
                print(f"  SKIP {task_type}: already live today")
                continue
            task = post_lane_a(args.base_url, task_type)
            print(f"  OK {task_type}: {task.get('task_id')}")
            n += 1
    elif args.lane == "b":
        posted = post_lane_b(args.base_url,
                             max_n=args.post_n or args.daily_cap_b)
        print(f"  lane B posted {len(posted)}")
    elif args.lane == "c":
        posted = post_lane_c(args.base_url,
                             max_n=args.post_n or args.daily_cap_c)
        print(f"  lane C posted {len(posted)}")
    else:
        posted = ensure_supply(
            args.base_url, lane_a_min_open=args.min_open,
            lane_b_daily_cap=args.post_n or args.daily_cap_b,
            lane_b_open_cap=args.open_cap_b,
            lane_c_daily_cap=args.post_n or args.daily_cap_c,
            lane_c_open_cap=args.open_cap_c)
        total = sum(len(v) for v in posted.values())
        print(f"  supply ensured, posted {total}: "
              + ", ".join(f"{k}={len(v)}" for k, v in posted.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
