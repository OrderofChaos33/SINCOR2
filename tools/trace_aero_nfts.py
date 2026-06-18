#!/usr/bin/env python3
"""Trace Aerodrome Slipstream NFT transfers for a wallet; report owners + liquidity."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

AERO_NPM = "0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53"
WALLET = "0xAf9B539D8043C634b7E611818518BA7E850F289e"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
RPCS = ["https://mainnet.base.org", "https://base-rpc.publicnode.com"]
CHUNK = 2000


def rpc(method: str, params: list) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "SINCOR-aero-trace/1.0"}
    last = None
    for url in RPCS:
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            if "error" in data:
                raise RuntimeError(data["error"])
            return data["result"]
        except Exception as e:
            last = e
    raise RuntimeError(last)


def pad_addr(addr: str) -> str:
    return "0x" + addr.lower().replace("0x", "").zfill(64)


def topic_addr(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def get_logs(from_b: int, to_b: int, topic_filter: list) -> list:
    return rpc(
        "eth_getLogs",
        [{"fromBlock": hex(from_b), "toBlock": hex(to_b), "address": AERO_NPM, "topics": topic_filter}],
    )


def scan_wallet_transfers(start: int, end: int) -> list[dict]:
    w = pad_addr(WALLET)
    # topic1 = from wallet OR topic2 = to wallet — fetch both directions in chunks
    events: list[dict] = []
    for fb in range(start, end + 1, CHUNK):
        tb = min(fb + CHUNK - 1, end)
        for topics in (
            [TRANSFER_TOPIC, w, None],
            [TRANSFER_TOPIC, None, w],
        ):
            try:
                logs = get_logs(fb, tb, topics)
            except Exception as e:
                print(f"chunk {fb}-{tb} err: {e}", file=sys.stderr)
                continue
            for log in logs:
                t = log["topics"]
                if len(t) < 4:
                    continue
                events.append(
                    {
                        "block": int(log["blockNumber"], 16),
                        "tx": log["transactionHash"],
                        "from": topic_addr(t[1]),
                        "to": topic_addr(t[2]),
                        "token_id": int(t[3], 16),
                    }
                )
        print(f"scanned {fb}-{tb} ({len(events)} events so far)", file=sys.stderr)
    # dedupe
    seen = set()
    out = []
    for e in sorted(events, key=lambda x: (x["block"], x["token_id"])):
        k = (e["tx"], e["token_id"])
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def owner_of(token_id: int) -> str | None:
    data = "0x6352211e" + hex(token_id)[2:].zfill(64)
    try:
        raw = rpc("eth_call", [{"to": AERO_NPM, "data": data}, "latest"])
        return topic_addr(raw)
    except Exception:
        return None


def position_liquidity(token_id: int) -> int | None:
    data = "0x99fbab88" + hex(token_id)[2:].zfill(64)
    try:
        raw = rpc("eth_call", [{"to": AERO_NPM, "data": data}, "latest"])
        # liquidity is 7th return word in positions() tuple — simplified decode word index 7
        raw = raw[2:].zfill(64 * 12)
        words = [raw[i : i + 64] for i in range(0, len(raw), 64)]
        if len(words) < 8:
            return None
        return int(words[7], 16)
    except Exception:
        return None


def main() -> None:
    latest = int(rpc("eth_blockNumber", []), 16)
    # Af9B Aero activity clustered ~47.21M from prior scan; widen slightly
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 47180000
    end = int(sys.argv[2]) if len(sys.argv) > 2 else latest

    print(f"Tracing Aerodrome NPM transfers for {WALLET}")
    print(f"Blocks {start} → {end}\n")

    events = scan_wallet_transfers(start, end)
    token_ids = sorted({e["token_id"] for e in events})
    print(f"\n=== {len(events)} transfer events, {len(token_ids)} unique token IDs ===\n")

    for e in events:
        print(
            f"block {e['block']} | #{e['token_id']} | {e['from'][:10]}… → {e['to'][:10]}… | {e['tx'][:18]}…"
        )

    print("\n=== Current state ===")
    actionable = []
    for tid in token_ids:
        owner = owner_of(tid)
        liq = position_liquidity(tid) if owner else None
        status = "burned/unknown" if not owner else owner
        liq_s = str(liq) if liq is not None else "?"
        line = f"#{tid} owner={status} liquidity={liq_s}"
        print(line)
        if liq and liq > 0:
            actionable.append((tid, owner, liq))

    if actionable:
        print("\n=== Positions with liquidity > 0 ===")
        for tid, owner, liq in actionable:
            print(f"  #{tid} | owner {owner} | liquidity {liq}")
    else:
        print("\nNo Aerodrome positions with liquidity found for traced token IDs.")


if __name__ == "__main__":
    main()