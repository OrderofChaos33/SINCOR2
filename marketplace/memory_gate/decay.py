"""Ebbinghaus decay: Score = similarity × e^{-λ t}."""

from __future__ import annotations

import math

from .types import LAMBDA_PER_HOUR


def ebbinghaus(
    similarity: float,
    age_hours: float,
    lam: float = LAMBDA_PER_HOUR,
) -> float:
    if age_hours < 0:
        raise ValueError("age_hours must be >= 0")
    if lam < 0:
        raise ValueError("lambda must be >= 0")
    decay = math.exp(-lam * age_hours)
    return float(similarity) * decay


def decay_factor(age_hours: float, lam: float = LAMBDA_PER_HOUR) -> float:
    if age_hours < 0:
        raise ValueError("age_hours must be >= 0")
    return math.exp(-lam * age_hours)


def half_life_hours(lam: float = LAMBDA_PER_HOUR) -> float:
    if lam <= 0:
        return float("inf")
    return math.log(2) / lam
