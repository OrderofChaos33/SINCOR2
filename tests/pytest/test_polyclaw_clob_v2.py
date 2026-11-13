"""CLOB V2 order-placement migration tests for the Polyclaw adapter.

Polymarket cut production over to CLOB V2 (official migration guide:
docs.polymarket.com/v2-migration; go-live 2026-04-28). The V1 SDK
(``py-clob-client``) cannot sign valid V2 orders: the signed struct drops
``taker`` / ``expiration`` / ``nonce`` / ``feeRateBps`` and adds
``timestamp`` (ms) / ``metadata`` / ``builder``; the EIP-712 exchange
domain version moves "1" -> "2" with new verifying contracts; collateral
is pUSD instead of USDC.e; fees are set by the protocol at match time.

These tests pin the V2 wire shape field-by-field against the official
spec, prove the signatures recover under the *V2* domain (and NOT under
the V1 domain), and drive the adapter's live path end to end with the
network mocked at the HTTP layer (``py_clob_client_v2.client`` get/post).

Everything is paper-only: the key below is the public Anvil/Hardhat
account #0 test vector, POLYCLAW_LIVE is set only inside these tests, and
the mocked HTTP layer guarantees nothing can reach Polymarket.
"""

import dataclasses
import json
import time

import pytest

# Import the adapter before any py_clob_client_v2 import: it sanitizes
# the bracketed-IPv6 NO_PROXY entries that otherwise crash httpx inside
# the SDK at import time.
import sincor2.execution_adapter  # noqa: F401,E402

# Public test vector (Anvil/Hardhat account #0) — NOT a real credential.
TEST_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

TOKEN_ID = "12345678901234567890123456789012"
CONDITION_ID = "0x" + "cd" * 32
HOST = "https://clob.polymarket.com"

# Official V2 addresses (docs.polymarket.com "Place Orders" domain table;
# identical values ship in py_clob_client_v2.config for chain 137).
CTF_EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"
PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
# V1 exchange — used only as the negative control for signature recovery.
CTF_EXCHANGE_V1 = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

BYTES32_ZERO = "0x" + "00" * 32


@pytest.fixture
def sdk_http(monkeypatch):
    """Mock the V2 client's HTTP layer; returns the capture dict.

    Routes mirror the real CLOB V2 response shapes. ``state`` lets a
    test flip neg-risk and the POST /order response.
    """
    import sincor2.execution_adapter  # noqa: F401  (sanitizes NO_PROXY first)
    import py_clob_client_v2.client as v2client

    state = {
        "neg_risk": False,
        "order_response": {
            "success": True,
            "orderID": "0xORDERID",
            "status": "matched",
            "errorMsg": "",
            "tradeIDs": [],
            "transactionsHashes": ["0x" + "ab" * 32],
        },
        "posted": [],   # parsed JSON bodies of POST /order
        "requests": [],  # (method, path) log
    }

    def fake_get(url, headers=None, params=None, **kwargs):
        state["requests"].append(("GET", url))
        if "/clob-markets/" in url:
            return {
                "t": [{"t": TOKEN_ID}],
                "mts": "0.01",
                "nr": state["neg_risk"],
                "fd": {"r": 0.0, "e": 0},
            }
        if "/markets-by-token/" in url:
            return {"condition_id": CONDITION_ID}
        if url.endswith("/tick-size"):
            return {"minimum_tick_size": "0.01"}
        if url.endswith("/neg-risk"):
            return {"neg_risk": state["neg_risk"]}
        if url.endswith("/version"):
            return {"version": 2}
        if "/balance-allowance/update" in url:
            return {}
        if "/balance-allowance" in url:
            return {
                "balance": "20000000",
                "allowances": {CTF_EXCHANGE_V2: "20000000"},
            }
        if "/auth/derive-api-key" in url or "/auth/api-key" in url:
            return {
                "apiKey": "test-api-key",
                "secret": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
                "passphrase": "test-api-passphrase",
            }
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url, headers=None, data=None, params=None, **kwargs):
        state["requests"].append(("POST", url))
        if "/auth/api-key" in url:
            return {
                "apiKey": "test-api-key",
                "secret": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
                "passphrase": "test-api-passphrase",
            }
        if url.endswith("/order"):
            state["posted"].append(json.loads(data))
            return dict(state["order_response"])
        raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr(v2client, "get", fake_get)
    monkeypatch.setattr(v2client, "post", fake_post)
    return state


def _make_client():
    from py_clob_client_v2 import ApiCreds, ClobClient

    return ClobClient(
        HOST,
        chain_id=137,
        key=TEST_KEY,
        creds=ApiCreds(
            api_key="test-api-key",
            api_secret="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
            api_passphrase="test-api-passphrase",
        ),
        signature_type=0,
    )


def _recover(order, verifying_contract, domain_version="2"):
    """Recover the signer from an independently rebuilt EIP-712 payload.

    The typed data here is constructed from the official V2 spec (domain
    name/version + the 11-field Order struct), not from SDK internals —
    so recovery only succeeds if the SDK really signed V2-shaped data
    under the V2 domain.
    """
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    typed = {
        "primaryType": "Order",
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Order": [
                {"name": "salt", "type": "uint256"},
                {"name": "maker", "type": "address"},
                {"name": "signer", "type": "address"},
                {"name": "tokenId", "type": "uint256"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "side", "type": "uint8"},
                {"name": "signatureType", "type": "uint8"},
                {"name": "timestamp", "type": "uint256"},
                {"name": "metadata", "type": "bytes32"},
                {"name": "builder", "type": "bytes32"},
            ],
        },
        "domain": {
            "name": "Polymarket CTF Exchange",
            "version": domain_version,
            "chainId": 137,
            "verifyingContract": verifying_contract,
        },
        "message": {
            "salt": int(order.salt),
            "maker": order.maker,
            "signer": order.signer,
            "tokenId": int(order.tokenId),
            "makerAmount": int(order.makerAmount),
            "takerAmount": int(order.takerAmount),
            "side": int(order.side),
            "signatureType": int(order.signatureType),
            "timestamp": int(order.timestamp),
            "metadata": bytes.fromhex(order.metadata[2:]),
            "builder": bytes.fromhex(order.builder[2:]),
        },
    }
    return Account.recover_message(
        encode_typed_data(full_message=typed), signature=order.signature
    )


class TestV2SdkGrounding:
    def test_contract_config_collateral_is_pusd(self):
        from py_clob_client_v2.config import get_contract_config

        cfg = get_contract_config(137)
        assert cfg.collateral == PUSD
        assert cfg.exchange_v2 == CTF_EXCHANGE_V2
        assert cfg.neg_risk_exchange_v2 == NEG_RISK_EXCHANGE_V2


class TestV2OrderConstruction:
    def test_limit_order_fields_match_official_v2_spec(self, sdk_http):
        from py_clob_client_v2 import OrderArgs, PartialCreateOrderOptions, Side
        from py_clob_client_v2.order_utils.model.order_data_v2 import SignedOrderV2

        client = _make_client()
        before_ms = int(time.time() * 1000)
        signed = client.create_order(
            OrderArgs(token_id=TOKEN_ID, price=0.50, size=10, side=Side.BUY),
            PartialCreateOrderOptions(),
        )
        after_ms = int(time.time() * 1000)

        assert isinstance(signed, SignedOrderV2)
        field_names = {f.name for f in dataclasses.fields(signed)}
        # The exact V2 signed-order field set (+ expiration, which rides
        # in the POST body only and is NOT part of the signed struct).
        assert field_names == {
            "salt", "maker", "signer", "tokenId", "makerAmount",
            "takerAmount", "side", "signatureType", "timestamp",
            "metadata", "builder", "expiration", "signature",
        }
        # V1-only fields are gone.
        for legacy in ("taker", "nonce", "feeRateBps"):
            assert legacy not in field_names

        assert signed.maker == TEST_ADDRESS
        assert signed.signer == TEST_ADDRESS
        assert signed.tokenId == TOKEN_ID
        # BUY at 0.50 x 10 shares: maker gives 5 pUSD, takes 10 shares
        # (6-decimal base units).
        assert signed.makerAmount == "5000000"
        assert signed.takerAmount == "10000000"
        assert int(signed.side) == 0  # BUY as uint8
        assert int(signed.signatureType) == 0  # EOA
        # timestamp is milliseconds since epoch (replaces nonce).
        assert before_ms <= int(signed.timestamp) <= after_ms
        assert signed.metadata == BYTES32_ZERO
        assert signed.builder == BYTES32_ZERO
        assert signed.signature.startswith("0x") and len(signed.signature) == 132

    def test_wire_payload_shape(self, sdk_http):
        from py_clob_client_v2 import OrderArgs, PartialCreateOrderOptions, Side
        from py_clob_client_v2.order_utils.model.order_data_v2 import (
            order_to_json_v2,
        )

        client = _make_client()
        signed = client.create_order(
            OrderArgs(token_id=TOKEN_ID, price=0.50, size=10, side=Side.BUY),
            PartialCreateOrderOptions(),
        )
        payload = order_to_json_v2(signed, "test-api-key", "FOK")
        assert set(payload) == {"order", "owner", "orderType", "deferExec", "postOnly"}
        assert payload["owner"] == "test-api-key"
        assert payload["orderType"] == "FOK"
        order = payload["order"]
        assert set(order) == {
            "salt", "maker", "signer", "tokenId", "makerAmount",
            "takerAmount", "side", "expiration", "signatureType",
            "timestamp", "metadata", "builder", "signature",
        }
        for legacy in ("nonce", "feeRateBps", "taker"):
            assert legacy not in order
        assert order["side"] == "BUY"  # wire uses the string form
        assert isinstance(order["salt"], int)
        assert order["signature"] == signed.signature

    def test_market_order_amounts(self, sdk_http):
        from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

        client = _make_client()
        signed = client.create_market_order(
            MarketOrderArgs(
                token_id=TOKEN_ID,
                amount=10,
                side=Side.BUY,
                price=0.40,
                order_type=OrderType.FOK,
            )
        )
        # Market BUY: amount is the pUSD spend (maker), shares are taker.
        assert signed.makerAmount == "10000000"
        assert signed.takerAmount == "25000000"
        assert int(signed.side) == 0


class TestV2SigningDomain:
    def _limit_order(self):
        from py_clob_client_v2 import OrderArgs, PartialCreateOrderOptions, Side

        client = _make_client()
        return client.create_order(
            OrderArgs(token_id=TOKEN_ID, price=0.50, size=10, side=Side.BUY),
            PartialCreateOrderOptions(),
        )

    def test_signature_recovers_under_v2_domain(self, sdk_http):
        signed = self._limit_order()
        assert _recover(signed, CTF_EXCHANGE_V2, "2") == TEST_ADDRESS

    def test_signature_does_not_recover_under_v1_domain(self, sdk_http):
        """Negative control: a V1-shaped signature would recover the
        test address under the V1 domain; the V2 signature must not."""
        signed = self._limit_order()
        assert _recover(signed, CTF_EXCHANGE_V1, "1") != TEST_ADDRESS

    def test_neg_risk_order_signs_against_neg_risk_exchange_v2(self, sdk_http):
        sdk_http["neg_risk"] = True
        signed = self._limit_order()
        assert _recover(signed, NEG_RISK_EXCHANGE_V2, "2") == TEST_ADDRESS


@pytest.fixture
def live_adapter_env(monkeypatch, sdk_http):
    """Adapter in live posture: throwaway key, V2 HTTP fully mocked."""
    monkeypatch.setenv("POLYCLAW_LIVE", "true")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("POLYCLAW_HALT", raising=False)
    # No API creds in env: the adapter must derive them via the V2
    # create/derive flow (mocked at the HTTP layer).
    for var in ("POLYMARKET_API_KEY", "POLYMARKET_API_SECRET",
                "POLYMARKET_API_PASSPHRASE", "POLYMARKET_FUNDER"):
        monkeypatch.delenv(var, raising=False)
    from sincor2.execution_adapter import PolymarketAdapter

    return PolymarketAdapter(), sdk_http


class TestAdapterV2LivePath:
    def test_bootstrap_and_market_buy_post_v2_payload(self, live_adapter_env,
                                                      monkeypatch):
        adapter, state = live_adapter_env
        client = adapter.ensure_client()
        assert client is not None
        assert adapter.key_error() is None
        assert adapter.trading_address() == TEST_ADDRESS
        # Balance/allowance warm-up read the pUSD collateral allowance.
        assert adapter._allowances_ready is True
        # pUSD balance: 20_000_000 raw base units = 20.0
        assert adapter.collateral_balance() == pytest.approx(20.0)

        monkeypatch.setattr(
            "sincor2.execution_adapter._book_ask", lambda token_id: 0.40)
        result = adapter.place_market_buy(TOKEN_ID, 10.0)
        assert result.success is True
        assert result.simulated is False
        assert result.order_id == "0xORDERID"
        assert result.price == 0.40
        assert result.size_tokens == pytest.approx(25.0)

        # Exactly one POST /order, carrying the V2 wire body.
        assert len(state["posted"]) == 1
        body = state["posted"][0]
        assert body["orderType"] == "FOK"
        order = body["order"]
        for legacy in ("nonce", "feeRateBps", "taker"):
            assert legacy not in order
        assert order["maker"] == TEST_ADDRESS
        assert order["signer"] == TEST_ADDRESS
        assert order["tokenId"] == TOKEN_ID
        assert order["makerAmount"] == "10000000"  # 10 pUSD spend
        assert order["takerAmount"] == "25000000"  # shares at 0.40
        assert order["side"] == "BUY"
        assert order["metadata"] == BYTES32_ZERO
        assert order["builder"] == BYTES32_ZERO
        assert int(order["timestamp"]) > 1_700_000_000_000  # ms epoch

    def test_rejected_order_maps_to_failure(self, live_adapter_env, monkeypatch):
        adapter, state = live_adapter_env
        state["order_response"] = {
            "success": False,
            "orderID": "",
            "status": "",
            "errorMsg": "insufficient balance",
            "tradeIDs": [],
            "transactionsHashes": [],
        }
        assert adapter.ensure_client() is not None
        monkeypatch.setattr(
            "sincor2.execution_adapter._book_ask", lambda token_id: 0.40)
        result = adapter.place_market_buy(TOKEN_ID, 10.0)
        assert result.success is False
        assert result.simulated is False
        assert "insufficient balance" in result.error
