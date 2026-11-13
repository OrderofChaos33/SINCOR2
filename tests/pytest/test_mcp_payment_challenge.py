"""MCP payment challenge (dev-watch item 80, ScriptMasterLabs 2026-10-02).

MCP clients swallow JSON-RPC errors before the model sees them, so a
JSON-RPC -32000 "Payment Required" error means the challenge (accepts
array, price, payTo) never reaches the agent. The fix: return the
challenge as a SUCCESSFUL result with isError:true.
"""

from __future__ import annotations

import json

import pytest

import sincor2.mcp_server as ms


@pytest.fixture()
def paid_get_quote():
    """Register get_quote as payment-gated for the duration of a test."""
    ms.register_paid_tool("get_quote", price="0.01",
                          pay_to="0xTreasury0000000000000000000000000001",
                          accepts=["eip3009-usdc-base"])
    yield
    ms.PAID_TOOLS.discard("get_quote")
    ms.PAID_TOOL_PRICING.pop("get_quote", None)


def _challenge_payload(response: dict) -> dict:
    assert "error" not in response, "must not be a JSON-RPC error object"
    result = response["result"]
    assert result.get("isError") is True
    return json.loads(result["content"][0]["text"])


def test_payment_challenge_result_shape():
    result = ms.payment_challenge_result(
        "0.01", "0xTreasury", ["eip3009-usdc-base"])
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["price"] == "0.01"
    assert payload["payTo"] == "0xTreasury"
    assert payload["accepts"] == ["eip3009-usdc-base"]
    assert "retry_instructions" in payload
    assert "EIP-3009" in payload["retry_instructions"]
    assert "tools/call" in payload["retry_instructions"]


def test_tools_call_returns_challenge_not_rpc_error(paid_get_quote):
    response = ms._handle_tools_call(
        7, {"name": "get_quote", "arguments": {}})
    # NOT a JSON-RPC error object: no "error" key, certainly not -32000.
    assert "error" not in response
    assert response["jsonrpc"] == "2.0"
    payload = _challenge_payload(response)
    assert payload["price"] == "0.01"
    assert payload["payTo"] == "0xTreasury0000000000000000000000000001"
    assert payload["accepts"] == ["eip3009-usdc-base"]
    assert "retry_instructions" in payload


def test_tools_call_with_token_passes_gate(paid_get_quote):
    # A call carrying an x402 access token is not challenged; it proceeds
    # to normal dispatch (get_quote with bad args -> -32602, proving the
    # gate let it through instead of challenging).
    response = ms._handle_tools_call(
        8, {"name": "get_quote", "arguments": {"x402_access_token": "tok"}})
    assert response.get("error", {}).get("code") != -32000
    result = response.get("result") or {}
    assert "payment_required" not in json.dumps(result)


def test_challenge_survives_error_swallowing_client(paid_get_quote):
    # Simulate an agent-side client that drops JSON-RPC errors before the
    # model sees them (the ScriptMasterLabs behavior).
    response = ms._handle_tools_call(
        9, {"name": "get_quote", "arguments": {}})

    def error_swallowing_client(responses):
        seen = []
        for resp in responses:
            if "error" in resp:
                continue  # swallowed before the model sees it
            seen.append(resp.get("result"))
        return seen

    seen = error_swallowing_client([response])
    assert len(seen) == 1
    payload = json.loads(seen[0]["content"][0]["text"])
    assert payload["status"] == "payment_required"
    assert payload["price"] == "0.01"
    assert payload["payTo"]


def test_challenge_stripping_client_loses_challenge_residual(paid_get_quote):
    # RESIDUAL (documented, not fixable server-side): a client that ALSO
    # drops results with isError:true will strip the challenge too. The
    # server cannot force such a client to surface anything; the hedge is
    # that compliant clients (which only drop error OBJECTS) keep it.
    response = ms._handle_tools_call(
        10, {"name": "get_quote", "arguments": {}})

    def challenge_stripping_client(responses):
        seen = []
        for resp in responses:
            if "error" in resp:
                continue
            result = resp.get("result") or {}
            if isinstance(result, dict) and result.get("isError"):
                continue  # strips the challenge as well
            seen.append(result)
        return seen

    seen = challenge_stripping_client([response])
    assert seen == [], (
        "documented residual: a client dropping isError results loses the "
        "challenge; no server-side representation can survive that filter"
    )
