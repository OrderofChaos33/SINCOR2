"""Deterministic 64-d hashed bag-of-tokens embeddings.

Phase-1 filtering is intentionally LLM-free: each skill or requirement token is
Keccak-hashed into a fixed-width vector, then cosine similarity ranks agents.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

from .keccak import keccak256
from .types import VECTOR_DIM


def embed_tokens(tokens: Sequence[str], dim: int = VECTOR_DIM) -> Tuple[float, ...]:
    """Hash-bag embedding. Identical token sets produce identical unit vectors.

    Each token is expanded with Keccak into ``dim`` independent 32-bit
    projections so unrelated skill sets stay near-orthogonal.
    """
    if dim <= 0:
        raise ValueError("dim must be positive")
    vec = [0.0] * dim
    seen = False
    for raw in tokens:
        token = " ".join(str(raw).strip().lower().split())
        if not token:
            continue
        seen = True
        need = dim * 4
        buf = bytearray()
        counter = 0
        encoded = token.encode("utf-8")
        while len(buf) < need:
            buf.extend(keccak256(encoded + counter.to_bytes(4, "big")))
            counter += 1
        for i in range(dim):
            raw_u = int.from_bytes(buf[i * 4 : i * 4 + 4], "big")
            vec[i] += (raw_u / 2147483648.0) - 1.0
    if not seen:
        return tuple(vec)
    mag = math.sqrt(sum(x * x for x in vec))
    if mag < 1e-12:
        return tuple(vec)
    return tuple(x / mag for x in vec)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vector length mismatch")
    dot = 0.0
    n_left = 0.0
    n_right = 0.0
    for a, b in zip(left, right):
        dot += a * b
        n_left += a * a
        n_right += b * b
    denom = math.sqrt(n_left) * math.sqrt(n_right)
    if denom < 1e-12:
        return 0.0
    value = dot / denom
    if value > 1.0:
        return 1.0
    if value < -1.0:
        return -1.0
    return value
