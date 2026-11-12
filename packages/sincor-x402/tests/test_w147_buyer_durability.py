"""W-147 regression tests: VerifiedBuyer never double-pays — across a
process restart, and under concurrency.

Red-team hunt 46 CONFIRMED both on the unfixed package: (a) a fresh
VerifiedBuyer instance (restart) signed a second authorization for the
same instruction_id because the journal was an in-memory dict; (b) two
concurrent fetch() calls for one instruction both signed because the
journal lock was released between the check and the signing. These
tests pin the fixed behavior: a durable file-backed journal and an
atomic claim held across check-then-sign.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sincor_x402 import (AmbiguousPayment, IdempotencyConflict,
                         VerifiedBuyer, default_journal_path)

from x402pkg_helpers import BUYER

URL = "https://example.invalid/api"
PAY_TO = "0x2222222222222222222222222222222222222222"
AMOUNT = 1000


def _challenge():
    return {"x402Version": 1, "accepts": [{
        "scheme": "exact", "network": "base",
        "maxAmountRequired": str(AMOUNT),
        "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "payTo": PAY_TO, "maxTimeoutSeconds": 60}]}


def _transport_factory(paid_nonces, delay_402=0.0, paid_status=200):
    def transport(method, url, body, headers):
        if "X-PAYMENT" in headers or "PAYMENT-SIGNATURE" in headers:
            paid_nonces.append(json.loads(headers["X-PAYMENT"])["payload"]
                               ["authorization"]["nonce"])
            if paid_status != 200:
                return paid_status, {}, b"gateway exploded"
            return 200, {}, json.dumps({"data": "ok"}).encode()
        if delay_402:
            time.sleep(delay_402)
        return 402, {}, json.dumps(_challenge()).encode()
    return transport


def _buyer(signer, transport, journal):
    return VerifiedBuyer(BUYER, signer, transport=transport,
                         journal_path=journal)


# --- (a) restart durability ----------------------------------------------

def test_restart_fresh_instance_does_not_repay(tmp_path, signer):
    journal = tmp_path / "journal.json"
    paid = []
    b1 = _buyer(signer, _transport_factory(paid), journal)
    out1 = b1.fetch(URL, instruction_id="instr-restart", body={"q": "x"})
    assert out1["paid"] is True
    assert signer.calls == 1 and len(paid) == 1

    # Fresh instance, same journal file — the process-restart case.
    b2 = _buyer(signer, _transport_factory(paid), journal)
    out2 = b2.fetch(URL, instruction_id="instr-restart", body={"q": "x"})
    assert out2 == out1                      # recorded outcome returned
    assert b2.signatures_created == 0        # never signed again
    assert signer.calls == 1                 # still one signature total
    assert len(paid) == 1                    # still one payment sent


def test_restart_conflict_and_ambiguity_survive(tmp_path, signer):
    journal = tmp_path / "journal.json"
    paid = []
    b1 = _buyer(signer, _transport_factory(paid, paid_status=500), journal)
    with pytest.raises(AmbiguousPayment):
        b1.fetch(URL, instruction_id="instr-amb", body={"q": "x"})
    assert signer.calls == 1

    b2 = _buyer(signer, _transport_factory(paid), journal)
    with pytest.raises(AmbiguousPayment):
        b2.fetch(URL, instruction_id="instr-amb", body={"q": "x"})
    with pytest.raises(IdempotencyConflict):
        b2.fetch(URL, instruction_id="instr-amb", body={"q": "DIFFERENT"})
    assert signer.calls == 1 and len(paid) == 1


def test_restart_midflight_resends_same_payload_not_new_signature(
        tmp_path, signer):
    journal = tmp_path / "journal.json"
    paid = []
    state = {"dropped": False}
    base = _transport_factory(paid)

    def flaky(method, url, body, headers):
        if ("X-PAYMENT" in headers) and not state["dropped"]:
            state["dropped"] = True
            paid.append(json.loads(headers["X-PAYMENT"])["payload"]
                        ["authorization"]["nonce"])
            raise ConnectionError("connection dropped after send")
        return base(method, url, body, headers)

    b1 = _buyer(signer, flaky, journal)
    with pytest.raises(ConnectionError):
        b1.fetch(URL, instruction_id="instr-mid", body={"q": "x"})

    b2 = _buyer(signer, _transport_factory(paid), journal)
    out = b2.fetch(URL, instruction_id="instr-mid", body={"q": "x"})
    assert out["ok"] is True and out["paid"] is True
    assert signer.calls == 1                     # no second signature
    assert len(paid) == 2 and paid[0] == paid[1]  # same nonce resent


def test_journal_file_is_written_and_default_path_is_file_backed(
        tmp_path, signer):
    journal = tmp_path / "nested" / "journal.json"
    buyer = _buyer(signer, _transport_factory([]), journal)
    buyer.fetch(URL, instruction_id="instr-file", body={"q": "x"})
    assert journal.exists()
    data = json.loads(journal.read_text())
    assert data["entries"]["instr-file"]["outcome"]["paid"] is True
    assert data["entries"]["instr-file"]["claimed"] is True

    default_buyer = VerifiedBuyer(BUYER, signer,
                                  transport=_transport_factory([]))
    assert default_buyer.journal_path == default_journal_path()
    assert default_buyer.journal_path is not None


def test_cross_process_restart_does_not_repay(tmp_path):
    """Two separate OS processes, one journal file: the second process
    must not sign — the journal is durable on disk, not per-process."""
    journal = tmp_path / "journal.json"
    src = Path(__file__).resolve().parent.parent / "src"
    child = tmp_path / "child_buyer.py"
    child.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(src)!r})\n"
        "from sincor_x402 import VerifiedBuyer\n"
        "challenge = {'x402Version': 1, 'accepts': [{\n"
        "    'scheme': 'exact', 'network': 'base',\n"
        "    'maxAmountRequired': '1000',\n"
        "    'asset': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',\n"
        f"    'payTo': {PAY_TO!r}, 'maxTimeoutSeconds': 60}}]}}\n"
        "paid = []\n"
        "def transport(method, url, body, headers):\n"
        "    if 'X-PAYMENT' in headers:\n"
        "        paid.append(1)\n"
        "        return 200, {}, json.dumps({'data': 'ok'}).encode()\n"
        "    return 402, {}, json.dumps(challenge).encode()\n"
        f"buyer = VerifiedBuyer({BUYER!r}, lambda td: '0x' + 'cd' * 32,\n"
        f"                      transport=transport,"
        f" journal_path={str(journal)!r})\n"
        "out = buyer.fetch('https://example.invalid/api',\n"
        "                  instruction_id='instr-xproc', body={'q': 'x'})\n"
        "print(json.dumps({'paid': out['paid'],\n"
        "                  'signatures': buyer.signatures_created,\n"
        "                  'sent': len(paid)}))\n")
    results = []
    for _ in range(2):
        out = subprocess.run([sys.executable, str(child)],
                             capture_output=True, text=True,
                             env={**os.environ, "PYTHONPATH": str(src)})
        assert out.returncode == 0, out.stderr
        results.append(json.loads(out.stdout.strip()))
    assert results[0] == {"paid": True, "signatures": 1, "sent": 1}
    # Second process: recorded outcome, zero new signatures, zero sends.
    assert results[1] == {"paid": True, "signatures": 0, "sent": 0}


# --- (b) concurrency -------------------------------------------------------

def test_concurrent_same_instruction_signs_exactly_once(tmp_path, signer):
    journal = tmp_path / "journal.json"
    paid = []
    buyer = _buyer(signer,
                   _transport_factory(paid, delay_402=0.3), journal)
    results, errors = [], []

    def worker():
        try:
            results.append(buyer.fetch(URL, instruction_id="instr-race",
                                       body={"q": "x"}))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(results) == 2 and all(r["paid"] for r in results)
    assert buyer.signatures_created == 1
    assert signer.calls == 1
    assert len(paid) == 1 and len(set(paid)) == 1


def test_concurrent_instances_sharing_journal_sign_once(tmp_path, signer):
    journal = tmp_path / "journal.json"
    paid = []
    buyers = [_buyer(signer, _transport_factory(paid, delay_402=0.3),
                     journal) for _ in range(2)]
    results, errors = [], []

    def worker(buyer):
        try:
            results.append(buyer.fetch(URL, instruction_id="instr-xinst",
                                       body={"q": "x"}))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(b,)) for b in buyers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(results) == 2
    assert sum(b.signatures_created for b in buyers) == 1
    assert signer.calls == 1
    assert len(paid) == 1
