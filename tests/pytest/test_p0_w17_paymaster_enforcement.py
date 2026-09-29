"""P0/W-17 regression: SincorPaymaster must enforce its sponsorship policy.

Covers the red-team PoC (poc_w17_paymaster.py): validatePaymasterUserOp
used to return validationData=0 for ANY UserOp, ignoring `probation` and
`maxSponsoredOps`, so the paymaster's EntryPoint deposit was drainable by
UserOp spam the moment it was funded.

Enforced now, in validatePaymasterUserOp:
  1. sender explicitly allowlisted (probation[sender] == true),
  2. sender under its per-wallet op cap, counting in-flight ops
     (sponsoredCount + inFlight < maxSponsoredOps),
  3. global spend ceiling (sponsoredWei + reservedWei + maxCost <= maxSponsoredWei),
with maxSponsoredWei defaulting to 0 (fail-closed).

Self-contained: compile + eth-tester/py-evm. Run with --noconftest.
"""
import os
import pytest
import solcx
from eth_abi import encode as abi_encode
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider
from web3.exceptions import ContractLogicError
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONTRACTS = os.path.join(REPO, "contracts")
solcx.set_solc_version("0.8.24")

MOCK_EP = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
interface IPaymaster {
    function validatePaymasterUserOp(bytes calldata uo, bytes32 h, uint256 c)
        external returns (bytes memory, uint256);
    function postOp(uint8 mode, bytes calldata ctx, uint256 cost) external;
}
contract MockEntryPoint {
    function validate(address pm, bytes calldata userOp, uint256 maxCost)
        external returns (bytes memory ctx, uint256 vd)
    {
        (ctx, vd) = IPaymaster(pm).validatePaymasterUserOp(
            userOp, bytes32(uint256(0xdead)), maxCost);
    }
    function settle(address pm, uint8 mode, bytes calldata ctx, uint256 cost)
        external
    {
        IPaymaster(pm).postOp(mode, ctx, cost);
    }
    function decodeCtx(bytes calldata ctx)
        external pure returns (address s, uint256 c)
    {
        (s, c) = abi.decode(ctx, (address, uint256));
    }
    function depositTo(address) external payable {}
}
"""


def compile_contract(filename):
    path = os.path.join(CONTRACTS, filename)
    out = solcx.compile_files(
        [path], output_values=["abi", "bin"],
        allow_paths=[CONTRACTS], optimize=True, optimize_runs=200,
    )
    key = next(k for k in out
               if k.endswith(":" + os.path.basename(filename).replace(".sol", "")))
    return out[key]["abi"], out[key]["bin"]


def compile_source(name, source):
    out = solcx.compile_source(
        source, output_values=["abi", "bin"],
        allow_paths=[CONTRACTS], optimize=True, optimize_runs=200,
    )
    key = next(k for k in out if k.endswith(":" + name))
    return out[key]["abi"], out[key]["bin"]


def deploy(w3, abi, bytecode, args=(), sender=None):
    sender = sender or w3.eth.accounts[0]
    c = w3.eth.contract(abi=abi, bytecode=bytecode)
    txh = c.constructor(*args).transact({"from": sender})
    addr = w3.eth.get_transaction_receipt(txh)["contractAddress"]
    return w3.eth.contract(address=addr, abi=abi)


def make_userop(w3, sender):
    # Unpacked ERC-4337 UserOperation: sender is the first 32-byte word.
    return w3.to_bytes(hexstr=sender).rjust(32, b"\x00") + b"\x00" * 480


@pytest.fixture()
def env():
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    deployer, attacker, agent = w3.eth.accounts[0], w3.eth.accounts[1], w3.eth.accounts[2]
    ep_abi, ep_bin = compile_source("MockEntryPoint", MOCK_EP)
    mock_ep = deploy(w3, ep_abi, ep_bin, sender=deployer)
    pm_abi, pm_bin = compile_contract("paymaster/SincorPaymaster.sol")
    pm = deploy(w3, pm_abi, pm_bin, args=(mock_ep.address,), sender=deployer)
    return w3, mock_ep, pm, deployer, attacker, agent


def _revert_reason(exc):
    msg = str(exc)
    for marker in ("execution reverted: ", "reverted: "):
        if marker in msg:
            return msg.split(marker, 1)[1].split("\n")[0].strip().strip("'\"")
    return msg


def do_validate(mock_ep, pm, uo, max_cost, sender):
    """Validate via eth_call (assert vd==0, capture context), then commit the
    reservation on-chain with an identical transact."""
    ctx, vd = mock_ep.functions.validate(pm.address, uo, max_cost).call({"from": sender})
    assert vd == 0
    mock_ep.functions.validate(pm.address, uo, max_cost).transact({"from": sender})
    return ctx


def test_non_allowlisted_sender_rejected(env):
    """The red-team PoC: hostile UserOp from an address NEVER on probation."""
    w3, mock_ep, pm, deployer, attacker, agent = env
    assert pm.functions.probation(attacker).call() is False
    uo = make_userop(w3, attacker)
    with pytest.raises((ContractLogicError, TransactionFailed)) as ei:
        mock_ep.functions.validate(pm.address, uo, 10**15).call({"from": attacker})
    assert _revert_reason(ei.value) == "not allowlisted"
    # Attack left no state behind: nothing reserved, nothing counted.
    assert pm.functions.reservedWei().call() == 0
    assert pm.functions.sponsoredCount(attacker).call() == 0


def test_allowlisted_under_cap_accepted_and_settled(env):
    """Honest flow: allowlisted sender under cap and ceiling gets sponsored."""
    w3, mock_ep, pm, deployer, attacker, agent = env
    pm.functions.setProbation(agent, True).transact({"from": deployer})
    pm.functions.setMaxSponsoredWei(10**18).transact({"from": deployer})
    uo = make_userop(w3, agent)
    max_cost = 10**15
    ctx = do_validate(mock_ep, pm, uo, max_cost, agent)
    # Single canonical encode path: context == abi.encode(sender, maxCost).
    s, c = mock_ep.functions.decodeCtx(ctx).call()
    assert s == agent and c == max_cost
    assert pm.functions.reservedWei().call() == max_cost
    # Settle via postOp: reservation released, actual cost accrued, op counted.
    actual = 6 * 10**14
    mock_ep.functions.settle(pm.address, 0, ctx, actual).transact({"from": deployer})
    assert pm.functions.reservedWei().call() == 0
    assert pm.functions.sponsoredWei().call() == actual
    assert pm.functions.sponsoredCount(agent).call() == 1


def test_per_wallet_op_cap_rejected(env):
    """Allowlisted sender at maxSponsoredOps is rejected."""
    w3, mock_ep, pm, deployer, attacker, agent = env
    pm.functions.setProbation(agent, True).transact({"from": deployer})
    pm.functions.setMaxSponsoredWei(10**20).transact({"from": deployer})
    pm.functions.setMaxSponsoredOps(2).transact({"from": deployer})
    uo = make_userop(w3, agent)
    for _ in range(2):
        ctx = do_validate(mock_ep, pm, uo, 10**15, agent)
        mock_ep.functions.settle(pm.address, 0, ctx, 10**15).transact({"from": deployer})
    assert pm.functions.sponsoredCount(agent).call() == 2
    with pytest.raises((ContractLogicError, TransactionFailed)) as ei:
        mock_ep.functions.validate(pm.address, uo, 10**15).call({"from": agent})
    assert _revert_reason(ei.value) == "op cap reached"


def test_bundle_in_flight_op_cap_rejected(env):
    """Adversarial-review finding: two UserOps from the same sender in one
    EntryPoint handleOps bundle. Without the in-flight counter, both validate
    (sponsoredCount unchanged between validates) and sponsoredCount ends 2 > cap 1.
    The second validate in the bundle must revert."""
    w3, mock_ep, pm, deployer, attacker, agent = env
    pm.functions.setProbation(agent, True).transact({"from": deployer})
    pm.functions.setMaxSponsoredWei(10**20).transact({"from": deployer})
    pm.functions.setMaxSponsoredOps(1).transact({"from": deployer})
    uo = make_userop(w3, agent)
    # First op in the bundle validates and is marked in-flight (no postOp yet).
    ctx = do_validate(mock_ep, pm, uo, 10**15, agent)
    assert pm.functions.inFlight(agent).call() == 1
    assert pm.functions.sponsoredCount(agent).call() == 0
    # Second op in the same bundle: 0 settled + 1 in-flight >= cap (1) -> revert.
    with pytest.raises((ContractLogicError, TransactionFailed)) as ei:
        mock_ep.functions.validate(pm.address, uo, 10**15).call({"from": agent})
    assert _revert_reason(ei.value) == "op cap reached"
    # postOp clears the in-flight slot; the cap still holds afterwards.
    mock_ep.functions.settle(pm.address, 0, ctx, 10**15).transact({"from": deployer})
    assert pm.functions.inFlight(agent).call() == 0
    assert pm.functions.sponsoredCount(agent).call() == 1
    with pytest.raises((ContractLogicError, TransactionFailed)) as ei:
        mock_ep.functions.validate(pm.address, uo, 10**15).call({"from": agent})
    assert _revert_reason(ei.value) == "op cap reached"


def test_postop_unmatched_validate_does_not_underflow(env):
    """postOp with a context that has no matching in-flight validate must not
    underflow the in-flight counter (guarded decrement)."""
    w3, mock_ep, pm, deployer, attacker, agent = env
    pm.functions.setProbation(agent, True).transact({"from": deployer})
    pm.functions.setMaxSponsoredWei(10**20).transact({"from": deployer})
    assert pm.functions.inFlight(agent).call() == 0
    # Canonical context, but no validate ever reserved an in-flight slot.
    ctx = abi_encode(["address", "uint256"], [agent, 10**15])
    mock_ep.functions.settle(pm.address, 0, ctx, 10**14).transact({"from": deployer})
    assert pm.functions.inFlight(agent).call() == 0
    # The slot still works normally afterwards.
    ctx = do_validate(mock_ep, pm, make_userop(w3, agent), 10**15, agent)
    assert pm.functions.inFlight(agent).call() == 1
    mock_ep.functions.settle(pm.address, 0, ctx, 10**15).transact({"from": deployer})
    assert pm.functions.inFlight(agent).call() == 0


def test_setprobation_rejects_zero_address(env):
    """Reviewer suggestion: allowlisting address(0) is a footgun."""
    w3, mock_ep, pm, deployer, attacker, agent = env
    with pytest.raises((ContractLogicError, TransactionFailed)) as ei:
        pm.functions.setProbation(
            "0x0000000000000000000000000000000000000000", True
        ).call({"from": deployer})
    assert _revert_reason(ei.value) == "zero wallet"


def test_global_spend_ceiling_enforced(env):
    """In-flight reservations count against the ceiling; over-ceiling rejected."""
    w3, mock_ep, pm, deployer, attacker, agent = env
    pm.functions.setProbation(agent, True).transact({"from": deployer})
    pm.functions.setMaxSponsoredWei(10**15).transact({"from": deployer})
    uo = make_userop(w3, agent)
    # First op reserves 6e14: fits (0 + 0 + 6e14 <= 1e15).
    ctx = do_validate(mock_ep, pm, uo, 6 * 10**14, agent)
    # Second op of 6e14 would exceed: reserved 6e14 + 6e14 > 1e15.
    with pytest.raises((ContractLogicError, TransactionFailed)) as ei:
        mock_ep.functions.validate(pm.address, uo, 6 * 10**14).call({"from": agent})
    assert _revert_reason(ei.value) == "spend ceiling"
    # After settling the first op (actual 6e14), a 4e14 op fits exactly.
    mock_ep.functions.settle(pm.address, 0, ctx, 6 * 10**14).transact({"from": deployer})
    do_validate(mock_ep, pm, uo, 4 * 10**14, agent)
    assert pm.functions.sponsoredWei().call() == 6 * 10**14
    assert pm.functions.reservedWei().call() == 4 * 10**14


def test_ceiling_defaults_fail_closed(env):
    """maxSponsoredWei starts at 0: nothing sponsored until the owner opens it."""
    w3, mock_ep, pm, deployer, attacker, agent = env
    assert pm.functions.maxSponsoredWei().call() == 0
    pm.functions.setProbation(agent, True).transact({"from": deployer})
    uo = make_userop(w3, agent)
    with pytest.raises((ContractLogicError, TransactionFailed)) as ei:
        mock_ep.functions.validate(pm.address, uo, 10**15).call({"from": agent})
    assert _revert_reason(ei.value) == "spend ceiling"
    # Only the owner can open the ceiling.
    with pytest.raises((ContractLogicError, TransactionFailed)):
        pm.functions.setMaxSponsoredWei(10**18).call({"from": attacker})
