"""Regression tests for the MetaMask adapter stream-decode breaks (B round 2).

The total-extraction fix (test_metamask_adapter_total_extraction.py) made
post-execution JSON handling total, but the same executed-but-reported-
failed class survived one level down, at stream decode:

1. ``_run()`` called ``subprocess.run(..., text=True)`` with no ``errors=``
   argument, so decoding happened INSIDE subprocess.run. Non-UTF-8 bytes
   in either stream raised UnicodeDecodeError instead of returning the
   CompletedProcess — ``_run`` never learned the exit code was 0, and the
   caller's broad except converted it to OrderResult(success=False) for a
   trade the CLI executed. stderr is decoded too, so non-UTF-8 warning /
   progress bytes on stderr poisoned even a perfectly valid stdout
   payload. Real CLIs emit such bytes; a caller retrying on the false
   "failure" can double-execute.

   Pinned behavior after the fix (decode tolerantly, errors="replace"):
   a completed 0-exit process ALWAYS reaches the returncode check.
   - Garbage stdout (undecodable prefix + a valid payload): the payload
     is unreachable, json parsing falls to the raw fallback -> hash-less
     success, order_id "" (the pinned design for unparseable shapes).
   - Valid stdout + garbage stderr: success WITH the hash in order_id.
   - Nonzero exit stays failure, and its stderr message still surfaces.

2. Pre-execution totality: ``status()`` did ``doc.get("data")`` unguarded
   on the ``mm doctor`` result. A non-dict doctor payload (``[]``) raised
   AttributeError out of require_ready() — which transfer() calls BEFORE
   its try block — so transfer()/swap_execute() RAISED instead of
   returning an OrderResult. No trade has executed at that point (no
   double-execute risk), but the write methods are exception-total
   end-to-end: a non-dict doctor payload means not-ready, and not-ready
   surfaces as OrderResult(success=False, error=...) like every other
   failure, never as a raise.

3. Wart: ``_extract_tx_hash`` used ``payload.get("txHash") or
   payload.get("hash")`` — a truthy non-string txHash (e.g. a dict)
   shadowed a valid string ``hash``, and the coercion then discarded
   both. The fallback is now consulted properly: txHash if it is a str,
   else hash if it is a str, else "".

The fake-binary harness mirrors the devwatch-315 sentinel pattern, except
the fake writes RAW BYTES (from files) to its streams — env-var text
cannot carry arbitrary non-UTF-8 bytes through a text harness.
"""

from __future__ import annotations

import pytest

from sincor2.adapters import metamask_agent_wallet as mmw
from sincor2.adapters.metamask_agent_wallet import MetaMaskAgentWallet, WalletStatus
from sincor2.execution_adapter import OrderResult

TX_HASH = "0x" + "ab" * 32
RECIPIENT = "0x" + "11" * 20

NON_UTF8 = b"\xff\xfe\x80\x81\xc3\x28 not utf-8 "
VALID_PAYLOAD = ('{"data": {"txHash": "%s"}}' % TX_HASH).encode("utf-8")

FAKE_MM = """\
#!/usr/bin/env python3
import os, sys

def _emit(stream, tag, key):
    path = os.environ.get("FAKE_MM_%s_FILE_%s" % (tag, key))
    if path:
        with open(path, "rb") as fh:
            stream.buffer.write(fh.read())
    else:
        stream.buffer.write(
            os.environ.get("FAKE_MM_%s_%s" % (tag, key), "").encode("utf-8")
        )
    stream.buffer.flush()

key = (sys.argv[1] if len(sys.argv) > 1 else "DEFAULT").upper()
_emit(sys.stdout, "STDOUT", key)
_emit(sys.stderr, "STDERR", key)
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
def live_wallet(fake_mm, monkeypatch):
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


@pytest.fixture()
def real_wallet(fake_mm, monkeypatch):
    """Live-mode wallet with the REAL require_ready()/status() path."""
    monkeypatch.setattr(mmw, "kill_switch_tripped", lambda *a, **k: False)
    monkeypatch.setenv("METAMASK_AGENT_LIVE", "true")
    return MetaMaskAgentWallet(mm_bin=fake_mm)


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


# ----------------------------------------------------------------------
# Break 1a: non-UTF-8 bytes in STDOUT at exit 0. The embedded payload is
# unreachable behind undecodable garbage -> pinned hash-less success,
# never a reported failure for an executed trade.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ALL_METHODS)
def test_nonutf8_stdout_reports_hashless_success(live_wallet, mm_bytes, method):
    mm_bytes(_stream_key(method), stdout=NON_UTF8 + VALID_PAYLOAD)
    res = _call(live_wallet, method)
    assert isinstance(res, OrderResult)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == ""
    assert isinstance(res.order_id, str)
    assert res.error == ""


# ----------------------------------------------------------------------
# Break 1b (sharpest): stdout is a perfectly valid payload, stderr
# carries non-UTF-8 bytes, exit 0. stderr bytes are irrelevant on a 0
# exit and must not poison the result: success WITH the hash.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ALL_METHODS)
def test_nonutf8_stderr_valid_stdout_reports_success_with_hash(
    live_wallet, mm_bytes, method
):
    mm_bytes(_stream_key(method), stdout=VALID_PAYLOAD, stderr=NON_UTF8)
    res = _call(live_wallet, method)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == TX_HASH
    assert res.error == ""


# ----------------------------------------------------------------------
# Guard: nonzero exit is still failure, and the stderr text still
# surfaces in the error (decode tolerance must not swallow it).
# ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ALL_METHODS)
def test_nonutf8_stderr_nonzero_exit_still_failure(live_wallet, mm_bytes, method):
    mm_bytes(
        _stream_key(method),
        stderr=NON_UTF8 + b"policy rejected this transfer",
        exit_code=3,
    )
    res = _call(live_wallet, method)
    assert res.success is False
    assert res.simulated is False
    assert "policy rejected" in res.error


# ----------------------------------------------------------------------
# Break 2: non-dict doctor payload -> status() reports not-ready and the
# write methods return a failure OrderResult; nothing raises.
# ----------------------------------------------------------------------


def test_status_tolerates_nondict_doctor_payload(real_wallet, mm_bytes):
    mm_bytes("doctor", stdout=b"[]")
    st = real_wallet.status()
    assert isinstance(st, WalletStatus)
    assert st.authenticated is False
    assert st.initialized is False


@pytest.mark.parametrize("method", EXEC_METHODS)
def test_nondict_doctor_payload_returns_failure_result(
    real_wallet, mm_bytes, method
):
    mm_bytes("doctor", stdout=b"[]")
    res = _call(real_wallet, method)
    assert isinstance(res, OrderResult)
    assert res.success is False
    assert res.simulated is False
    assert "not authenticated/initialized" in res.error


# ----------------------------------------------------------------------
# Wart: a truthy non-string txHash must not shadow a valid string
# ``hash`` fallback.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("method", ALL_METHODS)
def test_wrong_type_txhash_falls_back_to_hash_string(
    live_wallet, mm_bytes, method
):
    payload = (
        '{"data": {"txHash": {"nested": 1}, "hash": "%s"}}' % TX_HASH
    ).encode("utf-8")
    mm_bytes(_stream_key(method), stdout=payload)
    res = _call(live_wallet, method)
    assert res.success is True
    assert res.simulated is False
    assert res.order_id == TX_HASH
