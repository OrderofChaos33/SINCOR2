"""OFAC SDN list parser — lean vendored snapshot for the P20 oracle.

Downloads nothing on import. :func:`parse_sdn_xml` turns the official
``sdn.xml`` (OFAC Specially Designated Nationals list) into the lean JSON
snapshot the sanctions screener needs: names, aliases, programs, and IDs
(including digital-currency addresses). The raw ~30MB XML is never vendored.

Snapshot schema (``src/sincor2/defi/data/p20_ofac_sdn.json``)::

    {
      "source": "OFAC SDN List",
      "source_url": "https://www.treasury.gov/ofac/downloads/sdn.xml",
      "publication_date": "2026-09-23",      # from publshInformation
      "record_count": 19391,                 # from publshInformation
      "fetched_at": "2026-09-29T16:30:00+00:00",
      "entries": [
        {"uid": "36", "name": "AEROCARIBBEAN AIRLINES", "type": "Entity",
         "programs": ["CUBA"], "aliases": ["AERO CARIBBEAN"],
         "digital_currency_addresses": [{"currency": "BTC", "address": "..."}],
         "ids": [{"type": "...", "number": "..."}]}
      ]
    }

Name screening uses exact match on :func:`normalize_name` output. This is
deliberately conservative: production name screening wants fuzzy matching,
which is documented follow-up work, not silently implemented here.
"""

import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Set, Tuple

SDN_SOURCE_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
SDN_XMLNS = ("https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview"
             "/exports/XML")

SNAPSHOT_FILENAME = "p20_ofac_sdn.json"


def snapshot_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", SNAPSHOT_FILENAME)


def _t(elem, tag):
    """Text of namespaced child, '' when absent."""
    if elem is None:
        return ""
    child = elem.find(f"{{{SDN_XMLNS}}}{tag}")
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def normalize_name(name: str) -> str:
    """Canonical form for name screening: uppercased, punctuation and
    whitespace collapsed. Exact-match only — no fuzzy logic."""
    upper = name.upper()
    # Keep letters, digits, and spaces; collapse the rest.
    cleaned = re.sub(r"[^A-Z0-9 ]", " ", upper)
    return re.sub(r"\s+", " ", cleaned).strip()


def _entry_name(entry) -> str:
    last = _t(entry, "lastName")
    first = _t(entry, "firstName")
    if first:
        return f"{last}, {first}" if last else first
    return last


def _parse_digital_currency(id_type: str) -> str:
    """'Digital Currency Address - BTC' -> 'BTC'; '' when not a DCA id."""
    m = re.match(r"Digital Currency Address\s*-\s*(\S+)", id_type or "")
    return m.group(1).upper() if m else ""


def parse_sdn_xml(xml_bytes: bytes, fetched_at: str) -> Dict[str, Any]:
    """Parse official sdn.xml bytes into the lean snapshot dict.

    Raises ValueError when the document is not a well-formed SDN export
    (wrong root, missing publication info, or zero entries).
    """
    root = ET.fromstring(xml_bytes)
    if root.tag != f"{{{SDN_XMLNS}}}sdnList":
        raise ValueError(f"unexpected SDN root tag: {root.tag}")
    pub = root.find(f"{{{SDN_XMLNS}}}publshInformation")
    publication_date = _t(pub, "Publish_Date")
    record_count_raw = _t(pub, "Record_Count")
    if not publication_date or not record_count_raw:
        raise ValueError("SDN export missing publshInformation")
    record_count = int(record_count_raw)

    entries: List[Dict[str, Any]] = []
    for e in root.iter(f"{{{SDN_XMLNS}}}sdnEntry"):
        programs: List[str] = []
        pl = e.find(f"{{{SDN_XMLNS}}}programList")
        if pl is not None:
            programs = [p.text.strip() for p in pl if p.text and p.text.strip()]

        aliases: List[str] = []
        al = e.find(f"{{{SDN_XMLNS}}}akaList")
        if al is not None:
            for aka in al:
                nm = _t(aka, "lastName")
                fn = _t(aka, "firstName")
                full = f"{nm}, {fn}" if fn else nm
                if full:
                    aliases.append(full)

        dcas: List[Dict[str, str]] = []
        ids: List[Dict[str, str]] = []
        il = e.find(f"{{{SDN_XMLNS}}}idList")
        if il is not None:
            for i in il:
                id_type = _t(i, "idType")
                id_number = _t(i, "idNumber")
                if not id_number:
                    continue
                ccy = _parse_digital_currency(id_type)
                if ccy:
                    dcas.append({"currency": ccy, "address": id_number})
                else:
                    ids.append({"type": id_type, "number": id_number})

        entries.append({
            "uid": _t(e, "uid"),
            "name": _entry_name(e),
            "type": _t(e, "sdnType"),
            "programs": programs,
            "aliases": aliases,
            "digital_currency_addresses": dcas,
            "ids": ids,
        })

    if not entries:
        raise ValueError("SDN export contained zero entries")
    return {
        "source": "OFAC SDN List",
        "source_url": SDN_SOURCE_URL,
        "publication_date": publication_date,
        "record_count": record_count,
        "fetched_at": fetched_at,
        "entry_count": len(entries),
        "entries": entries,
    }


def build_screening_sets(snapshot: Dict[str, Any]) -> Tuple[Set[str], Set[str]]:
    """Derive the screener's match sets from a snapshot.

    Returns (addresses, names):
    - addresses: every digital-currency address; EVM (0x…) addresses are
      lowercased for canonical comparison, others kept verbatim.
    - names: normalize_name() of every primary name and alias.
    """
    addresses: Set[str] = set()
    names: Set[str] = set()
    for entry in snapshot["entries"]:
        for dca in entry.get("digital_currency_addresses", []):
            addr = dca["address"].strip()
            addresses.add(addr.lower() if addr.startswith("0x") else addr)
        primary = normalize_name(entry.get("name", ""))
        if primary:
            names.add(primary)
        for alias in entry.get("aliases", []):
            norm = normalize_name(alias)
            if norm:
                names.add(norm)
    return addresses, names


def load_snapshot(path: str = None) -> Dict[str, Any]:
    """Load and validate a vendored snapshot. Raises on any defect."""
    with open(path or snapshot_path(), encoding="utf-8") as f:
        data = json.load(f)
    for key in ("source", "source_url", "publication_date",
                "fetched_at", "entries"):
        if key not in data:
            raise ValueError(f"SDN snapshot missing metadata key: {key}")
    if not isinstance(data["entries"], list) or not data["entries"]:
        raise ValueError("SDN snapshot has no entries")
    return data


def write_snapshot_atomic(snapshot: Dict[str, Any], path: str) -> None:
    """Write a snapshot atomically: temp file + fsync + os.replace, so a
    crash or failed verification can never leave a half-written list."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# -- OFAC-wired oracle ------------------------------------------------------

def _snapshot_epoch(fetched_at: str) -> float:
    import datetime
    try:
        return datetime.datetime.fromisoformat(fetched_at).timestamp()
    except (ValueError, TypeError):
        import time
        return time.time()


def build_ofac_oracle(path: str = None, kyc=None, geo=None):
    """Build the production-path oracle screening the real OFAC SDN list.

    The sanctions set is the union of every digital-currency address and
    every normalized primary/alias name in the vendored snapshot. KYC
    issuers and the geo blocklist remain operator configuration (default:
    empty) — OFAC data only feeds the sanctions adapter.

    ``list_updated_at`` comes from the snapshot's ``fetched_at``, so the
    24h-alert / 48h-fail-closed freshness guards measure the real age of
    the local list. Pair with the daily updater (see
    docs/ops/OFAC_SDN_UPDATER.md).
    """
    from sincor2.defi.compliance_automation import (
        AMLAdapter, ComplianceOracle, GeoRegistry, KYCAdapter,
    )
    import time
    snap = load_snapshot(path)
    addresses, names = build_screening_sets(snap)
    aml = AMLAdapter(sanctions=addresses | names, fund_flows={},
                     list_updated_at=_snapshot_epoch(snap["fetched_at"]))
    oracle = ComplianceOracle(
        kyc or KYCAdapter({}),
        aml,
        geo or GeoRegistry(admin="ofac-oracle-admin"),
        reference_only=False,
    )
    # Provenance, surfaced for operators and auditors.
    oracle.ofac_source = snap["source"]
    oracle.ofac_source_url = snap["source_url"]
    oracle.ofac_publication_date = snap["publication_date"]
    oracle.ofac_record_count = snap["record_count"]
    oracle.ofac_entry_count = snap["entry_count"]
    oracle.ofac_fetched_at = snap["fetched_at"]
    oracle.ofac_screening_addresses = len(addresses)
    oracle.ofac_screening_names = len(names)
    return oracle


def screen_wallet_address(oracle, address: str, now: float = None):
    """Screen a wallet address against the OFAC snapshot.

    EVM addresses are lowercased for canonical comparison (the snapshot
    stores them lowercased); other chains pass through verbatim.
    """
    acct = (address.lower() if address[:2].lower() == "0x" else address)
    return oracle.aml.screen(acct, now=now)


def screen_legal_name(oracle, name: str, now: float = None):
    """Screen a legal name against the OFAC snapshot (exact match on the
    normalized form — see :func:`normalize_name`)."""
    return oracle.aml.screen(normalize_name(name), now=now)
