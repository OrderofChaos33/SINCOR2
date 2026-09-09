"""Idempotent KYA mount used by a2a_bootstrap.register_a2a."""

from __future__ import annotations

import logging

logger = logging.getLogger("sincor.kya.bootstrap")


def mount_kya_stack(app) -> bool:
    try:
        names = getattr(app, "blueprints", {}) or {}
        if "kya" in names:
            return True
        from sincor2.kya.blueprint import mount_kya

        return bool(mount_kya(app))
    except Exception as err:
        logger.warning("KYA stack mount skipped: %s", err)
        return False
