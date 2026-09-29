#!/usr/bin/env python3
"""OBS-02 end-to-end operator walkthrough (runnable proof it works).

Steps: key ceremony -> log -> sign -> export -> verify -> tamper-refusal.

Run:
    PYTHONPATH=src:. ~/.venvs/sincor2/bin/python scripts/obs02_audit_trail_walkthrough.py

Uses a throwaway temp dir only; touches no real state, no network, no keys
other than the freshly generated ceremony pair held in-process.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sincor2.obs_skus.audit_trail import (  # noqa: E402
    AuditTrail,
    generate_keypair,
    install_hooks,
    uninstall_hooks,
)

PASS = "PASS"
FAIL = "FAIL"


def check(name: str, cond: bool) -> None:
    print(f"[{PASS if cond else FAIL}] {name}")
    if not cond:
        raise SystemExit(f"walkthrough failed at: {name}")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="obs02-walkthrough-"))
    log_path = tmp / "audit.jsonl"
    archive = tmp / "archive"

    # 1. KEY CEREMONY (operator-run): generate, hold seed in-process only.
    seed_hex, verify_hex = generate_keypair()
    check("key ceremony: 32-byte Ed25519 seed generated (nothing written to disk)",
          len(bytes.fromhex(seed_hex)) == 32 and len(verify_hex) == 64)
    os.environ["SINCOR_AUDIT_TRAIL_ENABLED"] = "1"
    os.environ["SINCOR_AUDIT_SIGNING_KEY"] = seed_hex

    # 2. LOG: record attributed entries, incl. one via the real span hook.
    trail = AuditTrail(log_path=log_path)
    uninstall = install_hooks(trail)
    try:
        from sincor2.observability import trace_task
        with trace_task(task_id="walkthrough-1", skill_id="demo",
                        agent_id="demo-agent"):
            pass
    finally:
        uninstall()
    trail.record("demo-agent", "task.completed", causal_parent="walkthrough-1",
                 detail={"result": "ok"})
    trail.record("demo-agent", "payout.requested", causal_parent="walkthrough-1",
                 detail={"amount_usd": "12.50"})
    check("log: 3 attributed entries appended", len(trail.entries()) == 3)

    # 3. SIGN: every entry carries an Ed25519 signature over its chain hash.
    rep = trail.verify_chain()
    check("sign: chain verifies, all entries signed",
          rep["ok"] and rep["signed"] == 3 and rep["unsigned"] == 0)

    # 4. EXPORT: forensic archive (JSONL + signed manifest).
    trail.export_archive(archive)
    manifest = json.loads((archive / "manifest.json").read_text())
    check("export: manifest pins head hash + verify key",
          manifest["head_hash"] == trail.head_hash()
          and manifest["verify_key"] == verify_hex
          and bool(manifest["manifest_sig"]))

    # 5. VERIFY: offline re-verification with the published verify key only.
    del os.environ["SINCOR_AUDIT_SIGNING_KEY"]  # verifier has no secret
    vrep = AuditTrail.verify_archive(archive, verify_key_hex=verify_hex)
    check("verify: archive verifies offline with verify key alone",
          vrep["ok"] and vrep["entries"] == 3)

    # 6. TAMPER-REFUSAL: flip one bit in the archived log -> verification fails.
    raw_lines = (archive / "audit_log.jsonl").read_bytes().splitlines(keepends=True)
    raw = bytearray(raw_lines[1])
    marker = b'"entry_hash":"'
    i = raw.index(marker) + len(marker)
    raw[i] ^= 0x01
    raw_lines[1] = bytes(raw)
    (archive / "audit_log.jsonl").write_bytes(b"".join(raw_lines))
    trep = AuditTrail.verify_archive(archive, verify_key_hex=verify_hex)
    kinds = {f["kind"] for f in trep["failures"]}
    check("tamper-refusal: 1-bit flip detected and rejected",
          not trep["ok"] and bool(kinds & {"hash_mismatch", "bad_signature",
                                            "link_break", "manifest_head_mismatch"}))
    print(f"\nTamper report kinds: {sorted(kinds)}")
    print(f"\nWalkthrough complete. Scratch dir (safe to delete): {tmp}")
    print("Verdict: OBS-02 audit trail works end-to-end.")


if __name__ == "__main__":
    main()
