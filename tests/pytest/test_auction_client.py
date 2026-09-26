"""Auction contract client — config gating, offline tx encoding, no key handling."""
from __future__ import annotations

import inspect

import pytest

from sincor2.onchain.auction_client import (
    AUCTION_ENV_ADDRESS,
    CHAIN_ID_ENV,
    ESCROW_ENV_ADDRESS,
    RPC_ENV_URL,
    AuctionClient,
    AuctionConfig,
    AuctionNotConfiguredError,
    compute_commitment,
)

AUCTION_ADDR = "0x" + "aa" * 20
ESCROW_ADDR = "0x" + "bb" * 20
SENDER = "0x" + "11" * 20


@pytest.fixture
def env(monkeypatch):
    for var in (AUCTION_ENV_ADDRESS, ESCROW_ENV_ADDRESS, RPC_ENV_URL, CHAIN_ID_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(AUCTION_ENV_ADDRESS, AUCTION_ADDR)
    monkeypatch.setenv(ESCROW_ENV_ADDRESS, ESCROW_ADDR)
    monkeypatch.setenv(RPC_ENV_URL, "http://127.0.0.1:9")  # unroutable: proves offline
    return monkeypatch


@pytest.fixture
def client(env):
    return AuctionClient(AuctionConfig.from_env())


def test_from_env_missing_vars_listed(monkeypatch):
    for var in (AUCTION_ENV_ADDRESS, ESCROW_ENV_ADDRESS, RPC_ENV_URL):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(AuctionNotConfiguredError) as exc:
        AuctionConfig.from_env()
    assert AUCTION_ENV_ADDRESS in str(exc.value)
    assert RPC_ENV_URL in str(exc.value)


def test_from_env_ok(env):
    cfg = AuctionConfig.from_env()
    assert cfg.configured
    assert cfg.chain_id == 8453


def test_every_method_raises_when_unconfigured():
    c = AuctionClient(AuctionConfig())
    with pytest.raises(AuctionNotConfiguredError):
        c.build_commit_tx("0x" + "01" * 32, "0x" + "02" * 32, SENDER)
    with pytest.raises(AuctionNotConfiguredError):
        c.build_reveal_tx("0x" + "01" * 32, 100, "0x" + "02" * 32, "0x" + "03" * 32, SENDER)
    with pytest.raises(AuctionNotConfiguredError):
        c.build_timeout_tx("0x" + "01" * 32, SENDER)
    with pytest.raises(AuctionNotConfiguredError):
        c.build_select_winner_and_fund_tx("0x" + "01" * 32, 100, SENDER, value_wei=100)
    with pytest.raises(AuctionNotConfiguredError):
        c.build_initialize_escrow_tx("0x" + "01" * 32, SENDER, SENDER, 1, 1, 1, 2, 3, 4, SENDER)
    with pytest.raises(AuctionNotConfiguredError):
        c.build_deposit_stake_tx("0x" + "01" * 32, SENDER, value_wei=5)
    with pytest.raises(AuctionNotConfiguredError):
        c.build_withdraw_tx(SENDER)
    with pytest.raises(AuctionNotConfiguredError):
        c.pending_withdrawals(SENDER)
    with pytest.raises(AuctionNotConfiguredError):
        c.vickrey_result("0x" + "01" * 32)
    with pytest.raises(AuctionNotConfiguredError):
        c.get_past_events("auction", "Committed")


def test_no_private_key_plumbing():
    # Safety property: the tx builder must not accept key material at all.
    params = set(inspect.signature(AuctionClient.build_transaction).parameters)
    assert not ({"private_key", "key", "secret"} & params)


def _offline(**kw):
    kw.setdefault("nonce", 7)
    kw.setdefault("gas", 120_000)
    kw.setdefault("gas_price_wei", 10**9)
    return kw


def test_build_commit_tx_encodes(client):
    tx = client.build_commit_tx("0x" + "01" * 32, "0x" + "02" * 32, SENDER, **_offline())
    assert tx["to"].lower() == AUCTION_ADDR.lower()
    assert tx["nonce"] == 7 and tx["chainId"] == 8453 and tx["value"] == 0
    fn, params = client.auction.decode_function_input(tx["data"])
    assert fn.fn_name == "commit"
    assert params["auctionId"].hex() == "01" * 32


def test_build_reveal_tx_encodes(client):
    salt = "0x" + "ab" * 32
    agent_hash = "0x" + compute_commitment(10**18, bytes.fromhex("ab" * 32), "agent-1").hex()
    tx = client.build_reveal_tx("0x" + "01" * 32, 10**18, salt, agent_hash, SENDER, **_offline())
    fn, params = client.auction.decode_function_input(tx["data"])
    assert fn.fn_name == "reveal"
    assert params["price"] == 10**18
    assert params["salt"].hex() == "ab" * 32


def test_build_timeout_and_fund_tx_encode(client):
    tx = client.build_timeout_tx("0x" + "01" * 32, SENDER, **_offline())
    fn, _ = client.auction.decode_function_input(tx["data"])
    assert fn.fn_name == "timeout"

    tx = client.build_select_winner_and_fund_tx(
        "0x" + "01" * 32, 2 * 10**18, SENDER, value_wei=2 * 10**18, **_offline())
    fn, params = client.auction.decode_function_input(tx["data"])
    assert fn.fn_name == "selectWinnerAndFund"
    assert tx["value"] == 2 * 10**18  # payable funding amount rides along


def test_escrow_builders_encode(client):
    tx = client.build_deposit_stake_tx("0x" + "05" * 32, SENDER, value_wei=10**17, **_offline())
    assert tx["to"].lower() == ESCROW_ADDR.lower()
    fn, _ = client.escrow.decode_function_input(tx["data"])
    assert fn.fn_name == "depositStake"
    assert tx["value"] == 10**17  # exact-stake requirement enforced on-chain

    tx = client.build_withdraw_tx(SENDER, **_offline())
    fn, _ = client.escrow.decode_function_input(tx["data"])
    assert fn.fn_name == "withdraw"

    tx = client.build_initialize_escrow_tx(
        "0x" + "06" * 32, SENDER, "0x" + "22" * 20, 10**18, 5 * 10**17,
        100, 200, 300, 400, SENDER, **_offline())
    fn, params = client.escrow.decode_function_input(tx["data"])
    assert fn.fn_name == "initializeEscrow"
    assert params["bidAmount"] == 10**18


def test_compute_commitment_pairs_with_shim():
    # Byte-identical to sincor2.a2a_inbound_market.sealed_commitment so shim
    # clients can reuse commitments when the contracts go live.
    from sincor2.a2a_inbound_market import sealed_commitment

    salt = bytes.fromhex("cd" * 32)
    assert compute_commitment(3 * 10**18, salt, "agent-9") == sealed_commitment(
        3 * 10**18, salt, "agent-9")


def test_get_past_events_rejects_unknown_contract(client):
    with pytest.raises(ValueError):
        client.get_past_events("nope", "Committed")
