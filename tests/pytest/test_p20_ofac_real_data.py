"""P20 OFAC real-data tests: vendored SDN snapshot + updater + oracle wiring.

The vendored snapshot (src/sincor2/defi/data/p20_ofac_sdn.json) is
deterministic — spot checks pin entries verified present in it. The
synthetic reference fixture is untouched; these tests do not depend on
network access (fetch is monkeypatched).
"""

import json
import os
import time

import pytest

from sincor2.defi import ofac_sdn_updater
from sincor2.defi.compliance_automation import Verdict
from sincor2.defi.ofac_sdn import (
    build_ofac_oracle,
    build_screening_sets,
    load_snapshot,
    normalize_name,
    parse_sdn_xml,
    screen_legal_name,
    screen_wallet_address,
    snapshot_path,
)

# Entries verified present in the vendored snapshot (OFAC SDN, pub 09/23/2026).
KNOWN_EVM_ADDR = "0x252a8bd2319d8a555b872990601221b3a2053bce"  # MESRI, Behzad
KNOWN_TRX_ADDR = "TNiq9AXBp9EjUqhDhrwrfvAA8U3GUQZH81"  # BANK MARKAZI (uid 4632)
KNOWN_NAME = "BANK MARKAZI JOMHOURI ISLAMI IRAN"
KNOWN_ALIAS = "ZAYDAN, Muhammad"  # alias of ABBAS, Abu (uid 2674)
CLEAN_NAME = "SINCOR TEST CLEAN ENTITY 9999"
CLEAN_ADDR = "0x000000000000000000000000000000000000dEaD"

NS = ("https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview"
      "/exports/XML")


def _fake_sdn_xml(pub_date: str, record_count: int = 15000) -> bytes:
    return (f'<sdnList xmlns="{NS}">'
            f"<publshInformation><Publish_Date>{pub_date}</Publish_Date>"
            f"<Record_Count>{record_count}</Record_Count></publshInformation>"
            '<sdnEntry><uid>1</uid><lastName>FAKE TEST ENTITY</lastName>'
            "<sdnType>Entity</sdnType>"
            "<programList><program>FAKE</program></programList>"
            "</sdnEntry></sdnList>").encode()


# -- snapshot validity ------------------------------------------------------

def test_snapshot_loads_with_provenance():
    snap = load_snapshot()
    assert snap["source"] == "OFAC SDN List"
    assert snap["source_url"] == \
        "https://www.treasury.gov/ofac/downloads/sdn.xml"
    assert snap["publication_date"] == "09/23/2026"
    assert snap["entry_count"] == len(snap["entries"]) >= 15000
    assert snap["fetched_at"]  # ISO timestamp present


def test_screening_sets_are_substantial():
    snap = load_snapshot()
    addresses, names = build_screening_sets(snap)
    assert len(addresses) >= 1000      # digital-currency addresses
    assert len(names) >= 30000        # primary names + aliases


def test_normalize_name_is_conservative():
    assert normalize_name("Bank Markazi Jomhouri Islami Iran") == \
        "BANK MARKAZI JOMHOURI ISLAMI IRAN"
    assert normalize_name("ABBAS, Abu") == "ABBAS ABU"


# -- oracle wiring: real hits and clean passes -------------------------------

@pytest.fixture()
def oracle():
    return build_ofac_oracle()


def test_known_evm_address_is_hit(oracle):
    res = screen_wallet_address(oracle, KNOWN_EVM_ADDR.upper(), now=time.time())
    # uppercased input must still match: canonical lowercase comparison
    assert res.verdict == Verdict.FAIL
    assert "sanctions list" in res.detail


def test_known_trx_address_is_hit(oracle):
    res = screen_wallet_address(oracle, KNOWN_TRX_ADDR, now=time.time())
    assert res.verdict == Verdict.FAIL


def test_known_name_is_hit(oracle):
    res = screen_legal_name(oracle, "bank markazi jomhouri islami iran",
                            now=time.time())
    assert res.verdict == Verdict.FAIL


def test_known_alias_is_hit(oracle):
    res = screen_legal_name(oracle, KNOWN_ALIAS, now=time.time())
    assert res.verdict == Verdict.FAIL


def test_clean_name_and_address_pass(oracle):
    assert screen_legal_name(oracle, CLEAN_NAME,
                             now=time.time()).verdict == Verdict.PASS
    assert screen_wallet_address(oracle, CLEAN_ADDR,
                                 now=time.time()).verdict == Verdict.PASS


def test_oracle_carries_provenance_not_reference_only(oracle):
    assert oracle.reference_only is False
    assert oracle.ofac_publication_date == "09/23/2026"
    assert oracle.ofac_entry_count >= 15000
    assert oracle.ofac_screening_addresses >= 1000


# -- freshness guards ---------------------------------------------------------

def test_stale_snapshot_fails_closed(oracle):
    now = time.time()
    oracle.aml.list_updated_at = now - 72 * 3600  # 72h old
    res = screen_legal_name(oracle, CLEAN_NAME, now=now)
    assert res.verdict == Verdict.STALE
    assert "48h" in res.detail


def test_aging_snapshot_alerts_but_passes(oracle):
    now = time.time()
    oracle.aml.list_updated_at = now - 30 * 3600  # 30h old
    res = screen_legal_name(oracle, CLEAN_NAME, now=now)
    assert res.verdict == Verdict.PASS
    assert "freshness alert" in res.detail


# -- updater: idempotency, atomicity, verification -----------------------------

@pytest.fixture()
def workdir(tmp_path):
    dest = str(tmp_path / "p20_ofac_sdn.json")
    snap = load_snapshot()
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(snap, f)
    return dest


def _patch_fetch(monkeypatch, payload: bytes):
    monkeypatch.setattr(ofac_sdn_updater, "fetch_sdn_xml",
                        lambda url, timeout=180: payload)
    monkeypatch.setattr(ofac_sdn_updater, "MIN_BYTES", 10)
    monkeypatch.setattr(ofac_sdn_updater, "MIN_RECORDS", 1)


def test_updater_idempotent_on_same_publication_date(workdir, monkeypatch):
    _patch_fetch(monkeypatch, _fake_sdn_xml("09/23/2026"))
    before = os.path.getmtime(workdir)
    result = ofac_sdn_updater.update_snapshot(out_path=workdir)
    assert result["status"] == "unchanged"
    assert os.path.getmtime(workdir) == before  # untouched


def test_updater_installs_new_publication(workdir, monkeypatch):
    _patch_fetch(monkeypatch, _fake_sdn_xml("09/30/2026"))
    result = ofac_sdn_updater.update_snapshot(out_path=workdir)
    assert result["status"] == "updated"
    assert result["publication_date"] == "09/30/2026"
    assert result["previous_publication_date"] == "09/23/2026"
    assert load_snapshot(workdir)["publication_date"] == "09/30/2026"


def test_updater_rejects_garbage_atomically(workdir, monkeypatch):
    _patch_fetch(monkeypatch, b"<html>not an SDN export</html>")
    with open(workdir, "rb") as f:
        original = f.read()
    with pytest.raises(ofac_sdn_updater.UpdateError):
        ofac_sdn_updater.update_snapshot(out_path=workdir)
    with open(workdir, "rb") as f:
        assert f.read() == original  # half-written list impossible
    leftovers = [p for p in os.listdir(os.path.dirname(workdir))
                 if ".tmp." in p]
    assert leftovers == []


def test_updater_rejects_truncated_record_count(workdir, monkeypatch):
    _patch_fetch(monkeypatch, _fake_sdn_xml("09/30/2026", record_count=12))
    monkeypatch.setattr(ofac_sdn_updater, "MIN_RECORDS", 10_000)
    with pytest.raises(ofac_sdn_updater.UpdateError):
        ofac_sdn_updater.update_snapshot(out_path=workdir)


def test_parse_rejects_wrong_root():
    with pytest.raises(ValueError):
        parse_sdn_xml(b"<html></html>", "2026-09-29T00:00:00+00:00")
