"""OFAC SDN list parser — lean vendored snapshot for the P20 oracle.

Downloads nothing on import. :func:`parse_sdn_xml` turns the official
``sdn.xml`` (OFAC Specially Designated Nationals list) into the lean JSON
snapshot the sanctions screener needs: names, aliases, programs, and IDs
(including digital-currency addresses). The raw ~30MB XML is never vendored.

Snapshot schema (the parsed form, however it is stored)::

    {
      "source": "OFAC SDN List",
      "source_url": "https://www.treasury.gov/ofac/downloads/sdn.xml",
      "publication_date": "2026-10-01",      # from publshInformation
      "record_count": 19483,                 # from publshInformation
      "fetched_at": "2026-10-01T21:57:58+00:00",
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

On-disk storage (vendored copy): the snapshot is stored as UTF-8 *text*
under ``src/sincor2/defi/data/`` — a small JSON manifest
(``p20_ofac_sdn.manifest.json``) plus chunk files, each holding a slice
of the base64-encoded gzipped snapshot JSON. Text form is deliberate:
the GitHub connector's push route validates UTF-8 text and rejects
binary content outright, so a ``.gz`` file could never be pushed
through it, while these chunk files can. :func:`load_snapshot`
reassembles the chunks, verifies both SHA-256 digests recorded in the
manifest, gunzips, and parses — and it still reads plain-JSON and
gzip snapshot files at explicit paths (``SINCOR_DATA_DIR`` copies,
test fixtures) for backward compatibility.
"""

import base64
import gzip
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Set, Tuple

SDN_SOURCE_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
SDN_XMLNS = ("https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview"
             "/exports/XML")

#: The vendored snapshot's manifest file. ``snapshot_path()`` points
#: here; the payload lives in the chunk files the manifest names.
SNAPSHOT_FILENAME = "p20_ofac_sdn.manifest.json"

#: Manifest ``format`` marker for the base64-text-chunk storage form.
TEXT_FORMAT = "sincor-ofac-sdn-text/1"

#: Base64 characters per chunk file (before line wrapping). With the
#: 100-column wrap each chunk file stays comfortably under 700 KB.
_CHUNK_CHARS = 600_000
_WRAP_COLUMNS = 100

_GZIP_MAGIC = b"\x1f\x8b"


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


def _serialize_snapshot(snapshot: Dict[str, Any]) -> bytes:
    """Canonical payload bytes for a snapshot dict (compact JSON)."""
    return json.dumps(
        snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _validate_snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("source", "source_url", "publication_date",
                "fetched_at", "entries"):
        if key not in data:
            raise ValueError(f"SDN snapshot missing metadata key: {key}")
    if not isinstance(data["entries"], list) or not data["entries"]:
        raise ValueError("SDN snapshot has no entries")
    return data


def _load_text_chunks(manifest_path: str,
                      manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Reassemble a text-chunk snapshot: read the chunk files named by
    the manifest (siblings of the manifest file), base64-decode,
    verify both SHA-256 digests, gunzip, parse. Raises on any defect —
    a torn or tampered chunk set must never screen as a valid list."""
    names = manifest.get("chunks")
    if not isinstance(names, list) or not names:
        raise ValueError("SDN text snapshot manifest names no chunks")
    if manifest.get("chunk_count") != len(names):
        raise ValueError("SDN text snapshot chunk_count mismatch")
    base_dir = os.path.dirname(os.path.abspath(manifest_path))
    parts: List[str] = []
    for name in names:
        if os.path.basename(name) != name:
            raise ValueError(f"SDN chunk name is not a plain file: {name}")
        with open(os.path.join(base_dir, name), encoding="utf-8") as f:
            parts.append("".join(f.read().split()))
    try:
        gz = base64.b64decode("".join(parts), validate=True)
    except ValueError as e:
        raise ValueError(f"SDN text snapshot is not valid base64: {e}") from e
    if hashlib.sha256(gz).hexdigest() != manifest.get("sha256_gzip"):
        raise ValueError("SDN text snapshot gzip digest mismatch")
    payload = gzip.decompress(gz)
    if hashlib.sha256(payload).hexdigest() != manifest.get("sha256_json"):
        raise ValueError("SDN text snapshot JSON digest mismatch")
    return json.loads(payload.decode("utf-8"))


def load_snapshot(path: str = None) -> Dict[str, Any]:
    """Load and validate a vendored snapshot. Raises on any defect.

    Transparently handles every on-disk form:

    - the text-chunk form (``path`` names a manifest JSON whose
      ``format`` is :data:`TEXT_FORMAT`) — chunks are reassembled and
      digest-verified, see :func:`_load_text_chunks`;
    - gzip bytes (magic-byte detection, never filename) — decompressed
      in memory;
    - plain JSON (e.g. a refreshed copy under ``SINCOR_DATA_DIR`` or a
      test fixture at an explicit path) — parsed as-is.
    """
    target = path or snapshot_path()
    with open(target, "rb") as f:
        raw = f.read()
    if raw[:2] == _GZIP_MAGIC:
        data = json.loads(gzip.decompress(raw).decode("utf-8"))
    else:
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict) and data.get("format") == TEXT_FORMAT:
            data = _load_text_chunks(target, data)
    return _validate_snapshot(data)


def _atomic_write_bytes(payload: bytes, path: str) -> None:
    """Temp file + fsync + os.replace: a crash can never leave a
    half-written file at ``path``."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_text_chunks(snapshot: Dict[str, Any], payload: bytes,
                       manifest_path: str) -> None:
    """Write a snapshot in text-chunk form next to ``manifest_path``.

    Chunk file names embed the gzip digest, so a new generation never
    overwrites the live one in place: chunks land first, the manifest
    (written last, atomically) is the commit point, and the previous
    generation's chunk files are removed afterwards.
    """
    base_dir = os.path.dirname(os.path.abspath(manifest_path))
    stem = os.path.basename(manifest_path)
    for suffix in (".manifest.json", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    gz = gzip.compress(payload, mtime=0)
    sha_gzip = hashlib.sha256(gz).hexdigest()
    sha_json = hashlib.sha256(payload).hexdigest()
    encoded = base64.b64encode(gz).decode("ascii")
    slices = [encoded[i:i + _CHUNK_CHARS]
              for i in range(0, len(encoded), _CHUNK_CHARS)] or [""]
    names: List[str] = []
    sizes: List[int] = []
    for idx, text in enumerate(slices):
        name = f"{stem}.b64.{sha_gzip[:8]}.{idx:03d}.txt"
        wrapped = "\n".join(
            text[i:i + _WRAP_COLUMNS]
            for i in range(0, len(text), _WRAP_COLUMNS)) + "\n"
        blob = wrapped.encode("utf-8")
        _atomic_write_bytes(blob, os.path.join(base_dir, name))
        names.append(name)
        sizes.append(len(blob))
    manifest = {
        "format": TEXT_FORMAT,
        "source": snapshot["source"],
        "source_url": snapshot["source_url"],
        "publication_date": snapshot["publication_date"],
        "record_count": snapshot["record_count"],
        "entry_count": snapshot["entry_count"],
        "fetched_at": snapshot["fetched_at"],
        "encoding": "base64(gzip(utf-8 json))",
        "chunk_count": len(names),
        "chunks": names,
        "chunk_bytes": sizes,
        "payload_bytes_gzip": len(gz),
        "payload_bytes_json": len(payload),
        "sha256_gzip": sha_gzip,
        "sha256_json": sha_json,
    }
    _atomic_write_bytes(
        (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
        manifest_path)
    # Retire the previous generation's chunk files (any same-stem chunk
    # not in the live set). A failure here only leaves inert orphans —
    # the loader follows the manifest, never the directory listing.
    keep = set(names)
    for fname in os.listdir(base_dir):
        if (fname.startswith(f"{stem}.b64.") and fname.endswith(".txt")
                and fname not in keep):
            try:
                os.remove(os.path.join(base_dir, fname))
            except OSError:
                pass


def write_snapshot_atomic(snapshot: Dict[str, Any], path: str) -> None:
    """Write a snapshot atomically, so a crash or failed verification
    can never leave a half-written list.

    The form follows the target: the manifest filename (the vendored
    snapshot and ``SINCOR_DATA_DIR`` refresh targets) is written in
    text-chunk form (see :func:`_write_text_chunks`); a ``.gz`` path is
    written gzip-compressed (``mtime=0``: bytes depend only on the
    content); any other path keeps the historical plain-JSON form.
    :func:`load_snapshot` reads all three.
    """
    payload = _serialize_snapshot(snapshot)
    if os.path.basename(path) == SNAPSHOT_FILENAME or \
            path.endswith(".manifest.json"):
        _write_text_chunks(snapshot, payload, path)
    elif path.endswith(".gz"):
        _atomic_write_bytes(gzip.compress(payload, mtime=0), path)
    else:
        _atomic_write_bytes(payload, path)


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
