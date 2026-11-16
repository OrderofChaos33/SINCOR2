"""Packaging smoke tests: the package stands alone.

- Imports clean from its own src/ layout.
- Every import in the package source is Python standard library
  (the declared dependency set is empty at runtime).
- No reach-ins to platform internals: no ``sincor2`` import anywhere
  in the package source.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PKG_SRC = Path(__file__).resolve().parents[1] / "src" / "p21_yield_aggregator"
STDLIB = sys.stdlib_module_names


def test_package_imports_clean():
    import p21_yield_aggregator as pkg

    assert pkg.__version__ == "0.1.0"
    agg = pkg.get_default_aggregator()
    plan = agg.plan_rebalance(capital_usd=1_000, risk_budget=0.30)
    assert plan.mode == "dry_run"
    assert plan.executed is False


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_only_stdlib_imports():
    for py in PKG_SRC.glob("*.py"):
        for name in _imports_of(py):
            assert name in STDLIB or name == "p21_yield_aggregator", (
                f"{py.name} imports non-stdlib {name!r}"
            )


def test_no_platform_reach_ins():
    for py in PKG_SRC.glob("*.py"):
        text = py.read_text()
        # Import-level check only: data strings such as the protocol id
        # "sincor_shared_liquidity" are content, not reach-ins.
        assert "src.sincor2" not in text, f"{py.name} imports the platform tree"
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not a.name.startswith("sincor2") for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("sincor2")
