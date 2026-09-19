"""PERMIT TO OPERATE PDF for a verified Genesis application."""
from __future__ import annotations
from datetime import datetime, timezone
from io import BytesIO


def render_permit(*, license_number: str, wallet: str, issued: str | None = None) -> bytes:
    issued = issued or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    width, height = letter
    c.setStrokeColorRGB(0.77, 0.71, 0.54)
    c.setLineWidth(2)
    c.rect(0.6 * inch, 0.6 * inch, width - 1.2 * inch, height - 1.2 * inch)
    c.setFont("Times-Bold", 18)
    c.drawCentredString(width / 2, height - 1.3 * inch, "PERMIT TO OPERATE")
    c.setFont("Times-Roman", 11)
    c.drawCentredString(width / 2, height - 1.6 * inch, "SINCOR GENESIS  ·  AGENT LICENSE")
    c.setFont("Courier", 12)
    c.drawString(1.1 * inch, height - 2.4 * inch, f"LICENSE NUMBER   {license_number}")
    c.drawString(1.1 * inch, height - 2.75 * inch, f"WALLET ADDRESS   {wallet}")
    c.drawString(1.1 * inch, height - 3.1 * inch, f"DATE             {issued}")
    c.setFont("Times-Italic", 10)
    c.drawString(1.1 * inch, 1.3 * inch, "Stamp: verified on Base. Settlement via x402 / AXM.")
    c.circle(6.3 * inch, 1.6 * inch, 0.55 * inch)
    c.setFont("Times-Bold", 8)
    c.drawCentredString(6.3 * inch, 1.55 * inch, "SINCOR")
    c.save()
    return buf.getvalue()
