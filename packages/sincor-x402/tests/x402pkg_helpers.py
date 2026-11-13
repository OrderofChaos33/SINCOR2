"""Shared test helpers for the sincor-x402 package tests.

These helpers live in their own uniquely-named module (not in conftest.py)
so the tests can import them explicitly. Importing ``from conftest import
...`` breaks when this package's tests are collected in the same pytest
run as the SINCOR2 repo's own suites, whose ``conftest`` module then wins
the top-level name. Fixtures stay in conftest.py; plain helpers live here.
"""
import json

TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
BUYER = "0x1111111111111111111111111111111111111111"
AMOUNT_ATOMIC = "10000"  # $0.01 USDC


def good_work(**kwargs):
    return {"answer": 42, "echo": kwargs}


def make_payment_payload(requirement, *, claimed=None, value=None,
                         auth_to=None, signature="0x" + "ab" * 65):
    """Build an X-PAYMENT payload the way an honest buyer would."""
    return {
        "x402Version": 1,
        "scheme": "exact",
        "network": "base",
        "accepted": claimed if claimed is not None
        else requirement.to_accepts_dict(),
        "payload": {
            "signature": signature,
            "authorization": {
                "from": BUYER,
                "to": auth_to if auth_to is not None else requirement.pay_to,
                "value": int(value if value is not None
                             else requirement.max_amount_required),
                "validAfter": 0,
                "validBefore": 9999999999,
                "nonce": "0x" + "00" * 32,
            },
        },
    }


def payment_header(payload) -> str:
    return json.dumps(payload)


def endpoint_transport(endpoint, counter=None):
    """TransportFn: buyer HTTP calls land directly on the endpoint."""
    def _transport(method, url, body, headers):
        if counter is not None:
            counter["calls"] = counter.get("calls", 0) + 1
            if any(k.lower() == "x-payment" for k in headers):
                counter["paid_calls"] = counter.get("paid_calls", 0) + 1
                counter.setdefault("paid_headers", []).append(
                    headers.get("X-PAYMENT") or headers.get("x-payment"))
        resp = endpoint.handle(headers=headers, kwargs=body or {})
        return resp.status, resp.headers, json.dumps(resp.body).encode()
    return _transport


class CountingSigner:
    def __init__(self):
        self.calls = 0

    def __call__(self, typed_data):
        self.calls += 1
        return "0x" + "cd" * 65
