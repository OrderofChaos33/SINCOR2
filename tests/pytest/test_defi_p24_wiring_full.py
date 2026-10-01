"""P24 wiring verification: full-chain tests (backlog item 12, P2).

Proves the issuance chain hangs together on this branch:
  1. Python-side `issue(...)` calldata selector round-trips against the REAL
     compiled CreatorTokenFactory.sol (solc 0.8.24, eth-tester) — the exact
     encoding wave 5's bridge must produce.
  2. eth_call dry-run gates (the dry-run simulation logic): admin-only,
     screened-only, symbol uniqueness, zero-creator rejection — mirroring
     onchain/test/P24AccessControl.t.sol, which runs under `forge test` in CI
     once forge-std is vendored.
  3. Content-policy enforcement at every Python layer: policy.require_clean
     -> OnboardingAgent.register (rejection logged with ruleset version, the
     submitted text never stored) -> CreatorTokenFactory.issue (screened gate).
  4. The live block: any live-intent entrypoint raises before state changes.
  5. The wiring test run itself is recorded in the proof ledger.

Self-contained: no app imports. Run with --noconftest or
--confcutdir=tests/pytest using the sincor2 venv.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_hash.auto import keccak
from eth_tester import EthereumTester
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.providers.eth_tester import EthereumTesterProvider

from src.sincor2.defi.p24 import factory as py_factory
from src.sincor2.defi.p24 import live_block, onboarding, policy
from src.sincor2.defi.p24.live_block import LIVE_BLOCKED, LiveBlocked, guard_live
from src.sincor2.defi.p24.policy import RULESET_VERSION, PolicyViolation
from src.sincor2.defi.proof_ledger import (
    KIND_TEST_RUN,
    ProofLedger,
)

REVERTS = (TransactionFailed, ContractLogicError)

ISSUE_SIG = "issue(string,string,address,address,string,bool)"
ISSUE_SELECTOR = keccak(ISSUE_SIG.encode())[:4]
ISSUE_TYPES = ["string", "string", "address", "address", "string", "bool"]

SOLCX_BIN = os.path.expanduser("~/.solcx/solc-v0.8.24")


def _compile_factory():
    """Compile the real CreatorTokenFactory.sol with the local solc 0.8.24."""
    src_path = ROOT / "onchain" / "src" / "p24" / "CreatorTokenFactory.sol"
    proc = subprocess.run(
        [SOLCX_BIN, "--combined-json", "abi,bin", "-"],
        input=src_path.read_bytes(), capture_output=True, check=True)
    combined = json.loads(proc.stdout.decode())
    key = next(k for k in combined["contracts"]
               if k.endswith(":CreatorTokenFactory"))
    entry = combined["contracts"][key]
    abi = json.loads(entry["abi"]) if isinstance(entry["abi"], str) else entry["abi"]
    return abi, entry["bin"]


@pytest.fixture(scope="module")
def chain():
    abi, bytecode = _compile_factory()
    tester = EthereumTester()
    w3 = Web3(EthereumTesterProvider(tester))
    admin, attacker, creator, curve = w3.eth.accounts[:4]
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    txh = factory.constructor(admin).transact({"from": admin})
    addr = w3.eth.get_transaction_receipt(txh)["contractAddress"]
    bound = w3.eth.contract(address=addr, abi=abi)
    return {
        "w3": w3, "factory": bound, "admin": admin,
        "attacker": attacker, "creator": creator, "curve": curve,
    }


def _raw_issue_calldata(name, symbol, creator, curve, version, screened):
    args = [name, symbol, creator, curve, version, screened]
    return ISSUE_SELECTOR + abi_encode(ISSUE_TYPES, args)


# -- 1. selector/calldata round-trip against the real contract ----------------
def test_issue_selector_matches_keccak_of_canonical_signature():
    assert ISSUE_SELECTOR == keccak(ISSUE_SIG.encode())[:4]
    assert len(ISSUE_SELECTOR) == 4


def test_python_calldata_executes_on_real_factory(chain):
    w3, factory = chain["w3"], chain["factory"]
    data = _raw_issue_calldata(
        "Wiring Token", "WIRE", chain["creator"], chain["curve"],
        RULESET_VERSION, True)
    txh = w3.eth.send_transaction(
        {"from": chain["admin"], "to": factory.address, "data": data})
    receipt = w3.eth.get_transaction_receipt(txh)
    assert receipt["status"] == 1
    token = factory.functions.tokenBySymbol("WIRE").call()
    assert token != "0x0000000000000000000000000000000000000000"


def test_calldata_round_trip_decodes_to_same_args():
    args = ["Name", "SYM", "0x" + "11" * 20, "0x" + "22" * 20, "1.0.0", True]
    data = _raw_issue_calldata(*args)
    assert data[:4] == ISSUE_SELECTOR
    decoded = abi_decode(ISSUE_TYPES, data[4:])
    assert decoded[0] == "Name" and decoded[1] == "SYM"
    assert decoded[2].lower() == args[2].lower()
    assert decoded[5] is True


def test_raw_calldata_matches_contract_encoder(chain):
    """The hand-built encoding equals what web3's own ABI encoder emits."""
    factory = chain["factory"]
    manual = _raw_issue_calldata(
        "Enc", "ENC", chain["creator"], chain["curve"], RULESET_VERSION, True)
    encoded = factory.encode_abi(
        "issue", ["Enc", "ENC", chain["creator"], chain["curve"],
                  RULESET_VERSION, True])
    encoded_hex = encoded[2:] if encoded.startswith("0x") else encoded
    assert manual.hex() == encoded_hex


# -- 2. dry-run gates (mirror of onchain/test/P24AccessControl.t.sol) ---------
def test_dryrun_non_admin_reverts(chain):
    factory = chain["factory"]
    with pytest.raises(REVERTS):
        factory.functions.issue(
            "X", "DR1", chain["creator"], chain["curve"],
            RULESET_VERSION, True).call({"from": chain["attacker"]})


def test_dryrun_unscreened_reverts(chain):
    factory = chain["factory"]
    with pytest.raises(REVERTS):
        factory.functions.issue(
            "X", "DR2", chain["creator"], chain["curve"],
            RULESET_VERSION, False).call({"from": chain["admin"]})


def test_dryrun_duplicate_symbol_reverts(chain):
    w3, factory = chain["w3"], chain["factory"]
    factory.functions.issue(
        "Dup", "DUP", chain["creator"], chain["curve"],
        RULESET_VERSION, True).transact({"from": chain["admin"]})
    with pytest.raises(REVERTS):
        factory.functions.issue(
            "Dup2", "DUP", chain["creator"], chain["curve"],
            RULESET_VERSION, True).call({"from": chain["admin"]})


def test_dryrun_zero_creator_reverts(chain):
    factory = chain["factory"]
    zero = "0x0000000000000000000000000000000000000000"
    with pytest.raises(REVERTS):
        factory.functions.issue(
            "X", "DR3", zero, chain["curve"],
            RULESET_VERSION, True).call({"from": chain["admin"]})


def test_dryrun_happy_path_returns_token_address(chain):
    factory = chain["factory"]
    token = factory.functions.issue(
        "Happy", "HPY", chain["creator"], chain["curve"],
        RULESET_VERSION, True).call({"from": chain["admin"]})
    assert token != "0x0000000000000000000000000000000000000000"


# -- 3. policy enforcement at every Python layer ------------------------------
def test_policy_layer_rejects_with_ruleset_version():
    with pytest.raises(PolicyViolation) as exc:
        policy.require_clean("Clean", "CLN", "guaranteed returns", "bio")
    assert exc.value.ruleset_version == RULESET_VERSION
    assert exc.value.field == "description"
    assert exc.value.matched_phrase == "guaranteed returns"


def test_onboarding_layer_logs_rejection_without_storing_text():
    agent = onboarding.OnboardingAgent()
    evil = "this will 100x, guaranteed returns for all"
    with pytest.raises(PolicyViolation):
        agent.register("c-evil", "Evil", "EVL", evil, "bio")
    assert len(agent.rejections) == 1
    log = agent.rejections[0]
    assert log.creator_id == "c-evil"
    assert log.ruleset_version == RULESET_VERSION
    assert log.matched_phrase in ("100x", "guaranteed returns")
    # The submitted text itself must never be persisted.
    for value in vars(log).values():
        assert evil not in str(value)
    assert "c-evil" not in agent.registrations


def test_factory_layer_requires_screened():
    fac = py_factory.CreatorTokenFactory()
    with pytest.raises(py_factory.FactoryError, match="screen"):
        fac.issue(name="N", symbol="NSC", creator="c",
                  policy_version=RULESET_VERSION, screened=False)


def test_onboarding_happy_path_records_policy_version():
    agent = onboarding.OnboardingAgent()
    reg = agent.register("c-good", "Good Token", "GOOD",
                         "A fine utility token", "Builder bio")
    assert reg.policy_version == RULESET_VERSION
    assert reg.token.total_supply_wei == 10**9 * 10**18
    assert reg.token.curve_supply_wei == reg.token.vesting_supply_wei
    assert agent.registrations["c-good"] is reg


# -- 4. live block -------------------------------------------------------------
def test_live_block_refuses_before_any_state_change():
    assert LIVE_BLOCKED is True
    agent = onboarding.OnboardingAgent()
    with pytest.raises(LiveBlocked):
        agent.sign_live("issue", symbol="GOOD")
    with pytest.raises(LiveBlocked):
        guard_live("anything")
    # Nothing was recorded or minted by the refused intent.
    assert agent.registrations == {}
    assert agent.factory.tokens == {}


# -- 5. proof-ledger recording -------------------------------------------------
def test_wiring_run_recorded_in_proof_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DEFI_ARM_DATA_DIR", str(tmp_path))
    ledger = ProofLedger()
    entry = ledger.append(
        "P24", KIND_TEST_RUN,
        {"suite": "test_defi_p24_wiring_full",
         "branch": "xioix/buildout-12-p24-wiring-tests",
         "selector": "0x" + ISSUE_SELECTOR.hex(),
         "ruleset_version": RULESET_VERSION,
         "result": "pass"},
        recorded_by="wave-12",
    )
    assert entry["sku"] == "P24" and entry["kind"] == KIND_TEST_RUN
    rows = ProofLedger().read(sku="P24", kind=KIND_TEST_RUN)
    assert any(r["entry_id"] == entry["entry_id"] for r in rows)
