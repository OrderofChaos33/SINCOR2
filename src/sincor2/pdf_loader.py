"""Safe PDF entrypoint — never crash gunicorn on import.

Railway Metal can serve a stale snapshot where pdf_generator still does
`from weasyprint import ...`. This wrapper absorbs that failure so
/health stays up. PDF routes return 503 until a clean image ships.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("sincor2.pdf_loader")

try:
    from sincor2.pdf_generator import get_pdf_generator as _real_get_pdf_generator

    def get_pdf_generator(output_dir: str | None = None):
        return _real_get_pdf_generator(output_dir)

    logger.info("[PDF] ReportLab generator loaded via pdf_loader")
except Exception as exc:  # noqa: BLE001 — must never kill worker boot
    logger.warning("[PDF] generator import failed (non-fatal): %s", exc)

    def get_pdf_generator(output_dir: str | None = None):  # type: ignore[misc]
        raise RuntimeError(
            f"PDF generator unavailable: {exc}. "
            "Redeploy with a clean image (no WeasyPrint)."
        ) from exc
