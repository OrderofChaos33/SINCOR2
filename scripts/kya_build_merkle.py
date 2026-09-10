#!/usr/bin/env python3
"""Build the production KYA airdrop merkle tree from a wallet list.

    python scripts/kya_build_merkle.py --from-file recipients.txt
    python scripts/kya_build_merkle.py --from-file recipients.json --source axm-disperse-2026

Accepts JSON array, JSON {addresses|wallets}, or any text blob containing 0x addresses.
Writes data/kya/airdrop_merkle.json (root + addresses). Never invents a root.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sincor2.kya.merkle import MerkleTree, parse_addresses  # noqa: E402


def load_addresses(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x) for x in data]
        if isinstance(data, dict):
            return [str(x) for x in (data.get("addresses") or data.get("wallets") or [])]
    except json.JSONDecodeError:
        pass
    return parse_addresses(text)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--from-file", required=True)
    p.add_argument("--source", default="disperse-recipients")
    p.add_argument("--out", default=str(ROOT / "data" / "kya" / "airdrop_merkle.json"))
    args = p.parse_args()
    wallets = load_addresses(Path(args.from_file))
    if not wallets:
        print("no addresses found", file=sys.stderr)
        return 2
    tree = MerkleTree(wallets)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **tree.manifest(),
        "source": args.source,
        "token": "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a",
        "addresses": tree.addresses,
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} count={tree.manifest()['count']} root={tree.hex_root()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
