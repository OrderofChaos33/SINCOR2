"""SH-2: tests for the read-only persistence consistency sweep.

The sweep inspects JSON / JSONL store files on disk and reports lost-update
symptoms (duplicate ids, seq gaps, broken hash-chain links, corrupt JSON)
as Finding records. It must never modify the files it inspects.
"""
import hashlib
import json

import pytest

from sincor2.persistence_sweep import Finding, format_work_orders, sweep


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def _json(path, obj):
    return _write(path, json.dumps(obj))


def _jsonl(path, rows):
    return _write(path, "".join(json.dumps(r) + "\n" for r in rows))


def _audit_rows(seqs=(0, 1, 2)):
    rows = []
    prev = "00" * 32
    for i, seq in enumerate(seqs):
        entry_hash = f"{i + 1:064x}"
        rows.append({"seq": seq, "prev_hash": prev, "entry_hash": entry_hash,
                     "agent_id": "a1", "action": "x"})
        prev = entry_hash
    return rows


def _checks(findings):
    return [f.check for f in findings]


# ---------------------------------------------------------------- healthy

def test_healthy_dict_store_no_findings(tmp_path):
    p = _json(tmp_path / "store.json", {"agents": {"a": 1}, "version": 3})
    assert sweep([p]) == []


def test_healthy_list_store_no_findings(tmp_path):
    p = _json(tmp_path / "store.json",
              [{"id": "a", "v": 1}, {"id": "b", "v": 2}])
    assert sweep([{"path": p, "id_key": "id"}]) == []


def test_healthy_proof_events_no_findings(tmp_path):
    p = _jsonl(tmp_path / "proof.jsonl",
               [{"event_id": "e1"}, {"event_id": "e2"}])
    assert sweep([p]) == []


def test_healthy_audit_trail_no_findings(tmp_path):
    p = _jsonl(tmp_path / "audit.jsonl", _audit_rows())
    assert sweep([p]) == []


def test_repeated_non_id_fields_are_not_duplicates(tmp_path):
    p = _json(tmp_path / "store.json",
              [{"id": "a", "kind": "trade"}, {"id": "b", "kind": "trade"}])
    assert sweep([{"path": p, "id_key": "id"}]) == []


def test_empty_jsonl_is_healthy(tmp_path):
    p = _write(tmp_path / "empty.jsonl", "")
    assert sweep([p]) == []


# ---------------------------------------------------------------- duplicates

def test_duplicate_event_id_flagged(tmp_path):
    p = _jsonl(tmp_path / "proof.jsonl",
               [{"event_id": "e1"}, {"event_id": "e2"}, {"event_id": "e1"}])
    findings = sweep([p])
    assert "duplicate_event_id" in _checks(findings)
    f = findings[_checks(findings).index("duplicate_event_id")]
    assert f.store == str(p)
    assert "e1" in f.detail


def test_duplicate_id_in_list_store_flagged(tmp_path):
    p = _json(tmp_path / "store.json",
              [{"id": "a"}, {"id": "b"}, {"id": "a"}])
    findings = sweep([{"path": p, "id_key": "id"}])
    assert "duplicate_id" in _checks(findings)


def test_duplicate_id_in_dict_collection_flagged(tmp_path):
    p = _json(tmp_path / "store.json",
              {"claims": [{"claim_id": "c1"}, {"claim_id": "c1"}]})
    findings = sweep([{"path": p, "id_key": "claim_id",
                       "collection": "claims"}])
    assert "duplicate_id" in _checks(findings)


# ---------------------------------------------------------------- audit seq

def test_seq_gap_flagged(tmp_path):
    p = _jsonl(tmp_path / "audit.jsonl", _audit_rows(seqs=(0, 1, 3)))
    assert "seq_gap" in _checks(sweep([p]))


def test_seq_duplicate_flagged(tmp_path):
    p = _jsonl(tmp_path / "audit.jsonl", _audit_rows(seqs=(0, 1, 1)))
    checks = _checks(sweep([p]))
    assert "seq_duplicate" in checks or "seq_gap" in checks


def test_seq_out_of_order_flagged(tmp_path):
    p = _jsonl(tmp_path / "audit.jsonl", _audit_rows(seqs=(0, 2, 1)))
    checks = _checks(sweep([p]))
    assert any(c.startswith("seq_") for c in checks)


def test_chain_link_break_flagged(tmp_path):
    rows = _audit_rows()
    rows[2]["prev_hash"] = "ff" * 32
    p = _jsonl(tmp_path / "audit.jsonl", rows)
    assert "chain_link_break" in _checks(sweep([p]))


# ---------------------------------------------------------------- corruption

def test_corrupt_json_flagged_not_raised(tmp_path):
    p = _write(tmp_path / "store.json", "{not json")
    findings = sweep([p])
    assert "unparseable_json" in _checks(findings)


def test_corrupt_jsonl_line_flagged_not_raised(tmp_path):
    p = _write(tmp_path / "proof.jsonl",
               '{"event_id": "e1"}\nnot-json\n{"event_id": "e2"}\n')
    findings = sweep([p])
    assert "unparseable_line" in _checks(findings)


def test_wrong_top_level_type_flagged(tmp_path):
    p = _json(tmp_path / "store.json", [{"id": "a"}])
    findings = sweep([{"path": p, "expected_type": "dict"}])
    assert "wrong_top_level_type" in _checks(findings)


def test_missing_file_flagged_not_raised(tmp_path):
    findings = sweep([tmp_path / "nope.json"])
    assert "missing_file" in _checks(findings)


def test_count_mismatch_flagged(tmp_path):
    p = _json(tmp_path / "store.json",
              {"count": 5, "records": [{"id": "a"}]})
    assert "count_mismatch" in _checks(sweep([p]))


def test_negative_count_flagged(tmp_path):
    p = _json(tmp_path / "store.json", {"count": -1, "records": []})
    assert "negative_count" in _checks(sweep([p]))


# ---------------------------------------------------------------- read-only

def test_sweep_leaves_files_byte_identical(tmp_path):
    files = [
        _json(tmp_path / "a.json", {"count": 1, "records": [{"id": "x"}]}),
        _jsonl(tmp_path / "b.jsonl",
               [{"event_id": "e1"}, {"event_id": "e1"}]),
        _jsonl(tmp_path / "c.jsonl", _audit_rows(seqs=(0, 2))),
        _write(tmp_path / "d.json", "{corrupt"),
    ]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    listing_before = sorted(x.name for x in tmp_path.iterdir())
    sweep(files)
    after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert before == after
    assert sorted(x.name for x in tmp_path.iterdir()) == listing_before


# ---------------------------------------------------------------- API shape

def test_sweep_accepts_str_path_and_spec_mix(tmp_path):
    good = _json(tmp_path / "good.json", {"a": 1})
    bad = _jsonl(tmp_path / "bad.jsonl",
                 [{"event_id": "e"}, {"event_id": "e"}])
    findings = sweep([str(good), {"path": bad}])
    assert all(isinstance(f, Finding) for f in findings)
    assert findings and all(f.store == str(bad) for f in findings)


def test_format_work_orders_renders_lines(tmp_path):
    p = _jsonl(tmp_path / "proof.jsonl",
               [{"event_id": "e1"}, {"event_id": "e1"}])
    out = format_work_orders(sweep([p]))
    assert str(p) in out
    assert "duplicate_event_id" in out
    assert "e1" in out


def test_format_work_orders_empty(tmp_path):
    assert "no findings" in format_work_orders([]).lower()
