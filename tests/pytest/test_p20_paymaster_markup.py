"""P20 — x402 Paymaster Gas Sponsorship Markup.

The rule under test (SincorPaymaster):

  * Every settled sponsored op accrues a markup owed by the sender:
    markup = actualGasCost * markupBps / 10000, with markupBps
    defaulting to 800 (8% — the researched P20 list price, unratified;
    a built constant in an undeployed contract, owner-adjustable).
  * Accrual happens in postOp alongside the existing settlement
    accounting and never touches the sponsorship caps/ceiling.
  * The sender pays the accrued markup down with payMarkup(); the ETH
    is forwarded to `markupRecipient` — the address bound by the owner
    via setMarkupRecipient (the withdrawTo bind: it defaults to the
    owner and is the only destination markup can ever be paid to).
  * Overpayment and zero payment revert; accrual is per-sender and
    conserves exactly against totalMarkupAccrued / totalMarkupCollected.

Self-contained: compile + eth-tester/py-evm, mirroring the W-17
paymaster enforcement suite. Run with --noconftest.
"""

import os

import pytest
import solcx
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONTRACTS = os.path.join(REPO, "contracts")
solcx.set_solc_version("0.8.24")

MOCK_EP = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
struct PackedUserOperation {
    address sender;
    uint256 nonce;
    bytes initCode;
    bytes callData;
    bytes32 accountGasLimits;
    uint256 preVerificationGas;
    bytes32 gasFees;
    bytes paymasterAndData;
    bytes signature;
}
interface IPaymaster {
    function validatePaymasterUserOp(
        PackedUserOperation calldata uo,
        bytes32 h,
        uint256 c
    ) external returns (bytes memory, uint256);
    function postOp(uint8 mode, bytes calldata ctx, uint256 cost, uint256 feePerGas) external;
}
contract MockEntryPoint {
    function validate(address pm, PackedUserOperation calldata userOp, uint256 maxCost)
        external returns (bytes memory ctx, uint256 vd)
    {
        (ctx, vd) = IPaymaster(pm).validatePaymasterUserOp(
            userOp, bytes32(uint256(0xdead)), maxCost);
    }
    function settle(address pm, uint8 mode, bytes calldata ctx, uint256 cost, uint256 feePerGas)
        external
    {
        IPaymaster(pm).postOp(mode, ctx, cost, feePerGas);
    }
    function depositTo(address) external payable {}
    function withdrawTo(address payable, uint256) external {}
}
"""

ETH = 10**18


def _compile():
    pm_path = os.path.join(CONTRACTS, "paymaster", "SincorPaymaster.sol")
    out = solcx.compile_files(
        [pm_path], output_values=["abi", "bin"],
        allow_paths=[CONTRACTS], optimize=True, optimize_runs=200,
    )
    key = next(k for k in out if k.endswith(":SincorPaymaster"))
    pm = (out[key]["abi"], out[key]["bin"])
    out2 = solcx.compile_source(
        MOCK_EP, output_values=["abi", "bin"], optimize=True, optimize_runs=200,
    )
    key2 = next(k for k in out2 if k.endswith(":MockEntryPoint"))
    ep = (out2[key2]["abi"], out2[key2]["bin"])
    return pm, ep


(PM_ABI, PM_BIN), (EP_ABI, EP_BIN) = _compile()


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
    owner, sender, sender2, recipient = (
        tester.get_accounts()[0], tester.get_accounts()[1],
        tester.get_accounts()[2], tester.get_accounts()[3],
    )
    ep = _deploy(w3, (EP_ABI, EP_BIN), sender=owner)
    pm = _deploy(w3, (PM_ABI, PM_BIN), ep.address, sender=owner)
    pm.functions.setProbation(sender, True).transact({"from": owner})
    pm.functions.setProbation(sender2, True).transact({"from": owner})
    pm.functions.setMaxSponsoredWei(100 * ETH).transact({"from": owner})
    pm.functions.setMaxSponsoredOps(100).transact({"from": owner})
    return {
        "w3": w3, "ep": ep, "pm": pm, "owner": owner, "sender": sender,
        "sender2": sender2, "recipient": recipient,
    }


def _user_op(sender):
    return (
        sender, 0, b"", b"", b"\x00" * 32, 0, b"\x00" * 32, b"", b"",
    )


def _settle_one(env, sender, gas_cost, max_cost=None):
    max_cost = gas_cost if max_cost is None else max_cost
    ctx, _ = env["ep"].functions.validate(
        env["pm"].address, _user_op(sender), max_cost
    ).call({"from": env["owner"]})
    env["ep"].functions.validate(
        env["pm"].address, _user_op(sender), max_cost
    ).transact({"from": env["owner"]})
    env["ep"].functions.settle(
        env["pm"].address, 0, ctx, gas_cost, 0
    ).transact({"from": env["owner"]})


def test_markup_defaults_to_researched_rate_and_owner_recipient(env):
    assert env["pm"].functions.markupBps().call() == 800
    assert env["pm"].functions.markupRecipient().call() == env["owner"]


def test_markup_accrues_eight_percent_on_settlement(env):
    _settle_one(env, env["sender"], 1 * ETH)
    assert env["pm"].functions.markupAccrued(env["sender"]).call() == 8 * ETH // 100
    assert env["pm"].functions.totalMarkupAccrued().call() == 8 * ETH // 100


def test_pay_markup_forwards_to_bound_recipient(env):
    pm = env["pm"]
    pm.functions.setMarkupRecipient(env["recipient"]).transact({"from": env["owner"]})
    _settle_one(env, env["sender"], 1 * ETH)
    before = env["w3"].eth.get_balance(env["recipient"])
    pm.functions.payMarkup().transact({"from": env["sender"], "value": 5 * ETH // 100})
    after = env["w3"].eth.get_balance(env["recipient"])
    assert after - before == 5 * ETH // 100
    assert pm.functions.markupAccrued(env["sender"]).call() == 3 * ETH // 100
    assert pm.functions.totalMarkupCollected().call() == 5 * ETH // 100


def test_pay_markup_overpay_and_zero_revert(env):
    pm = env["pm"]
    _settle_one(env, env["sender"], 1 * ETH)
    with pytest.raises(TransactionFailed):
        pm.functions.payMarkup().transact({"from": env["sender"], "value": 9 * ETH // 100})
    with pytest.raises(TransactionFailed):
        pm.functions.payMarkup().transact({"from": env["sender"], "value": 0})


def test_markup_rate_settable_capped_and_owner_only(env):
    pm = env["pm"]
    pm.functions.setMarkupBps(1000).transact({"from": env["owner"]})
    _settle_one(env, env["sender"], 1 * ETH)
    assert pm.functions.markupAccrued(env["sender"]).call() == ETH // 10
    with pytest.raises(TransactionFailed):
        pm.functions.setMarkupBps(10_001).transact({"from": env["owner"]})
    with pytest.raises(TransactionFailed):
        pm.functions.setMarkupBps(500).transact({"from": env["sender"]})
    with pytest.raises(TransactionFailed):
        pm.functions.setMarkupRecipient(env["sender"]).transact({"from": env["sender"]})


def test_zero_rate_accrues_nothing(env):
    pm = env["pm"]
    pm.functions.setMarkupBps(0).transact({"from": env["owner"]})
    _settle_one(env, env["sender"], 1 * ETH)
    assert pm.functions.markupAccrued(env["sender"]).call() == 0


def test_markup_conserves_across_senders(env):
    pm = env["pm"]
    _settle_one(env, env["sender"], 1 * ETH)
    _settle_one(env, env["sender2"], 2 * ETH)
    total = pm.functions.totalMarkupAccrued().call()
    parts = (
        pm.functions.markupAccrued(env["sender"]).call()
        + pm.functions.markupAccrued(env["sender2"]).call()
    )
    assert total == parts == (8 + 16) * ETH // 100
    pm.functions.payMarkup().transact({"from": env["sender2"], "value": 16 * ETH // 100})
    outstanding = (
        pm.functions.markupAccrued(env["sender"]).call()
        + pm.functions.markupAccrued(env["sender2"]).call()
    )
    assert outstanding + pm.functions.totalMarkupCollected().call() == total
