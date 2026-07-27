"""Safe PDF entrypoint — never crash gunicorn on import. FORCE 2026-07-27-v5"""

from __future__ import annotations

import logging

logger = logging.getLogger("sincor2.pdf_loader")

try:
    from sincor2.pdf_generator import get_pdf_generator as _real_get_pdf_generator

    def get_pdf_generator(output_dir: str | None = None):
        return _real_get_pdf_generator(output_dir)

    logger.info("[PDF] ReportLab generator loaded via pdf_loader (v5)")
except Exception as exc:  # noqa: BLE001
    logger.warning("[PDF] generator import failed (non-fatal): %s", exc)

    def get_pdf_generator(output_dir: str | None = None):  # type: ignore[misc]
        raise RuntimeError(
            f"PDF generator unavailable: {exc}. "
            "Set Railway NO_CACHE=1 and redeploy for a clean image."
        ) from exc
