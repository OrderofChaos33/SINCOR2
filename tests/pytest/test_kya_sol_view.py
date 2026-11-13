"""eth-tester tests for the KYARegistry.attestationStatus audit-readiness view.

attestationStatus(kyaId) answers "is this attestation trustworthy right
now" in a single call, so off-chain consumers no longer need to
cross-reference the byKya, everRevoked, revokedAgent and revokedPrincipal
mappings. Semantics (verified in the contract, restated here):

    verified        = a.verified && !a.revoked && !_actorTombstoned(a)
    revoked         = a.revoked
    tombstoned      = _actorTombstoned(a)
    everRevokedFlag = everRevoked[kyaId]

Pure view: no state changes, no new storage. This file adds ONLY the
new view; it does not modify any existing contract logic and does not
modify tests/pytest/test_kya_no_revive.py.

Self-contained: compiles contracts/kya/KYARegistry.sol with solc 0.8.24,
drives it with web3 + eth-tester (PyEVM backend). Deliberately does NOT
import sincor2 and does not rely on the repo-root conftest. Run with:

    ~/.venvs/sincor2/bin/python -m pytest --confcutdir=tests/pytest \\
        tests/pytest/test_kya_sol_view.py -q
"""

import os

import pytest
import solcx
from eth_tester import EthereumTester, PyEVMBackend
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3.providers.eth_tester import EthereumTesterProvider

CONTRACTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "contracts")
)

SOLC_VERSION = "0.8.24"
REVERIFY_DELAY = 48 * 3600  # must match KYARegistry.REVERIFY_DELAY


def compile_registry():
    solcx.set_solc_version(SOLC_VERSION)
    out = solcx.compile_files(
        [os.path.join(CONTRACTS_DIR, "kya", "KYARegistry.sol")],
        output_values=["abi", "bin"],
        allow_paths=[CONTRACTS_DIR],
        optimize=True,
        optimize_runs=200,
    )
    key = next(k for k in out if k.endswith(":KYARegistry"))
    return out[key]["abi"], out[key]["bin"]


@pytest.fixture()
def env():
    tester = EthereumTester(PyEVMBackend())
    w3 = Web3(EthereumTesterProvider(tester))
    abi, bytecode = compile_registry()
    owner, agent, nominee, stranger = w3.eth.accounts[:4]
    c = w3.eth.contract(abi=abi, bytecode=bytecode)
    txh = c.constructor().transact({"from": owner})
    addr = w3.eth.get_transaction_receipt(txh)["contractAddress"]
    reg = w3.eth.contract(address=addr, abi=abi)
    return w3, tester, reg, owner, agent, nominee, stranger


def travel(tester, w3, seconds):
    """Move chain time forward `seconds` and mine a block."""
    ts = w3.eth.get_block("latest")["timestamp"]
    tester.time_travel(ts + seconds)
    tester.mine_block()


def status(reg, kya_id):
    """attestationStatus as a (verified, revoked, tombstoned, everRevokedFlag) tuple."""
    return tuple(reg.functions.attestationStatus(kya_id).call())


# ---------------------------------------------------------------------------
# Core status semantics
# ---------------------------------------------------------------------------


def test_status_live_verified_attestation(env):
    """list + verify on a fresh actor: fully trustworthy right now."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x11" * 32
    reg.functions.list(kya_id, b"\xaa" * 32, agent, b"\x01" * 32).transact(
        {"from": owner}
    )
    assert status(reg, kya_id) == (False, False, False, False)  # listed, unverified
    reg.functions.verify(kya_id, b"\x01" * 32).transact({"from": owner})
    assert status(reg, kya_id) == (True, False, False, False)


def test_status_revoked_attestation(env):
    """revoke() sets revoked + tombstones the actor + stamps everRevoked."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x22" * 32
    reg.functions.list(kya_id, b"\xaa" * 32, agent, b"\x01" * 32).transact(
        {"from": owner}
    )
    reg.functions.verify(kya_id, b"\x01" * 32).transact({"from": owner})
    assert status(reg, kya_id) == (True, False, False, False)
    reg.functions.revoke(kya_id, b"\x01" * 32).transact({"from": owner})
    assert status(reg, kya_id) == (False, True, True, True)


def test_status_fresh_kyaid_for_tombstoned_actor(env):
    """Fresh kyaId naming a tombstoned actor: listable (visible on-chain)
    but never verifiable -- the view shows the tombstone with no flags."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya1, kya2 = b"\x31" * 32, b"\x32" * 32
    ah = b"\xaa" * 32
    reg.functions.list(kya1, ah, agent, b"\x01" * 32).transact({"from": owner})
    reg.functions.revoke(kya1, b"\x01" * 32).transact({"from": owner})

    reg.functions.list(kya2, ah, agent, b"\x02" * 32).transact({"from": owner})
    assert status(reg, kya2) == (False, False, True, False)
    # verify() is gated by the same tombstone the view reports.
    with pytest.raises(TransactionFailed):
        reg.functions.verify(kya2, b"\x02" * 32).transact({"from": owner})
    assert status(reg, kya2) == (False, False, True, False)


def test_status_never_listed_kyaid(env):
    """A kyaId that was never listed reports all-false: no record, no
    flags, no tombstone (the zero-identity bucket was never burned)."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    assert status(reg, b"\x99" * 32) == (False, False, False, False)


def test_status_sibling_unverified_by_sweep(env):
    """revoke() unverifies live VERIFIED hash-sharing siblings: the sibling
    is not revoked and not ever-revoked, but the burned hash keeps
    tombstoned=true, so verified=false."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    k1, k2 = b"\x51" * 32, b"\x52" * 32
    H = b"\xe1" * 32
    W1, W2 = agent, nominee
    reg.functions.list(k1, H, W1, b"\x01" * 32).transact({"from": owner})
    reg.functions.list(k2, H, W2, b"\x02" * 32).transact({"from": owner})
    reg.functions.verify(k1, b"\x01" * 32).transact({"from": owner})
    reg.functions.verify(k2, b"\x02" * 32).transact({"from": owner})
    assert status(reg, k2) == (True, False, False, False)

    reg.functions.revoke(k1, b"\x01" * 32).transact({"from": owner})
    assert status(reg, k2) == (False, False, True, False)
    # everRevoked is per-kyaId: the swept sibling keeps a clean record.
    assert reg.functions.everRevoked(k2).call() is False


# ---------------------------------------------------------------------------
# Extra coverage: graft, re-admission intermediates, full lifecycle
# ---------------------------------------------------------------------------


def test_status_grafted_attestation_onto_burned_wallet(env):
    """A live VERIFIED attestation grafted onto a burned wallet is instantly
    unverified by list(): live and not revoked, but tombstoned."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kyaV, kyaB = b"\x61" * 32, b"\x62" * 32
    Hv, Hb = b"\x51" * 32, b"\x52" * 32
    Wv, Wb = nominee, stranger
    reg.functions.list(kyaV, Hv, Wv, b"\x01" * 32).transact({"from": owner})
    reg.functions.verify(kyaV, b"\x01" * 32).transact({"from": owner})
    reg.functions.list(kyaB, Hb, Wb, b"\x02" * 32).transact({"from": owner})
    reg.functions.verify(kyaB, b"\x02" * 32).transact({"from": owner})
    reg.functions.revoke(kyaB, b"\x02" * 32).transact({"from": owner})
    assert reg.functions.revokedPrincipal(Wb).call() is True

    # Graft the live attestation onto the burned wallet.
    reg.functions.list(kyaV, Hv, Wb, b"\x03" * 32).transact({"from": owner})
    assert status(reg, kyaV) == (False, False, True, False)
    with pytest.raises(TransactionFailed):
        reg.functions.verify(kyaV, b"\x03" * 32).transact({"from": owner})
    assert status(reg, kyaV) == (False, False, True, False)


def test_status_after_reverification_finalize_before_reverify(env):
    """After the timelocked cycle completes, the tombstones are cleared
    but the record starts unverified -- and everRevokedFlag persists."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x71" * 32
    ah = b"\xaa" * 32
    reg.functions.list(kya_id, ah, agent, b"\x01" * 32).transact({"from": owner})
    reg.functions.verify(kya_id, b"\x01" * 32).transact({"from": owner})
    reg.functions.revoke(kya_id, b"\x01" * 32).transact({"from": owner})
    assert status(reg, kya_id) == (False, True, True, True)

    reg.functions.requestReverification(kya_id).transact({"from": owner})
    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(kya_id, ah, agent, b"\x02" * 32).transact(
        {"from": owner}
    )
    assert status(reg, kya_id) == (False, False, False, True)


def test_status_full_lifecycle_end_to_end(env):
    """One kyaId through the whole state machine: the view tracks every
    transition, and re-verification after the timelocked path restores
    verified=true while everRevokedFlag stays set."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x81" * 32
    ah = b"\xaa" * 32
    reg.functions.list(kya_id, ah, agent, b"\x01" * 32).transact({"from": owner})
    assert status(reg, kya_id) == (False, False, False, False)

    reg.functions.verify(kya_id, b"\x01" * 32).transact({"from": owner})
    assert status(reg, kya_id) == (True, False, False, False)

    reg.functions.revoke(kya_id, b"\x01" * 32).transact({"from": owner})
    assert status(reg, kya_id) == (False, True, True, True)

    reg.functions.requestReverification(kya_id).transact({"from": owner})
    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(kya_id, ah, agent, b"\x02" * 32).transact(
        {"from": owner}
    )
    assert status(reg, kya_id) == (False, False, False, True)

    reg.functions.verify(kya_id, b"\x02" * 32).transact({"from": owner})
    assert status(reg, kya_id) == (True, False, False, True)
    # Cross-check against the underlying mappings: the view aggregates
    # exactly what the four reads report.
    a = reg.functions.byKya(kya_id).call()
    assert (a[4], a[5]) == (True, False)
    assert reg.functions.revokedAgent(ah).call() is False
    assert reg.functions.revokedPrincipal(agent).call() is False
    assert reg.functions.everRevoked(kya_id).call() is True


def test_status_is_view_state_unchanged(env):
    """Calling attestationStatus does not change any on-chain state: the
    attestation, tombstones, indexes and flags read identically after."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x91" * 32
    ah = b"\xaa" * 32
    reg.functions.list(kya_id, ah, agent, b"\x01" * 32).transact({"from": owner})
    reg.functions.verify(kya_id, b"\x01" * 32).transact({"from": owner})

    before = reg.functions.byKya(kya_id).call()
    assert status(reg, kya_id) == (True, False, False, False)
    after = reg.functions.byKya(kya_id).call()
    assert before == after
    assert status(reg, b"\x92" * 32) == (False, False, False, False)  # never-listed
