# pdf_utils.py
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Table, TableStyle, SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.utils import ImageReader
import io, qrcode

def _fmt_addr(addr):
    if not addr:
        return ""
    lines = [
        (addr.company or addr.name or "").strip(),
        (addr.address_line1 or "").strip(),
        (addr.address_line2 or "").strip(),
        f"{(addr.postal_code or '').strip()} {(addr.city or '').strip()}".strip(),
        (addr.country or "").strip(),
    ]
    lines = [l for l in lines if l]
    return "\n".join(lines)

def generate_cmr_pdf_bytes(booking, carrier_info: dict) -> bytes:
    """
    booking: din SQLAlchemy Booking med .shipper_address, .consignee_address etc.
    carrier_info: {"name": "...", "address": "...", "orgno": "...", "phone": "...", "email": "..."}
    Returnerar PDF som bytes.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=14*mm, bottomMargin=14*mm, leftMargin=14*mm, rightMargin=14*mm)

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    normal.alignment = TA_LEFT

    elems = []

    # Rubrik
    elems.append(Paragraph("<b>CMR / International Consignment Note</b>", styles["Title"]))
    elems.append(Spacer(1, 6))

    # QR-kod på bokningsnummer
    cmr_number = getattr(booking, "booking_number", None) or getattr(booking, "id", "N/A")
    qr = qrcode.make(str(cmr_number))
    qr_buf = io.BytesIO()
    qr.save(qr_buf, format="PNG")
    qr_buf.seek(0)
    qr_img = ImageReader(qr_buf)

    # Topp: Shipper, Consignee, Carrier + QR
    shipper = _fmt_addr(getattr(booking, "shipper_address", None))
    consignee = _fmt_addr(getattr(booking, "consignee_address", None))

    carrier_lines = [
        carrier_info.get("name",""),
        carrier_info.get("address",""),
    ]
    orgline = " ".join([x for x in [carrier_info.get("orgno",""), carrier_info.get("phone",""), carrier_info.get("email","")] if x])
    if orgline:
        carrier_lines.append(orgline)
    carrier_text = "\n".join([l for l in carrier_lines if l])

    hdr_data = [
        [
            Paragraph("<b>1. Shipper / Avsändare</b><br/>" + (shipper or "-"), normal),
            Paragraph("<b>2. Consignee / Mottagare</b><br/>" + (consignee or "-"), normal),
            Paragraph("<b>3. Carrier / Transportör</b><br/>" + (carrier_text or "-"), normal),
            qr_img
        ]
    ]
    hdr_tbl = Table(hdr_data, colWidths=[60*mm, 60*mm, 45*mm, 20*mm])
    hdr_tbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.6, colors.black),
        ("VALIGN", (0,0), (-2,-1), "TOP"),
        ("ALIGN", (-1,0), (-1,0), "CENTER"),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    elems.append(hdr_tbl)
    elems.append(Spacer(1, 6))

    # Pickup/Delivery + referenser
    pickup_place = getattr(booking, "pickup_city", None) or getattr(getattr(booking, "shipper_address", None), "city", "")
    delivery_place = getattr(booking, "delivery_city", None) or getattr(getattr(booking, "consignee_address", None), "city", "")
    pickup_date = getattr(booking, "pickup_date", None) or getattr(booking, "earliest_pickup_date", None)
    delivery_date = getattr(booking, "delivery_date", None)

    ref1 = getattr(booking, "customer_reference", "") or ""
    ref2 = getattr(booking, "additional_reference", "") or ""

    row2 = [
        [
            Paragraph(f"<b>4. Place & date of taking over</b><br/>{pickup_place or '-'}<br/>{pickup_date or '-'}", normal),
            Paragraph(f"<b>5. Place designated for delivery</b><br/>{delivery_place or '-'}<br/>{delivery_date or '-'}", normal),
            Paragraph(f"<b>6. References</b><br/>Ref1: {ref1 or '-'}<br/>Ref2: {ref2 or '-'}", normal),
        ]
    ]
    tbl2 = Table(row2, colWidths=[70*mm, 70*mm, 45*mm])
    tbl2.setStyle(TableStyle([("BOX",(0,0),(-1,-1),0.6,colors.black), ("VALIGN",(0,0),(-1,-1),"TOP"), ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    elems.append(tbl2)
    elems.append(Spacer(1, 6))

    # Goods block
    goods_desc = getattr(booking, "goods_description", "") or "-"
    packages = getattr(booking, "packages", None) or getattr(booking, "pallets", None) or "-"
    gross_weight = getattr(booking, "gross_weight_kg", None) or getattr(booking, "weight_kg", None) or "-"
    volume_cbm = getattr(booking, "volume_cbm", None) or "-"
    hs_code = getattr(booking, "hs_code", None) or "-"
    dangerous = getattr(booking, "dangerous_goods", None)
    dangerous_str = "Yes" if dangerous else "No"

    goods_tbl = Table([
        [Paragraph("<b>7. Description of goods</b>", normal), Paragraph("<b>8. Packages</b>", normal),
         Paragraph("<b>9. Gross weight (kg)</b>", normal), Paragraph("<b>10. Volume (m³)</b>", normal), Paragraph("<b>11. HS code</b>", normal), Paragraph("<b>12. DG</b>", normal)],
        [Paragraph(goods_desc, normal), str(packages), str(gross_weight), str(volume_cbm), str(hs_code), dangerous_str]
    ], colWidths=[65*mm, 20*mm, 30*mm, 25*mm, 25*mm, 15*mm])
    goods_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#f3f3f3")),
        ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4)
    ]))
    elems.append(goods_tbl)
    elems.append(Spacer(1, 6))

    # Incoterms / instr / tillval
    incoterms = getattr(booking, "incoterms", "") or "-"
    instructions = getattr(booking, "instructions", "") or "-"
    accessorials = []
    if getattr(booking, "tail_lift", False): accessorials.append("Tail-lift")
    if getattr(booking, "pre_notice", False): accessorials.append("Pre-notice")
    accessorials_str = ", ".join(accessorials) if accessorials else "-"

    info_tbl = Table([
        [Paragraph("<b>13. Incoterms</b>", normal), Paragraph("<b>14. Special instructions</b>", normal), Paragraph("<b>15. Accessorials</b>", normal)],
        [incoterms, Paragraph(instructions, normal), accessorials_str]
    ], colWidths=[35*mm, 95*mm, 55*mm])
    info_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#f3f3f3")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4)
    ]))
    elems.append(info_tbl)
    elems.append(Spacer(1, 6))

    # Signaturblock
    sign_tbl = Table([
        [Paragraph("<b>16. Shipper signature (date/place)</b>", normal),
         Paragraph("<b>17. Carrier signature (date/place)</b>", normal),
         Paragraph("<b>18. Consignee signature (date/place)</b>", normal)],
        ["\n\n\n\n", "\n\n\n\n", "\n\n\n\n"]
    ], colWidths=[60*mm, 60*mm, 60*mm])
    sign_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("TOPPADDING",(0,1),(-1,1),18),
    ]))
    elems.append(sign_tbl)
    elems.append(Spacer(1, 4))

    # Footer med CMR/Booking nr
    elems.append(Paragraph(f"CMR/Booking No: <b>{cmr_number}</b>", normal))

    # Bygg PDF
    doc.build(elems)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
