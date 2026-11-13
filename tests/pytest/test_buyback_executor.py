"""Buyback executor skeleton tests — all offline, fake adapter only.

Proves: disarmed-by-default refuses; each of the 8 gate combinations behaves
correctly (only all-true proceeds, and then only through the injected fake
adapter); approval expiry is honored; refusals never touch the adapter; and no
real broadcast/signing/network path exists in the module.
"""

import json
import os

import pytest

from sincor2.onchain import buyback_executor as be


ENV_VARS = (be.ENV_ARMED, be.ENV_APPROVAL_PATH, be.ENV_POOL_TOKEN_A,
            be.ENV_POOL_TOKEN_B, be.ENV_POOL_FEE, be.ENV_POOL_TICK_SPACING,
            be.ENV_MAX_BUY_WEI, be.ENV_SLIPPAGE_BPS)

PIN = be.PoolPin(token_a="0xAAA", token_b="0xBBB", fee=3000, tick_spacing=60)

LIVE_APPROVAL = {
    "approver": "founder",
    "scope": "axm-buyback",
    "issued_at": "2026-10-12T00:00:00+00:00",
    "expires_at": "2999-01-01T00:00:00+00:00",
    "note": "test approval",
}

EXPIRED_APPROVAL = dict(LIVE_APPROVAL, expires_at="2020-01-01T00:00:00+00:00")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def _cfg(monkeypatch, tmp_path, *, pool=False, armed=False,
         approval: "dict | None | str" = "live"):
    """Build a config exercising one gate combination.

    approval: "live" | "expired" | "missing" | "wrong-scope" | "file" (JSON file).
    """
    if pool:
        monkeypatch.setenv(be.ENV_POOL_TOKEN_A, PIN.token_a)
        monkeypatch.setenv(be.ENV_POOL_TOKEN_B, PIN.token_b)
        monkeypatch.setenv(be.ENV_POOL_FEE, str(PIN.fee))
        monkeypatch.setenv(be.ENV_POOL_TICK_SPACING, str(PIN.tick_spacing))
    else:
        for var in (be.ENV_POOL_TOKEN_A, be.ENV_POOL_TOKEN_B,
                    be.ENV_POOL_FEE, be.ENV_POOL_TICK_SPACING):
            monkeypatch.delenv(var, raising=False)
    if armed:
        monkeypatch.setenv(be.ENV_ARMED, "true")
    else:
        monkeypatch.delenv(be.ENV_ARMED, raising=False)
    monkeypatch.setenv(be.ENV_MAX_BUY_WEI, str(10 ** 18))
    cfg = be.BuybackConfig.from_env()

    record = None
    if approval == "live":
        record = dict(LIVE_APPROVAL)
    elif approval == "expired":
        record = dict(EXPIRED_APPROVAL)
    elif approval == "wrong-scope":
        record = dict(LIVE_APPROVAL, scope="something-else")
    elif approval == "file":
        path = tmp_path / "approval.json"
        path.write_text(json.dumps(LIVE_APPROVAL), encoding="utf-8")
        monkeypatch.setenv(be.ENV_APPROVAL_PATH, str(path))
    if record is not None:
        cfg = be.BuybackConfig(pool=cfg.pool, max_buy_wei=cfg.max_buy_wei,
                               slippage_bps=cfg.slippage_bps, approval=record)
    return cfg


# --- disarmed by default ------------------------------------------------------

def test_disarmed_by_default_refuses(monkeypatch, tmp_path):
    """Fresh env: no pool, no armed flag, no approval -> refusal."""
    cfg = be.BuybackConfig.from_env()
    assert cfg.pool is None
    result = be.check_gates(cfg)
    assert result["pass"] is False
    assert result["gates"] == {"pool_seeded": False, "armed_flag": False,
                               "founder_approval": False}
    assert len(result["reasons"]) == 3
    ex = be.BuybackExecutor(cfg)
    fake = be.FakeBuybackAdapter()
    with pytest.raises(be.BuybackRefused) as ei:
        ex.execute_buyback(10 ** 18, fake)
    assert "pool" in str(ei.value).lower()
    assert fake.calls == []  # adapter never touched on refusal


def test_default_config_has_no_armed_flag_in_env():
    assert os.environ.get(be.ENV_ARMED, "").strip().lower() != "true"


# --- all 8 gate combinations ---------------------------------------------------

@pytest.mark.parametrize("pool,armed,approval", [
    (False, False, "missing"),
    (True, False, "missing"),
    (False, True, "missing"),
    (False, False, "live"),
    (True, True, "missing"),
    (True, False, "live"),
    (False, True, "live"),
    (True, True, "live"),
])
def test_gate_combinations(monkeypatch, tmp_path, pool, armed, approval):
    """All 2^3 combinations: only all-true proceeds (against the fake)."""
    cfg = _cfg(monkeypatch, tmp_path, pool=pool, armed=armed, approval=approval)
    result = be.check_gates(cfg)
    all_true = pool and armed and approval == "live"
    assert result["pass"] is all_true
    assert result["gates"]["pool_seeded"] is pool
    assert result["gates"]["armed_flag"] is armed
    assert result["gates"]["founder_approval"] is (approval == "live")

    ex = be.BuybackExecutor(cfg)
    fake = be.FakeBuybackAdapter()
    if all_true:
        out = ex.execute_buyback(10 ** 17, fake)
        assert out["status"] == "executed"
        assert out["adapter"] == "FakeBuybackAdapter"
        assert len(fake.calls) == 1
        assert fake.calls[0]["amount_in_wei"] == 10 ** 17
        assert fake.calls[0]["pool"] == PIN.as_dict()
    else:
        with pytest.raises(be.BuybackRefused):
            ex.execute_buyback(10 ** 17, fake)
        assert fake.calls == []


def test_each_single_missing_gate_refuses_with_named_reason(monkeypatch, tmp_path):
    """The three single-missing-gate cases each name the failed gate."""
    cases = [
        dict(pool=False, armed=True, approval="live"),    # pool missing
        dict(pool=True, armed=False, approval="live"),   # flag missing
        dict(pool=True, armed=True, approval="missing"),  # approval missing
    ]
    expected = ["pool_seeded", "armed_flag", "founder_approval"]
    for kwargs, gate in zip(cases, expected):
        cfg = _cfg(monkeypatch, tmp_path, **kwargs)
        with pytest.raises(be.BuybackRefused) as ei:
            be.BuybackExecutor(cfg).execute_buyback(10 ** 17,
                                                    be.FakeBuybackAdapter())
        assert ei.value.gates[gate] is False
        assert any(gate in r for r in ei.value.reasons)


# --- approval semantics --------------------------------------------------------

def test_expired_approval_refuses(monkeypatch, tmp_path):
    cfg = _cfg(monkeypatch, tmp_path, pool=True, armed=True, approval="expired")
    result = be.check_gates(cfg)
    assert result["pass"] is False
    assert result["gates"]["founder_approval"] is False
    assert any("expired" in r for r in result["reasons"])
    fake = be.FakeBuybackAdapter()
    with pytest.raises(be.BuybackRefused):
        be.BuybackExecutor(cfg).execute_buyback(10 ** 17, fake)
    assert fake.calls == []


def test_wrong_scope_approval_refuses(monkeypatch, tmp_path):
    cfg = _cfg(monkeypatch, tmp_path, pool=True, armed=True,
               approval="wrong-scope")
    assert be.check_gates(cfg)["pass"] is False
    with pytest.raises(be.BuybackRefused):
        be.BuybackExecutor(cfg).execute_buyback(10 ** 17,
                                                be.FakeBuybackAdapter())


def test_approval_from_file_path(monkeypatch, tmp_path):
    cfg = _cfg(monkeypatch, tmp_path, pool=True, armed=True, approval="file")
    result = be.check_gates(cfg)
    assert result["pass"] is True
    fake = be.FakeBuybackAdapter()
    out = be.BuybackExecutor(cfg).execute_buyback(10 ** 17, fake)
    assert out["status"] == "executed"
    assert fake.calls[0]["pool"]["fee"] == 3000


def test_malformed_approval_file_refuses(monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(be.ENV_APPROVAL_PATH, str(bad))
    monkeypatch.setenv(be.ENV_ARMED, "true")
    monkeypatch.setenv(be.ENV_POOL_TOKEN_A, PIN.token_a)
    monkeypatch.setenv(be.ENV_POOL_TOKEN_B, PIN.token_b)
    monkeypatch.setenv(be.ENV_POOL_FEE, str(PIN.fee))
    monkeypatch.setenv(be.ENV_POOL_TICK_SPACING, str(PIN.tick_spacing))
    monkeypatch.setenv(be.ENV_MAX_BUY_WEI, str(10 ** 18))
    cfg = be.BuybackConfig.from_env()
    assert be.check_gates(cfg)["gates"]["founder_approval"] is False


# --- size guard -----------------------------------------------------------------

def test_buy_above_max_refuses_without_touching_adapter(monkeypatch, tmp_path):
    cfg = _cfg(monkeypatch, tmp_path, pool=True, armed=True, approval="live")
    assert be.check_gates(cfg)["pass"] is True
    fake = be.FakeBuybackAdapter()
    with pytest.raises(be.BuybackRefused):
        be.BuybackExecutor(cfg).execute_buyback(10 ** 19, fake)
    assert fake.calls == []


def test_zero_max_buy_blocks_everything(monkeypatch, tmp_path):
    cfg = _cfg(monkeypatch, tmp_path, pool=True, armed=True, approval="live")
    cfg = be.BuybackConfig(pool=cfg.pool, max_buy_wei=0,
                           slippage_bps=cfg.slippage_bps,
                           approval=cfg.approval)
    with pytest.raises(be.BuybackRefused):
        be.BuybackExecutor(cfg).execute_buyback(1, be.FakeBuybackAdapter())


# --- adapter contract ------------------------------------------------------------

def test_adapter_must_implement_interface(monkeypatch, tmp_path):
    cfg = _cfg(monkeypatch, tmp_path, pool=True, armed=True, approval="live")
    with pytest.raises(TypeError):
        be.BuybackExecutor(cfg).execute_buyback(10 ** 17, object())


def test_plan_contains_no_signing_or_broadcast(monkeypatch, tmp_path):
    cfg = _cfg(monkeypatch, tmp_path, pool=True, armed=True, approval="live")
    plan = be.BuybackExecutor(cfg).plan(10 ** 17)
    d = plan.as_dict()
    assert "none" in d["signing"]
    assert d["broadcast"] == "adapter-injected only"
    assert d["approval_approver"] == "founder"


# --- no real broadcast path exists --------------------------------------------------

def test_module_has_no_network_signing_or_key_code():
    """AST check: no banned imports and no banned name/attribute references
    in real code (the docstring may name libraries in the negative)."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(be))
    # strip the module docstring from the scan
    body = [n for n in tree.body
            if not (isinstance(n, ast.Expr)
                    and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str))]
    imported = set()
    names = set()
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Import):
            imported.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    banned_imports = {"web3", "eth_account", "eth_keys", "requests", "urllib",
                      "socket", "subprocess", "keyring", "brownie"}
    banned_names = {"sign", "signTransaction", "sendTransaction",
                    "sendRawTransaction", "privateKey", "private_key",
                    "sign_message", "mainnet"}
    assert not (imported & banned_imports), imported & banned_imports
    assert not (names & banned_names), names & banned_names
    # double-check: no import of a banned module hiding under an alias
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] not in banned_imports, a.name


def test_no_real_adapter_shipped():
    adapters = [name for name in dir(be)
                if name.endswith("Adapter") and name != "BuybackAdapter"]
    assert adapters == ["FakeBuybackAdapter"], adapters
