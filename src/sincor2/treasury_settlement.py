"""
Treasury settlement helpers — fee-only realized inflow.

Used by A2A settlement success path and any paid vertical that must
move the CEO KPI (Treasury inflow) without double-counting principal.

Safety: never moves funds. Only records measurement events.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from sincor2.treasury_inflow import record_inflow

logger = logging.getLogger(__name__)

TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"


def record_platform_fee_inflow(
    *,
    fee_amount: float | Decimal | str | int,
    asset: str = "AXM",
    source: str = "a2a_settlement",
    tx_hash: Optional[str] = None,
    task_id: Optional[str] = None,
    note: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Record **platform fee only** as realized (projected=False) inflow.

    Returns the event dict on success, None if fee is zero/negative or write fails.
    """
    try:
        amount = float(Decimal(str(fee_amount)))
    except Exception:
        logger.warning("record_platform_fee_inflow: invalid fee_amount=%r", fee_amount)
        return None

    if amount <= 0:
        return None

    note = note or (f"platform fee task={task_id}" if task_id else "platform fee")
    try:
        event = record_inflow(
            amount,
            asset=asset.upper(),
            source=source,
            usd_estimate=amount,  # caller may refine; fee is the measured unit
            tx_hash=tx_hash,
            note=note,
            projected=False,
        )
        logger.info(
            "Realized treasury fee recorded: %.6f %s source=%s tx=%s",
            amount,
            asset,
            source,
            (tx_hash or "")[:18],
        )
        return event.to_dict() if hasattr(event, "to_dict") else dict(event.__dict__)
    except Exception as exc:
        logger.error("record_platform_fee_inflow failed: %s", exc)
        return None


def extract_fee_from_quote_or_settlement(payload: Dict[str, Any]) -> float:
    """
    Best-effort extraction of platform fee from quote or settlement record.
    Prefers explicit fields; never invents amounts.
    """
    if not payload:
        return 0.0
    for key in (
        "platform_fee_wei",
        "platform_fee",
        "fee_to_treasury",
        "treasury_fee",
    ):
        if key in payload and payload[key] is not None:
            try:
                return float(Decimal(str(payload[key])))
            except Exception:
                continue
    split = payload.get("treasury_fee_split") or payload.get("fee_split") or {}
    if isinstance(split, dict):
        for key in ("platform_fee_wei", "platform_fee", "amount", "fee"):
            if key in split and split[key] is not None:
                try:
                    return float(Decimal(str(split[key])))
                except Exception:
                    continue
    return 0.0
