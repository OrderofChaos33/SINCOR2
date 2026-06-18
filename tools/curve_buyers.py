#!/usr/bin/env python3
"""Scan SincBondingCurve Buy events for unique buyer wallets."""
import json
import urllib.request
from pathlib import Path

CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
TOPIC = "0xf152feb5fe7641aae5c7f8e8187c26eef1a9d970f7c3794ac442f0842282d93f"
CHUNK = 9999


def load_rpc() -> str:
    env_path = Path(__file__).resolve().parents[1] / "onchain" / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("BASE_RPC_URL="):
                return line.split("=", 1)[1].strip()
    return "https://mainnet.base.org"


RPC = load_rpc()


def rpc(method: str, params: list) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        RPC, data=body, headers={"Content-Type": "application/json", "User-Agent": "SINCOR-curve-buyers/1.0"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["result"]


def main() -> None:
    latest = int(rpc("eth_blockNumber", []), 16)
    start = latest - 45000
    buyers: dict[str, int] = {}
    total_events = 0

    fb = start
    while fb <= latest:
        tb = min(fb + CHUNK, latest)
        logs = rpc(
            "eth_getLogs",
            [{"fromBlock": hex(fb), "toBlock": hex(tb), "address": CURVE, "topics": [TOPIC]}],
        )
        for log in logs:
            total_events += 1
            topic1 = log["topics"][1]
            buyer = "0x" + topic1[-40:].lower()
            buyers[buyer] = buyers.get(buyer, 0) + 1
        print(f"blocks {fb}-{tb}: +{len(logs)} events (unique buyers: {len(buyers)})")
        fb = tb + 1

    print("\n=== last ~1 day on curve ===")
    print(f"Buy events: {total_events}")
    print(f"Unique buyers: {len(buyers)}")
    if buyers:
        print("Top repeat buyers:")
        for addr, n in sorted(buyers.items(), key=lambda x: -x[1])[:10]:
            print(f"  {addr} x{n}")


if __name__ == "__main__":
    main()