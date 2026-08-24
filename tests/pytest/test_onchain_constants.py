from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sincor2.onchain.constants import (  # noqa: E402
    AXIOM_TOKEN,
    SINC_DECIMALS,
    SINC_TOKEN,
    STALE_ADDRESSES,
    TREASURY,
    assert_live_not_stale,
    catalog,
    is_stale,
    resolve_address,
)
from sincor2.onchain.probe import (  # noqa: E402
    decode_abi_string,
    decode_uint,
    encode_abi_string,
    encode_uint,
    validate_at_startup,
)

import pytest  # noqa: E402


RETIRED_SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
RETIRED_AXM = "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822"


def test_live_pointers_match_canonical_doc() -> None:
    assert AXIOM_TOKEN.lower() == "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
    assert SINC_TOKEN.lower() == "0xe1d836087f6573b665d25ce088793e916d7892f8"
    assert TREASURY.lower() == "0x09e2891432827d8835d2e9b83b25e2a5ba9612ac"
    assert SINC_DECIMALS == 8
    assert_live_not_stale()


def test_retired_addresses_are_stale() -> None:
    assert is_stale(RETIRED_SINC)
    assert is_stale(RETIRED_AXM)
    assert RETIRED_SINC.lower() in STALE_ADDRESSES
    assert not is_stale(SINC_TOKEN)


def test_stale_env_override_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("SINC_CONTRACT_ADDRESS", RETIRED_SINC)
    assert resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN) == SINC_TOKEN
    monkeypatch.setenv("AXIOM_CONTRACT_ADDRESS", RETIRED_AXM)
    assert resolve_address("AXIOM_CONTRACT_ADDRESS", AXIOM_TOKEN) == AXIOM_TOKEN


def test_malformed_env_override_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("SINC_CONTRACT_ADDRESS", "0xdead")
    assert resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN) == SINC_TOKEN
    monkeypatch.setenv("SINC_CONTRACT_ADDRESS", "")
    assert resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN) == SINC_TOKEN


def test_live_env_override_is_honored(monkeypatch) -> None:
    other = "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
    monkeypatch.setenv("SINC_CONTRACT_ADDRESS", other)
    assert resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN).lower() == other.lower()


def test_catalog_addresses_are_live() -> None:
    snap = catalog()
    assert not is_stale(str(snap["axiom"]["address"]))
    assert not is_stale(str(snap["sinc"]["address"]))
    assert snap["sinc"]["decimals"] == 8
    assert snap["axiom"]["decimals"] == 18


def test_abi_string_roundtrip() -> None:
    encoded = encode_abi_string("SINC")
    assert decode_abi_string(encoded) == "SINC"
    assert decode_uint(encode_uint(8)) == 8
    # bytes32 short symbol (no offset/length)
    short = "0x" + b"AXM".hex().ljust(64, "0")
    assert decode_abi_string(short) == "AXM"


def test_probe_offline_does_not_block() -> None:
    report = validate_at_startup(rpc_url="")
    assert report.catalog_ok is True
    assert report.ok is True
    assert report.probes == []


def test_probe_matching_symbols() -> None:
    def eth_call(to: str, data: str) -> str:
        sel = data[:10].lower()
        if to.lower() == AXIOM_TOKEN.lower():
            return encode_abi_string("AXM") if sel == "0x95d89b41" else encode_uint(18)
        if to.lower() == SINC_TOKEN.lower():
            return encode_abi_string("SINC") if sel == "0x95d89b41" else encode_uint(8)
        raise AssertionError(to)

    report = validate_at_startup(eth_call=eth_call)
    assert report.ok
    assert report.probes[0].ok and report.probes[1].ok


def test_probe_mismatch_and_rpc_error() -> None:
    def bad(_to: str, _data: str) -> str:
        return encode_abi_string("NOPE")

    mismatch = validate_at_startup(eth_call=bad)
    assert mismatch.catalog_ok is True
    assert mismatch.ok is False
    assert any("expected" in (p.error or "") for p in mismatch.probes)

    def boom(_to: str, _data: str) -> str:
        raise RuntimeError("timeout")

    failed = validate_at_startup(eth_call=boom)
    assert failed.ok is False
    assert all(p.error.startswith("rpc:") for p in failed.probes)


def test_runtime_modules_import_constants() -> None:
    from sincor2.a2a_integration import AXIOM_CONTRACT, SINC_CONTRACT
    from marketplace.settlement import AXIOM_TOKEN as SETTLE_AXM
    from marketplace.settlement import SINC_TOKEN as SETTLE_SINC
    from sincor2.payment_verifier import AXIOM_CONTRACT as PAY_AXM
    from sincor2.settings import _canonical_sinc

    assert not is_stale(AXIOM_CONTRACT)
    assert not is_stale(SINC_CONTRACT)
    assert not is_stale(SETTLE_AXM)
    assert not is_stale(SETTLE_SINC)
    assert not is_stale(PAY_AXM)
    assert not is_stale(_canonical_sinc())


def test_registry_health_and_catalog() -> None:
    flask = pytest.importorskip("flask")
    from sincor2.blueprints.registry import registry_bp
    from sincor2.onchain.constants import SINC_TOKEN

    app = flask.Flask(__name__)
    app.register_blueprint(registry_bp)
    client = app.test_client()
    health = client.get("/api/registry/health")
    assert health.status_code == 200
    body = health.get_json()
    assert body["ok"] is True
    assert body["sinc"].lower() == SINC_TOKEN.lower()
    cat = client.get("/api/registry/catalog")
    assert cat.status_code == 200
    assert "axiom" in cat.get_json()["catalog"]
    sim = client.post("/api/registry/simulate", json={"scenario": "all"})
    assert sim.status_code == 200
    payload = sim.get_json()
    assert payload["probe"]["matching"]["ok"] is True
