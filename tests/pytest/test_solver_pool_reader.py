"""Reader tests with a stubbed transport (no network).

Regression guard for the live-observed failure of 2026-11-11: public
RPC endpoints silently truncate batch responses, and the reader used
to treat absent response items as failed calls — silently losing
pools from discovery. Absent items must be retried individually;
items returned with an error count as genuine failures.
"""

import pytest

from sincor2.solver.pool_reader import BasePoolReader, RpcError, _compress
from sincor2.solver.pools import AXM, SINC, USDC, WETH, checksum


class TruncatingReader(BasePoolReader):
    """Returns only the first 2 items of any batch; singles succeed."""

    def __init__(self):
        super().__init__(rpc_urls=("stub://",))
        self.single_calls = 0

    def _post(self, payload, url=None):
        if isinstance(payload, list):
            return [
                {"id": item["id"], "result": "0x" + "ab" * 32}
                for item in payload[:2]
            ]
        return [{"id": payload["id"], "result": "0x" + "cd" * 32}]

    def block_number(self):  # not used here
        return 1

    def gas_price(self):
        return 1


class ErrorReader(BasePoolReader):
    """Every call comes back with an explicit JSON-RPC error."""

    def __init__(self):
        super().__init__(rpc_urls=("stub://",))

    def _post(self, payload, url=None):
        if isinstance(payload, list):
            return [{"id": item["id"], "error": {"code": 3}} for item in payload]
        return [{"id": payload["id"], "error": {"code": 3}}]


class TransientErrorReader(BasePoolReader):
    """Batch items all error (lagging node); individual calls succeed."""

    def __init__(self):
        super().__init__(rpc_urls=("stub://",))

    def _post(self, payload, url=None):
        if isinstance(payload, list):
            return [{"id": item["id"],
                     "error": {"code": -32000, "message": "header not found"}}
                    for item in payload]
        return [{"id": payload["id"], "result": "0x" + "ef" * 32}]


def test_batch_retries_items_dropped_from_truncated_response():
    reader = TruncatingReader()
    calls = [("0xPOOL", "0x00")] * 5
    results = reader.eth_call_batch(calls, block=100)
    assert len(results) == 5
    assert all(r is not None for r in results)
    # first two came from the batch, the rest from individual retries
    assert results[0] == "0x" + "ab" * 32
    assert results[4] == "0x" + "cd" * 32


def test_batch_persistent_errors_are_failures():
    reader = ErrorReader()
    results = reader.eth_call_batch([("0xPOOL", "0x00")] * 3, block=100)
    assert results == [None, None, None]


def test_batch_transient_errors_recovered_by_individual_retry():
    # pinned-block calls against a lagging node return "header not
    # found" inside the batch; the individual retry (endpoint-rotating)
    # must recover them instead of dropping the pools they describe
    reader = TransientErrorReader()
    results = reader.eth_call_batch([("0xPOOL", "0x00")] * 3, block=100)
    assert results == ["0x" + "ef" * 32] * 3


class DecimalsReader(BasePoolReader):
    """Answers decimals() calls from a table; sinc_decimals is tunable
    so the test can replay the original SINC=18 bug against the guard."""

    def __init__(self, sinc_decimals):
        super().__init__(rpc_urls=("stub://",))
        self._table = {checksum(WETH): 18, checksum(USDC): 6,
                       checksum(AXM): 18, checksum(SINC): sinc_decimals}

    def eth_call_batch(self, calls, block):
        return ["0x" + self._table[checksum(to)].to_bytes(32, "big").hex()
                for to, _data in calls]


def test_decimals_verification_passes_when_chain_matches_table():
    reader = DecimalsReader(sinc_decimals=8)  # SINC really has 8
    tokens = [checksum(t) for t in (WETH, USDC, AXM, SINC)]
    reader._verify_decimals(tokens, block=1)  # must not raise


def test_decimals_verification_refuses_on_mismatch():
    reader = DecimalsReader(sinc_decimals=18)  # the bug, replayed
    tokens = [checksum(t) for t in (WETH, USDC, AXM, SINC)]
    with pytest.raises(RpcError):
        reader._verify_decimals(tokens, block=1)


def test_compress_matches_solidity_semantics():
    # floor division == Solidity's truncate-then-adjust for negatives
    assert _compress(0, 10) == 0
    assert _compress(25, 10) == 2
    assert _compress(-25, 10) == -3   # Solidity: -25/10 = -2, adjust -> -3
    assert _compress(-20, 10) == -2   # exact multiple: no adjustment
    assert _compress(-1, 60) == -1
