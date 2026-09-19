"""Celery tasks for on-chain snapshot refresh."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from sincor2.celery_app import celery
except Exception:  # pragma: no cover
    celery = None


def _refresh() -> dict:
    from sincor2.onchain.live_snapshot import fetch_live_onchain
    snap = fetch_live_onchain(force=True)
    logger.info(
        "onchain snapshot refreshed verified_at=%s sinc_holders=%s axm_holders=%s",
        snap.get("verified_at"),
        (snap.get("sinc") or {}).get("holders"),
        (snap.get("axiom") or {}).get("holders"),
    )
    return snap


if celery is not None:
    @celery.task(name="sincor2.onchain.tasks.refresh_onchain_snapshot")
    def refresh_onchain_snapshot() -> dict:
        return _refresh()
else:  # pragma: no cover
    def refresh_onchain_snapshot() -> dict:
        return _refresh()
