"""W-210 — DeFi products cross-instance lost updates (hunt 64).

products.promote() / approve_marketing() did _load_state -> mutate ->
_save_state with no lock, so two concurrent callers (separate
instances/processes sharing one state file) that each loaded the same
snapshot each saved a whole file containing only their own mutation —
the second save silently erased the first caller's promotion/approval.

Fix pattern (mirrors W-213 in resilience/auto_heal.py): every mutator
does reload-mutate-save under an exclusive flock on a sidecar lock
file, so the second caller's reload sees the first caller's write.

The interleaving is forced deterministically: _load_state is wrapped
with a barrier so, on the unfixed code, both threads hold the same
stale snapshot before either saves. On the fixed code the tail load
happens under the flock, the barrier simply times out for the lock
holder, and the second caller reloads the fresh state.
"""
from __future__ import annotations

import json
import threading

import pytest

from sincor2.defi import products
from sincor2.defi.proof_ledger import ProofLedger

SKU_A = "SINCOR-DEFI-P02-CLMM"
SKU_B = "SINCOR-DEFI-P01-VAULT"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    d = tmp_path / "arm_data"
    monkeypatch.setenv("SINCOR_DEFI_ARM_DATA_DIR", str(d))
    return d


def _seed(state_path, entries):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(entries), encoding="utf-8")


def _run_concurrently(monkeypatch, calls):
    """Run zero-arg calls in threads with _load_state rendezvous-hooked."""
    barrier = threading.Barrier(len(calls))
    orig_load = products._load_state
    active = {"on": True}

    def hooked(path=None):
        state = orig_load(path)
        if active["on"]:
            try:
                barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
        return state

    monkeypatch.setattr(products, "_load_state", hooked)
    results, errors = [], []

    def runner(fn):
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001 - surfaced by assertion
            errors.append(exc)

    threads = [threading.Thread(target=runner, args=(fn,)) for fn in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    active["on"] = False
    assert not any(t.is_alive() for t in threads), "concurrent calls deadlocked"
    assert not errors, f"concurrent calls raised: {errors!r}"
    return results


def test_concurrent_promotes_for_different_skus_both_survive(
    isolated, tmp_path, monkeypatch
):
    state_path = tmp_path / "products_state.json"
    _seed(state_path, {
        SKU_A: {"stage": "spec", "version": "0.1.0"},
        SKU_B: {"stage": "spec", "version": "0.1.0"},
    })
    ledger = ProofLedger()

    results = _run_concurrently(monkeypatch, [
        lambda: products.promote(SKU_A, "build", ledger=ledger,
                                 root=products.REPO_ROOT,
                                 state_path=str(state_path)),
        lambda: products.promote(SKU_B, "build", ledger=ledger,
                                 root=products.REPO_ROOT,
                                 state_path=str(state_path)),
    ])
    assert all(r["ok"] for r in results), results

    # Fresh reload must show BOTH promotions — neither lost.
    fresh = {p["sku"]: p for p in products.build_registry(str(state_path))}
    assert fresh[SKU_A]["stage"] == "build", (
        f"{SKU_A} promotion lost: stage={fresh[SKU_A]['stage']}")
    assert fresh[SKU_B]["stage"] == "build", (
        f"{SKU_B} promotion lost: stage={fresh[SKU_B]['stage']}")
    assert fresh[SKU_A]["version"] == "0.1.1"
    assert fresh[SKU_B]["version"] == "0.1.1"
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw[SKU_A]["stage"] == "build"
    assert raw[SKU_B]["stage"] == "build"


def test_concurrent_marketing_approvals_for_different_skus_both_survive(
    isolated, tmp_path, monkeypatch
):
    state_path = tmp_path / "products_state.json"
    _seed(state_path, {
        SKU_A: {"stage": "product", "version": "0.1.0"},
        SKU_B: {"stage": "product", "version": "0.1.0"},
    })

    results = _run_concurrently(monkeypatch, [
        lambda: products.approve_marketing(SKU_A, state_path=str(state_path)),
        lambda: products.approve_marketing(SKU_B, state_path=str(state_path)),
    ])
    assert all(r["ok"] for r in results), results

    fresh = {p["sku"]: p for p in products.build_registry(str(state_path))}
    assert fresh[SKU_A]["marketing_status"] == "approved", (
        f"{SKU_A} approval lost: {fresh[SKU_A]['marketing_status']}")
    assert fresh[SKU_B]["marketing_status"] == "approved", (
        f"{SKU_B} approval lost: {fresh[SKU_B]['marketing_status']}")


def test_sequential_promote_and_approve_still_work(isolated, tmp_path):
    # Non-concurrent behaviour is unchanged by the locking.
    state_path = tmp_path / "products_state.json"
    _seed(state_path, {SKU_A: {"stage": "spec", "version": "0.1.0"}})
    ledger = ProofLedger()
    out = products.promote(SKU_A, "build", ledger=ledger,
                           root=products.REPO_ROOT,
                           state_path=str(state_path))
    assert out["ok"], out["reasons"]
    assert out["version"] == "0.1.1"
    assert products.get_product(SKU_A, str(state_path))["stage"] == "build"
    # Marketing still refused before product stage.
    assert not products.approve_marketing(SKU_A, state_path=str(state_path))["ok"]
