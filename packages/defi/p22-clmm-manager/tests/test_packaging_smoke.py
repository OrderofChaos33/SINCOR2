# PACK-2 packaging smoke test: the package imports clean with ONLY its
# declared dependencies (Python standard library; see pyproject.toml and
# PACKAGING.md), exposes its public surface, and carries no reach-in to
# platform internals.
from __future__ import annotations

import sys
from pathlib import Path

PKG_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PKG_SRC) not in sys.path:
    sys.path.insert(0, str(PKG_SRC))


def test_package_imports_clean():
    import p22_clmm_manager

    assert p22_clmm_manager.__version__ == "0.1.0"
    for name in p22_clmm_manager.__all__:
        assert hasattr(p22_clmm_manager, name), name


def test_no_platform_imports_anywhere():
    """No module in the package may import the platform (sincor2) tree.

    Checked on import statements (AST), not raw text: docstrings and
    comments in the extracted core legitimately *mention* the platform
    module it came from — mentioning is not importing.
    """
    import ast

    pkg_dir = PKG_SRC / "p22_clmm_manager"
    for py in pkg_dir.glob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("sincor2"), (py.name, alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    assert not node.module.startswith("sincor2"), (py.name, node.module)
                if node.level > 0:
                    # The one platform reach-in (price_oracle) must stay
                    # redirected to the package-local stub.
                    assert node.module != "price_oracle", (py.name, node.module)


def test_only_stdlib_dependencies():
    """Every top-level import in the package is from the standard library."""
    import ast

    pkg_dir = PKG_SRC / "p22_clmm_manager"
    stdlib = sys.stdlib_module_names
    for py in pkg_dir.glob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in stdlib, (py.name, alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    assert node.module.split(".")[0] in stdlib, (py.name, node.module)


def test_price_feed_stub_binds_declared_assets():
    """price_feed_for works against the vendored stub with a duck-typed
    oracle, fails closed on undeclared assets, and wires no live feed."""
    from types import SimpleNamespace

    from p22_clmm_manager import price_feed_for

    oracle = SimpleNamespace(
        get_price=lambda asset, now, strict=False: SimpleNamespace(price_fp=12345)
    )
    feed = price_feed_for(oracle)
    assert feed.product_key == "P02_CLMM"
    assert feed.assets == ["ETH/USD", "USDC/USD"]
    assert feed.price("ETH/USD") == 12345
    assert feed.require_all() == {"ETH/USD": 12345, "USDC/USD": 12345}
    try:
        feed.price("BTC/USD")
    except Exception:
        pass
    else:
        raise AssertionError("undeclared asset must fail closed")
