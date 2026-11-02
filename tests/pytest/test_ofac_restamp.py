"""W-35 regression tests: the OFAC updater re-stamps ``fetched_at`` on a
successful verification whose publication date is unchanged.

Freshness (sanctions_screen / the P20 oracle) is measured from
``fetched_at`` with a 48h fail-closed window. Before the fix, an
unchanged publication date returned early without rewriting, so
``fetched_at`` measured the last list *change* instead of the last
successful *verification*. OFAC routinely goes >48h without
republishing, which self-halted the sanctions path despite a healthy
daily updater.

Every test here uses a synthetic snapshot in a tmp dir and a
monkeypatched fetch — never the network, never the vendored data file.
"""

import json
import os

import pytest

from sincor2.defi import ofac_sdn_updater
from sincor2.defi.ofac_sdn import load_snapshot

NS = ("https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview"
      "/exports/XML")

PUB_DATE = "10/28/2026"
OLD_FETCHED_AT = "2026-10-30T16:10:16+00:00"
VERIFY_TIME = "2026-11-02T10:13:00+00:00"
AGED_MTIME = 1_700_000_000  # fixed old mtime: untouched-vs-rewritten is observable


def _fake_sdn_xml(pub_date: str, record_count: int = 15000) -> bytes:
    return (f'<sdnList xmlns="{NS}">'
            f"<publshInformation><Publish_Date>{pub_date}</Publish_Date>"
            f"<Record_Count>{record_count}</Record_Count></publshInformation>"
            '<sdnEntry><uid>1</uid><lastName>FAKE TEST ENTITY</lastName>'
            "<sdnType>Entity</sdnType>"
            "<programList><program>FAKE</program></programList>"
            "</sdnEntry></sdnList>").encode()


def _snapshot_dict() -> dict:
    return {
        "source": "OFAC SDN List",
        "source_url": "https://www.treasury.gov/ofac/downloads/sdn.xml",
        "publication_date": PUB_DATE,
        "record_count": 15000,
        "fetched_at": OLD_FETCHED_AT,
        "entry_count": 2,
        "entries": [
            {"uid": "1", "name": "ALPHA ENTITY", "type": "Entity",
             "programs": ["FAKE"], "aliases": [],
             "digital_currency_addresses": [], "ids": []},
            {"uid": "2", "name": "BETA, Person", "type": "Individual",
             "programs": ["FAKE"], "aliases": ["BETA ALIAS"],
             "digital_currency_addresses":
                 [{"currency": "XBT", "address": "1FakeBitcoinAddress"}],
             "ids": []},
        ],
    }


@pytest.fixture()
def snap_file(tmp_path):
    dest = str(tmp_path / "p20_ofac_sdn.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(_snapshot_dict(), f)
    os.utime(dest, (AGED_MTIME, AGED_MTIME))
    return dest


def _patch_fetch(monkeypatch, payload: bytes):
    monkeypatch.setattr(ofac_sdn_updater, "fetch_sdn_xml",
                        lambda url, timeout=180: payload)
    monkeypatch.setattr(ofac_sdn_updater, "MIN_BYTES", 10)
    monkeypatch.setattr(ofac_sdn_updater, "MIN_RECORDS", 1)


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def test_unchanged_publication_restamps_fetched_at(snap_file, monkeypatch):
    _patch_fetch(monkeypatch, _fake_sdn_xml(PUB_DATE))
    before = load_snapshot(snap_file)
    result = ofac_sdn_updater.update_snapshot(out_path=snap_file,
                                              now=VERIFY_TIME)
    assert result["status"] == "unchanged"
    assert result["restamped"] is True
    assert result["fetched_at"] == VERIFY_TIME
    after = load_snapshot(snap_file)
    assert after["fetched_at"] == VERIFY_TIME  # advanced to verification time
    assert after["publication_date"] == before["publication_date"]
    assert after["entries"] == before["entries"]  # content-identical


def test_failed_fetch_never_touches_file(snap_file, monkeypatch):
    def _boom(url, timeout=180):
        raise ofac_sdn_updater.UpdateError("simulated network failure")

    monkeypatch.setattr(ofac_sdn_updater, "fetch_sdn_xml", _boom)
    original = _read_bytes(snap_file)
    with pytest.raises(ofac_sdn_updater.UpdateError):
        ofac_sdn_updater.update_snapshot(out_path=snap_file, now=VERIFY_TIME)
    assert _read_bytes(snap_file) == original
    assert os.path.getmtime(snap_file) == AGED_MTIME


def test_parse_failure_never_touches_file(snap_file, monkeypatch):
    _patch_fetch(monkeypatch, b"<html>definitely not an SDN export</html>")
    original = _read_bytes(snap_file)
    with pytest.raises(ofac_sdn_updater.UpdateError):
        ofac_sdn_updater.update_snapshot(out_path=snap_file, now=VERIFY_TIME)
    assert _read_bytes(snap_file) == original
    assert os.path.getmtime(snap_file) == AGED_MTIME


def test_changed_publication_still_rewrites_fully(snap_file, monkeypatch):
    _patch_fetch(monkeypatch, _fake_sdn_xml("10/29/2026"))
    result = ofac_sdn_updater.update_snapshot(out_path=snap_file,
                                              now=VERIFY_TIME)
    assert result["status"] == "updated"
    assert result["publication_date"] == "10/29/2026"
    assert result["previous_publication_date"] == PUB_DATE
    after = load_snapshot(snap_file)
    assert after["publication_date"] == "10/29/2026"
    assert after["fetched_at"] == VERIFY_TIME
    assert [e["name"] for e in after["entries"]] == ["FAKE TEST ENTITY"]
