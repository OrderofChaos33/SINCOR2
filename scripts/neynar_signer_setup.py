#!/usr/bin/env python3
"""Check or create Neynar Farcaster signer — writes approved signer_uuid to .env."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "neynar_signer_state.json"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and v and k not in os.environ:
                os.environ[k] = v


def _api(method: str, path: str, body: dict | None = None) -> dict:
    key = os.environ.get("NEYNAR_API_KEY", "")
    if not key:
        raise SystemExit("NEYNAR_API_KEY missing — run scripts/sync_neynar_secrets.ps1 first")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"https://api.neynar.com{path}",
        data=data,
        headers={"x-api-key": key, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Neynar API {path} failed ({e.code}): {e.read().decode()[:400]}") from e


def _write_env_signer(uuid: str) -> None:
    env_path = ROOT / ".env"
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    line = f"FARCASTER_SIGNER_UUID={uuid}"
    if "FARCASTER_SIGNER_UUID=" in text:
        lines = []
        for row in text.splitlines():
            if row.startswith("FARCASTER_SIGNER_UUID="):
                lines.append(line)
            else:
                lines.append(row)
        text = "\n".join(lines)
    else:
        text = text.rstrip() + "\n" + line + "\n"
    env_path.write_text(text, encoding="utf-8")


def _save_state(payload: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    _load_dotenv()
    signer = os.environ.get("FARCASTER_SIGNER_UUID", "").strip()

    if not signer and STATE_PATH.exists():
        try:
            saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            signer = (saved.get("signer_uuid") or "").strip()
        except Exception:
            pass

    if not signer:
        created = _api("POST", "/v2/farcaster/signer", {})
        signer = created.get("signer_uuid", "")
        _save_state(created)
        print("Created new signer (status: generated).")
        print("Next: approve in Warpcast via Neynar signed-key flow.")
        print("Docs: https://docs.neynar.com/docs/write-to-farcaster-with-neynar-managed-signers")
        print(f"Pending signer_uuid saved to {STATE_PATH}")
        print("Re-run after approval, or paste approved UUID from dev.neynar.com into .env")
        return 0

    info = _api("GET", f"/v2/farcaster/signer?signer_uuid={signer}")
    status = info.get("status", "unknown")
    fid = info.get("fid")
    print(f"signer_uuid: {signer}")
    print(f"status: {status}")
    print(f"fid: {fid}")

    if status == "approved":
        _write_env_signer(signer)
        print("FARCASTER_SIGNER_UUID written to .env — /launch/review approve will post live.")
        return 0

    print("Signer not approved yet. Complete Warpcast approval, then re-run this script.")
    if info.get("signer_approval_url"):
        print(f"approval_url: {info['signer_approval_url']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())