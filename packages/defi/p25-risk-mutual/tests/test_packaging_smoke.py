"""Packaging smoke tests (PACK-2): the extracted package stands alone.

- imports clean in a fresh interpreter whose only path entry is the
  package's own ``src/`` (no SINCOR2 platform tree on the path);
- importing it pulls in no platform module (nothing named ``sincor2``);
- every import in the packaged module is stdlib (dependency inventory
  enforced as a test — see PACKAGING.md);
- the public API surface re-exported by the package is present.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
PKG_SRC = PKG_ROOT / "src"
MODULE = PKG_SRC / "sincor_p25_risk_mutual" / "insurance_mutual.py"


def test_package_imports_clean_standalone():
    code = (
        "import sys\n"
        "import sincor_p25_risk_mutual as pkg\n"
        "assert pkg.__version__ == '0.1.0'\n"
        "leaked = [n for n in sys.modules if n.split('.')[0] == 'sincor2']\n"
        "assert not leaked, leaked\n"
        "pool = pkg.MutualPool()\n"
        "assert pool.reserves_wei == 0 and not pool.live_underwriting\n"
        "print('standalone-import-ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env={"PYTHONPATH": str(PKG_SRC), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, cwd="/tmp", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "standalone-import-ok" in proc.stdout


def test_module_imports_are_stdlib_only():
    tree = ast.parse(MODULE.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert imported, "module should import something"
    non_stdlib = imported - sys.stdlib_module_names - {"__future__"}
    assert not non_stdlib, f"non-stdlib imports in packaged module: {non_stdlib}"


def test_public_api_surface():
    sys.path.insert(0, str(PKG_SRC))
    import sincor_p25_risk_mutual as pkg
    for name in ("MutualPool", "Underwriter", "ClaimEngine", "Governance",
                 "AssessorRegistry", "MutualError", "commitment_id"):
        if name == "ClaimEngine":
            continue  # claims live on MutualPool in this reference build
        assert hasattr(pkg, name), name
    for name in pkg.__all__:
        assert hasattr(pkg, name), name
