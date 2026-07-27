"""
SINCOR2 PDF Generator — ReportLab ONLY
FORCE MARKER 2026-07-27-v5 — if you still see weasyprint in traceback, Metal snapshot is stale.
Never import weasyprint. Never load gobject/pango.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("sincor2.pdf_generator")

# Permanently disabled — Railway python:3.11-slim has no gobject/pango
WEASYPRINT_AVAILABLE = False
REPORTLAB_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.colors import HexColor

    REPORTLAB_AVAILABLE = True
except ImportError as e:
    logger.warning("[PDF] ReportLab not available: %s", e)


class PDFGenerator:
    """Generate training guide PDFs with ReportLab."""

    def __init__(self, output_dir: str):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not REPORTLAB_AVAILABLE:
            raise RuntimeError("ReportLab is required for PDF generation")

    def _styles(self):
        styles = getSampleStyleSheet()
        styles.add(
            ParagraphStyle(
                name="SincorTitle",
                parent=styles["Heading1"],
                fontSize=22,
                textColor=HexColor("#0f172a"),
                alignment=TA_CENTER,
                spaceAfter=18,
            )
        )
        styles.add(
            ParagraphStyle(
                name="SincorBody",
                parent=styles["Normal"],
                fontSize=11,
                leading=14,
                alignment=TA_LEFT,
                spaceAfter=8,
            )
        )
        return styles

    def _write_doc(self, filepath: Path, title: str, sections: list[str]) -> int:
        doc = SimpleDocTemplate(
            str(filepath),
            pagesize=letter,
            rightMargin=inch,
            leftMargin=inch,
            topMargin=inch,
            bottomMargin=inch,
        )
        styles = self._styles()
        story = [Paragraph(title, styles["SincorTitle"]), Spacer(1, 12)]
        for block in sections:
            story.append(Paragraph(block, styles["SincorBody"]))
            story.append(Spacer(1, 6))
        doc.build(story)
        # Approximate page count from content length
        return max(1, len(sections) // 8 + 1)

    def generate_starter_guide(self, order_id: str) -> Tuple[Path, int]:
        path = self.output_dir / f"sincor-starter-guide-{order_id}.pdf"
        pages = self._write_doc(
            path,
            "SINCOR Starter Training Guide",
            [
                f"Order: {order_id}",
                "Welcome to SINCOR Starter. Your agents are ready for lead gen and basic workflows.",
                "1. Complete onboarding at /onboarding",
                "2. Open the dashboard and review Scout + Outreach agents",
                "3. Download the quickstart checklist",
                "Support: support@getsincor.com",
            ],
        )
        return path, pages

    def generate_professional_guide(self, order_id: str) -> Tuple[Path, int]:
        path = self.output_dir / f"sincor-professional-guide-{order_id}.pdf"
        pages = self._write_doc(
            path,
            "SINCOR Professional Training Guide",
            [
                f"Order: {order_id}",
                "Professional tier unlocks advanced workflows and priority support.",
                "Configure custom agent pipelines in the dashboard.",
                "Integrate content + outreach schedulers as needed.",
                "Support: support@getsincor.com",
            ],
        )
        return path, pages

    def generate_enterprise_guide(self, order_id: str) -> Tuple[Path, int]:
        path = self.output_dir / f"sincor-enterprise-guide-{order_id}.pdf"
        pages = self._write_doc(
            path,
            "SINCOR Enterprise Training Guide",
            [
                f"Order: {order_id}",
                "Enterprise includes full agent swarm capacity and white-label options.",
                "Coordinate with your success manager for custom integrations.",
                "Support: support@getsincor.com",
            ],
        )
        return path, pages

    def generate_quickstart_checklist(self, order_id: str) -> Tuple[Path, int]:
        path = self.output_dir / f"quickstart-checklist-{order_id}.pdf"
        pages = self._write_doc(
            path,
            "SINCOR Quickstart Checklist",
            [
                f"Order: {order_id}",
                "[ ] Confirm email + login",
                "[ ] Complete onboarding profile",
                "[ ] Open dashboard",
                "[ ] Review active agents",
                "[ ] Run first outreach or content cycle",
            ],
        )
        return path, pages


_generator: Optional[PDFGenerator] = None


def get_pdf_generator(output_dir: str | None = None) -> PDFGenerator:
    global _generator
    if _generator is None:
        if not output_dir:
            raise ValueError("output_dir required on first call")
        _generator = PDFGenerator(output_dir)
    return _generator
