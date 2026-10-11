"""Base Sepolia preflight safety tests; all RPC responses are synthetic."""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from sincor2.onchain.testnet_preflight import (
    DEFAULT_RPC_PROVIDERS,
    RpcRequestError,
    TestnetPreflightError as _TestnetPreflightError,
    WrongNetworkError,
    enforce_relayer_testnet_gas,
    eth_to_wei,
    inspect_balances,
    rpc_providers,
)

ADDRESS = "0x" + "12" * 20


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size=-1):
        encoded = json.dumps(self.payload).encode()
        return encoded if size < 0 else encoded[:size]


class RpcSequence:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, request, timeout=0):
        payload = json.loads(request.data.decode())
        self.calls.append((request.full_url, payload["method"], payload["params"], timeout))
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": item})


class FakeEth:
    def __init__(self, chain_id, balance):
        self.chain_id = chain_id
        self.balance = balance
        self.reads = 0
        self.transaction_methods = 0

    def get_balance(self, _address):
        self.reads += 1
        return self.balance

    def get_transaction_count(self, *_args, **_kwargs):
        self.transaction_methods += 1
        raise AssertionError("underfunded preflight must stop before nonce lookup")


class FakeWeb3:
    def __init__(self, chain_id, balance):
        self.eth = FakeEth(chain_id, balance)


class BaseSepoliaPreflightTests(unittest.TestCase):
    def test_decimal_eth_converts_exactly_to_wei(self):
        self.assertEqual(eth_to_wei("0.05"), 50_000_000_000_000_000)
        self.assertEqual(eth_to_wei("0"), 0)

    def test_decimal_validation_rejects_negative_and_sub_wei_values(self):
        for value in (
            "-0.1", "nan", "Infinity", "0.0000000000000000001",
            str(1 << 256),
        ):
            with self.subTest(value=value), self.assertRaises(_TestnetPreflightError):
                eth_to_wei(value)

    def test_addresses_are_required_validated_and_deduplicated(self):
        report_opener = RpcSequence(["0x14a34", hex(75_000_000_000_000_000)])
        report = inspect_balances(
            [ADDRESS, ADDRESS.lower()], minimum_wei=50_000_000_000_000_000,
            override_url="https://rpc.example/token-redacted", opener=report_opener,
        )
        self.assertEqual(len(report.balances_wei), 1)
        self.assertEqual(len(report.underfunded), 0)
        self.assertEqual([call[1] for call in report_opener.calls], ["eth_chainId", "eth_getBalance"])

    def test_address_count_is_bounded(self):
        addresses = [f"0x{i:040x}" for i in range(33)]
        with self.assertRaisesRegex(_TestnetPreflightError, "at most 32"):
            inspect_balances(addresses, minimum_wei=1, opener=RpcSequence([]))

    def test_wrong_chain_fails_without_trying_a_fallback(self):
        opener = RpcSequence(["0x2105"])
        with self.assertRaises(WrongNetworkError):
            inspect_balances([ADDRESS], minimum_wei=1, opener=opener)
        self.assertEqual(len(opener.calls), 1)

    def test_transport_failure_falls_back_to_second_provider(self):
        opener = RpcSequence([
            OSError("provider offline"),
            "0x14a34", hex(25_000_000_000_000_000),
        ])
        report = inspect_balances(
            [ADDRESS], minimum_wei=50_000_000_000_000_000, opener=opener
        )
        self.assertEqual(report.provider, DEFAULT_RPC_PROVIDERS[1][0])
        self.assertEqual(report.underfunded, (ADDRESS,))

    def test_rpc_override_must_use_https(self):
        with self.assertRaises(_TestnetPreflightError):
            rpc_providers("http://insecure.example/rpc")

    def test_rpc_failure_does_not_expose_provider_url(self):
        secret_url = "https://rpc.example/rpc?token=do-not-print"
        opener = RpcSequence([OSError("offline")] * (1 + len(DEFAULT_RPC_PROVIDERS)))
        with self.assertRaises(RpcRequestError) as caught:
            inspect_balances([ADDRESS], minimum_wei=1, override_url=secret_url, opener=opener)
        self.assertNotIn("do-not-print", str(caught.exception))
        self.assertNotIn(secret_url, str(caught.exception))

    def test_malformed_rpc_result_fails_closed(self):
        opener = RpcSequence(["not-hex"] * (1 + len(DEFAULT_RPC_PROVIDERS)))
        with self.assertRaises(RpcRequestError):
            inspect_balances([ADDRESS], minimum_wei=1, opener=opener)

    def test_oversized_rpc_response_is_rejected(self):
        class OversizedResponse(FakeResponse):
            def read(self, size=-1):
                return b"x" * (size + 1)

        class OversizedProvider:
            def __call__(self, request, timeout=0):
                return OversizedResponse({})

        with self.assertRaises(RpcRequestError):
            inspect_balances([ADDRESS], minimum_wei=1, opener=OversizedProvider())

    def test_sepolia_relayer_is_blocked_below_threshold_without_writes(self):
        minimum = 50_000_000_000_000_000
        w3 = FakeWeb3(84532, minimum - 1)
        with patch.dict(os.environ, {"AUCTION_REQUIRE_BASE_SEPOLIA": "true"}, clear=False):
            with self.assertRaisesRegex(_TestnetPreflightError, "not signed or broadcast"):
                enforce_relayer_testnet_gas(w3, ADDRESS, minimum)
        self.assertEqual(w3.eth.reads, 1)

    def test_sepolia_relayer_allows_sufficient_balance(self):
        minimum = 50_000_000_000_000_000
        w3 = FakeWeb3(84532, minimum)
        with patch.dict(os.environ, {"AUCTION_REQUIRE_BASE_SEPOLIA": "true"}, clear=False):
            enforce_relayer_testnet_gas(w3, ADDRESS, minimum)
        self.assertEqual(w3.eth.reads, 1)

    def test_testnet_only_mode_rejects_non_sepolia_network(self):
        w3 = FakeWeb3(8453, 10**20)
        with patch.dict(os.environ, {"AUCTION_REQUIRE_BASE_SEPOLIA": "true"}, clear=False):
            with self.assertRaises(WrongNetworkError):
                enforce_relayer_testnet_gas(w3, ADDRESS, 1)
        self.assertEqual(w3.eth.reads, 0)

    def test_other_networks_are_not_subject_to_sepolia_gas_policy(self):
        w3 = FakeWeb3(8453, 0)
        with patch.dict(os.environ, {"AUCTION_REQUIRE_BASE_SEPOLIA": "false"}, clear=False):
            enforce_relayer_testnet_gas(w3, ADDRESS, 50_000_000_000_000_000)
        self.assertEqual(w3.eth.reads, 0)

    def test_testnet_only_mode_rejects_malformed_flag(self):
        with patch.dict(os.environ, {"AUCTION_REQUIRE_BASE_SEPOLIA": "maybe"}, clear=False):
            with self.assertRaisesRegex(_TestnetPreflightError, "must be true or false"):
                enforce_relayer_testnet_gas(FakeWeb3(8453, 0), ADDRESS, 1)

    def test_auction_relayer_stops_before_build_or_sign_when_underfunded(self):
        from sincor2.onchain.auction_relayer import AuctionRelayer

        calls = {"signed": 0, "built": 0}

        class FakeAccount:
            address = ADDRESS

            def sign_transaction(self, _tx):
                calls["signed"] += 1
                raise AssertionError("underfunded transaction must not be signed")

        fake_eth_account = types.SimpleNamespace(
            Account=types.SimpleNamespace(from_key=lambda _key: FakeAccount())
        )

        class FakeFunction:
            def build_transaction(self, _tx):
                calls["built"] += 1
                raise AssertionError("underfunded transaction must not be built")

        relayer = object.__new__(AuctionRelayer)
        relayer._relayer_key = "synthetic-test-key"
        relayer._w3 = FakeWeb3(84532, 1)
        with (
            patch.dict(sys.modules, {"eth_account": fake_eth_account}),
            patch.dict(os.environ, {
                "AUCTION_REQUIRE_BASE_SEPOLIA": "true",
                "AUCTION_SEPOLIA_MIN_GAS_ETH": "0.05",
            }, clear=False),
            self.assertRaisesRegex(_TestnetPreflightError, "not signed or broadcast"),
        ):
            relayer._send(FakeFunction())
        self.assertEqual(calls, {"signed": 0, "built": 0})
        self.assertEqual(relayer._w3.eth.transaction_methods, 0)


if __name__ == "__main__":
    unittest.main()
