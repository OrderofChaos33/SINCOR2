"""Idempotent KYA mount used by a2a_bootstrap.register_a2a.

The inbound fabric already registers Blueprint('kya') at /v1/kya.
This mount is additive: merkle quest, SLA receipts, SADAS, Polyclaw.
It must NOT skip just because 'kya' exists — that was the hole that
left /v1/quest as 404 on live.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("sincor.kya.bootstrap")


def mount_kya_stack(app) -> bool:
    try:
        from sincor2.kya.blueprint import mount_kya

        ok = bool(mount_kya(app))
        if ok:
            logger.info("KYA additive stack mounted (quest/SLA/SADAS)")
        return ok
    except Exception as err:
        logger.warning("KYA additive stack skipped: %s", err)
        return False
