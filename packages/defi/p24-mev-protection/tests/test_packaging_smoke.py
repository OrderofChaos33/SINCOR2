"""Packaging smoke tests for the standalone P24 package.

Prove the extracted package stands on its own:
- it imports clean with ONLY its declared dependencies (the Python
  standard library) and its own ``src/`` on the path — no SINCOR2
  platform package is importable or imported;
- every name in ``p24_mev_protection.__all__`` resolves;
- the module's configuration surface is exactly the five environment
  variables documented in DEPLOY.md — no undocumented config keys, and
  in particular no RPC endpoint / stream URL configuration: the module
  reads no network endpoint of any kind. (The protected RPC/stream line
  in ENGAGEMENT.md is a counterparty-side procurement pass-through, not
  a setting this module consumes.)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
PKG_SRC = PKG_ROOT / "src"
MODULE_FILE = PKG_SRC / "p24_mev_protection" / "mev_capture.py"

EXPECTED_ENV_VARS = {
    "TREASURY_ADDRESS",
    "MEV_FLOW_THRESHOLD_USD",
    "MEV_IMPACT_FLAG_BPS",
    "MEV_MIN_FLOAT_USD",
    "MEV_DRY_RUN",
}


def _run_isolated(code: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run ``code`` in a fresh interpreter whose sys.path contains ONLY
    this package's src/ — the platform repo is not on the path at all."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEV_")}
    env.pop("TREASURY_ADDRESS", None)
    env["PYTHONPATH"] = str(PKG_SRC)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, cwd="/tmp", timeout=60,
    )


def test_package_imports_clean_standalone():
    proc = _run_isolated(
        "import p24_mev_protection as p\n"
        "import p24_mev_protection.mev_capture as m\n"
        "print(p.__version__)\n"
        "print(m.ProtectionPolicy().guardrail_bps)\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert "0.1.0" in proc.stdout
    assert "100" in proc.stdout


def test_package_does_not_import_platform_internals():
    proc = _run_isolated(
        "import sys\n"
        "import p24_mev_protection\n"
        "import p24_mev_protection.mev_capture\n"
        "leaked = [m for m in sys.modules if m == 'sincor2' or m.startswith('sincor2.')]\n"
        "assert not leaked, leaked\n"
        "print('clean')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert "clean" in proc.stdout


def test_public_api_all_names_resolve():
    import p24_mev_protection as pkg

    for name in pkg.__all__:
        assert hasattr(pkg, name), name
    # core protection surface present
    for name in ("ProtectionPolicy", "ProtectedOrder", "MEVDetector",
                 "FlowMeter", "SignerRegistry", "LiveGate", "Bidder"):
        assert name in pkg.__all__


def test_config_surface_is_exactly_the_documented_env_vars():
    source = MODULE_FILE.read_text()
    found = set(re.findall(r'os\.getenv\(\s*"([A-Z_]+)"', source))
    found |= set(re.findall(r"os\.getenv\(\s*'([A-Z_]+)'", source))
    # TREASURY is read via a multi-line os.getenv( call
    assert found == EXPECTED_ENV_VARS, found


def test_module_declares_no_rpc_or_stream_endpoint_config():
    source = MODULE_FILE.read_text()
    # No network endpoint configuration of any kind in the module:
    # no RPC URL, no websocket/stream URL, no HTTP client usage.
    for token in ("RPC_URL", "rpc_url", "WEBSOCKET", "wss://", "https://",
                  "requests.", "urllib", "socket"):
        assert token not in source, token


def test_env_overrides_drive_module_configuration():
    proc = _run_isolated(
        "from p24_mev_protection import mev_capture as m\n"
        "print(m.FLOW_THRESHOLD_USD, m.IMPACT_FLAG_BPS, m.MIN_FLOAT_USD, m.DRY_RUN)\n"
        "print(m.TREASURY)\n",
        extra_env={
            "MEV_FLOW_THRESHOLD_USD": "2500.5",
            "MEV_IMPACT_FLAG_BPS": "75",
            "MEV_MIN_FLOAT_USD": "250.0",
            "MEV_DRY_RUN": "0",
            "TREASURY_ADDRESS": "0x000000000000000000000000000000000000dEaD",
        },
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "2500.5 75 250.0 False"
    assert lines[1] == "0x000000000000000000000000000000000000dEaD"


def test_env_defaults_match_deploy_doc():
    proc = _run_isolated(
        "from p24_mev_protection import mev_capture as m\n"
        "print(m.FLOW_THRESHOLD_USD, m.IMPACT_FLAG_BPS, m.MIN_FLOAT_USD, m.DRY_RUN)\n"
        "print(m.TREASURY)\n"
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "1000.0 50 100.0 True"
    assert lines[1] == "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
