"""Farcaster / Neynar adapter — posts only when NEYNAR_API_KEY + FARCASTER_SIGNER_UUID are set."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config" / "disclosure_strings.yaml"


def _disclosure() -> str:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return data.get("farcaster", "")


def post_approved_draft(draft: dict) -> dict:
    key = os.environ.get("NEYNAR_API_KEY", "")
    signer = os.environ.get("FARCASTER_SIGNER_UUID", "")
    channel = draft.get("channel", "farcaster")

    if channel != "farcaster":
        return {"ok": True, "skipped": True, "reason": "not_farcaster_channel"}

    text = draft["body"].strip()
    disc = _disclosure()
    if disc and disc not in text:
        text = f"{text}\n\n{disc}"

    if not key or not signer:
        out_path = Path(__file__).resolve().parents[1] / "outbox" / f"{draft['id']}.txt"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        return {
            "ok": True,
            "mode": "outbox",
            "path": str(out_path),
            "hint": "Set NEYNAR_API_KEY + FARCASTER_SIGNER_UUID to post live",
        }

    payload = json.dumps({"signer_uuid": signer, "text": text[:1024]}).encode()
    req = urllib.request.Request(
        "https://api.neynar.com/v2/farcaster/cast",
        data=payload,
        headers={"Content-Type": "application/json", "api_key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        return {"ok": True, "mode": "neynar", "result": result}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": e.read().decode()[:500]}