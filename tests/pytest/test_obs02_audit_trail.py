"""OBS-02 audit_trail tests: chain integrity, tamper detection, signatures,
archive round-trip, attribution, flag-off no-op, and self-adversarial cases.

Storage always uses tmp_path (never /tmp state leakage, never the real
data_dir). Env is scrubbed per-test so the flag/key state is deterministic.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sincor2.obs_skus import audit_trail
from sincor2.obs_skus.audit_trail import (
    AuditTrail,
    build_entry,
    generate_keypair,
    to_underwriting_event,
    verify_entry_signature,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for var in ("SINCOR_AUDIT_TRAIL_ENABLED", "SINCOR_AUDIT_SIGNING_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SINCOR_AUDIT_TRAIL_ENABLED", "1")
    # reset module singleton + hooks between tests
    audit_trail._default_trail = None
    audit_trail.uninstall_hooks()
    yield
    audit_trail.uninstall_hooks()
    audit_trail._default_trail = None


@pytest.fixture
def keyed(monkeypatch):
    seed_hex, verify_hex = generate_keypair()
    monkeypatch.setenv("SINCOR_AUDIT_SIGNING_KEY", seed_hex)
    return seed_hex, verify_hex


def _trail(tmp_path: Path, **kw) -> AuditTrail:
    return AuditTrail(log_path=tmp_path / "audit.jsonl", **kw)


def _fill(trail: AuditTrail, n: int = 5, agent: str = "agent-1") -> list:
    out = []
    for i in range(n):
        out.append(
            trail.record(
                agent_id=agent,
                action=f"step:{i}",
                causal_parent=f"span-{i}",
                detail={"i": i, "nested": {"ok": True}},
            )
        )
    return out


# ---------------------------------------------------------------- chain basics

def test_chain_integrity_signed(tmp_path, keyed):
    t = _trail(tmp_path)
    _fill(t, 7)
    rep = t.verify_chain()
    assert rep["ok"] is True
    assert rep["entries"] == 7
    assert rep["signed"] == 7 and rep["unsigned"] == 0
    assert rep["failures"] == []
    assert rep["head_hash"] == t.head_hash() != "00" * 32


def test_chain_integrity_unsigned_when_no_key(tmp_path):
    t = _trail(tmp_path)
    _fill(t, 3)
    rep = t.verify_chain()
    assert rep["ok"] is True  # hash chain still holds
    assert rep["unsigned"] == 3 and rep["signed"] == 0
    for e in t.entries():
        assert e["sig"] is None


def test_attribution_fields_present(tmp_path, keyed):
    t = _trail(tmp_path)
    e = t.record("agent-9", "task.completed", causal_parent="trace-abc",
                 detail={"result": "ok"})
    for f in ("seq", "agent_id", "action", "timestamp_ms", "causal_parent",
              "detail", "prev_hash", "entry_hash", "sig", "verify_key"):
        assert f in e, f"missing {f}"
    assert e["agent_id"] == "agent-9"
    assert e["causal_parent"] == "trace-abc"
    assert isinstance(e["timestamp_ms"], int)


def test_record_requires_attribution(tmp_path, keyed):
    t = _trail(tmp_path)
    with pytest.raises(ValueError):
        t.record("", "action")
    with pytest.raises(ValueError):
        t.record("agent", "")


# ---------------------------------------------------------------- flag off

def test_flag_off_is_strict_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_AUDIT_TRAIL_ENABLED", "0")
    p = tmp_path / "audit.jsonl"
    t = AuditTrail(log_path=p)
    assert t.enabled is False
    assert t.record("a", "b") is None
    assert not p.exists()
    assert audit_trail.record_action("a", "b") is None


def test_flag_off_default(tmp_path):
    # env scrubbed by fixture except ENABLE=1; delete it -> default off
    os.environ.pop("SINCOR_AUDIT_TRAIL_ENABLED")
    t = AuditTrail(log_path=tmp_path / "audit.jsonl")
    assert t.enabled is False


# ---------------------------------------------------------------- adversarial

def _flip_byte(path: Path, lineno: int = 2) -> None:
    """Flip one bit inside the target line's ``entry_hash`` hex value.

    Hex chars are pure ASCII, so the line stays valid UTF-8/JSON while the
    hash commitment changes — the classic silent-tamper attempt.
    """
    raw_lines = path.read_bytes().splitlines(keepends=True)
    raw = bytearray(raw_lines[lineno])
    marker = b'"entry_hash":"'
    idx = raw.index(marker) + len(marker)
    raw[idx] ^= 0x01  # one bit: 'a'->'`' etc., still ASCII
    raw_lines[lineno] = bytes(raw)
    path.write_bytes(b"".join(raw_lines))


def test_tamper_flip_byte_detected(tmp_path, keyed):
    t = _trail(tmp_path)
    _fill(t, 5)
    _flip_byte(t.log_path, lineno=2)
    t2 = AuditTrail(log_path=t.log_path)  # re-read from disk
    rep = t2.verify_chain()
    assert rep["ok"] is False
    kinds = {f["kind"] for f in rep["failures"]}
    assert kinds & {"hash_mismatch", "bad_signature", "link_break"}
    assert any(f["seq"] == 2 for f in rep["failures"])


def test_fully_corrupt_line_reported_not_crash(tmp_path, keyed):
    """A line mangled into non-UTF-8 bytes is *reported* as tamper, not a crash."""
    t = _trail(tmp_path)
    _fill(t, 3)
    raw_lines = t.log_path.read_bytes().splitlines(keepends=True)
    raw = bytearray(raw_lines[1])
    raw[50] ^= 0xFF  # almost surely invalid UTF-8
    raw_lines[1] = bytes(raw)
    t.log_path.write_bytes(b"".join(raw_lines))
    rep = AuditTrail(log_path=t.log_path).verify_chain()  # must not raise
    assert rep["ok"] is False
    assert any(f["kind"] == "undecodable_line" for f in rep["failures"])


def test_tamper_reorder_detected(tmp_path, keyed):
    t = _trail(tmp_path)
    _fill(t, 4)
    lines = t.log_path.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[1], lines[2] = lines[2], lines[1]  # swap seq 1 and 2
    t.log_path.write_text("".join(lines), encoding="utf-8")
    rep = AuditTrail(log_path=t.log_path).verify_chain()
    assert rep["ok"] is False
    assert any(f["kind"] == "seq_break" for f in rep["failures"])


def test_tamper_forge_entry_without_key_fails_signature(tmp_path, keyed):
    """Attacker rewrites an entry's detail and recomputes entry_hash, but
    cannot produce a valid Ed25519 signature without the seed."""
    t = _trail(tmp_path)
    _fill(t, 3)
    entries = t.entries()
    forged = dict(entries[1])
    forged["detail"] = {"i": 1, "pwned": True}
    # attacker recomputes the hash link honestly (they can read the file)
    body = {k: forged[k] for k in ("seq", "agent_id", "action", "timestamp_ms",
                                   "causal_parent", "detail", "prev_hash")}
    forged["entry_hash"] = audit_trail._hash(
        bytes.fromhex(forged["prev_hash"]) + audit_trail._canonical(body)
    ).hex()
    # ... but the old signature no longer matches the new hash
    lines = [_canonical_line(e) for e in [entries[0], forged, entries[2]]]
    t.log_path.write_text("".join(lines), encoding="utf-8")
    rep = AuditTrail(log_path=t.log_path).verify_chain()
    assert rep["ok"] is False
    assert any(f["kind"] == "bad_signature" and f["seq"] == 1
               for f in rep["failures"])


def _canonical_line(e: dict) -> str:
    return json.dumps(e, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True) + "\n"


def test_truncate_tail_detected_via_pinned_head(tmp_path, keyed):
    """A bare chain cannot self-detect tail truncation — verify_chain on the
    truncated file still passes. The pinned head (archive manifest) catches
    it. This test proves both halves honestly."""
    t = _trail(tmp_path)
    _fill(t, 5)
    pinned_head = t.head_hash()
    lines = t.log_path.read_text(encoding="utf-8").splitlines(keepends=True)
    t.log_path.write_text("".join(lines[:3]), encoding="utf-8")  # drop tail
    truncated = AuditTrail(log_path=t.log_path)
    assert truncated.verify_chain()["ok"] is True  # honest limitation
    assert truncated.head_hash() != pinned_head  # ...caught by the pin


def test_truncate_tail_detected_by_archive(tmp_path, keyed):
    t = _trail(tmp_path)
    _fill(t, 5)
    dest = tmp_path / "archive"
    t.export_archive(dest)
    # attacker drops the last archived entry
    lines = (dest / "audit_log.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
    (dest / "audit_log.jsonl").write_text("".join(lines[:-1]), encoding="utf-8")
    rep = AuditTrail.verify_archive(dest)
    assert rep["ok"] is False
    assert any(f["kind"] in ("manifest_count_mismatch", "manifest_head_mismatch")
               for f in rep["failures"])


def test_recover_head_after_restart(tmp_path, keyed):
    t = _trail(tmp_path)
    _fill(t, 4)
    head = t.head_hash()
    t2 = AuditTrail(log_path=t.log_path)  # new process, same file
    assert t2.head_hash() == head
    e = t2.record("agent-1", "after.restart")
    assert e["seq"] == 4
    assert t2.verify_chain()["ok"] is True


# ---------------------------------------------------------------- archive

def test_archive_round_trip_signed(tmp_path, keyed):
    _, verify_hex = keyed
    t = _trail(tmp_path)
    _fill(t, 6)
    dest = tmp_path / "archive"
    t.export_archive(dest)
    assert (dest / "audit_log.jsonl").exists()
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sku"] == "OBS-02"
    assert manifest["entries"] == 6
    assert manifest["verify_key"] == verify_hex
    assert manifest["manifest_sig"]
    rep = AuditTrail.verify_archive(dest)
    assert rep["ok"] is True
    assert rep["entries"] == 6 and rep["signed"] == 6


def test_archive_tamper_entry_rejected(tmp_path, keyed):
    t = _trail(tmp_path)
    _fill(t, 4)
    dest = tmp_path / "archive"
    t.export_archive(dest)
    _flip_byte(dest / "audit_log.jsonl", lineno=1)
    rep = AuditTrail.verify_archive(dest)
    assert rep["ok"] is False


def test_archive_tamper_manifest_count_rejected(tmp_path, keyed):
    t = _trail(tmp_path)
    _fill(t, 4)
    dest = tmp_path / "archive"
    t.export_archive(dest)
    mpath = dest / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    manifest["entries"] = 99
    # attacker cannot re-sign the manifest without the seed -> keep old sig
    mpath.write_text(_canonical_line(manifest), encoding="utf-8")
    rep = AuditTrail.verify_archive(dest)
    assert rep["ok"] is False
    assert any(f["kind"] in ("manifest_count_mismatch", "manifest_bad_signature")
               for f in rep["failures"])


def test_archive_verifies_with_explicit_key_only(tmp_path, keyed):
    """verify_archive works offline with just the published verify key."""
    _, verify_hex = keyed
    t = _trail(tmp_path)
    _fill(t, 2)
    dest = tmp_path / "archive"
    t.export_archive(dest)
    rep = AuditTrail.verify_archive(dest, verify_key_hex=verify_hex)
    assert rep["ok"] is True
    # wrong key must fail the manifest signature
    wrong = generate_keypair()[1]
    rep2 = AuditTrail.verify_archive(dest, verify_key_hex=wrong)
    assert rep2["ok"] is False
    assert any(f["kind"] == "manifest_bad_signature" for f in rep2["failures"])


def test_archive_unsigned_still_chain_verifies(tmp_path):
    t = _trail(tmp_path)  # no key in env
    _fill(t, 2)
    dest = tmp_path / "archive"
    t.export_archive(dest)
    rep = AuditTrail.verify_archive(dest)
    assert rep["ok"] is True
    assert rep["unsigned"] == 2


# ---------------------------------------------------------------- hooks

def test_install_hooks_records_spans(tmp_path, keyed):
    from sincor2 import observability

    t = _trail(tmp_path)
    uninstall = audit_trail.install_hooks(t)
    try:
        with observability.trace_task(task_id="t-1", skill_id="demo",
                                      agent_id="hook-agent") as span:
            span.set_attribute("x", "y")
        entries = t.entries()
        assert len(entries) == 1
        e = entries[0]
        assert e["agent_id"] == "hook-agent"
        assert e["action"] == "span:sincor.task"
        assert e["causal_parent"] == span.span_id
        assert e["detail"]["task_id"] == "t-1"
        assert e["detail"]["status"] == "ok"
    finally:
        uninstall()
    # after uninstall, spans are not captured
    with observability.trace_task(task_id="t-2", agent_id="hook-agent"):
        pass
    assert len(t.entries()) == 1


def test_hooks_noop_when_flag_off(tmp_path, monkeypatch, keyed, capsys):
    from sincor2 import observability

    monkeypatch.setenv("SINCOR_AUDIT_TRAIL_ENABLED", "0")
    t = _trail(tmp_path)
    uninstall = audit_trail.install_hooks(t)
    try:
        with observability.trace_task(task_id="t-9", agent_id="hook-agent"):
            pass
        assert t.entries() == []
        out = capsys.readouterr().out  # stdout tracing still works
        assert "sincor_trace" in out
    finally:
        uninstall()


def test_hook_failure_never_breaks_emit(tmp_path, monkeypatch, keyed):
    from sincor2 import observability

    t = _trail(tmp_path)
    uninstall = audit_trail.install_hooks(t)
    # break the trail object so record() raises; emit must still succeed
    monkeypatch.setattr(t, "record",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        with observability.trace_task(task_id="t-x", agent_id="hook-agent"):
            pass  # must not raise
    finally:
        uninstall()


# ---------------------------------------------------------------- misc

def test_build_entry_hash_chain_math():
    seed_hex, _ = generate_keypair()
    from nacl.signing import SigningKey
    sk = SigningKey(bytes.fromhex(seed_hex))
    e0 = build_entry(seq=0, agent_id="a", action="x", timestamp_ms=1,
                     causal_parent=None, detail={}, prev_hash=b"\x00" * 32,
                     signing_key=sk)
    e1 = build_entry(seq=0, agent_id="a", action="x", timestamp_ms=1,
                     causal_parent=None, detail={}, prev_hash=b"\x00" * 32,
                     signing_key=sk)
    assert e0["entry_hash"] == e1["entry_hash"]  # deterministic
    assert verify_entry_signature(e0) == "signed"
    # signature binds to the hash: different hash -> old sig fails
    e2 = build_entry(seq=1, agent_id="a", action="x", timestamp_ms=1,
                     causal_parent=None, detail={},
                     prev_hash=bytes.fromhex(e0["entry_hash"]), signing_key=sk)
    assert e2["prev_hash"] == e0["entry_hash"]
    assert verify_entry_signature({**e0, "entry_hash": e2["entry_hash"]}) == "bad_signature"


def test_to_underwriting_event_schema(tmp_path, keyed):
    t = _trail(tmp_path)
    e = t.record("agent-u", "bid.placed", causal_parent="span-1",
                 detail={"bid": 10})
    ev = to_underwriting_event(e)
    assert ev["event_type"] == "audit"
    assert ev["sku"] == "OBS-02"
    assert ev["agent_id"] == "agent-u"
    assert ev["entry_hash"] == e["entry_hash"]
    assert ev["evidence"] == {"bid": 10}


def test_default_log_path_uses_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    p = audit_trail.default_log_path()
    assert str(p).startswith(str(tmp_path / "data"))
    assert p.name == "audit_log.jsonl"
    assert "/tmp" not in str(p) or str(tmp_path).startswith("/tmp")
