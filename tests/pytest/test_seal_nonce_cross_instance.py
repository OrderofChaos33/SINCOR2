"""W-192/W-198: SealNonceRegistry cross-instance lost update / nonce reuse.

Two instances constructed before either persists must not lose bindings
and must not allow the same (signer, nonce) to bind to two different seals.
"""
import pytest

from sincor2.hierarchical_cnp import NonceReuseError, SealNonceRegistry


class _Seal:
    def __init__(self, agent_address, nonce, seal_id):
        self.agent_address = agent_address
        self.nonce = nonce
        self.seal_id = seal_id


ADDR = "0x" + "aa" * 20
ADDR2 = "0x" + "bb" * 20
SEAL1 = "0x" + "11" * 32
SEAL2 = "0x" + "22" * 32
SEAL3 = "0x" + "33" * 32


def test_cross_instance_distinct_nonces_not_lost(tmp_path):
    path = str(tmp_path / "nonces.json")
    a = SealNonceRegistry(path)
    b = SealNonceRegistry(path)  # constructed before either persists
    a.check_and_record(_Seal(ADDR, 1, SEAL1))
    b.check_and_record(_Seal(ADDR, 2, SEAL2))
    fresh = SealNonceRegistry(path)
    assert fresh.lookup(ADDR, 1) == SEAL1.lower()
    assert fresh.lookup(ADDR, 2) == SEAL2.lower()
    assert len(fresh) == 2


def test_cross_instance_same_nonce_reuse_rejected(tmp_path):
    path = str(tmp_path / "nonces.json")
    a = SealNonceRegistry(path)
    b = SealNonceRegistry(path)
    a.check_and_record(_Seal(ADDR, 7, SEAL1))
    # b has stale snapshot; same nonce, different seal must be rejected
    with pytest.raises(NonceReuseError):
        b.check_and_record(_Seal(ADDR, 7, SEAL2))
    fresh = SealNonceRegistry(path)
    assert fresh.lookup(ADDR, 7) == SEAL1.lower()


def test_cross_instance_same_seal_idempotent(tmp_path):
    path = str(tmp_path / "nonces.json")
    a = SealNonceRegistry(path)
    b = SealNonceRegistry(path)
    a.check_and_record(_Seal(ADDR, 7, SEAL1))
    b.check_and_record(_Seal(ADDR, 7, SEAL1))  # same seal = idempotent
    assert len(SealNonceRegistry(path)) == 1


def test_assert_available_sees_peer_binding(tmp_path):
    path = str(tmp_path / "nonces.json")
    a = SealNonceRegistry(path)
    b = SealNonceRegistry(path)
    a.check_and_record(_Seal(ADDR, 5, SEAL1))
    with pytest.raises(NonceReuseError):
        b.assert_available(_Seal(ADDR, 5, SEAL3))


def test_in_memory_registry_unchanged():
    r = SealNonceRegistry()
    r.check_and_record(_Seal(ADDR, 1, SEAL1))
    with pytest.raises(NonceReuseError):
        r.check_and_record(_Seal(ADDR, 1, SEAL2))
    assert r.lookup(ADDR, 1) == SEAL1.lower()
