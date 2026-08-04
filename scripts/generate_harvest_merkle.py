#!/usr/bin/env python3
"""
generate_harvest_merkle.py — Off-chain Merkle tree generator for the
SINCOR Harvest Moon claim campaign.

Usage:
    python scripts/generate_harvest_merkle.py \
        --wallets wallets.csv \
        --amount 100 \
        --output-dir outputs/harvest_2026-09

Inputs:
    wallets.csv — columns: address, eligible (1/0), [notes]

Outputs:
    merkle_root.txt     — hex root to set on HarvestClaim.sol
    proofs.json         — { address: { amount, proof: [hex, ...] } }
    eligibility.csv     — address, amount, proof_0, proof_1, ...
    ineligible.csv      — addresses excluded and reason

Security:
    - No private keys are used or needed
    - Output files contain no secrets
    - Root is the only on-chain value; proofs are served off-chain
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import NamedTuple

from eth_abi import encode
from eth_utils import keccak, to_checksum_address, is_address

logger = logging.getLogger("harvest_merkle")


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

class Leaf(NamedTuple):
    address: str   # checksummed
    amount: int    # token amount in wei (18-decimal units)
    hash: bytes    # keccak256(abi.encodePacked(address, amount))


# ─────────────────────────────────────────────────────────────────────────────
# Merkle helpers
# ─────────────────────────────────────────────────────────────────────────────

def _leaf_hash(address: str, amount: int) -> bytes:
    """Compute keccak256(abi.encodePacked(address, amount)).

    Matches the Solidity: keccak256(abi.encodePacked(msg.sender, amount))
    """
    packed = bytes.fromhex(address[2:].zfill(40)) + amount.to_bytes(32, "big")
    return keccak(packed)


def _hash_pair(a: bytes, b: bytes) -> bytes:
    """Sort and hash a pair (matches OpenZeppelin MerkleProof)."""
    return keccak(b"".join(sorted([a, b])))


def build_tree(leaves: list[Leaf]) -> tuple[bytes, dict[str, list[str]]]:
    """Build a Merkle tree from leaves.

    Returns:
        root: bytes   — the Merkle root
        proofs: dict  — mapping of address -> list of hex-encoded proof nodes
    """
    if not leaves:
        raise ValueError("Cannot build tree from empty leaf set")

    hashes = [leaf.hash for leaf in leaves]

    # Map from leaf hash to its index
    leaf_index = {h: i for i, h in enumerate(hashes)}

    # Track proof paths
    proof_paths: dict[int, list[bytes]] = {i: [] for i in range(len(hashes))}

    layer = list(hashes)

    while len(layer) > 1:
        next_layer: list[bytes] = []
        for i in range(0, len(layer), 2):
            left = layer[i]
            right = layer[i + 1] if i + 1 < len(layer) else left

            combined = _hash_pair(left, right)
            next_layer.append(combined)

            # Record proof elements for all leaves in this layer
            if i + 1 < len(layer):
                # left-side leaves need right as proof
                for orig_idx, path in proof_paths.items():
                    if _leaf_at_layer(hashes, layer, orig_idx) == left:
                        path.append(right)
                    elif _leaf_at_layer(hashes, layer, orig_idx) == right:
                        path.append(left)
            # If odd leaf, it pairs with itself — no proof element needed

        layer = next_layer

    root = layer[0]

    # Build proof dict keyed by address
    proofs: dict[str, list[str]] = {}
    for leaf in leaves:
        idx = leaf_index[leaf.hash]
        proofs[leaf.address] = ["0x" + node.hex() for node in proof_paths[idx]]

    return root, proofs


def _leaf_at_layer(original_hashes: list[bytes], layer: list[bytes], orig_idx: int) -> bytes:
    """Determine which layer node corresponds to the original leaf at orig_idx.

    Simplified: we track the position as the tree reduces.
    """
    # Each layer collapses pairs: position in layer = orig_idx // (2^depth)
    # For simplicity, use direct position tracking per layer call.
    # (Full implementation below uses iterative proof building instead.)
    return layer[orig_idx % len(layer)]


def build_tree_v2(leaves: list[Leaf]) -> tuple[bytes, dict[str, list[str]]]:
    """Correct iterative Merkle tree construction with proof extraction.

    Uses the standard sorted-pair OpenZeppelin approach.
    """
    if not leaves:
        raise ValueError("Empty leaf set")

    n = len(leaves)
    leaf_hashes = [leaf.hash for leaf in leaves]

    # Build layers bottom-up
    layers: list[list[bytes]] = [leaf_hashes]
    while len(layers[-1]) > 1:
        current = layers[-1]
        next_layer: list[bytes] = []
        for i in range(0, len(current), 2):
            left = current[i]
            right = current[i + 1] if i + 1 < len(current) else left
            next_layer.append(_hash_pair(left, right) if i + 1 < len(current) else left)
        layers.append(next_layer)

    root = layers[-1][0]

    # Extract proofs per leaf
    proofs: dict[str, list[str]] = {}
    for leaf_idx, leaf in enumerate(leaves):
        proof: list[str] = []
        idx = leaf_idx
        for layer in layers[:-1]:
            # Sibling index
            if idx % 2 == 0:
                sibling_idx = idx + 1
            else:
                sibling_idx = idx - 1

            if sibling_idx < len(layer):
                proof.append("0x" + layer[sibling_idx].hex())

            idx = idx // 2  # Move up to parent index

        proofs[leaf.address] = proof

    return root, proofs


# ─────────────────────────────────────────────────────────────────────────────
# CSV loading
# ─────────────────────────────────────────────────────────────────────────────

def load_wallets(
    csv_path: Path,
    amount_wei: int,
) -> tuple[list[Leaf], list[tuple[str, str]]]:
    """Load wallet CSV and return (eligible_leaves, ineligible_list).

    CSV format (header required):
        address, eligible, [notes]

    eligible = "1" or "true" (case-insensitive) to include.
    """
    eligible: list[Leaf] = []
    ineligible: list[tuple[str, str]] = []

    seen: set[str] = set()

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_addr = row.get("address", "").strip()
            flag = row.get("eligible", "0").strip().lower()

            # Validate address
            if not raw_addr or not is_address(raw_addr):
                ineligible.append((raw_addr, "invalid_address"))
                continue

            addr = to_checksum_address(raw_addr)

            # Deduplicate
            if addr in seen:
                ineligible.append((addr, "duplicate"))
                continue
            seen.add(addr)

            if flag in ("1", "true", "yes"):
                h = _leaf_hash(addr, amount_wei)
                eligible.append(Leaf(address=addr, amount=amount_wei, hash=h))
            else:
                reason = row.get("notes", "ineligible_flag").strip() or "ineligible_flag"
                ineligible.append((addr, reason))

    return eligible, ineligible


# ─────────────────────────────────────────────────────────────────────────────
# Output writers
# ─────────────────────────────────────────────────────────────────────────────

def write_outputs(
    output_dir: Path,
    root: bytes,
    leaves: list[Leaf],
    proofs: dict[str, list[str]],
    ineligible: list[tuple[str, str]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Merkle root
    root_hex = "0x" + root.hex()
    (output_dir / "merkle_root.txt").write_text(root_hex + "\n")
    logger.info("Merkle root: %s", root_hex)

    # proofs.json
    proofs_data: dict = {}
    for leaf in leaves:
        proofs_data[leaf.address] = {
            "amount": str(leaf.amount),
            "amount_ether": str(leaf.amount / 1e18),
            "proof": proofs[leaf.address],
        }
    with open(output_dir / "proofs.json", "w", encoding="utf-8") as f:
        json.dump(proofs_data, f, indent=2)

    # eligibility.csv
    max_proof_len = max((len(p) for p in proofs.values()), default=0)
    with open(output_dir / "eligibility.csv", "w", newline="", encoding="utf-8") as f:
        fieldnames = (
            ["address", "amount_wei"]
            + [f"proof_{i}" for i in range(max_proof_len)]
        )
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for leaf in leaves:
            row: dict = {"address": leaf.address, "amount_wei": leaf.amount}
            for i, node in enumerate(proofs[leaf.address]):
                row[f"proof_{i}"] = node
            writer.writerow(row)

    # ineligible.csv
    with open(output_dir / "ineligible.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["address", "reason"])
        writer.writerows(ineligible)

    logger.info(
        "Outputs written to %s | eligible=%d ineligible=%d",
        output_dir,
        len(leaves),
        len(ineligible),
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate Merkle tree for SINCOR Harvest Moon claim."
    )
    p.add_argument("--wallets", required=True, type=Path, help="CSV of wallets with eligibility flags")
    p.add_argument("--amount", required=True, type=float, help="SINC amount per wallet (in SINC, not wei)")
    p.add_argument("--output-dir", default=Path("outputs/harvest"), type=Path, help="Output directory")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s  %(message)s",
    )

    amount_wei = int(args.amount * 1e18)
    logger.info("Loading wallets from %s (amount=%s SINC = %d wei)", args.wallets, args.amount, amount_wei)

    leaves, ineligible = load_wallets(args.wallets, amount_wei)

    if not leaves:
        logger.error("No eligible wallets found — aborting")
        return 1

    logger.info("Building Merkle tree for %d eligible wallets", len(leaves))
    root, proofs = build_tree_v2(leaves)

    write_outputs(args.output_dir, root, leaves, proofs, ineligible)

    print(f"\n{'='*60}")
    print(f"  Merkle Root : 0x{root.hex()}")
    print(f"  Eligible    : {len(leaves)}")
    print(f"  Ineligible  : {len(ineligible)}")
    print(f"  Total SINC  : {len(leaves) * args.amount:,.2f}")
    print(f"  Output dir  : {args.output_dir}")
    print(f"{'='*60}\n")
    print("Next: call HarvestClaim.setRoot(root, 30, totalWei) from owner")

    return 0


if __name__ == "__main__":
    sys.exit(main())
