"""Auction relayer: id derivation, flags, and poster transactions (mocked)."""

import pytest

from sincor2.onchain import auction_relayer as relayer_mod
from sincor2.onchain.auction_relayer import (
    AUCTION_ID_DOMAIN,
    AuctionRelayer,
    RelayerNotConfiguredError,
    anchor_enabled,
    auction_id_for,
    fund_enabled,
    get_relayer,
)


def test_auction_id_deterministic_and_domain_separated():
    a = auction_id_for("tsk_abc123")
    assert isinstance(a, bytes) and len(a) == 32
    assert a == auction_id_for("tsk_abc123")
    assert a != auction_id_for("tsk_abc124")
    # Domain separation: not a raw keccak of the task id.
    from eth_hash.auto import keccak

    assert a != keccak(b"tsk_abc123")
    assert a == keccak(AUCTION_ID_DOMAIN + b"tsk_abc123")


def test_flags_default_off(monkeypatch):
    monkeypatch.delenv("AUCTION_ONCHAIN_ANCHOR", raising=False)
    monkeypatch.delenv("AUCTION_ONCHAIN_FUND", raising=False)
    assert anchor_enabled() is False
    assert fund_enabled() is False


def test_flags_read_env(monkeypatch):
    monkeypatch.setenv("AUCTION_ONCHAIN_ANCHOR", "1")
    monkeypatch.setenv("AUCTION_ONCHAIN_FUND", "1")
    assert anchor_enabled() is True
    assert fund_enabled() is True


def test_from_env_requires_all_three(monkeypatch):
    monkeypatch.delenv("COMMIT_REVEAL_AUCTION_ADDRESS", raising=False)
    monkeypatch.delenv("AUCTION_RPC_URL", raising=False)
    monkeypatch.delenv("AUCTION_RELAYER_KEY", raising=False)
    with pytest.raises(RelayerNotConfiguredError):
        AuctionRelayer.from_env()
    # get_relayer never raises: misconfiguration is a logged None.
    assert get_relayer() is None


class _FakeFn:
    def __init__(self, w3):
        self._w3 = w3
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self

    def build_transaction(self, params):
        from web3 import Web3

        tx = dict(params)
        tx.setdefault("to", Web3.to_checksum_address("0x" + "dd" * 20))
        tx.setdefault("data", "0x1234")
        return tx


class _FakeFunctions:
    def __init__(self, w3):
        self.openAuction = _FakeFn(w3)
        self.selectWinnerAndFund = _FakeFn(w3)
        self.vickreyResult = _FakeFn(w3)


class _FakeContract:
    def __init__(self, w3):
        self.functions = _FakeFunctions(w3)


class _FakeEth:
    def __init__(self):
        self.sent = []
        self.nonces = {}

    def get_transaction_count(self, addr):
        return self.nonces.get(addr, 0)

    @property
    def gas_price(self):
        return 10**9

    @property
    def chain_id(self):
        return 8453

    def estimate_gas(self, tx):
        return 100000

    def send_raw_transaction(self, raw):
        self.sent.append(raw)
        return b"\x01" * 32

    def wait_for_transaction_receipt(self, tx_hash, timeout=300):
        class R:
            status = 1

        return R()


class _FakeW3:
    def __init__(self):
        self.eth = _FakeEth()

    def contract(self, address=None, abi=None):
        return _FakeContract(self)


@pytest.fixture
def live_relayer(monkeypatch):
    monkeypatch.setenv("COMMIT_REVEAL_AUCTION_ADDRESS",
                       "0x" + "aa" * 20)
    monkeypatch.setenv("AUCTION_RPC_URL", "http://localhost:8545")
    monkeypatch.setenv("AUCTION_RELAYER_KEY", "0x" + "bb" * 32)
    r = AuctionRelayer.from_env()
    fake = _FakeW3()
    r._w3 = fake
    r._auction = fake.contract()
    # Sign with a real local key; nothing is broadcast (fake eth).
    import eth_account

    r._relayer_key = "0x" + "cc" * 32
    r._signer_address = eth_account.Account.from_key(
        r._relayer_key).address
    return r


def test_open_auction_sends_and_returns_id(live_relayer):
    out = live_relayer.open_auction("tsk_xyz", 300, 300)
    assert out["auction_id"] == "0x" + auction_id_for("tsk_xyz").hex()
    assert len(out["tx_hash"]) == 64
    fn = live_relayer._auction.functions.openAuction
    assert fn.calls[0][0][1:] == (300, 300)


def test_select_winner_and_fund_sends_value(live_relayer):
    tx_hash = live_relayer.select_winner_and_fund("tsk_xyz", 10**15)
    assert len(tx_hash) == 64
    fn = live_relayer._auction.functions.selectWinnerAndFund
    assert fn.calls[0][0][1] == 0  # default credit


def test_read_vickrey_none_on_revert(live_relayer, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("NoRevealedBids()")

    monkeypatch.setattr(
        live_relayer._auction.functions, "vickreyResult",
        lambda auction_id: _BoomCall(boom))
    assert live_relayer.read_vickrey("tsk_xyz") is None


class _BoomCall:
    def __init__(self, fn):
        self._fn = fn

    def call(self):
        return self._fn()
