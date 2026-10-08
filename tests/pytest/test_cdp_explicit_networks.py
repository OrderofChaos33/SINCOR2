"""CDP explicit-networks hedge (dev-watch item 78).

From CDP SDK 1.56.0, an x402 route with scheme "upto" and NO explicit
networks expands to BOTH Base and Solana (previously Base-only).  Routes
that must stay EVM-only therefore need an explicit networks list, and no
code path may rely on SDK defaults.  :func:`build_route_config` is the
only sanctioned constructor and fails closed on None/empty.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sincor2.x402_payments import (
    MANDATES_API_WATCH,
    X402_NETWORKS_EXPLICIT,
    build_payment_payload,
    build_route_config,
    facilitator_route,
)

SRC = Path(__file__).resolve().parent.parent.parent \
    / "src" / "sincor2" / "x402_payments.py"


# --- (a) default construction carries exactly the explicit networks ---------


def test_default_route_has_explicit_networks():
    cfg = build_route_config()
    assert cfg["networks"] == list(X402_NETWORKS_EXPLICIT)
    assert cfg["networks"] == ["base"]
    assert len(cfg["networks"]) > 0
    assert cfg["scheme"] == "exact"


def test_explicit_networks_constant_is_nonempty():
    assert isinstance(X402_NETWORKS_EXPLICIT, (tuple, list))
    assert len(X402_NETWORKS_EXPLICIT) > 0
    assert all(str(n).strip() for n in X402_NETWORKS_EXPLICIT)


def test_custom_networks_allowed_but_normalized():
    cfg = build_route_config(networks=["Base", " base-sepolia ", "base"])
    assert cfg["networks"] == ["base", "base-sepolia"]


# --- (b) None/empty networks raise (fail closed) ------------------------------


def test_none_networks_raises():
    with pytest.raises(ValueError, match="explicit"):
        build_route_config(networks=None)


def test_empty_networks_raises():
    with pytest.raises(ValueError, match="explicit"):
        build_route_config(networks=[])


def test_blank_networks_raises():
    with pytest.raises(ValueError, match="explicit"):
        build_route_config(networks=["   "])


# --- (c) no route is constructed without build_route_config -------------------
# Import-lint: parse the module AST and assert the only places a route
# (a "network"/"networks" key) is produced are build_route_config and
# build_payment_payload, and that build_payment_payload delegates to
# build_route_config while create_challenge delegates to
# build_payment_payload.


def _func_defs(tree):
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_no_route_bypass():
    tree = ast.parse(SRC.read_text())
    funcs = _func_defs(tree)
    allowed = {"build_route_config", "build_payment_payload"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {
            k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        if keys & {"network", "networks"}:
            # Find the enclosing function.
            owner = None
            for name, fdef in funcs.items():
                if (fdef.lineno <= node.lineno
                        and node.lineno <= (fdef.end_lineno or 0)):
                    owner = name
            assert owner in allowed, (
                f"route dict built outside sanctioned constructors: {owner}")
    # build_payment_payload must delegate to build_route_config...
    src = ast.get_source_segment(
        SRC.read_text(), funcs["build_payment_payload"]) or ""
    assert "build_route_config(" in src
    # ...and create_challenge must go through build_payment_payload.
    src = ast.get_source_segment(
        SRC.read_text(), funcs["create_challenge"]) or ""
    assert "build_payment_payload(" in src
    assert '"network": "base"' not in src  # no hardcoded route literal


def test_mandates_api_watch_is_tracked():
    # Design note exists so the Mandates API (CDP 1.58.0) is evaluated as
    # the session-key primitive rather than silently skipped.
    assert "authorizeMandatePaymentSession" in MANDATES_API_WATCH.__doc__
    assert "revokeWalletMandate" in MANDATES_API_WATCH.__doc__


# --- (d) SDK default-expansion is overridden ----------------------------------


def _mock_cdp_sdk_create_route(scheme, networks=None):
    """Simulates CDP SDK >= 1.56.0: an "upto" route with no explicit
    networks expands to Base AND Solana."""
    if networks is None:
        networks = ["base", "solana"] if scheme == "upto" else ["base"]
    return {"scheme": scheme, "networks": list(networks)}


def test_wrapper_overrides_sdk_default_expansion():
    # Naive call: the SDK would expand to Base + Solana.
    naive = _mock_cdp_sdk_create_route("upto")
    assert naive["networks"] == ["base", "solana"]
    # Our path: facilitator_route intercepts None and pins the explicit
    # list, so the SDK never sees a missing networks argument.
    cfg = facilitator_route(scheme="upto")
    routed = _mock_cdp_sdk_create_route("upto", networks=cfg["networks"])
    assert routed["networks"] == ["base"]
    assert "solana" not in routed["networks"]


def test_facilitator_route_rejects_empty():
    with pytest.raises(ValueError, match="explicit"):
        facilitator_route(networks=[])


def test_payment_payload_carries_explicit_networks():
    resource = {
        "chain_id": 8453, "amount_atomic": "1000000",
        "description": "test", "treasury": "0x" + "33" * 20,
        "token_address": "0x" + "44" * 20, "token": "USDC",
        "amount_display": 1.0, "token_decimals": 6, "skill_id": "",
    }
    payload = build_payment_payload(resource, "res-1", "x402-abc")
    assert payload["network"] == "base"
    assert payload["networks"] == ["base"]
    assert payload["scheme"] == "exact"
