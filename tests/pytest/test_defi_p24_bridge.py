"""eth-tester integration tests for ``sincor2.defi.p24.bridge``.

Drives the real ``onchain/src/p24/CreatorTokenFactory.sol`` (solc 0.8.24,
viaIR) through the bridge's entry points:

  - ``build_issue_calldata`` — exact ``issue(...)`` calldata, client-side
    validated against the onchain gates.
  - ``dry_run_issue`` — eth_call simulation of the exact call.
  - ``issue_creator_token`` — dry-run, then sign with the caller's
    ephemeral eth_account Account (in-memory only; nothing written to
    disk) and broadcast; parses the TokenIssued event.

Covers: calldata selector + round-trip decode, dry-run happy path,
unscreened rejection, duplicate-symbol rejection, non-admin signer
rejection, non-admin dry-run revert, full broadcast with onchain state
assertions, dry_run=True signing nothing, and the hard key rule — the
bridge module never imports eth_account.
"""

import os
import re

import pytest
import solcx
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from sincor2.defi.p24 import bridge
from sincor2.defi.p24.bridge import (
    BridgeError,
    BridgeSimulationError,
    bind_factory,
    build_issue_calldata,
    dry_run_issue,
    factory_abi,
    issue_creator_token,
)

P24_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "onchain", "src", "p24")
)
BRIDGE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "sincor2",
                 "defi", "p24", "bridge.py")
)

SOLC_VERSION = "0.8.24"
TX = {"maxFeePerGas": 10_000_000_000, "maxPriorityFeePerGas": 0}
GAS = {"max_fee_per_gas": 10_000_000_000, "max_priority_fee_per_gas": 0}
ZERO = "0x0000000000000000000000000000000000000000"


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    with open(os.path.join(P24_DIR, "CreatorTokenFactory.sol")) as f:
        src = f.read()
    std = {
        "language": "Solidity",
        "sources": {"CreatorTokenFactory.sol": {"content": src}},
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "viaIR": True,
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object"]}},
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION,
                                 allow_paths=P24_DIR)
    contracts = out["contracts"]["CreatorTokenFactory.sol"]
    compiled = {}
    for name in ("CreatorTokenFactory", "CreatorToken"):
        c = contracts[name]
        compiled[name] = (c["abi"], c["evm"]["bytecode"]["object"])
    return compiled


COMPILED = _compile()


class Env:
    """Fresh chain + deployed factory per test. Admin is an ephemeral
    eth_account Account (in-memory only) — the caller-supplied signer."""

    def __init__(self):
        self.tester = EthereumTester(PyEVMBackend())
        self.w3 = Web3(EthereumTesterProvider(self.tester))
        w3 = self.w3
        accts = self.tester.get_accounts()
        self.deployer = accts[0]
        self.creator = accts[2]
        self.curve_inventory = accts[3]
        self.stranger = accts[4]

        self.admin_acct = w3.eth.account.create()
        self.tester.add_account(self.admin_acct.key.hex())
        w3.eth.send_transaction({
            "from": self.deployer, "to": self.admin_acct.address,
            "value": w3.to_wei(50, "ether"), **TX})
        self.admin = self.admin_acct.address

        abi, bytecode = COMPILED["CreatorTokenFactory"]
        txh = w3.eth.contract(abi=abi, bytecode=bytecode).constructor(
            self.admin).transact({"from": self.deployer, **TX})
        self.factory = w3.eth.contract(
            address=w3.eth.get_transaction_receipt(txh).contractAddress, abi=abi)

        token_abi, _ = COMPILED["CreatorToken"]
        self.token_abi = token_abi

    def issue_kwargs(self, **over):
        kw = {
            "name": "Test Creator",
            "symbol": "TST",
            "creator": self.creator,
            "curve_inventory": self.curve_inventory,
            "policy_version": "1.0.0",
            "screened": True,
        }
        kw.update(over)
        return kw


@pytest.fixture()
def env():
    return Env()


# -- calldata -----------------------------------------------------------------

def test_calldata_has_issue_selector_and_decodes(env):
    data = build_issue_calldata(env.w3, env.factory, **env.issue_kwargs())
    assert data.startswith("0x")
    selector = Web3.keccak(
        text="issue(string,string,address,address,string,bool)")[:4].hex()
    assert data[2:10] == selector
    fn_obj, params = env.factory.decode_function_input(data)
    assert fn_obj.fn_name == "issue"
    assert params["name_"] == "Test Creator"
    assert params["symbol_"] == "TST"
    assert params["screened"] is True


def test_embedded_abi_matches_compiled(env):
    assert factory_abi() == COMPILED["CreatorTokenFactory"][0]
    bound = bind_factory(env.w3, env.factory.address)
    assert bound.functions.admin().call() == env.admin


# -- dry-run -------------------------------------------------------------------

def test_dry_run_succeeds_for_valid_params(env):
    sim = dry_run_issue(env.w3, env.factory, **env.issue_kwargs())
    assert sim["symbol"] == "TST"
    assert sim["admin"] == Web3.to_checksum_address(env.admin)
    assert sim["data"].startswith("0x")


def test_dry_run_rejects_unscreened_client_side(env):
    with pytest.raises(BridgeError, match="screened"):
        dry_run_issue(env.w3, env.factory, **env.issue_kwargs(screened=False))
    with pytest.raises(BridgeError, match="screened"):
        build_issue_calldata(env.w3, env.factory,
                             **env.issue_kwargs(screened=False))


def test_dry_run_rejects_empty_fields(env):
    with pytest.raises(BridgeError):
        dry_run_issue(env.w3, env.factory, **env.issue_kwargs(name=""))
    with pytest.raises(BridgeError):
        dry_run_issue(env.w3, env.factory, **env.issue_kwargs(symbol=""))
    with pytest.raises(BridgeError):
        dry_run_issue(env.w3, env.factory,
                      **env.issue_kwargs(creator=ZERO))


def test_dry_run_reverts_for_non_admin_sender(env):
    # eth_call from a non-admin reverts with NotAdmin -> BridgeSimulationError
    with pytest.raises(BridgeSimulationError, match="dry-run reverted"):
        dry_run_issue(env.w3, env.factory, admin=env.stranger,
                      **env.issue_kwargs())


# -- full issuance ---------------------------------------------------------------

def test_issue_broadcast_confirms_token_onchain(env):
    res = issue_creator_token(
        env.w3, env.factory, signer=env.admin_acct, dry_run=False,
        **env.issue_kwargs(), **GAS)
    assert res["dry_run"] is False
    assert res["tx_hash"].startswith("0x")
    assert res["token"] is not None

    w3 = env.w3
    assert w3.to_checksum_address(
        env.factory.functions.tokenBySymbol("TST").call()) == \
        w3.to_checksum_address(res["token"])

    token = w3.eth.contract(address=res["token"], abi=env.token_abi)
    assert token.functions.name().call() == "Test Creator"
    assert token.functions.symbol().call() == "TST"
    assert token.functions.creator().call() == w3.to_checksum_address(env.creator)
    assert token.functions.policyVersion().call() == "1.0.0"
    total = token.functions.TOTAL_SUPPLY().call()
    assert total == 1_000_000_000 * 10**18
    assert token.functions.balanceOf(env.curve_inventory).call() == total // 2
    assert token.functions.balanceOf(env.creator).call() == total // 2


def test_issue_rejects_non_admin_signer(env):
    stranger_acct = env.w3.eth.account.create()
    with pytest.raises(BridgeError, match="not the factory admin"):
        issue_creator_token(
            env.w3, env.factory, signer=stranger_acct, dry_run=False,
            **env.issue_kwargs(), **GAS)


def test_issue_rejects_duplicate_symbol(env):
    issue_creator_token(
        env.w3, env.factory, signer=env.admin_acct, dry_run=False,
        **env.issue_kwargs(), **GAS)
    with pytest.raises(BridgeError, match="already issued"):
        issue_creator_token(
            env.w3, env.factory, signer=env.admin_acct, dry_run=False,
            **env.issue_kwargs(), **GAS)
    with pytest.raises(BridgeError, match="already issued"):
        dry_run_issue(env.w3, env.factory, **env.issue_kwargs())


def test_dry_run_mode_signs_and_broadcasts_nothing(env):
    res = issue_creator_token(
        env.w3, env.factory, signer=env.admin_acct, dry_run=True,
        **env.issue_kwargs(), **GAS)
    assert res["dry_run"] is True
    assert "unsigned_tx" in res
    assert "tx_hash" not in res
    assert int(env.factory.functions.tokenBySymbol("TST").call(), 16) == 0


def test_signer_without_signing_api_rejected(env):
    with pytest.raises(BridgeError, match="sign_transaction"):
        issue_creator_token(
            env.w3, env.factory, signer=object(), dry_run=False,
            **env.issue_kwargs(), **GAS)


# -- hard key rule ----------------------------------------------------------------

def test_bridge_module_never_imports_eth_account():
    with open(BRIDGE_PATH) as f:
        src = f.read()
    hits = re.findall(r"^\s*(?:import|from)\s+eth_account\b", src,
                      flags=re.MULTILINE)
    assert hits == [], f"bridge imports eth_account: {hits}"
    assert "import eth_account" not in src
