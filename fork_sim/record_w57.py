"""Wave 57: run the ported fork-sim harness and record w57 ledger entries.

Run from the worktree root:
    PYTHONPATH=src:. python3 fork_sim/record_w57.py

- Performs the fork sims live against Base mainnet (read-only eth_call /
  getLogs / getBlock / getCode replay; no signing, no broadcast).
- Appends fork_sim entries for genuinely passing sims (entry_id ev_w57_<12hex>).
- Appends note entries for the 9 findings (no pinned on-chain counterpart) --
  a skipped sim is never recorded as a passed sim.
- Refuses to record anything if any sim fails.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
import uuid

WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKTREE, "src"))

HARNESS_PATH = os.path.join(WORKTREE, "fork_sim", "run_fork_sims.py")
BRANCH = "xioix/buildout-57-defi-forksim-record"
RECORDED_BY = BRANCH
LEDGER_PATH = os.path.join(WORKTREE, "data", "defi_product_arm",
                           "proof_ledger.json")


def _load_harness():
    spec = importlib.util.spec_from_file_location("fork_sims", HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fork_sims"] = mod
    spec.loader.exec_module(mod)
    return mod


def new_entry_id() -> str:
    return "ev_w57_" + uuid.uuid4().hex[:12]


def main() -> int:
    from sincor2.defi.gates import KIND_FORK_SIM, _passing_entries
    from sincor2.defi.proof_ledger import KIND_NOTE
    from sincor2.defi.products import mint_sku
    from sincor2.defi.proof_ledger import ProofLedger

    harness = _load_harness()
    print("Running fork sims against Base mainnet (read-only)...")
    outcomes, fc = harness.run_all()
    failed = [o for o in outcomes if not o.passed]
    if failed:
        print("REFUSING to record: failed sims:")
        for o in failed:
            print(f"  - {o.product_id}: {o.error}")
        return 1

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
        cwd=WORKTREE).stdout.strip()
    ledger = ProofLedger(path=LEDGER_PATH)
    existing_ids = {e.get("entry_id") for e in ledger.read()}
    recorded: list = []

    for oc in outcomes:
        if not oc.passed:
            continue
        eid = new_entry_id()
        assert eid not in existing_ids, f"duplicate entry_id {eid}"
        entry = {
            "entry_id": eid,
            "sku": mint_sku(oc.product_id),
            "kind": KIND_FORK_SIM,
            "timestamp": time.time(),
            "commit": commit,
            "recorded_by": RECORDED_BY,
            "details": {
                "command": "PYTHONPATH=src:. python3 fork_sim/record_w57.py",
                "suite": "fork_sim/run_fork_sims.py",
                "scope": oc.scope,
                "result": oc.detail,
                "method": ("read-only replay against Base mainnet state at "
                           "pinned block via eth_call/eth_getLogs/eth_getBlock/"
                           "eth_getCode; no signing, no broadcast, zero "
                           "chain-state writes"),
                "rpc": fc.rpc_url.split("@")[-1],
                "fork_block": fc.pinned_block,
                "fork_block_ts": fc.pinned_ts,
                "read_only": True,
                "no_broadcast": True,
                "branch": BRANCH,
                "passed": 1,
                "failed": 0,
            },
        }
        ledger._entries.append(entry)
        ledger._save()
        existing_ids.add(eid)
        recorded.append((oc.product_id, mint_sku(oc.product_id), eid))
        print(f"  fork_sim {mint_sku(oc.product_id)}: {eid}")

    for pid, reason in harness.FINDINGS:
        eid = new_entry_id()
        assert eid not in existing_ids, f"duplicate entry_id {eid}"
        entry = {
            "entry_id": eid,
            "sku": mint_sku(pid),
            "kind": KIND_NOTE,
            "timestamp": time.time(),
            "commit": commit,
            "recorded_by": RECORDED_BY,
            "details": {
                "note": f"fork_sim not applicable: {reason}",
                "branch": BRANCH,
            },
        }
        ledger._entries.append(entry)
        ledger._save()
        existing_ids.add(eid)
        recorded.append((pid, mint_sku(pid), eid))
        print(f"  note     {mint_sku(pid)}: {eid}")

    # Gate self-check: every passing sim must satisfy the test->audit gate.
    print("\ngate check (_passing_entries for fork_sim):")
    gate_ok = True
    for oc in outcomes:
        if not oc.passed:
            continue
        runs = _passing_entries(ledger, mint_sku(oc.product_id), KIND_FORK_SIM)
        ok = bool(runs)
        gate_ok &= ok
        print(f"  {mint_sku(oc.product_id)}: {'OK' if ok else 'MISSING'}")
    if not gate_ok:
        print("GATE CHECK FAILED")
        return 1
    print(f"\nrecorded {len(recorded)} entries ({len(outcomes)} fork_sim + "
          f"{len(harness.FINDINGS)} notes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
