"""P04 — Dual-Pool Re-Auction Fees: SincorTaskEscrow re-auction fee rule.

The rule under test (see contracts/escrow/REAUCTION_FEE_RULE.md):

  * A task that expires Unassigned, or Claimed-but-unfinished, can be
    re-auctioned by its creator with a fresh duration.
  * Re-auction charges REAUCTION_FEE_BPS (1000 = 10%, the researched
    P04 price, unratified) of the escrowed reward, paid to treasury;
    the remaining reward stays escrowed for the next attempt.
  * A claimed task's bond is returned to the agent in full on
    re-auction (recycling path; slashTask remains the punitive path).
  * Fund conservation: escrow balance == sum(remaining rewards)
    + sum(outstanding bonds) at every step.

Self-contained: compiles contracts/escrow/SincorTaskEscrow.sol with
solc 0.8.30 via solcx compile_standard, resolving the OpenZeppelin
imports from a local openzeppelin-contracts checkout (candidates:
$SINCOR_OZ_CONTRACTS, <repo>/onchain/lib/openzeppelin-contracts,
~/workspace/.deps/openzeppelin-contracts). Drives the contract with
web3 + eth-tester (PyEVM). Does NOT import sincor2. Run with:

    ~/.venvs/sincor2/bin/python -m pytest --confcutdir=tests/pytest \
        tests/pytest/test_p04_reauction_fee.py -q
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
ESCROW_PATH = os.path.join(REPO, "contracts", "escrow", "SincorTaskEscrow.sol")
SOLC_VERSION = "0.8.30"

# TaskStatus enum indices (must match SincorTaskEscrow.sol)
UNASSIGNED, CLAIMED, VERIFIED, SLASHED = range(4)

MOCK_TOKEN_SRC = """
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

contract MockToken {
    string public name = "Mock";
    string public symbol = "MCK";
    uint8 public decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    function mint(address to, uint256 amount) external {
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _move(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= amount, "allowance");
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - amount;
        }
        _move(from, to, amount);
        return true;
    }

    function _move(address from, address to, uint256 amount) internal {
        require(balanceOf[from] >= amount, "balance");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }
}
"""


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
    raise RuntimeError(
        "openzeppelin-contracts checkout not found; set SINCOR_OZ_CONTRACTS "
        "to its contracts/ directory"
    )


_IMPORT_RE = re.compile(r'import\s+(?:[^"\']*?from\s+)?["\']([^"\']+)["\']')


def _collect_sources():
    """Build the compile_standard source map: the escrow contract, the
    mock token, and the transitive OpenZeppelin closure under the
    canonical @openzeppelin/contracts/ unit names."""
    oz_dir = _oz_contracts_dir()
    sources = {}

    def add_oz(unit_name):
        if unit_name in sources:
            return
        rel = unit_name[len("@openzeppelin/contracts/"):]
        path = os.path.join(oz_dir, rel)
        with open(path) as f:
            content = f.read()
        sources[unit_name] = {"content": content}
        base = os.path.dirname(unit_name)
        for imp in _IMPORT_RE.findall(content):
            if imp.startswith("@openzeppelin/contracts/"):
                add_oz(imp)
            elif imp.startswith("."):
                add_oz(os.path.normpath(os.path.join(base, imp)))

    with open(ESCROW_PATH) as f:
        escrow_src = f.read()
    sources["contracts/escrow/SincorTaskEscrow.sol"] = {"content": escrow_src}
    for imp in _IMPORT_RE.findall(escrow_src):
        if imp.startswith("@openzeppelin/contracts/"):
            add_oz(imp)
    sources["MockToken.sol"] = {"content": MOCK_TOKEN_SRC}
    return sources


def _compile():
    solcx.set_solc_version(SOLC_VERSION)
    std = {
        "language": "Solidity",
        "sources": _collect_sources(),
        "settings": {
            "optimizer": {"enabled": True, "runs": 200},
            "outputSelection": {
                "contracts/escrow/SincorTaskEscrow.sol": {
                    "SincorTaskEscrow": ["abi", "evm.bytecode.object"]
                },
                "MockToken.sol": {"MockToken": ["abi", "evm.bytecode.object"]},
            },
        },
    }
    out = solcx.compile_standard(std, solc_version=SOLC_VERSION)
    esc = out["contracts"]["contracts/escrow/SincorTaskEscrow.sol"]["SincorTaskEscrow"]
    tok = out["contracts"]["MockToken.sol"]["MockToken"]
    return (esc["abi"], esc["evm"]["bytecode"]["object"]), (tok["abi"], tok["evm"]["bytecode"]["object"])


(ESCROW_ABI, ESCROW_BIN), (TOKEN_ABI, TOKEN_BIN) = _compile()


@pytest.fixture()
def env():
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    accts = tester.get_accounts()
    creator, agent, treasury, stranger = accts[0], accts[1], accts[2], accts[3]

    token = w3.eth.contract(abi=TOKEN_ABI, bytecode=TOKEN_BIN)
    tx = token.constructor().transact({"from": creator})
    token_addr = w3.eth.wait_for_transaction_receipt(tx)["contractAddress"]
    token = w3.eth.contract(address=token_addr, abi=TOKEN_ABI)

    escrow = w3.eth.contract(abi=ESCROW_ABI, bytecode=ESCROW_BIN)
    tx = escrow.constructor(token_addr, treasury).transact({"from": creator})
    escrow_addr = w3.eth.wait_for_transaction_receipt(tx)["contractAddress"]
    escrow = w3.eth.contract(address=escrow_addr, abi=ESCROW_ABI)

    token.functions.mint(creator, 1_000_000).transact({"from": creator})
    token.functions.mint(agent, 100_000).transact({"from": creator})
    token.functions.approve(escrow_addr, 2**256 - 1).transact({"from": creator})
    token.functions.approve(escrow_addr, 2**256 - 1).transact({"from": agent})

    return {
        "tester": tester, "w3": w3, "token": token, "escrow": escrow,
        "creator": creator, "agent": agent, "treasury": treasury,
        "stranger": stranger, "escrow_addr": escrow_addr,
    }


def _create(env, reward, bond, duration):
    tx = env["escrow"].functions.createTask(reward, bond, duration).transact(
        {"from": env["creator"]}
    )
    receipt = env["w3"].eth.wait_for_transaction_receipt(tx)
    return receipt["logs"][0]["topics"][1]  # not used; id from nextTaskId order


def _create_id(env, reward, bond, duration):
    escrow = env["escrow"]
    task_id = escrow.functions.nextTaskId().call()
    escrow.functions.createTask(reward, bond, duration).transact({"from": env["creator"]})
    return task_id


def _task(env, task_id):
    t = env["escrow"].functions.tasks(task_id).call()
    # (creator, assignedAgent, rewardAmount, bondRequired, expiryTimestamp,
    #  status, timesReauctioned)
    return {
        "creator": t[0], "agent": t[1], "reward": t[2], "bond": t[3],
        "expiry": t[4], "status": t[5], "times_reauctioned": t[6],
    }


def _bal(env, who):
    return env["token"].functions.balanceOf(who).call()


def _warp_past_expiry(env, task_id, buffer=10):
    expiry = _task(env, task_id)["expiry"]
    env["tester"].time_travel(expiry + buffer)


def test_reauction_unclaimed_expired_charges_ten_percent(env):
    tid = _create_id(env, reward=1000, bond=0, duration=100)
    _warp_past_expiry(env, tid)
    treasury_before = _bal(env, env["treasury"])

    fee = env["escrow"].functions.reauctionTask(tid, 500).call({"from": env["creator"]})
    env["escrow"].functions.reauctionTask(tid, 500).transact({"from": env["creator"]})

    assert fee == 100
    task = _task(env, tid)
    assert task["reward"] == 900
    assert task["status"] == UNASSIGNED
    assert task["times_reauctioned"] == 1
    assert _bal(env, env["treasury"]) - treasury_before == 100
    assert _bal(env, env["escrow_addr"]) == 900


def test_reauction_claimed_expired_returns_bond_and_charges_fee(env):
    tid = _create_id(env, reward=1000, bond=200, duration=100)
    env["escrow"].functions.claimTask(tid).transact({"from": env["agent"]})
    agent_before = _bal(env, env["agent"])
    _warp_past_expiry(env, tid)

    env["escrow"].functions.reauctionTask(tid, 500).transact({"from": env["creator"]})

    task = _task(env, tid)
    assert task["status"] == UNASSIGNED
    assert task["agent"] == "0x0000000000000000000000000000000000000000"
    assert task["reward"] == 900
    assert _bal(env, env["agent"]) - agent_before == 200  # bond returned in full
    assert _bal(env, env["escrow_addr"]) == 900  # only the reduced reward remains


def test_reauction_before_expiry_reverts(env):
    tid = _create_id(env, reward=1000, bond=0, duration=10_000)
    with pytest.raises(TransactionFailed):
        env["escrow"].functions.reauctionTask(tid, 500).transact({"from": env["creator"]})


def test_reauction_non_creator_reverts(env):
    tid = _create_id(env, reward=1000, bond=0, duration=100)
    _warp_past_expiry(env, tid)
    with pytest.raises(TransactionFailed):
        env["escrow"].functions.reauctionTask(tid, 500).transact({"from": env["stranger"]})


def test_reauction_verified_task_reverts(env):
    tid = _create_id(env, reward=1000, bond=0, duration=100)
    env["escrow"].functions.claimTask(tid).transact({"from": env["agent"]})
    env["escrow"].functions.completeTask(tid).transact({"from": env["creator"]})
    _warp_past_expiry(env, tid)
    with pytest.raises(TransactionFailed):
        env["escrow"].functions.reauctionTask(tid, 500).transact({"from": env["creator"]})


def test_reauction_zero_duration_reverts(env):
    tid = _create_id(env, reward=1000, bond=0, duration=100)
    _warp_past_expiry(env, tid)
    with pytest.raises(TransactionFailed):
        env["escrow"].functions.reauctionTask(tid, 0).transact({"from": env["creator"]})


def test_reauction_fee_floors_and_second_reauction_uses_reduced_reward(env):
    tid = _create_id(env, reward=999, bond=0, duration=100)
    _warp_past_expiry(env, tid)
    fee1 = env["escrow"].functions.reauctionTask(tid, 100).call({"from": env["creator"]})
    env["escrow"].functions.reauctionTask(tid, 100).transact({"from": env["creator"]})
    assert fee1 == 99  # floor(999 * 1000 / 10000)
    assert _task(env, tid)["reward"] == 900

    _warp_past_expiry(env, tid)
    fee2 = env["escrow"].functions.reauctionTask(tid, 100).call({"from": env["creator"]})
    env["escrow"].functions.reauctionTask(tid, 100).transact({"from": env["creator"]})
    assert fee2 == 90  # 10% of the reduced 900
    task = _task(env, tid)
    assert task["reward"] == 810
    assert task["times_reauctioned"] == 2
    assert _bal(env, env["escrow_addr"]) == 810


def test_conservation_across_mixed_reauctions(env):
    a = _create_id(env, reward=1000, bond=0, duration=100)
    b = _create_id(env, reward=2000, bond=300, duration=100)
    env["escrow"].functions.claimTask(b).transact({"from": env["agent"]})
    _warp_past_expiry(env, a)
    env["escrow"].functions.reauctionTask(a, 500).transact({"from": env["creator"]})
    env["escrow"].functions.reauctionTask(b, 500).transact({"from": env["creator"]})

    remaining = _task(env, a)["reward"] + _task(env, b)["reward"]
    assert remaining == 900 + 1800
    # Escrow holds exactly the remaining rewards; bonds were returned,
    # fees (100 + 200) went to treasury.
    assert _bal(env, env["escrow_addr"]) == remaining
    assert _bal(env, env["treasury"]) == 300
