"""Read-only consistency sweep for JSON persistence stores (SH-2).

A lightweight, loop-side checker that detects lost-update *symptoms* in
the JSON / JSONL store files used across SINCOR2 (the W-192..W-215
lost-update family) without waiting for a red-team hunt to find them:

* unparseable JSON, or a top-level type that does not match expectation;
* duplicate ids in list-of-records stores (given an id key);
* duplicate ``event_id`` values in proof-events-shaped JSONL files —
  the exact corruption that bricks ``ProofEventStore._load`` (W-212);
* JSONL audit-trail seq breaks: gaps, duplicates, out-of-order seqs,
  and hash-chain link breaks (``prev_hash`` != previous ``entry_hash``,
  the W-206 fork symptom);
* conserved-count violations where a store carries a count field next
  to the collection it counts (negative or mismatched counts).

The sweep is strictly READ-ONLY: files are opened for reading only —
never written, locked, truncated, or renamed — so it is safe to run
against live store files. Every problem is returned as a :class:`Finding`;
``sweep()`` never raises for a bad store, it reports it.
``format_work_orders()`` renders findings as board-ready work-order lines.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

__all__ = ["Finding", "sweep", "format_work_orders"]

# Id keys checked automatically for plain (unspecified) list stores and
# JSONL files when no explicit id key is given.
_AUTO_ID_KEYS = ("event_id", "id")
# Top-level fields treated as a conserved record count when an int.
_COUNT_KEYS = ("count", "total", "entry_count", "num_records", "n_records")


@dataclass(frozen=True)
class Finding:
    """One consistency problem in one store file."""

    store: str   # path of the inspected file, as given
    check: str   # machine name of the check that fired
    detail: str  # human-readable specifics (ids, seqs, line numbers)


# ------------------------------------------------------------------ specs

def _normalize(spec: Any) -> dict:
    """Accept a path, (path, kind) tuple, or spec mapping; return a dict."""
    if isinstance(spec, Mapping):
        out = dict(spec)
        out["path"] = Path(out["path"])
        return out
    if isinstance(spec, (tuple, list)):
        path, kind = spec
        return {"path": Path(path), "kind": kind}
    return {"path": Path(spec)}


def _kind_for(spec: dict, path: Path) -> str:
    kind = spec.get("kind")
    if kind:
        return str(kind)
    if path.suffix == ".jsonl":
        # Shape is decided per-file by content checks below; the JSONL
        # checker applies proof-event and audit-trail checks as the
        # fields present dictate.
        return "jsonl"
    return "json"


# ------------------------------------------------------------------ helpers

def _dup_findings(store: str, records: list, key: str, check: str,
                  where: str = "") -> list:
    seen: dict = {}
    findings = []
    for idx, rec in enumerate(records):
        if not isinstance(rec, Mapping) or key not in rec:
            continue
        value = rec[key]
        try:
            hash(value)
        except TypeError:
            continue
        if value in seen:
            findings.append(Finding(
                store, check,
                f"duplicate {key} {value!r} at record {idx} "
                f"(first seen at record {seen[value]}){where}"))
        else:
            seen[value] = idx
    return findings


def _read_text(path: Path) -> str:
    # Read-only open: the sweep never writes, locks, or renames stores.
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ------------------------------------------------------------------ JSON

def _check_json(spec: dict) -> list:
    path: Path = spec["path"]
    store = str(path)
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return [Finding(store, "missing_file", "store file does not exist")]
    except OSError as exc:
        return [Finding(store, "unreadable_file", f"cannot read store: {exc}")]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return [Finding(store, "unparseable_json",
                        f"JSON parse failed: {exc}")]

    findings = []
    expected = spec.get("expected_type")
    if expected:
        actual = type(data).__name__
        if actual != expected:
            findings.append(Finding(
                store, "wrong_top_level_type",
                f"expected top-level {expected}, found {actual}"))
            return findings

    id_key = spec.get("id_key")
    collection = spec.get("collection")
    if isinstance(data, list):
        if id_key:
            findings += _dup_findings(store, data, id_key, "duplicate_id")
        else:
            for key in _AUTO_ID_KEYS:
                if any(isinstance(r, Mapping) and key in r for r in data):
                    check = ("duplicate_event_id" if key == "event_id"
                             else "duplicate_id")
                    findings += _dup_findings(store, data, key, check)
                    break
    elif isinstance(data, dict):
        if id_key and collection and isinstance(data.get(collection), list):
            findings += _dup_findings(store, data[collection], id_key,
                                      "duplicate_id",
                                      where=f" in collection {collection!r}")
        findings += _check_counts(store, data)
    return findings


def _check_counts(store: str, data: dict) -> list:
    findings = []
    lists = [v for v in data.values() if isinstance(v, list)]
    for key in _COUNT_KEYS:
        value = data.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        if value < 0:
            findings.append(Finding(
                store, "negative_count",
                f"count field {key!r} is negative: {value}"))
        elif len(lists) == 1 and value != len(lists[0]):
            findings.append(Finding(
                store, "count_mismatch",
                f"count field {key!r} is {value} but the store holds "
                f"{len(lists[0])} records"))
    return findings


# ------------------------------------------------------------------ JSONL

def _check_jsonl(spec: dict) -> list:
    path: Path = spec["path"]
    store = str(path)
    try:
        text = _read_text(path)
    except FileNotFoundError:
        return [Finding(store, "missing_file", "store file does not exist")]
    except OSError as exc:
        return [Finding(store, "unreadable_file", f"cannot read store: {exc}")]

    findings = []
    records = []  # (line_no, record) for parseable object lines
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            findings.append(Finding(
                store, "unparseable_line",
                f"line {line_no} is not valid JSON: {exc}"))
            continue
        if not isinstance(rec, Mapping):
            findings.append(Finding(
                store, "wrong_top_level_type",
                f"line {line_no} is a JSON {type(rec).__name__}, "
                f"expected an object record"))
            continue
        records.append((line_no, rec))

    rows = [r for _, r in records]

    # (d) proof-events shape: duplicate event_id = the W-212 brick.
    id_key = spec.get("id_key")
    if id_key:
        check = ("duplicate_event_id" if id_key == "event_id"
                 else "duplicate_id")
        findings += _dup_findings(store, rows, id_key, check)
    elif any("event_id" in r for r in rows):
        findings += _dup_findings(store, rows, "event_id",
                                  "duplicate_event_id")

    # (c) audit-trail shape: seq strictly +1, hash chain linked.
    if any("seq" in r for r in rows):
        findings += _check_seq(store, records)
    return findings


def _check_seq(store: str, records: list) -> list:
    findings = []
    prev_seq: Optional[int] = None
    prev_hash: Optional[str] = None
    chain_shaped = all("prev_hash" in r and "entry_hash" in r
                       for _, r in records)
    for line_no, rec in records:
        seq = rec.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            findings.append(Finding(
                store, "seq_not_integer",
                f"line {line_no}: seq {seq!r} is not an integer"))
            continue
        if prev_seq is not None:
            if seq == prev_seq:
                findings.append(Finding(
                    store, "seq_duplicate",
                    f"line {line_no}: seq {seq} repeats the previous "
                    f"entry (two writers forked the trail)"))
            elif seq < prev_seq:
                findings.append(Finding(
                    store, "seq_out_of_order",
                    f"line {line_no}: seq {seq} follows seq {prev_seq}"))
            elif seq > prev_seq + 1:
                findings.append(Finding(
                    store, "seq_gap",
                    f"line {line_no}: seq jumps {prev_seq} -> {seq} "
                    f"({seq - prev_seq - 1} entries missing)"))
        if chain_shaped and prev_hash is not None:
            if rec.get("prev_hash") != prev_hash:
                findings.append(Finding(
                    store, "chain_link_break",
                    f"line {line_no}: prev_hash does not match the "
                    f"previous entry_hash (hash chain forked)"))
        prev_seq = seq
        if chain_shaped:
            prev_hash = rec.get("entry_hash")
    return findings


# ------------------------------------------------------------------ entry

def sweep(paths: Iterable[Union[str, Path, Mapping, tuple]]) -> list:
    """Inspect store files read-only; return a list of Findings.

    Each item may be a path (``str``/``Path``), a ``(path, kind)`` tuple,
    or a spec mapping with keys: ``path`` (required), ``kind``
    (``"json"``/``"jsonl"``; default from suffix), ``id_key``,
    ``collection`` (for dict stores whose records live under one key),
    and ``expected_type`` (``"dict"``/``"list"``). A bad store yields
    findings, never an exception.
    """
    findings: list = []
    for spec in paths:
        norm = _normalize(spec)
        kind = _kind_for(norm, norm["path"])
        try:
            if kind == "jsonl":
                findings += _check_jsonl(norm)
            else:
                findings += _check_json(norm)
        except Exception as exc:  # a checker bug must not kill the sweep
            findings.append(Finding(str(norm["path"]), "sweep_error",
                                    f"checker failed: {exc!r}"))
    return findings


def format_work_orders(findings: Iterable[Finding]) -> str:
    """Render findings as board-ready work-order lines."""
    rows = list(findings)
    if not rows:
        return "persistence-sweep: no findings — all inspected stores healthy"
    lines = [f"persistence-sweep: {len(rows)} finding(s) — work orders:"]
    for f in rows:
        lines.append(
            f"WORK ORDER [persistence-sweep] store={f.store} "
            f"check={f.check}: {f.detail}")
    return "\n".join(lines)
