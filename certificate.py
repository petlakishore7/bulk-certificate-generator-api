import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#b8923a")
GREY = colors.HexColor("#555555")


def _fit_font_size(text, font, max_width, start, minimum):
    size = start
    while size > minimum and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def generate_certificate_pdf(path, *, recipient_name, event_name, issuer, issue_date, certificate_id):
    """Render the single predefined certificate template to a PDF at `path`.

    Written to a temp file and renamed so a partially written file is never left behind.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        c = canvas.Canvas(tmp_path, pagesize=(PAGE_W, PAGE_H))
        c.setTitle(f"Certificate - {recipient_name}")
        c.setAuthor(issuer)
        mid = PAGE_W / 2

        # Double border
        c.setStrokeColor(GOLD)
        c.setLineWidth(4)
        c.rect(25, 25, PAGE_W - 50, PAGE_H - 50)
        c.setStrokeColor(NAVY)
        c.setLineWidth(1)
        c.rect(36, 36, PAGE_W - 72, PAGE_H - 72)

        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 38)
        c.drawCentredString(mid, 465, "CERTIFICATE OF COMPLETION")

        c.setFillColor(GREY)
        c.setFont("Helvetica", 15)
        c.drawCentredString(mid, 415, "This is to certify that")

        max_w = PAGE_W - 200
        size = _fit_font_size(recipient_name, "Helvetica-Bold", max_w, 36, 12)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", size)
        c.drawCentredString(mid, 365, recipient_name)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.5)
        c.line(mid - max_w / 2, 352, mid + max_w / 2, 352)

        c.setFillColor(GREY)
        c.setFont("Helvetica", 15)
        c.drawCentredString(mid, 318, "has successfully completed")

        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 20)
        y = 282
        for line in simpleSplit(event_name, "Helvetica-Bold", 20, max_w):
            c.drawCentredString(mid, y, line)
            y -= 26

        c.setFillColor(GREY)
        c.setFont("Helvetica", 12)
        c.drawString(90, 95, f"Date: {issue_date}")
        c.drawRightString(PAGE_W - 90, 95, f"Issued by: {issuer}")
        c.setFont("Helvetica", 8)
        c.drawCentredString(mid, 55, f"Certificate ID: {certificate_id}")

        c.showPage()
        c.save()
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
