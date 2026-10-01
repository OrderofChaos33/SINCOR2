"""eth-tester regression tests for the W-10 KYARegistry hardening.

Covers: no-revive guard (list() of an ever-revoked kyaId reverts), the
timelocked re-verification path (request + finalize after REVERIFY_DELAY),
two-step owner rotation (nominate + accept by the nominee), the per-actor
tombstone (new-kyaId same-actor verify reverts), the list()-graft guard
(identity change via list() unverifies), the revoke()-sibling sweep
(live VERIFIED siblings sharing the burned hash -- the FULL hashToKya
array, not just the agentToKya pointer target -- or the burned wallet are
unverified, each emitting SiblingUnverified when it actually flips), and
hashToKya index integrity across hash moves.

Self-contained: compiles contracts/kya/KYARegistry.sol with solc 0.8.24,
drives it with web3 + eth-tester (PyEVM backend). Deliberately does NOT
import sincor2 and does not rely on the repo-root conftest. Run with:

    ~/.venvs/sincor2/bin/python -m pytest --confcutdir=tests/pytest \\
        tests/pytest/test_kya_no_revive.py -q
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


def listed(reg, w3, kya_id, sender, record):
    txh = reg.functions.list(
        kya_id, b"\x11" * 32, w3.eth.accounts[1], record
    ).transact({"from": sender})
    return w3.eth.get_transaction_receipt(txh)


def _principal_ids(reg, principal):
    """Read the whole principalToKya[principal] array (index getter)."""
    ids = []
    i = 0
    while True:
        try:
            ids.append(reg.functions.principalToKya(principal, i).call())
        except Exception:
            break
        i += 1
    return ids


def _hash_ids(reg, agent_hash):
    """Read the whole hashToKya[agent_hash] array (index getter)."""
    ids = []
    i = 0
    while True:
        try:
            ids.append(reg.functions.hashToKya(agent_hash, i).call())
        except Exception:
            break
        i += 1
    return ids


# ---------------------------------------------------------------------------
# No-revive guard
# ---------------------------------------------------------------------------


def test_relist_after_revoke_reverts(env):
    """Core W-10 regression: list -> verify -> revoke -> re-list MUST revert."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x11" * 32
    listed(reg, w3, kya_id, owner, b"\xaa" * 32)
    reg.functions.verify(kya_id, b"\xaa" * 32).transact({"from": owner})
    reg.functions.revoke(kya_id, b"\xaa" * 32).transact({"from": owner})

    assert reg.functions.everRevoked(kya_id).call() is True
    a = reg.functions.byKya(kya_id).call()
    assert a[5] is True  # revoked flag still set

    # Attacker (even holding the owner key) re-lists the same kyaId.
    with pytest.raises(TransactionFailed):
        reg.functions.list(kya_id, b"\x11" * 32, agent, b"\xbb" * 32).transact(
            {"from": owner}
        )

    # State untouched by the failed re-list.
    a = reg.functions.byKya(kya_id).call()
    assert a[5] is True and a[4] is False
    assert a[2] == b"\xaa" * 32  # recordHash not overwritten


def test_relist_reverts_even_without_prior_verify(env):
    """Guard applies to revoked-never-verified listings too."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x22" * 32
    listed(reg, w3, kya_id, owner, b"\xaa" * 32)
    reg.functions.revoke(kya_id, b"\xaa" * 32).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        reg.functions.list(kya_id, b"\x33" * 32, agent, b"\xcc" * 32).transact(
            {"from": owner}
        )


def test_revoke_unlisted_kyaid_reverts(env):
    """revoke() of a never-listed kyaId reverts: no kyaId burning."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    with pytest.raises(TransactionFailed):
        reg.functions.revoke(b"\x99" * 32, b"\xaa" * 32).transact({"from": owner})
    assert reg.functions.everRevoked(b"\x99" * 32).call() is False


def test_non_owner_cannot_list_or_revoke(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x44" * 32
    with pytest.raises(TransactionFailed):
        reg.functions.list(kya_id, b"\x11" * 32, agent, b"\xaa" * 32).transact(
            {"from": stranger}
        )
    listed(reg, w3, kya_id, owner, b"\xaa" * 32)
    with pytest.raises(TransactionFailed):
        reg.functions.revoke(kya_id, b"\xaa" * 32).transact({"from": stranger})


# ---------------------------------------------------------------------------
# Timelocked re-verification
# ---------------------------------------------------------------------------


def test_finalize_before_delay_reverts(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x55" * 32
    listed(reg, w3, kya_id, owner, b"\xaa" * 32)
    reg.functions.revoke(kya_id, b"\xaa" * 32).transact({"from": owner})
    reg.functions.requestReverification(kya_id).transact({"from": owner})
    # Immediate finalize: delay not elapsed.
    with pytest.raises(TransactionFailed):
        reg.functions.finalizeReverification(
            kya_id, b"\x11" * 32, agent, b"\xdd" * 32
        ).transact({"from": owner})
    a = reg.functions.byKya(kya_id).call()
    assert a[5] is True  # still revoked


def test_finalize_without_request_reverts(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x66" * 32
    listed(reg, w3, kya_id, owner, b"\xaa" * 32)
    reg.functions.revoke(kya_id, b"\xaa" * 32).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        reg.functions.finalizeReverification(
            kya_id, b"\x11" * 32, agent, b"\xdd" * 32
        ).transact({"from": owner})


def test_request_on_never_revoked_reverts(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x77" * 32
    listed(reg, w3, kya_id, owner, b"\xaa" * 32)
    with pytest.raises(TransactionFailed):
        reg.functions.requestReverification(kya_id).transact({"from": owner})


def test_reverification_succeeds_after_delay(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x88" * 32
    listed(reg, w3, kya_id, owner, b"\xaa" * 32)
    reg.functions.verify(kya_id, b"\xaa" * 32).transact({"from": owner})
    reg.functions.revoke(kya_id, b"\xaa" * 32).transact({"from": owner})
    reg.functions.requestReverification(kya_id).transact({"from": owner})

    travel(tester, w3, REVERIFY_DELAY + 60)

    reg.functions.finalizeReverification(
        kya_id, b"\x11" * 32, agent, b"\xdd" * 32
    ).transact({"from": owner})
    a = reg.functions.byKya(kya_id).call()
    assert a[5] is False  # revoked cleared
    assert a[4] is False  # fresh record starts UNverified: must pass verify() again
    assert a[2] == b"\xdd" * 32
    assert reg.functions.reverifyRequestedAt(kya_id).call() == 0
    # The kyaId stays marked ever-revoked: each revival needs a fresh cycle.
    assert reg.functions.everRevoked(kya_id).call() is True
    with pytest.raises(TransactionFailed):
        reg.functions.list(kya_id, b"\x11" * 32, agent, b"\xee" * 32).transact(
            {"from": owner}
        )
    # ...and the re-verified record can complete verify() again.
    reg.functions.verify(kya_id, b"\xdd" * 32).transact({"from": owner})
    assert reg.functions.byKya(kya_id).call()[4] is True


def test_second_revive_needs_fresh_timelocked_cycle(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x99" * 32
    listed(reg, w3, kya_id, owner, b"\xaa" * 32)
    reg.functions.revoke(kya_id, b"\xaa" * 32).transact({"from": owner})

    # First revival cycle.
    reg.functions.requestReverification(kya_id).transact({"from": owner})
    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(
        kya_id, b"\x11" * 32, agent, b"\xdd" * 32
    ).transact({"from": owner})

    # Revoked again: direct re-list still blocked, and finalize without a
    # fresh request reverts.
    reg.functions.revoke(kya_id, b"\xdd" * 32).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        reg.functions.list(kya_id, b"\x11" * 32, agent, b"\xff" * 32).transact(
            {"from": owner}
        )
    with pytest.raises(TransactionFailed):
        reg.functions.finalizeReverification(
            kya_id, b"\x11" * 32, agent, b"\xff" * 32
        ).transact({"from": owner})

    # Fresh cycle works.
    reg.functions.requestReverification(kya_id).transact({"from": owner})
    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(
        kya_id, b"\x11" * 32, agent, b"\xff" * 32
    ).transact({"from": owner})
    assert reg.functions.byKya(kya_id).call()[5] is False


# ---------------------------------------------------------------------------
# Two-step owner rotation
# ---------------------------------------------------------------------------


def test_non_owner_cannot_nominate(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    with pytest.raises(TransactionFailed):
        reg.functions.nominateOwner(nominee).transact({"from": stranger})
    assert reg.functions.pendingOwner().call() == "0x0000000000000000000000000000000000000000"


def test_nominate_zero_address_or_self_reverts(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    with pytest.raises(TransactionFailed):
        reg.functions.nominateOwner("0x0000000000000000000000000000000000000000").transact(
            {"from": owner}
        )
    with pytest.raises(TransactionFailed):
        reg.functions.nominateOwner(owner).transact({"from": owner})


def test_two_step_rotation(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\xaa" * 32

    # Step 1: nomination does NOT change ownership.
    txh = reg.functions.nominateOwner(nominee).transact({"from": owner})
    assert reg.functions.owner().call() == owner
    assert reg.functions.pendingOwner().call() == nominee

    # A third party cannot accept on the nominee's behalf.
    with pytest.raises(TransactionFailed):
        reg.functions.acceptOwnership().transact({"from": stranger})
    assert reg.functions.owner().call() == owner

    # Step 2: the nominee accepts.
    reg.functions.acceptOwnership().transact({"from": nominee})
    assert reg.functions.owner().call() == nominee
    assert reg.functions.pendingOwner().call() == "0x0000000000000000000000000000000000000000"

    # Old owner key is dead for owner actions; new owner works.
    with pytest.raises(TransactionFailed):
        reg.functions.list(kya_id, b"\x11" * 32, agent, b"\xaa" * 32).transact(
            {"from": owner}
        )
    listed(reg, w3, kya_id, nominee, b"\xaa" * 32)
    assert reg.functions.byKya(kya_id).call()[2] == b"\xaa" * 32


def test_accept_without_nomination_reverts(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    with pytest.raises(TransactionFailed):
        reg.functions.acceptOwnership().transact({"from": nominee})


def test_cancel_nomination(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    reg.functions.nominateOwner(nominee).transact({"from": owner})
    reg.functions.cancelNomination().transact({"from": owner})
    assert reg.functions.pendingOwner().call() == "0x0000000000000000000000000000000000000000"
    with pytest.raises(TransactionFailed):
        reg.functions.acceptOwnership().transact({"from": nominee})
    # Non-owner cannot cancel.
    reg.functions.nominateOwner(nominee).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        reg.functions.cancelNomination().transact({"from": stranger})


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _event_args(reg, event_name, receipt):
    return [dict(e["args"]) for e in getattr(reg.events, event_name)().process_receipt(receipt)]


def test_owner_action_events(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    txh = reg.functions.nominateOwner(nominee).transact({"from": owner})
    evts = _event_args(reg, "OwnerNominated", w3.eth.get_transaction_receipt(txh))
    assert len(evts) == 1
    assert evts[0]["currentOwner"] == owner and evts[0]["nominee"] == nominee

    txh = reg.functions.acceptOwnership().transact({"from": nominee})
    evts = _event_args(reg, "OwnershipTransferred", w3.eth.get_transaction_receipt(txh))
    assert len(evts) == 1
    assert evts[0]["previousOwner"] == owner and evts[0]["newOwner"] == nominee


def test_revocation_and_reverification_events(env):
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\xcc" * 32
    txh = reg.functions.list(kya_id, b"\x11" * 32, agent, b"\xaa" * 32).transact(
        {"from": owner}
    )
    evts = _event_args(reg, "Listed", w3.eth.get_transaction_receipt(txh))
    assert len(evts) == 1 and evts[0]["kyaId"] == kya_id

    txh = reg.functions.revoke(kya_id, b"\xaa" * 32).transact({"from": owner})
    evts = _event_args(reg, "Revoked", w3.eth.get_transaction_receipt(txh))
    assert len(evts) == 1 and evts[0]["kyaId"] == kya_id

    txh = reg.functions.requestReverification(kya_id).transact({"from": owner})
    evts = _event_args(reg, "ReverificationRequested", w3.eth.get_transaction_receipt(txh))
    assert len(evts) == 1
    assert evts[0]["kyaId"] == kya_id
    assert evts[0]["readyAt"] - evts[0]["requestedAt"] == REVERIFY_DELAY

    travel(tester, w3, REVERIFY_DELAY + 60)
    txh = reg.functions.finalizeReverification(
        kya_id, b"\x11" * 32, agent, b"\xdd" * 32
    ).transact({"from": owner})
    evts = _event_args(reg, "Reverified", w3.eth.get_transaction_receipt(txh))
    assert len(evts) == 1 and evts[0]["kyaId"] == kya_id


# ---------------------------------------------------------------------------
# Honest flow unaffected
# ---------------------------------------------------------------------------


def test_honest_list_verify_update_flow(env):
    """Un-revoked kyaIds keep the original list/verify/update behavior."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\xdd" * 32
    listed(reg, w3, kya_id, owner, b"\xaa" * 32)
    assert reg.functions.everRevoked(kya_id).call() is False
    reg.functions.verify(kya_id, b"\xaa" * 32).transact({"from": owner})
    a = reg.functions.byKya(kya_id).call()
    assert a[4] is True and a[5] is False
    # Owner can still update the record of an active attestation.
    listed(reg, w3, kya_id, owner, b"\xbb" * 32)
    a = reg.functions.byKya(kya_id).call()
    assert a[2] == b"\xbb" * 32 and a[5] is False


# ---------------------------------------------------------------------------
# Per-actor tombstone (adversarial-review rework of W-10)
# ---------------------------------------------------------------------------


def test_new_kyaid_same_actor_cannot_verify_instantly(env):
    """Reviewer PoC 1: revoke KYA1 -> list+verify KYA2 (SAME agent hash and
    wallet) back-to-back MUST NOT yield a verified attestation."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya1, kya2 = b"\x11" * 32, b"\x22" * 32
    ah = b"\xaa" * 32
    reg.functions.list(kya1, ah, agent, b"\xaa" * 32).transact({"from": owner})
    reg.functions.verify(kya1, b"\xaa" * 32).transact({"from": owner})
    reg.functions.revoke(kya1, b"\xaa" * 32).transact({"from": owner})

    # The actor's identities are tombstoned and the pointer is cleared.
    assert reg.functions.revokedAgent(ah).call() is True
    assert reg.functions.revokedPrincipal(agent).call() is True
    assert reg.functions.agentToKya(ah).call() == b"\x00" * 32

    # Fresh kyaId, same actor: list() succeeds (record visible on-chain)...
    reg.functions.list(kya2, ah, agent, b"\xbb" * 32).transact({"from": owner})
    assert reg.functions.byKya(kya2).call()[4] is False  # ...but unverified.

    # ...and verify() MUST revert: the actor is tombstoned.
    with pytest.raises(TransactionFailed):
        reg.functions.verify(kya2, b"\xbb" * 32).transact({"from": owner})
    a = reg.functions.byKya(kya2).call()
    assert a[4] is False and a[5] is False


def test_tombstone_fires_on_either_identity_component(env):
    """Same wallet + fresh agent hash, and same agent hash + fresh wallet,
    are both blocked from instant verification; a fully fresh actor is not."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya1 = b"\x31" * 32
    reg.functions.list(kya1, b"\xaa" * 32, agent, b"\xaa" * 32).transact({"from": owner})
    reg.functions.revoke(kya1, b"\xaa" * 32).transact({"from": owner})

    # Same wallet, fresh agent hash.
    reg.functions.list(b"\x32" * 32, b"\xbb" * 32, agent, b"\x01" * 32).transact(
        {"from": owner}
    )
    with pytest.raises(TransactionFailed):
        reg.functions.verify(b"\x32" * 32, b"\x01" * 32).transact({"from": owner})

    # Same agent hash, fresh wallet.
    reg.functions.list(b"\x33" * 32, b"\xaa" * 32, nominee, b"\x02" * 32).transact(
        {"from": owner}
    )
    with pytest.raises(TransactionFailed):
        reg.functions.verify(b"\x33" * 32, b"\x02" * 32).transact({"from": owner})

    # Fresh agent hash AND fresh wallet: unaffected, verifies fine.
    reg.functions.list(b"\x34" * 32, b"\xcc" * 32, stranger, b"\x03" * 32).transact(
        {"from": owner}
    )
    reg.functions.verify(b"\x34" * 32, b"\x03" * 32).transact({"from": owner})
    assert reg.functions.byKya(b"\x34" * 32).call()[4] is True


def test_tombstoned_actor_readmitted_after_timelocked_cycle_on_new_kyaid(env):
    """The 48h path re-admits a tombstoned actor under a FRESH kyaId and
    clears the tombstone for exactly the admitted identities."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya1, kya2 = b"\x41" * 32, b"\x42" * 32
    ah = b"\xaa" * 32
    reg.functions.list(kya1, ah, agent, b"\xaa" * 32).transact({"from": owner})
    reg.functions.revoke(kya1, b"\xaa" * 32).transact({"from": owner})

    # Re-admission under a fresh kyaId: list, request, wait 48h, finalize.
    reg.functions.list(kya2, ah, agent, b"\xbb" * 32).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        reg.functions.finalizeReverification(kya2, ah, agent, b"\xbb" * 32).transact(
            {"from": owner}
        )
    with pytest.raises(TransactionFailed):
        reg.functions.verify(kya2, b"\xbb" * 32).transact({"from": owner})

    reg.functions.requestReverification(kya2).transact({"from": owner})
    # verify() before the delay elapses still reverts.
    with pytest.raises(TransactionFailed):
        reg.functions.verify(kya2, b"\xbb" * 32).transact({"from": owner})

    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(kya2, ah, agent, b"\xcc" * 32).transact(
        {"from": owner}
    )

    assert reg.functions.revokedAgent(ah).call() is False
    assert reg.functions.revokedPrincipal(agent).call() is False
    a = reg.functions.byKya(kya2).call()
    assert a[5] is False and a[4] is False  # live but unverified

    # Now verify() succeeds: the actor is re-admitted.
    reg.functions.verify(kya2, b"\xcc" * 32).transact({"from": owner})
    assert reg.functions.byKya(kya2).call()[4] is True
    assert reg.functions.agentToKya(ah).call() == kya2
    # The old kyaId stays burned.
    assert reg.functions.everRevoked(kya1).call() is True


def test_second_revocation_re_tombstones_actor(env):
    """After a timelocked re-admission, a fresh revoke re-tombstones the
    actor: instant re-verification under another new kyaId is blocked."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya1, kya2 = b"\x81" * 32, b"\x82" * 32
    ah = b"\xaa" * 32
    reg.functions.list(kya1, ah, agent, b"\xaa" * 32).transact({"from": owner})
    reg.functions.revoke(kya1, b"\xaa" * 32).transact({"from": owner})
    reg.functions.requestReverification(kya1).transact({"from": owner})
    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(kya1, ah, agent, b"\xbb" * 32).transact(
        {"from": owner}
    )
    reg.functions.verify(kya1, b"\xbb" * 32).transact({"from": owner})
    assert reg.functions.revokedAgent(ah).call() is False

    # Revoked again for cause: tombstone is back, fresh-kyaId verify blocked.
    reg.functions.revoke(kya1, b"\xbb" * 32).transact({"from": owner})
    assert reg.functions.revokedAgent(ah).call() is True
    reg.functions.list(kya2, ah, agent, b"\xcc" * 32).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        reg.functions.verify(kya2, b"\xcc" * 32).transact({"from": owner})
    assert reg.functions.byKya(kya2).call()[4] is False


def test_reverification_path_rejects_live_attestation(env):
    """Reviewer PoC 3: request/finalize on a LIVE (revived) attestation must
    revert -- the timelocked path is not a silent live-record rewrite."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x51" * 32
    reg.functions.list(kya_id, b"\xa1" * 32, agent, b"\x01" * 32).transact(
        {"from": owner}
    )
    reg.functions.revoke(kya_id, b"\x01" * 32).transact({"from": owner})
    reg.functions.requestReverification(kya_id).transact({"from": owner})
    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(kya_id, b"\xa1" * 32, agent, b"\x02" * 32).transact(
        {"from": owner}
    )
    reg.functions.verify(kya_id, b"\x02" * 32).transact({"from": owner})

    # Attestation is live now: the re-verification path must refuse it.
    with pytest.raises(TransactionFailed):
        reg.functions.requestReverification(kya_id).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        reg.functions.finalizeReverification(
            kya_id, b"\xb2" * 32, nominee, b"\x03" * 32
        ).transact({"from": owner})

    # State untouched: principal and hash NOT swapped, still verified.
    a = reg.functions.byKya(kya_id).call()
    assert a[0] == b"\xa1" * 32 and a[1] == agent
    assert a[4] is True and a[5] is False
    assert reg.functions.agentToKya(b"\xa1" * 32).call() == kya_id


def test_agent_hash_change_clears_stale_pointer(env):
    """Secondary (b): a list() update moving to a new agent hash clears the
    old agentToKya pointer; revoke() clears its pointer too."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x61" * 32
    old_h, new_h, final_h = b"\x61" * 32, b"\x62" * 32, b"\x63" * 32
    reg.functions.list(kya_id, old_h, agent, b"\xaa" * 32).transact({"from": owner})
    assert reg.functions.agentToKya(old_h).call() == kya_id

    # Honest update to a new agent hash.
    reg.functions.list(kya_id, new_h, agent, b"\xbb" * 32).transact({"from": owner})
    assert reg.functions.agentToKya(old_h).call() == b"\x00" * 32
    assert reg.functions.agentToKya(new_h).call() == kya_id

    # revoke() clears the pointer as well.
    reg.functions.revoke(kya_id, b"\xbb" * 32).transact({"from": owner})
    assert reg.functions.agentToKya(new_h).call() == b"\x00" * 32

    # finalize() with another new hash: no stale pointer left behind.
    reg.functions.requestReverification(kya_id).transact({"from": owner})
    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(kya_id, final_h, agent, b"\xcc" * 32).transact(
        {"from": owner}
    )
    assert reg.functions.agentToKya(new_h).call() == b"\x00" * 32
    assert reg.functions.agentToKya(final_h).call() == kya_id


def test_update_with_new_agent_hash_unverifies(env):
    """Corrected semantic (re-review finding 1): changing the agent hash or
    the principal via list() is an identity change and sets verified=false
    -- a grafted identity cannot inherit the old record's VERIFIED flag.
    The pointer is still repointed; the honest recordHash-only path is
    unaffected (see test_record_hash_only_update_keeps_verified)."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x71" * 32
    old_h, new_h = b"\x71" * 32, b"\x72" * 32
    reg.functions.list(kya_id, old_h, agent, b"\xaa" * 32).transact({"from": owner})
    reg.functions.verify(kya_id, b"\xaa" * 32).transact({"from": owner})
    # Identity change (agent hash): verified drops, pointer repoints.
    reg.functions.list(kya_id, new_h, agent, b"\xbb" * 32).transact({"from": owner})
    a = reg.functions.byKya(kya_id).call()
    assert a[4] is False and a[5] is False  # identity change => unverified
    assert a[2] == b"\xbb" * 32
    assert reg.functions.agentToKya(old_h).call() == b"\x00" * 32
    assert reg.functions.agentToKya(new_h).call() == kya_id
    # Identity change (principal only) also unverifies.
    reg.functions.list(kya_id, new_h, nominee, b"\xcc" * 32).transact({"from": owner})
    a = reg.functions.byKya(kya_id).call()
    assert a[4] is False and a[1] == nominee
    assert _principal_ids(reg, agent) == []
    assert _principal_ids(reg, nominee) == [kya_id]
    assert reg.functions.revokedAgent(new_h).call() is False
    assert reg.functions.revokedPrincipal(agent).call() is False
    assert reg.functions.revokedPrincipal(nominee).call() is False


def test_record_hash_only_update_keeps_verified(env):
    """Honest path preserved: list() touching only the record hash keeps
    verified=true."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\xd1" * 32
    reg.functions.list(kya_id, b"\x71" * 32, agent, b"\xaa" * 32).transact({"from": owner})
    reg.functions.verify(kya_id, b"\xaa" * 32).transact({"from": owner})
    reg.functions.list(kya_id, b"\x71" * 32, agent, b"\xbb" * 32).transact({"from": owner})
    a = reg.functions.byKya(kya_id).call()
    assert a[4] is True and a[5] is False
    assert a[2] == b"\xbb" * 32
    # The principal index is not duplicated by the metadata-only update.
    assert _principal_ids(reg, agent) == [kya_id]


# ---------------------------------------------------------------------------
# Re-review delta: list()-graft guard + revoke() sibling sweep
# ---------------------------------------------------------------------------


def test_list_graft_of_burned_wallet_unverifies(env):
    """Reviewer PoC (finding 1, wallet graft): list(kyaV,Hv,Wv)->verify;
    list(kyaB,Hb,Wb)->verify->revoke(kyaB) burns Hb,Wb; grafting the live
    VERIFIED kyaV onto the burned wallet Wb must NOT keep verified=true,
    and verify() must revert while Wb is tombstoned."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kyaV, kyaB = b"\xa1" * 32, b"\xa2" * 32
    Hv, Hb = b"\x51" * 32, b"\x52" * 32
    Wv, Wb = nominee, stranger
    reg.functions.list(kyaV, Hv, Wv, b"\xaa" * 32).transact({"from": owner})
    reg.functions.verify(kyaV, b"\xaa" * 32).transact({"from": owner})
    reg.functions.list(kyaB, Hb, Wb, b"\xbb" * 32).transact({"from": owner})
    reg.functions.verify(kyaB, b"\xbb" * 32).transact({"from": owner})
    reg.functions.revoke(kyaB, b"\xbb" * 32).transact({"from": owner})
    assert reg.functions.revokedPrincipal(Wb).call() is True
    assert reg.functions.revokedAgent(Hb).call() is True

    # Graft: re-point the live VERIFIED attestation at the burned wallet.
    reg.functions.list(kyaV, Hv, Wb, b"\xcc" * 32).transact({"from": owner})
    a = reg.functions.byKya(kyaV).call()
    assert a[4] is False  # identity change => unverified, instantly
    assert a[5] is False  # not revoked, just unverified
    assert a[1] == Wb
    # Tombstone gate: verify() reverts -- no 48h, no Verified event.
    with pytest.raises(TransactionFailed):
        reg.functions.verify(kyaV, b"\xcc" * 32).transact({"from": owner})
    assert reg.functions.byKya(kyaV).call()[4] is False
    # The principal index moved the kyaId with the identity.
    assert _principal_ids(reg, Wv) == []
    assert set(_principal_ids(reg, Wb)) == {kyaB, kyaV}


def test_list_graft_of_burned_agent_hash_unverifies(env):
    """Reviewer PoC (finding 1, agent-hash graft): same attack via a burned
    agent hash instead of a burned wallet."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kyaV, kyaB = b"\xa3" * 32, b"\xa4" * 32
    Hv, Hb = b"\x53" * 32, b"\x54" * 32
    Wv, Wb = nominee, stranger
    reg.functions.list(kyaV, Hv, Wv, b"\xaa" * 32).transact({"from": owner})
    reg.functions.verify(kyaV, b"\xaa" * 32).transact({"from": owner})
    reg.functions.list(kyaB, Hb, Wb, b"\xbb" * 32).transact({"from": owner})
    reg.functions.verify(kyaB, b"\xbb" * 32).transact({"from": owner})
    reg.functions.revoke(kyaB, b"\xbb" * 32).transact({"from": owner})

    # Graft: re-point the live VERIFIED attestation at the burned hash.
    reg.functions.list(kyaV, Hb, Wv, b"\xcc" * 32).transact({"from": owner})
    a = reg.functions.byKya(kyaV).call()
    assert a[4] is False
    assert a[5] is False
    assert a[0] == Hb
    assert reg.functions.agentToKya(Hb).call() == kyaV
    with pytest.raises(TransactionFailed):
        reg.functions.verify(kyaV, b"\xcc" * 32).transact({"from": owner})


def test_revoke_unverifies_hash_sharing_sibling(env):
    """Reviewer PoC (finding 2a): list(k1,H,W1)+list(k2,H,W2), verify both,
    revoke(k1) burns H,W1 -- the live VERIFIED sibling k2 sharing the burned
    hash must be unverified, but keep its agentToKya pointer (verify() stays
    tombstone-gated)."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    k1, k2 = b"\xb1" * 32, b"\xb2" * 32
    H = b"\x61" * 32
    W1, W2 = agent, nominee
    reg.functions.list(k1, H, W1, b"\x01" * 32).transact({"from": owner})
    reg.functions.list(k2, H, W2, b"\x02" * 32).transact({"from": owner})
    reg.functions.verify(k1, b"\x01" * 32).transact({"from": owner})
    reg.functions.verify(k2, b"\x02" * 32).transact({"from": owner})
    assert reg.functions.byKya(k2).call()[4] is True
    assert reg.functions.agentToKya(H).call() == k2  # last writer wins

    reg.functions.revoke(k1, b"\x01" * 32).transact({"from": owner})
    assert reg.functions.revokedAgent(H).call() is True
    assert reg.functions.revokedPrincipal(W1).call() is True

    a2 = reg.functions.byKya(k2).call()
    assert a2[4] is False and a2[5] is False  # unverified, not revoked
    assert reg.functions.agentToKya(H).call() == k2  # pointer kept
    # The sibling cannot re-verify while the hash is tombstoned.
    with pytest.raises(TransactionFailed):
        reg.functions.verify(k2, b"\x02" * 32).transact({"from": owner})
    # The revoked attestation's own state is intact.
    a1 = reg.functions.byKya(k1).call()
    assert a1[4] is False and a1[5] is True


def test_revoke_unverifies_wallet_sharing_sibling(env):
    """Reviewer PoC (finding 2b): the burned wallet's other verified
    kyaIds (different agent hash) are found through the principalToKya
    index and unverified; an unrelated verified attestation is untouched."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    k1, k3, k4 = b"\xb3" * 32, b"\xb4" * 32, b"\xb5" * 32
    H1, H3, H4 = b"\x61" * 32, b"\x63" * 32, b"\x64" * 32
    W1, W4 = agent, stranger
    # k3 shares the wallet W1 with k1 under a different hash; k4 unrelated.
    reg.functions.list(k1, H1, W1, b"\x01" * 32).transact({"from": owner})
    reg.functions.list(k3, H3, W1, b"\x03" * 32).transact({"from": owner})
    reg.functions.list(k4, H4, W4, b"\x04" * 32).transact({"from": owner})
    for kid in (k1, k3, k4):
        reg.functions.verify(kid, b"\x00" * 32).transact({"from": owner})
    assert set(_principal_ids(reg, W1)) == {k1, k3}

    reg.functions.revoke(k1, b"\x01" * 32).transact({"from": owner})

    a3 = reg.functions.byKya(k3).call()
    assert a3[4] is False and a3[5] is False  # unverified, not revoked
    # The wallet-sharing sibling cannot re-verify while W1 is tombstoned.
    with pytest.raises(TransactionFailed):
        reg.functions.verify(k3, b"\x03" * 32).transact({"from": owner})
    # Unrelated verified attestation untouched.
    assert reg.functions.byKya(k4).call()[4] is True


def test_revoke_sibling_sweep_covers_hash_and_wallet_together(env):
    """Full finding-2 shape: one revoke unverifies BOTH the hash-sharing
    sibling and the wallet-sharing sibling at once."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    k1, k2, k3 = b"\xc1" * 32, b"\xc2" * 32, b"\xc3" * 32
    H, H3 = b"\x71" * 32, b"\x73" * 32
    W1, W2 = agent, nominee
    reg.functions.list(k1, H, W1, b"\x01" * 32).transact({"from": owner})
    reg.functions.list(k2, H, W2, b"\x02" * 32).transact({"from": owner})
    reg.functions.list(k3, H3, W1, b"\x03" * 32).transact({"from": owner})
    for kid in (k1, k2, k3):
        reg.functions.verify(kid, b"\x00" * 32).transact({"from": owner})

    reg.functions.revoke(k1, b"\x01" * 32).transact({"from": owner})

    assert reg.functions.byKya(k2).call()[4] is False  # hash sibling
    assert reg.functions.byKya(k3).call()[4] is False  # wallet sibling
    assert reg.functions.agentToKya(H).call() == k2  # pointer kept for k2


def test_readmission_after_48h_allows_verify(env):
    """(iv) The timelocked path still works end-to-end: after 48h and
    finalize, verify() succeeds on the admitted identities."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    kya_id = b"\x91" * 32
    ah = b"\xaa" * 32
    reg.functions.list(kya_id, ah, agent, b"\xaa" * 32).transact({"from": owner})
    reg.functions.revoke(kya_id, b"\xaa" * 32).transact({"from": owner})
    reg.functions.requestReverification(kya_id).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        reg.functions.verify(kya_id, b"\xaa" * 32).transact({"from": owner})

    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(kya_id, ah, agent, b"\xbb" * 32).transact(
        {"from": owner}
    )
    reg.functions.verify(kya_id, b"\xbb" * 32).transact({"from": owner})
    assert reg.functions.byKya(kya_id).call()[4] is True


# ---------------------------------------------------------------------------
# Round-3 delta: hashToKya index + full hash-sibling sweep in revoke()
# ---------------------------------------------------------------------------


def test_revoke_unverifies_all_three_hash_sharers_poc_a(env):
    """Round-3 CONFIRMED residual (PoC-A): with list(k1,H,W1),
    list(k2,H,W2), list(k3,H,W3) ALL verified, revoke(k1) must unverify
    ALL THREE. The re-review code only unverified the agentToKya pointer
    target (k3, last writer wins), leaving the non-pointer sibling k2
    verified=true while revokedAgent[H] was set."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    k1, k2, k3 = b"\xd1" * 32, b"\xd2" * 32, b"\xd3" * 32
    H = b"\xe1" * 32
    W1, W2, W3 = agent, nominee, stranger
    reg.functions.list(k1, H, W1, b"\x01" * 32).transact({"from": owner})
    reg.functions.list(k2, H, W2, b"\x02" * 32).transact({"from": owner})
    reg.functions.list(k3, H, W3, b"\x03" * 32).transact({"from": owner})
    for kid in (k1, k2, k3):
        reg.functions.verify(kid, b"\x00" * 32).transact({"from": owner})
    assert set(_hash_ids(reg, H)) == {k1, k2, k3}
    assert reg.functions.agentToKya(H).call() == k3  # last writer wins
    assert reg.functions.revokedAgent(H).call() is False

    txh = reg.functions.revoke(k1, b"\x01" * 32).transact({"from": owner})
    receipt = w3.eth.get_transaction_receipt(txh)

    # ALL THREE are unverified now: the revoked target, the pointer
    # target, AND the non-pointer sibling that used to slip through.
    for kid, revoked in ((k1, True), (k2, False), (k3, False)):
        a = reg.functions.byKya(kid).call()
        assert a[4] is False, kid
        assert a[5] is revoked, kid
    assert reg.functions.revokedAgent(H).call() is True
    assert reg.functions.revokedPrincipal(W1).call() is True
    # The hash pointer keeps its semantics: it resolved to k3 (not the
    # revoked kyaId), so it is kept -- verify() stays tombstone-gated.
    assert reg.functions.agentToKya(H).call() == k3
    with pytest.raises(TransactionFailed):
        reg.functions.verify(k3, b"\x03" * 32).transact({"from": owner})
    with pytest.raises(TransactionFailed):
        reg.functions.verify(k2, b"\x02" * 32).transact({"from": owner})

    # Per-sibling events: exactly the two siblings that actually flipped.
    evts = _event_args(reg, "SiblingUnverified", receipt)
    assert len(evts) == 2
    by_id = {e["kyaId"]: e for e in evts}
    assert set(by_id) == {k2, k3}
    assert by_id[k2]["agentIdHash"] == H and by_id[k2]["principal"] == W2
    assert by_id[k3]["agentIdHash"] == H and by_id[k3]["principal"] == W3

    # No verified attestation names the burned hash.
    for kid in _hash_ids(reg, H):
        assert reg.functions.byKya(kid).call()[4] is False


def test_graft_repoint_then_revoke_sweeps_earlier_sharers_poc_a2(env):
    """PoC-A2: graft-repoint onto a hash, then revoke the grafter -- the
    EARLIER hash-sharers must be unverified. Exercises the hashToKya
    bucket move on graft: the grafter must leave H1's bucket and join
    H2's, or the sweep misses the earlier sharers (stale bucket) or
    sweeps a hash the grafter left."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    k1, k2, k3 = b"\xe1" * 32, b"\xe2" * 32, b"\xe3" * 32
    H1, H2 = b"\xf1" * 32, b"\xf2" * 32
    W1, W2, W3 = agent, nominee, stranger
    reg.functions.list(k1, H1, W1, b"\x01" * 32).transact({"from": owner})
    reg.functions.list(k2, H2, W2, b"\x02" * 32).transact({"from": owner})
    reg.functions.list(k3, H2, W3, b"\x03" * 32).transact({"from": owner})
    for kid in (k1, k2, k3):
        reg.functions.verify(kid, b"\x00" * 32).transact({"from": owner})

    # Graft: re-point k1 onto H2. Identity change => unverified + event,
    # and the index moves k1 from H1's bucket to H2's.
    txh = reg.functions.list(k1, H2, W1, b"\x11" * 32).transact({"from": owner})
    graft_evts = _event_args(
        reg, "SiblingUnverified", w3.eth.get_transaction_receipt(txh)
    )
    assert len(graft_evts) == 1
    assert graft_evts[0]["kyaId"] == k1
    assert graft_evts[0]["agentIdHash"] == H2
    assert graft_evts[0]["principal"] == W1
    assert reg.functions.byKya(k1).call()[4] is False
    assert _hash_ids(reg, H1) == []
    assert set(_hash_ids(reg, H2)) == {k1, k2, k3}
    assert reg.functions.agentToKya(H1).call() == b"\x00" * 32
    assert reg.functions.agentToKya(H2).call() == k1

    # The grafter re-verifies on the new (untombstoned) hash...
    reg.functions.verify(k1, b"\x11" * 32).transact({"from": owner})
    assert reg.functions.byKya(k1).call()[4] is True

    # ...then is revoked for cause: burns H2 and W1. The earlier sharers
    # k2, k3 must be unverified; the grafted-away hash H1 stays clean.
    txh = reg.functions.revoke(k1, b"\x11" * 32).transact({"from": owner})
    sweep_evts = _event_args(
        reg, "SiblingUnverified", w3.eth.get_transaction_receipt(txh)
    )
    assert {e["kyaId"] for e in sweep_evts} == {k2, k3}
    for kid in (k2, k3):
        a = reg.functions.byKya(kid).call()
        assert a[4] is False and a[5] is False
    a1 = reg.functions.byKya(k1).call()
    assert a1[4] is False and a1[5] is True
    assert reg.functions.revokedAgent(H2).call() is True
    assert reg.functions.revokedPrincipal(W1).call() is True
    assert reg.functions.revokedAgent(H1).call() is False
    # No verified attestation names the burned hash H2.
    for kid in _hash_ids(reg, H2):
        assert reg.functions.byKya(kid).call()[4] is False


def test_hash_index_integrity_on_hash_moves(env):
    """hashToKya bucket moves: a hash change moves the kyaId between
    buckets with no duplicates and no stale entries; metadata-only
    updates and principal-only moves leave the hash buckets alone."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    k1, k2 = b"\xf1" * 32, b"\xf2" * 32
    H1, H2, H3 = b"\x01" * 32, b"\x02" * 32, b"\x03" * 32
    reg.functions.list(k1, H1, agent, b"\xaa" * 32).transact({"from": owner})
    assert _hash_ids(reg, H1) == [k1]

    # recordHash-only update: no duplicate push.
    reg.functions.list(k1, H1, agent, b"\xbb" * 32).transact({"from": owner})
    assert _hash_ids(reg, H1) == [k1]

    # Hash move: k1 leaves H1's bucket, joins H2's.
    reg.functions.list(k1, H2, agent, b"\xcc" * 32).transact({"from": owner})
    assert _hash_ids(reg, H1) == []
    assert _hash_ids(reg, H2) == [k1]

    # Principal-only move: hash bucket untouched.
    reg.functions.list(k1, H2, nominee, b"\xdd" * 32).transact({"from": owner})
    assert _hash_ids(reg, H2) == [k1]

    # Second hash move + a second kyaId joining the same bucket.
    reg.functions.list(k1, H3, nominee, b"\xee" * 32).transact({"from": owner})
    reg.functions.list(k2, H3, stranger, b"\xff" * 32).transact({"from": owner})
    assert _hash_ids(reg, H2) == []
    assert len(_hash_ids(reg, H3)) == 2  # no duplicates
    assert set(_hash_ids(reg, H3)) == {k1, k2}
    assert reg.functions.agentToKya(H3).call() == k2

    # finalizeReverification also moves the hash bucket.
    reg.functions.revoke(k2, b"\xff" * 32).transact({"from": owner})
    reg.functions.requestReverification(k2).transact({"from": owner})
    travel(tester, w3, REVERIFY_DELAY + 60)
    reg.functions.finalizeReverification(k2, H2, stranger, b"\x10" * 32).transact(
        {"from": owner}
    )
    assert _hash_ids(reg, H3) == [k1]
    assert _hash_ids(reg, H2) == [k2]


def test_sibling_unverified_events_wallet_side(env):
    """Wallet-side sweep emits exactly one SiblingUnverified per sibling
    that actually flips; an already-unverified wallet-sharer emits
    nothing (flip-idempotent); an unrelated attestation is untouched."""
    w3, tester, reg, owner, agent, nominee, stranger = env
    k1, k3, k4, k5 = b"\xa1" * 32, b"\xa3" * 32, b"\xa4" * 32, b"\xa5" * 32
    H1, H3, H4, H5 = b"\x11" * 32, b"\x13" * 32, b"\x14" * 32, b"\x15" * 32
    W1 = agent
    reg.functions.list(k1, H1, W1, b"\x01" * 32).transact({"from": owner})
    reg.functions.list(k3, H3, W1, b"\x03" * 32).transact({"from": owner})
    reg.functions.list(k4, H4, stranger, b"\x04" * 32).transact({"from": owner})
    reg.functions.list(k5, H5, W1, b"\x05" * 32).transact({"from": owner})
    for kid in (k1, k3, k4):
        reg.functions.verify(kid, b"\x00" * 32).transact({"from": owner})
    # k5 shares wallet W1 but was never verified.

    txh = reg.functions.revoke(k1, b"\x01" * 32).transact({"from": owner})
    evts = _event_args(
        reg, "SiblingUnverified", w3.eth.get_transaction_receipt(txh)
    )
    assert len(evts) == 1
    assert evts[0]["kyaId"] == k3
    assert evts[0]["agentIdHash"] == H3
    assert evts[0]["principal"] == W1
    assert reg.functions.byKya(k3).call()[4] is False
    assert reg.functions.byKya(k4).call()[4] is True  # unrelated untouched
    assert reg.functions.byKya(k5).call()[4] is False  # never verified, no event
