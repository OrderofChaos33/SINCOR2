"""Regression tests for the Polyclaw live-bootstrap path.

Production failure (2026-11-10 logs):
    [POLYCLAW] live bootstrap FAILED:
    'PolymarketAdapter' object has no attribute '_get_client'

Root cause: polyclaw_scheduler._bootstrap_live_client() (and the status
view) were written against adapter methods the unified
execution_adapter.PolymarketAdapter never implemented — ``_get_client()``,
``trading_address()``, ``key_error()`` and the ``_allowances_ready`` flag.
The adapter's lazy init is ``_ensure_client()``. The AttributeError fired
before any py-clob-client call, so this was a wiring bug, not the
Polymarket V2 SDK change.

These tests exercise the bootstrap path end to end with the CLOB client
fully mocked: no network, no real orders, no real keys. The "key" below
is the public Anvil/Hardhat account #0 test vector; POLYCLAW_LIVE is set
only inside these tests so the bootstrap branch executes, and the mocked
client guarantees nothing can sign or post.
"""

import pytest

# Public test vector (Anvil/Hardhat account #0) — NOT a real credential.
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


class FakeClobClient:
    """Stand-in for py_clob_client_v2.client.ClobClient (no network)."""

    allowance = "1000000"  # raw units (1 pUSD) unless a test overrides
    instances = []

    def __init__(self, host, chain_id=None, key=None, creds=None,
                 signature_type=None, funder=None, **kwargs):
        self.host = host
        self.key = key
        self.creds = creds
        FakeClobClient.instances.append(self)

    def create_or_derive_api_key(self):
        from py_clob_client_v2 import ApiCreds
        return ApiCreds(api_key="k", api_secret="s", api_passphrase="p")

    def set_api_creds(self, creds):
        self.creds = creds

    def get_address(self):
        return TEST_ADDRESS

    def update_balance_allowance(self, params):
        return {}

    def get_balance_allowance(self, params):
        return {"balance": "0", "allowance": FakeClobClient.allowance}


@pytest.fixture
def live_env(monkeypatch):
    """Env that takes the bootstrap down its live branch, SDK mocked."""
    # Import the adapter first: it sanitizes the bracketed-IPv6 NO_PROXY
    # entries that otherwise crash httpx inside py_clob_client_v2 at import.
    import sincor2.execution_adapter  # noqa: F401
    from sincor2 import polyclaw_scheduler as ps

    monkeypatch.setenv("POLYCLAW_LIVE", "true")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", TEST_KEY)
    monkeypatch.setenv("POLYMARKET_API_KEY", "test-api-key")
    monkeypatch.setenv("POLYMARKET_API_SECRET", "test-api-secret")
    monkeypatch.setenv("POLYMARKET_API_PASSPHRASE", "test-passphrase")
    monkeypatch.setattr(
        "py_clob_client_v2.client.ClobClient", FakeClobClient)
    FakeClobClient.instances = []
    FakeClobClient.allowance = "1000000"
    monkeypatch.setattr(ps, "_live_adapter", None)
    monkeypatch.setattr(ps, "_bootstrap_error", None)
    return ps


class TestLiveBootstrap:
    def test_bootstrap_completes_without_error(self, live_env):
        """The exact path that AttributeError'd in production."""
        ps = live_env
        ps._bootstrap_live_client()
        assert ps._bootstrap_error is None
        assert ps._live_adapter is not None

    def test_bootstrap_warms_allowances(self, live_env):
        ps = live_env
        ps._bootstrap_live_client()
        adapter = ps._live_adapter
        assert adapter._allowances_ready is True
        assert adapter.trading_address() == TEST_ADDRESS
        assert len(FakeClobClient.instances) >= 1

    def test_bootstrap_zero_allowance_not_ready_but_no_error(self, live_env):
        """Client init OK + zero allowance => adapter stored, flag False,
        so run_polyclaw_cycle re-warms next cycle instead of crashing."""
        FakeClobClient.allowance = "0"
        ps = live_env
        ps._bootstrap_live_client()
        assert ps._bootstrap_error is None
        assert ps._live_adapter is not None
        assert ps._live_adapter._allowances_ready is False

    def test_bootstrap_without_key_reports_error_not_raise(self, monkeypatch):
        from sincor2 import polyclaw_scheduler as ps
        monkeypatch.setenv("POLYCLAW_LIVE", "true")
        for var in ("POLYMARKET_PRIVATE_KEY", "POLYCLAW_PRIVATE_KEY",
                    "POLYMARKET_PK"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(ps, "_live_adapter", None)
        monkeypatch.setattr(ps, "_bootstrap_error", None)
        ps._bootstrap_live_client()
        assert ps._live_adapter is None
        assert "no private key" in (ps._bootstrap_error or "")


class TestAdapterSurface:
    def test_dry_adapter_defaults(self, monkeypatch):
        """Fresh dry-run adapter exposes the accessors the scheduler uses."""
        for var in ("POLYMARKET_PRIVATE_KEY", "POLYCLAW_PRIVATE_KEY",
                    "POLYMARKET_PK"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("POLYCLAW_LIVE", "false")
        from sincor2.execution_adapter import PolymarketAdapter
        adapter = PolymarketAdapter()
        assert adapter._allowances_ready is False
        assert adapter.trading_address() is None
        assert adapter.key_error() is None
        assert adapter.ensure_client() is None  # dry-run: no client

    def test_trading_address_derived_locally_from_key(self, monkeypatch):
        """With a key but no client yet, the address derives offline."""
        monkeypatch.setenv("POLYCLAW_LIVE", "false")
        monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", TEST_KEY)
        from sincor2.execution_adapter import PolymarketAdapter
        adapter = PolymarketAdapter()
        assert adapter.trading_address() == TEST_ADDRESS


class TestStatusViewAfterBootstrap:
    def test_status_reports_bootstrapped_adapter(self, live_env, monkeypatch):
        from flask import Flask
        ps = live_env
        ps._bootstrap_live_client()
        assert ps._live_adapter is not None
        monkeypatch.setattr(ps, "_is_admin_request", lambda req: False)
        app = Flask(__name__)
        with app.test_request_context("/api/polyclaw/status"):
            body, status = ps._status_view()
        assert status == 200
        payload = body.get_json()
        assert payload["trading_address"] == TEST_ADDRESS
        assert payload["allowances_ready"] is True
        assert payload["bootstrap_error"] is None
