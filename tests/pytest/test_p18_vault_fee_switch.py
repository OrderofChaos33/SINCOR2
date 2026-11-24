"""P18 — Agentic Liquidity Vault: fee-rule switch (default OFF).

The rule under test (Sinc4626Vault):

  * `feesEnabled` defaults to FALSE. While off, harvest() charges no
    performance fee and no management fee — the vault's current no-fee
    reality for outside depositors.
  * While on, harvest() charges the existing performance fee on
    realized yield PLUS a management fee of `managementFeeBps`
    (default 100 bps = 1%/yr, the catalog P18 price) of AUM, accrued
    pro-rata over the time since the fee clock last settled.
  * The fee clock settles on every harvest and on every enable/disable
    flip, so a disabled period is NEVER back-charged when fees are
    switched on later.
  * Only DEFAULT_ADMIN_ROLE may flip the switch or set the rate.

Self-contained: compiles onchain/src/sincor/Sinc4626Vault.sol with
solc 0.8.30 via solcx compile_standard, resolving OpenZeppelin imports
from a local openzeppelin-contracts checkout (candidates:
$SINCOR_OZ_CONTRACTS, <repo>/onchain/lib/openzeppelin-contracts,
~/workspace/.deps/openzeppelin-contracts). Run with:

    ~/.venvs/sincor2/bin/python -m pytest --confcutdir=tests/pytest \
        tests/pytest/test_p18_vault_fee_switch.py -q
"""

import os
import re

import pytest
import solcx
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SOLC_VERSION = "0.8.30"
YEAR = 365 * 86_400

MOCKS_SRC = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

contract MockAsset {
    string public name = "Mock Asset";
    string public symbol = "MA";
    uint8 public decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        totalSupply += amount;
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _move(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= amount, "allowance");
        if (allowed != type(uint256).max) allowance[from][msg.sender] = allowed - amount;
        _move(from, to, amount);
        return true;
    }

    function _move(address from, address to, uint256 amount) internal {
        require(balanceOf[from] >= amount, "balance");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
    }
}

contract MockRouter {
    address public asset;
    uint256 public yieldToPay;

    constructor(address asset_) { asset = asset_; }

    function setYield(uint256 amount) external { yieldToPay = amount; }
    function allocate(uint256) external {}
    function deallocate(uint256 amount) external returns (uint256) { return amount; }
    function totalManagedAssets() external pure returns (uint256) { return 0; }

    function harvestStrategies() external returns (uint256 grossYield, uint256 loss) {
        grossYield = yieldToPay;
        if (grossYield > 0) {
            MockAsset(asset).transfer(msg.sender, grossYield);
            yieldToPay = 0;
        }
        return (grossYield, 0);
    }
}

contract MockFlywheel {
    uint256 public totalReceived;
    address public treasuryAddr;

    constructor(address treasury_) { treasuryAddr = treasury_; }

    function processFees(address baseAsset, uint256 feeAmount, address)
        external returns (uint256 buybackAmount, uint256 treasuryAmount)
    {
        MockAsset(baseAsset).transferFrom(msg.sender, address(this), feeAmount);
        totalReceived += feeAmount;
        return (0, feeAmount);
    }

    function treasury() external view returns (address) { return treasuryAddr; }
}
"""

_IMPORT_RE = re.compile(r'import\s+(?:[^"\']*?from\s+)?["\']([^"\']+)["\']')
_NAMED_IMPORT_RE = re.compile(r'import\s*\{[^}]*\}\s*from\s*["\']([^"\']+)["\']')


def _oz_contracts_dir():
    candidates = [
        os.environ.get("SINCOR_OZ_CONTRACTS"),
        os.path.join(REPO, "onchain", "lib", "openzeppelin-contracts", "contracts"),
        os.path.expanduser("~/workspace/.deps/openzeppelin-contracts/contracts"),
        os.path.expanduser("~/workspace/sincor2/onchain/lib/openzeppelin-contracts/contracts"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(os.path.join(cand, "token", "ERC20", "IERC20.sol")):
            return cand
    raise RuntimeError("openzeppelin-contracts checkout not found; set SINCOR_OZ_CONTRACTS")


def _collect_sources():
    oz_dir = _oz_contracts_dir()
    sources = {}

    def add_file(unit_name, path):
        if unit_name in sources:
            return
        with open(path) as f:
            content = f.read()
        sources[unit_name] = {"content": content}
        base = os.path.dirname(unit_name)
        for imp in set(_IMPORT_RE.findall(content)) | set(_NAMED_IMPORT_RE.findall(content)):
            if imp.startswith("@openzeppelin/contracts/"):
                rel = imp[len("@openzeppelin/contracts/"):]
                add_file(imp, os.path.join(oz_dir, rel))
            elif imp.startswith("."):
                resolved = os.path.normpath(os.path.join(base, imp))
                if resolved.startswith("@openzeppelin/contracts/"):
                    rel = resolved[len("@openzeppelin/contracts/"):]
                    add_file(resolved, os.path.join(oz_dir, rel))
                else:
                    add_file(resolved, os.path.join(REPO, "onchain", "src", resolved.split("src/", 1)[-1]) if "src/" in resolved else os.path.join(REPO, resolved))

    vault_rel = "onchain/src/sincor/Sinc4626Vault.sol"
    with open(os.path.join(REPO, vault_rel)) as f:
        vault_src = f.read()
    sources[vault_rel] = {"content": vault_src}
    for imp in set(_IMPORT_RE.findall(vault_src)) | set(_NAMED_IMPORT_RE.findall(vault_src)):
        if imp.startswith("@openzeppelin/contracts/"):
            rel = imp[len("@openzeppelin/contracts/"):]
            add_file(imp, os.path.join(oz_dir, rel))
        elif imp.startswith("."):
            resolved = os.path.normpath(os.path.join("onchain/src/sincor", imp))
            add_file(resolved, os.path.join(REPO, resolved))
    sources["Mocks.sol"] = {"content": MOCKS_SRC}
    return sources


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    std = {
        "language": "Solidity",
        "sources": _collect_sources(),
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "outputSelection": {
                "onchain/src/sincor/Sinc4626Vault.sol": {
                    "Sinc4626Vault": ["abi", "evm.bytecode.object"]
                },
                "Mocks.sol": {
                    "MockAsset": ["abi", "evm.bytecode.object"],
                    "MockRouter": ["abi", "evm.bytecode.object"],
                    "MockFlywheel": ["abi", "evm.bytecode.object"],
                },
            },
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION)
    c = out["contracts"]
    v = c["onchain/src/sincor/Sinc4626Vault.sol"]["Sinc4626Vault"]
    m = c["Mocks.sol"]
    return {
        "vault": (v["abi"], v["evm"]["bytecode"]["object"]),
        "asset": (m["MockAsset"]["abi"], m["MockAsset"]["evm"]["bytecode"]["object"]),
        "router": (m["MockRouter"]["abi"], m["MockRouter"]["evm"]["bytecode"]["object"]),
        "flywheel": (m["MockFlywheel"]["abi"], m["MockFlywheel"]["evm"]["bytecode"]["object"]),
    }


COMPILED = _compile()


def _deploy(w3, pair, *args, sender):
    abi, bin_ = pair
    contract = w3.eth.contract(abi=abi, bytecode=bin_)
    tx = contract.constructor(*args).transact({"from": sender})
    addr = w3.eth.wait_for_transaction_receipt(tx)["contractAddress"]
    return w3.eth.contract(address=addr, abi=abi)


@pytest.fixture()
def env():
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    admin, user, stranger = tester.get_accounts()[0], tester.get_accounts()[1], tester.get_accounts()[2]

    asset = _deploy(w3, COMPILED["asset"], sender=admin)
    router = _deploy(w3, COMPILED["router"], asset.address, sender=admin)
    flywheel = _deploy(w3, COMPILED["flywheel"], admin, sender=admin)
    # idleBufferBps = 10000 -> everything stays idle; performanceFeeBps = 1500 (15%)
    vault = _deploy(
        w3, COMPILED["vault"],
        asset.address, "Sinc Vault", "SVLT", admin, router.address,
        flywheel.address, 10_000, 1500, admin,
        sender=admin,
    )
    asset.functions.mint(user, 1_000_000).transact({"from": admin})
    asset.functions.mint(router.address, 1_000_000).transact({"from": admin})
    asset.functions.approve(vault.address, 2**256 - 1).transact({"from": user})
    return {
        "tester": tester, "w3": w3, "asset": asset, "router": router,
        "flywheel": flywheel, "vault": vault, "admin": admin,
        "user": user, "stranger": stranger, "t0": w3.eth.get_block("latest")["timestamp"],
    }


def _deposit(env, amount=10_000):
    env["vault"].functions.deposit(amount, env["user"]).transact({"from": env["user"]})


def _harvest(env):
    return env["vault"].functions.harvest().call({"from": env["admin"]}), (
        env["vault"].functions.harvest().transact({"from": env["admin"]})
    )


def test_fees_default_off(env):
    assert env["vault"].functions.feesEnabled().call() is False
    assert env["vault"].functions.managementFeeBps().call() == 100


def test_harvest_charges_nothing_while_disabled(env):
    _deposit(env)
    env["router"].functions.setYield(1000).transact({"from": env["admin"]})
    (yield_, fee), _ = _harvest(env)
    assert yield_ == 1000
    assert fee == 0
    assert env["flywheel"].functions.totalReceived().call() == 0
    assert env["vault"].functions.totalAssets().call() == 11_000


def test_enabled_performance_fee_on_yield(env):
    _deposit(env)
    env["vault"].functions.setFeesEnabled(True).transact({"from": env["admin"]})
    env["router"].functions.setYield(1000).transact({"from": env["admin"]})
    (yield_, fee), _ = _harvest(env)
    assert yield_ == 1000
    assert fee == 150  # 15% of 1000, no time elapsed for the mgmt fee
    assert env["flywheel"].functions.totalReceived().call() == 150


def test_management_fee_accrues_one_percent_per_year(env):
    _deposit(env)
    env["vault"].functions.setFeesEnabled(True).transact({"from": env["admin"]})
    env["tester"].time_travel(env["t0"] + YEAR)
    (yield_, fee), _ = _harvest(env)
    assert yield_ == 0
    # 1% of 10,000 AUM over one year, minus the few blocks between the
    # fixture's t0 read and the enable flip (pro-rata to the second).
    assert 99 <= fee <= 100
    assert env["flywheel"].functions.totalReceived().call() == fee


def test_disabled_period_is_never_backcharged(env):
    _deposit(env)
    env["tester"].time_travel(env["t0"] + YEAR)  # a year with fees OFF
    env["vault"].functions.setFeesEnabled(True).transact({"from": env["admin"]})
    (yield_, fee), _ = _harvest(env)
    assert fee == 0  # clock settled at the flip: no back-charge
    env["tester"].time_travel(env["t0"] + 2 * YEAR)
    (yield_, fee), _ = _harvest(env)
    assert 99 <= fee <= 100  # only the enabled year accrues (pro-rata)


def test_only_admin_can_flip_or_set(env):
    with pytest.raises(TransactionFailed):
        env["vault"].functions.setFeesEnabled(True).transact({"from": env["stranger"]})
    with pytest.raises(TransactionFailed):
        env["vault"].functions.setManagementFeeBps(200).transact({"from": env["stranger"]})
    with pytest.raises(Exception):
        env["vault"].functions.setManagementFeeBps(10_001).transact({"from": env["admin"]})


def test_management_fee_rate_settable(env):
    _deposit(env)
    env["vault"].functions.setManagementFeeBps(200).transact({"from": env["admin"]})
    env["vault"].functions.setFeesEnabled(True).transact({"from": env["admin"]})
    env["tester"].time_travel(env["t0"] + YEAR)
    (_, fee), _ = _harvest(env)
    assert 198 <= fee <= 200  # 2% of 10,000 over one year (pro-rata)
