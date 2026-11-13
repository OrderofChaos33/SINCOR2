"""Cross-implementation proof for the Hub settlement engine.

Compiles the vendored TEST COPY of SincorSettlementHub (tests/fixtures/,
protocolFeeBps corrected to the 500 bps fee canon — see its header) plus a
mock USDC, deploys both to a LOCAL eth-tester chain, and drives the whole
loop from Python: sign a SEAL in Python → engine verifies off-chain →
engine submits → Solidity ecrecover + transferFrom settle it.

This is the signature-match proof: if the Python EIP-712 digest differed
from the Hub's by a single byte, ecrecover would not return the buyer and
every settlement here would revert with InvalidSealSignature.

No network, no mainnet, no real funds — eth-tester only.
"""
import base64
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("web3")
pytest.importorskip("eth_account")
pytest.importorskip("eth_tester")
solcx = pytest.importorskip("solcx")

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

from sincor_x402 import (AllowanceInsufficient, HubSettlementEngine,
                         SealEnvelope, SettlementReverted, VerificationFailed,
                         compute_sigil_commitment, sha256_hex, to_bytes32)
from sincor_x402.types import canonical_json

FIXTURES = Path(__file__).resolve().parent / "fixtures"
AMOUNT = 10_000            # $0.01 in atomic USDC (6 decimals)
FEE = AMOUNT * 500 // 10_000        # canon 5%  -> 500
PAYOUT = AMOUNT - FEE               # provider  -> 9_500
SPEC = {"required_keys": ["answer"]}
SPEC_HASH = sha256_hex(SPEC)
RESULT = {"answer": 42}


def _compile():
    solcx.set_solc_version("0.8.24")
    compiled = solcx.compile_files(
        [str(FIXTURES / "SincorSettlementHub.sol"),
         str(FIXTURES / "MockUSDC.sol")],
        output_values=["abi", "bin"], solc_version="0.8.24",
        evm_version="cancun")
    hub = next(v for k, v in compiled.items() if k.endswith(":SincorSettlementHub"))
    usdc = next(v for k, v in compiled.items() if k.endswith(":MockUSDC"))
    return hub, usdc


def _deploy(w3, compiled, args, frm):
    contract = w3.eth.contract(abi=compiled["abi"], bytecode=compiled["bin"])
    txh = contract.constructor(*args).transact({"from": frm})
    receipt = w3.eth.wait_for_transaction_receipt(txh)
    assert receipt.status == 1
    return w3.eth.contract(address=receipt.contractAddress, abi=compiled["abi"])


def _send_signed(w3, acct, fn, gas=200_000):
    tx = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "chainId": w3.eth.chain_id, "gas": gas})
    signed = acct.sign_transaction(tx)
    receipt = w3.eth.wait_for_transaction_receipt(
        w3.eth.send_raw_transaction(signed.raw_transaction))
    assert receipt.status == 1
    return receipt


def _typed_data_signer(acct):
    """Injected buyer signer: eth_account does the EIP-712 signing; the
    engine only ever sees the callable, never the key."""
    def _sign(typed_data):
        signable = encode_typed_data(
            domain_data=typed_data["domain"],
            message_types=typed_data["types"],
            message_data=typed_data["message"])
        return "0x" + bytes(acct.sign_message(signable).signature).hex()
    return _sign


class CountingTxSigner:
    """Injected relayer signer; counts calls so tests can prove the engine
    never even built a transaction when it refuses to settle."""
    def __init__(self, acct):
        self.acct = acct
        self.calls = 0

    def __call__(self, tx):
        self.calls += 1
        return self.acct.sign_transaction(tx).raw_transaction


@pytest.fixture(scope="module")
def hub_env():
    hub_c, usdc_c = _compile()
    w3 = Web3(EthereumTesterProvider())
    deployer, provider, treasury = (w3.eth.accounts[0], w3.eth.accounts[2],
                                    w3.eth.accounts[3])
    buyer, buyer_noallow, relayer, attacker = (Account.create() for _ in range(4))
    for acct in (buyer, buyer_noallow, relayer):
        w3.eth.send_transaction({"from": deployer, "to": acct.address,
                                 "value": 2 * 10**18})
    usdc = _deploy(w3, usdc_c, [], deployer)
    for acct in (buyer, buyer_noallow):
        usdc.functions.mint(acct.address, 1_000_000).transact({"from": deployer})
    hub = _deploy(w3, hub_c, [usdc.address, treasury], deployer)
    assert hub.functions.protocolFeeBps().call() == 500  # canon, not the draft's 100
    # The happy-path buyer does the required ERC-20 approval setup step.
    _send_signed(w3, buyer, usdc.functions.approve(hub.address, 1_000_000))

    relayer_signer = CountingTxSigner(relayer)
    engine = HubSettlementEngine(
        hub_address=hub.address, relayer_address=relayer.address,
        relayer_signer=relayer_signer, w3=w3)
    assert engine.usdc_address == usdc.address  # read from the Hub itself
    return SimpleNamespace(
        w3=w3, hub=hub, usdc=usdc, engine=engine, provider=provider,
        treasury=treasury, buyer=buyer, buyer_noallow=buyer_noallow,
        relayer=relayer, attacker=attacker, relayer_signer=relayer_signer)


def _header(env, *, task, signer=None, buyer=None, amount=AMOUNT,
            schema_hash=SPEC_HASH, deadline=None):
    return env.engine.create_buyer_seal_header(
        buyer_signer=_typed_data_signer(signer or env.buyer),
        buyer_address=(buyer or env.buyer).address,
        task_id=task, provider_address=env.provider,
        amount_atomic=amount, expected_schema_hash=schema_hash,
        deadline=deadline)


def _settle(env, header, *, spec=SPEC, spec_hash=SPEC_HASH, result=RESULT):
    return env.engine.verify_and_settle(
        payment_header_b64=header, spec=spec, spec_hash=spec_hash,
        result=result)


def _balances(env):
    bal = env.usdc.functions.balanceOf
    return (bal(env.buyer.address).call(), bal(env.provider).call(),
            bal(env.treasury).call())


# --- the cross-implementation happy path --------------------------------------

def test_hub_happy_path_settles_with_canon_fee_split(hub_env):
    env = hub_env
    before = _balances(env)
    nonce_before = env.engine.current_nonce(env.buyer.address)
    receipt = _settle(env, _header(env, task="hub-happy-1"))
    after = _balances(env)

    assert receipt.amount == AMOUNT
    # SealSettled event fields: provider 95%, treasury 5% (fee canon).
    assert receipt.net_payout == PAYOUT == 9_500
    assert receipt.protocol_fee == FEE == 500
    assert receipt.task_id == "0x" + to_bytes32("hub-happy-1").hex()
    assert receipt.provider == env.provider
    assert receipt.buyer == env.buyer.address
    assert after[0] == before[0] - AMOUNT
    assert after[1] == before[1] + PAYOUT
    assert after[2] == before[2] + FEE
    # The commitment recorded on-chain is exactly the engine's keccak canon.
    sigil = compute_sigil_commitment("hub-happy-1", RESULT)
    assert receipt.sigil_commitment == sigil
    assert env.engine.is_task_verified("hub-happy-1", sigil) is True
    assert env.engine.current_nonce(env.buyer.address) == nonce_before + 1


def test_seal_header_structure(hub_env):
    env = hub_env
    nonce_before = env.engine.current_nonce(env.buyer.address)
    header = _header(env, task="hub-struct-1")
    envelope = json.loads(base64.b64decode(header))
    assert envelope["x402Version"] == 1
    assert envelope["scheme"] == "sincor-seal-v1"
    assert envelope["network"] == f"eip155:{env.w3.eth.chain_id}"
    payload = envelope["payload"]
    assert payload["taskId"] == "0x" + to_bytes32("hub-struct-1").hex()
    assert payload["buyer"] == env.buyer.address
    assert payload["provider"] == env.provider
    assert payload["amount"] == AMOUNT
    assert payload["expectedSchemaHash"] == "0x" + SPEC_HASH
    assert payload["nonce"] == nonce_before
    assert payload["deadline"] > int(time.time())
    assert payload["v"] in (27, 28)
    assert len(payload["r"]) == len(payload["s"]) == 66
    parsed = SealEnvelope.parse(header)
    assert parsed.task_id == to_bytes32("hub-struct-1")
    assert parsed.amount == AMOUNT and parsed.nonce == nonce_before


def test_sigil_commitment_keccak_canon():
    manual = "0x" + keccak(
        to_bytes32("task-x") + canonical_json({"b": 1, "a": 2})).hex()
    # Key order in the result dict must not change the commitment.
    assert compute_sigil_commitment("task-x", {"a": 2, "b": 1}) == manual
    sha_variant = "0x" + hashlib.sha256(
        to_bytes32("task-x") + canonical_json({"a": 2, "b": 1})).hexdigest()
    assert manual != sha_variant  # keccak is the canon, not sha256


# --- on-chain negatives (the Hub enforces these) -------------------------------

def test_wrong_signer_rejected(hub_env):
    env = hub_env
    before = _balances(env)
    nonce_before = env.engine.current_nonce(env.buyer.address)
    # Envelope names the real buyer but is signed by the attacker's key.
    header = _header(env, task="hub-wrong-signer-1", signer=env.attacker)
    with pytest.raises(SettlementReverted) as excinfo:
        _settle(env, header)
    assert "InvalidSealSignature" in str(excinfo.value)
    assert _balances(env) == before
    assert env.engine.current_nonce(env.buyer.address) == nonce_before
    assert env.engine.is_task_verified(
        "hub-wrong-signer-1",
        compute_sigil_commitment("hub-wrong-signer-1", RESULT)) is False


def test_expired_deadline_rejected(hub_env):
    env = hub_env
    before = _balances(env)
    header = _header(env, task="hub-expired-1",
                     deadline=int(time.time()) - 5)
    with pytest.raises(SettlementReverted) as excinfo:
        _settle(env, header)
    assert "SealExpired" in str(excinfo.value)
    assert _balances(env) == before


def test_replayed_nonce_rejected(hub_env):
    env = hub_env
    header = _header(env, task="hub-replay-1")
    _settle(env, header)  # first submission settles and consumes the nonce
    settled_balances = _balances(env)
    with pytest.raises(SettlementReverted) as excinfo:
        _settle(env, header)  # same envelope again -> nonce already used
    assert "InvalidNonce" in str(excinfo.value)
    assert _balances(env) == settled_balances


# --- off-chain enforcement (the engine refuses; the Hub is never called) -------

def test_engine_refuses_when_verification_fails(hub_env):
    env = hub_env
    header = _header(env, task="hub-verify-fail-1")
    calls_before = env.relayer_signer.calls
    nonce_before = env.engine.current_nonce(env.buyer.address)
    before = _balances(env)
    with pytest.raises(VerificationFailed):
        _settle(env, header, result={"wrong": "shape"})
    # Hub never called: no relayer transaction was even signed, the buyer's
    # on-chain nonce is untouched, and nothing was recorded or paid.
    assert env.relayer_signer.calls == calls_before
    assert env.engine.current_nonce(env.buyer.address) == nonce_before
    assert _balances(env) == before
    assert env.engine.is_task_verified(
        "hub-verify-fail-1",
        compute_sigil_commitment("hub-verify-fail-1", {"wrong": "shape"})) is False


def test_seal_spec_mismatch_refused(hub_env):
    env = hub_env
    # Buyer validly signed a seal committing to a DIFFERENT schema hash;
    # settling it against our spec must be refused before the Hub is called.
    other_hash = sha256_hex({"required_keys": ["something-else"]})
    header = _header(env, task="hub-spec-mismatch-1", schema_hash=other_hash)
    calls_before = env.relayer_signer.calls
    with pytest.raises(VerificationFailed):
        _settle(env, header)
    assert env.relayer_signer.calls == calls_before


def test_missing_allowance_preflight(hub_env):
    env = hub_env
    header = _header(env, task="hub-noallow-1", signer=env.buyer_noallow,
                     buyer=env.buyer_noallow)
    calls_before = env.relayer_signer.calls
    with pytest.raises(AllowanceInsufficient) as excinfo:
        _settle(env, header)
    message = str(excinfo.value)
    assert "approve" in message.lower()
    assert env.hub.address in message
    assert str(AMOUNT) in message
    # Preflight fired before any transaction was built or signed.
    assert env.relayer_signer.calls == calls_before
    assert env.engine.current_nonce(env.buyer_noallow.address) == 0
