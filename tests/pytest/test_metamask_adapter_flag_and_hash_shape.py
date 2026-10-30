"""Regression tests for the MetaMask adapter residuals (Builder B round 3).

Builder B CONFIRMED the round-2 branch (30e56a8c) and left three residual
observations in the same totality family. This file pins their fixes:

O1. Readiness gate truthiness-coerced the doctor flags. status() did
    ``bool(data.get("authenticated"))`` / ``bool(data.get("initialized"))``,
    so a doctor payload with STRING values -- {"authenticated": "false",
    "initialized": "false"} -- read as ready (``bool("false")`` is True)
    and transfer()/swap_execute() proceeded to EXECUTE against a wallet
    that is not authenticated/initialized. Pinned fix (_doctor_flag, fail
    closed): a flag counts as true ONLY for boolean True or the string
    "true" (case-insensitive, whitespace-trimmed); False, "false",
    "yes", 1, 0, None and a missing key all read NOT ready, the write
    methods return a failure OrderResult (never raise), and the trade
    CLI is never invoked (proven by the fake's argv log).

O2. Corruption INSIDE the tx hash was reported as a clean success with a
    mangled hash. With errors="replace" decoding, a non-UTF-8 byte
    inside txHash yielded order_id='0xab\\ufffdcd...' -- the trade did
    execute (success is correct) but the recorded identifier can never
    match the onchain hash. Pinned fix: _extract_tx_hash accepts a
    candidate only if it matches ^0x[0-9a-fA-F]{64}$; a malformed
    txHash falls back to ``hash`` when IT is well-formed; when neither
    is, the result is "" -- the branch's established hash-less-success
    design.

O3. A lone-surrogate escape inside txHash ("0x\\ud800...") passed
    through verbatim and would raise UnicodeEncodeError in any
    downstream consumer that encodes it. The O2 hex-shape check closes
    this too (a surrogate is not a hex char); pinned below.

The fake-binary harness mirrors the stream-decode file: a real
executable on disk driven through the adapter's real subprocess runner,
streams settable per subcommand as text or raw bytes, plus an argv log
of every invocation. No stubbing of _run() anywhere in this file.
"""

from __future__ import annotations

import pytest

from sincor2.adapters import metamask_agent_wallet as mmw
from sincor2.adapters.metamask_agent_wallet import (
    MetaMaskAgentNotReady,
    MetaMaskAgentWallet,
    WalletStatus,
)
from sincor2.execution_adapter import OrderResult

TX_HASH = "0x" + "ab" * 32
TX_HASH_2 = "0x" + "cd" * 32
RECIPIENT = "0x" + "11" * 20

FAKE_MM = """\
#!/usr/bin/env python3
import os, sys

log = os.environ.get("FAKE_MM_ARGV_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(" ".join(sys.argv[1:]) + "\\n")

key = (sys.argv[1] if len(sys.argv) > 1 else "DEFAULT").upper()

def _emit(stream, tag):
    path = os.environ.get("FAKE_MM_%s_FILE_%s" % (tag, key))
    if path:
        with open(path, "rb") as fh:
            stream.buffer.write(fh.read())
    else:
        stream.buffer.write(
            os.environ.get("FAKE_MM_%s_%s" % (tag, key), "").encode("utf-8")
        )
    stream.buffer.flush()

_emit(sys.stdout, "STDOUT")
_emit(sys.stderr, "STDERR")
sys.exit(int(os.environ.get("FAKE_MM_EXIT_%s" % key,
                             os.environ.get("FAKE_MM_EXIT", "0"))))
"""


@pytest.fixture()
def fake_mm(tmp_path):
    path = tmp_path / "mm"
    path.write_text(FAKE_MM)
    path.chmod(0o755)
    return str(path)


@pytest.fixture()
def mm_bytes(tmp_path, monkeypatch):
    """Point the fake mm's streams for one subcommand at raw-byte files."""
    counter = {"n": 0}

    def set_stream(command, *, stdout=None, stderr=None, exit_code=None):
        key = command.upper()
        counter["n"] += 1
        if stdout is not None:
            p = tmp_path / f"out_{key}_{counter['n']}.bin"
            p.write_bytes(stdout)
            monkeypatch.setenv(f"FAKE_MM_STDOUT_FILE_{key}", str(p))
        if stderr is not None:
            p = tmp_path / f"err_{key}_{counter['n']}.bin"
            p.write_bytes(stderr)
            monkeypatch.setenv(f"FAKE_MM_STDERR_FILE_{key}", str(p))
        if exit_code is not None:
            monkeypatch.setenv(f"FAKE_MM_EXIT_{key}", str(exit_code))

    return set_stream


@pytest.fixture()
def argv_log(tmp_path, monkeypatch):
    """Path of the fake mm's invocation log + a reader for its lines."""
    path = tmp_path / "argv.log"
    monkeypatch.setenv("FAKE_MM_ARGV_LOG", str(path))

    def read():
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    return read


@pytest.fixture()
def real_wallet(fake_mm, monkeypatch):
    """Live-mode wallet with the REAL require_ready()/status() path."""
    monkeypatch.setattr(mmw, "kill_switch_tripped", lambda *a, **k: False)
    monkeypatch.setenv("METAMASK_AGENT_LIVE", "true")
    return MetaMaskAgentWallet(mm_bin=fake_mm)


@pytest.fixture()
def ready_wallet(fake_mm, monkeypatch):
    """Live-mode wallet, readiness stubbed (readiness is not under test)."""
    monkeypatch.setattr(mmw, "kill_switch_tripped", lambda *a, **k: False)
    monkeypatch.setenv("METAMASK_AGENT_LIVE", "true")
    w = MetaMaskAgentWallet(mm_bin=fake_mm)
    monkeypatch.setattr(
        w,
        "require_ready",
        lambda: WalletStatus(authenticated=True, initialized=True),
    )
    return w


def _call(wallet, method):
    if method == "transfer":
        return wallet.transfer(to=RECIPIENT, amount="1.5", force_live=True)
    if method == "swap_execute":
        return wallet.swap_execute("quote-123", force_live=True)
    if method == "send_to_treasury":
        return wallet.send_to_treasury("1.5")
    raise AssertionError(method)


def _stream_key(method):
    return "swap" if method == "swap_execute" else "transfer"


ALL_METHODS = ["transfer", "swap_execute", "send_to_treasury"]
EXEC_METHODS = ["transfer", "swap_execute"]

MISSING = object()


def _doctor_payload(monkeypatch, authenticated, initialized):
    """Set the fake doctor's stdout from raw JSON fragments per flag."""
    parts = []
    if authenticated is not MISSING:
        parts.append('"authenticated": %s' % authenticated)
    if initialized is not MISSING:
        parts.append('"initialized": %s' % initialized)
    monkeypatch.setenv(
        "FAKE_MM_STDOUT_DOCTOR", '{"data": {%s}}' % ", ".join(parts)
    )


# ----------------------------------------------------------------------
# O1: the doctor-flag matrix. Only boolean True and the string "true"
# (any case, whitespace-trimmed) count as ready -- fail closed on
# everything else.
# ----------------------------------------------------------------------

TRUEISH_FRAGS = ["true", '"true"', '" True "', '"TRUE"', '"tRuE"']
NOT_READY_FRAGS = ["false", '"false"', '"yes"', "1", "0", "null"]


@pytest.mark.parametrize("frag", TRUEISH_FRAGS, ids=TRUEISH_FRAGS)
def test_doctor_trueish_flags_read_ready(real_wallet, monkeypatch, frag):
    _doctor_payload(monkeypatch, frag, frag)
    st = real_wallet.status()
    assert st.authenticated is True
    assert st.initialized is True
    # require_ready must not raise for a genuinely ready wallet.
    assert real_wallet.require_ready().authenticated is True


@pytest.mark.parametrize("frag", NOT_READY_FRAGS, ids=NOT_READY_FRAGS)
def test_doctor_nontrue_authenticated_reads_not_ready(
    real_wallet, monkeypatch, frag
):
    _doctor_payload(monkeypatch, frag, "true")
    st = real_wallet.status()
    assert st.authenticated is False
    assert st.initialized is True
    with pytest.raises(MetaMaskAgentNotReady):
        real_wallet.require_ready()


@pytest.mark.parametrize("frag", NOT_READY_FRAGS, ids=NOT_READY_FRAGS)
def test_doctor_nontrue_initialized_reads_not_ready(
    real_wallet, monkeypatch, frag
):
    _doctor_payload(monkeypatch, "true", frag)
    st = real_wallet.status()
    assert st.authenticated is True
    assert st.initialized is False
    with pytest.raises(MetaMaskAgentNotReady):
        real_wallet.require_ready()


def test_doctor_missing_flags_read_not_ready(real_wallet, monkeypatch):
    _doctor_payload(monkeypatch, MISSING, MISSING)
    st = real_wallet.status()
    assert st.authenticated is False
    assert st.initialized is False
    with pytest.raises(MetaMaskAgentNotReady):
        real_wallet.require_ready()


# ----------------------------------------------------------------------
# O1 end-to-end: a not-ready wallet returns a failure OrderResult and
# the trade CLI is NEVER invoked -- even though the fake's trade
# subcommand is primed to succeed if it were called. The string-"false"
# pair is B's exact reproducing shape.
# ----------------------------------------------------------------------

NOT_READY_PAIRS = [
    ('"false"', '"false"'),
    ('"false"', "true"),
    ("true", '"false"'),
    ('"yes"', "true"),
    ("1", "true"),
    (MISSING, "true"),
    ("false", "false"),
]


@pytest.mark.parametrize("method", EXEC_METHODS)
@pytest.mark.parametrize("auth, init", NOT_READY_PAIRS)
def test_not_ready_wallet_never_invokes_trade_cli(
    real_wallet, monkeypatch, argv_log, method, auth, init
):
    _doctor_payload(monkeypatch, auth, init)
    # Prime the trade subcommand to succeed WITH a hash if invoked.
    monkeypatch.setenv(
        "FAKE_MM_STDOUT_%s" % _stream_key(method).upper(),
        '{"data": {"txHash": "%s"}}' % TX_HASH,
    )
    res = _call(real_wallet, method)
    assert isinstance(res, OrderResult)
    assert res.success is False
    assert res.simulated is False
    assert "not authenticated/initialized" in res.error
    trade_lines = [
        line
        for line in argv_log()
        if line.split(" ", 1)[0] in ("transfer", "swap")
    ]
    assert trade_lines == []


@pytest.mark.parametrize("method", EXEC_METHODS)
@pytest.mark.parametrize("frag", TRUEISH_FRAGS, ids=TRUEISH_FRAGS)
def test_ready_wallet_still_executes(
    real_wallet, monkeypatch, argv_log, method, frag
):
    _doctor_payload(monkeypatch, frag, frag)
    monkeypatch.setenv(
        "FAKE_MM_STDOUT_%s" % _stream_key(method).upper(),
        '{"data": {"txHash": "%s"}}' % TX_HASH,
    )
    res = _call(real_wallet, method)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == TX_HASH
    trade_lines = [
        line
        for line in argv_log()
        if line.split(" ", 1)[0] in ("transfer", "swap")
    ]
    assert len(trade_lines) == 1


# ----------------------------------------------------------------------
# O2: a candidate hash lands in order_id ONLY if it matches the tx-hash
# shape ^0x[0-9a-fA-F]{64}$. Malformed txHash -> well-formed ``hash``
# fallback wins; neither well-formed -> hash-less success ("").
# ----------------------------------------------------------------------

# A non-UTF-8 byte inside an otherwise right-length hash: tolerant
# decoding leaves a U+FFFD in the string, so the JSON parses but the
# hash is unreconcilable onchain.
CORRUPT_TXHASH = b'{"data": {"txHash": "0xab' + b"\xff" + b"c" * 61 + b'"}}'
CORRUPT_WITH_FALLBACK = (
    b'{"data": {"txHash": "0xab'
    + b"\xff"
    + b"c" * 61
    + b'", "hash": "'
    + TX_HASH_2.encode("utf-8")
    + b'"}}'
)


@pytest.mark.parametrize("method", ALL_METHODS)
def test_corrupt_byte_inside_txhash_reports_hashless_success(
    ready_wallet, mm_bytes, method
):
    mm_bytes(_stream_key(method), stdout=CORRUPT_TXHASH)
    res = _call(ready_wallet, method)
    assert isinstance(res, OrderResult)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == ""
    assert res.error == ""


@pytest.mark.parametrize("method", EXEC_METHODS)
def test_corrupt_txhash_falls_back_to_wellformed_hash(
    ready_wallet, mm_bytes, method
):
    mm_bytes(_stream_key(method), stdout=CORRUPT_WITH_FALLBACK)
    res = _call(ready_wallet, method)
    assert res.success is True
    assert res.order_id == TX_HASH_2


MALFORMED_HASHES = [
    "0xbbb",  # far too short (B's wart-probe shape)
    "0x" + "a" * 63,  # one hex char short
    "0x" + "a" * 65,  # one hex char long
    "0x" + "zz" * 32,  # right length, non-hex characters
    "",  # empty string: not a hash
]


@pytest.mark.parametrize("method", EXEC_METHODS)
@pytest.mark.parametrize("bad", MALFORMED_HASHES)
def test_malformed_txhash_reports_hashless_success(
    ready_wallet, monkeypatch, method, bad
):
    monkeypatch.setenv(
        "FAKE_MM_STDOUT_%s" % _stream_key(method).upper(),
        '{"data": {"txHash": "%s"}}' % bad,
    )
    res = _call(ready_wallet, method)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == ""


@pytest.mark.parametrize("method", EXEC_METHODS)
def test_malformed_txhash_falls_back_to_wellformed_hash(
    ready_wallet, monkeypatch, method
):
    monkeypatch.setenv(
        "FAKE_MM_STDOUT_%s" % _stream_key(method).upper(),
        '{"data": {"txHash": "0xbbb", "hash": "%s"}}' % TX_HASH_2,
    )
    res = _call(ready_wallet, method)
    assert res.success is True
    assert res.order_id == TX_HASH_2


@pytest.mark.parametrize("method", EXEC_METHODS)
def test_wellformed_txhash_still_wins_over_hash(
    ready_wallet, monkeypatch, method
):
    monkeypatch.setenv(
        "FAKE_MM_STDOUT_%s" % _stream_key(method).upper(),
        '{"data": {"txHash": "%s", "hash": "%s"}}' % (TX_HASH, TX_HASH_2),
    )
    res = _call(ready_wallet, method)
    assert res.success is True
    assert res.order_id == TX_HASH


@pytest.mark.parametrize("method", EXEC_METHODS)
def test_uppercase_hex_txhash_accepted_verbatim(
    ready_wallet, monkeypatch, method
):
    upper = "0x" + "AB" * 32
    monkeypatch.setenv(
        "FAKE_MM_STDOUT_%s" % _stream_key(method).upper(),
        '{"data": {"txHash": "%s"}}' % upper,
    )
    res = _call(ready_wallet, method)
    assert res.success is True
    assert res.order_id == upper


# ----------------------------------------------------------------------
# O3: a lone-surrogate escape inside txHash is not a hex char, so the
# shape check rejects it -- the surrogate never reaches order_id, where
# it would raise UnicodeEncodeError in a downstream consumer.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("method", EXEC_METHODS)
def test_lone_surrogate_txhash_reports_hashless_success(
    ready_wallet, monkeypatch, method
):
    # The JSON text carries the 6-char escape \ud800; json.loads turns
    # it into a lone surrogate inside an otherwise right-length string.
    payload = '{"data": {"txHash": "0x\\ud800' + "a" * 63 + '"}}'
    monkeypatch.setenv(
        "FAKE_MM_STDOUT_%s" % _stream_key(method).upper(), payload
    )
    res = _call(ready_wallet, method)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == ""
    # Must be safely encodable by any downstream consumer.
    assert res.order_id.encode("utf-8") == b""
