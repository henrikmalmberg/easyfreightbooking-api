# pdf_utils.py  (robust mot dina modelnamn)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Table, TableStyle, SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.utils import ImageReader
import io, qrcode
from datetime import date

def _get(obj, names, default=""):
    """försök flera attributnamn i ordning"""
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            return "" if v is None else v
    return default

def _get_addr(booking, which):
    # which = "shipper" eller "consignee"
    cand = []
    if which == "shipper":
        cand = ["shipper_address", "sender_address", "pickup_address"]
    else:
        cand = ["consignee_address", "receiver_address", "delivery_address"]
    for name in cand:
        a = getattr(booking, name, None)
        if a is not None:
            return a
    return None

def _fmt_addr(addr):
    if not addr:
        return ""
    lines = [
        _get(addr, ["company", "business_name", "name"]),
        _get(addr, ["address_line1", "address"]),
        _get(addr, ["address_line2"]),
        f"{_get(addr, ['postal_code','postal'])} {_get(addr, ['city'])}".strip(),
        _get(addr, ["country","country_code"]),
    ]
    lines = [str(l).strip() for l in lines if str(l).strip()]
    return "\n".join(lines)

def _first_truthy(*vals):
    for v in vals:
        if v:
            return v
    return None

def _date_to_str(d):
    try:
        return d.isoformat()
    except Exception:
        return str(d or "") or "-"

def _summarize_goods(goods):
    """Return (desc, packages, gross_kg, volume_cbm)"""
    if not goods:
        return "-", "-", "-", "-"
    try:
        pkgs = 0
        kg = 0.0
        desc_parts = []
        for g in goods:
            if not isinstance(g, dict):
                continue
            q = int(float(g.get("quantity") or 0))
            w = float(g.get("weight") or 0.0)
            t = str(g.get("type") or "")
            L = str(g.get("length") or "")
            W = str(g.get("width") or "")
            H = str(g.get("height") or "")
            ldm = g.get("ldm")
            mark = g.get("marks")
            pkgs += q
            kg += w
            bits = []
            if q: bits.append(f"{q}x")
            if t: bits.append(t)
            dims = "x".join([x for x in [L,W,H] if x])
            if dims: bits.append(dims + "cm")
            if ldm: bits.append(f"{ldm} LDM")
            if mark: bits.append(str(mark))
            if bits:
                desc_parts.append(" ".join(bits))
        desc = "; ".join(desc_parts) or "-"
        return desc, pkgs or "-", int(round(kg)) or "-", "-"
    except Exception:
        return "-", "-", "-", "-"

def generate_cmr_pdf_bytes(booking, carrier_info: dict) -> bytes:
    """
    Robust CMR-pdf mot olika modelnamn.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=14*mm, bottomMargin=14*mm, leftMargin=14*mm, rightMargin=14*mm
    )

    styles = getSampleStyleSheet()
    normal = styles["Normal"]; normal.alignment = TA_LEFT

    elems = []

    # Rubrik
    elems.append(Paragraph("<b>CMR / International Consignment Note</b>", styles["Title"]))
    elems.append(Spacer(1, 6))

    # QR på bokningsnummer
    cmr_number = _get(booking, ["booking_number", "id"], "N/A")
    qr = qrcode.make(str(cmr_number))
    qr_buf = io.BytesIO(); qr.save(qr_buf, format="PNG"); qr_buf.seek(0)
    qr_img = ImageReader(qr_buf)

    # Adresser
    shipper_addr   = _get_addr(booking, "shipper")
    consignee_addr = _get_addr(booking, "consignee")
    shipper  = _fmt_addr(shipper_addr)
    consignee = _fmt_addr(consignee_addr)

    # Carrier
    carrier_lines = [carrier_info.get("name",""), carrier_info.get("address","")]
    orgline = " ".join([x for x in [carrier_info.get("orgno",""), carrier_info.get("phone",""), carrier_info.get("email","")] if x])
    if orgline: carrier_lines.append(orgline)
    carrier_text = "\n".join([l for l in carrier_lines if l])

    hdr_tbl = Table([[
        Paragraph("<b>1. Shipper / Avsändare</b><br/>" + (shipper or "-"), normal),
        Paragraph("<b>2. Consignee / Mottagare</b><br/>" + (consignee or "-"), normal),
        Paragraph("<b>3. Carrier / Transportör</b><br/>" + (carrier_text or "-"), normal),
        qr_img
    ]], colWidths=[60*mm, 60*mm, 45*mm, 20*mm])
    hdr_tbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.6, colors.black),
        ("VALIGN", (0,0), (-2,-1), "TOP"),
        ("ALIGN", (-1,0), (-1,0), "CENTER"),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    elems.append(hdr_tbl); elems.append(Spacer(1, 6))

    # Pickup/Delivery + referenser
    pickup_place   = _first_truthy(_get(booking, ["pickup_city"]), _get(shipper_addr or object(), ["city"]))
    delivery_place = _first_truthy(_get(booking, ["delivery_city"]), _get(consignee_addr or object(), ["city"]))
    pickup_date = _first_truthy(
        _get(booking, ["pickup_date"]),
        _get(booking, ["loading_planned_date", "loading_requested_date"]),
    )
    delivery_date = _first_truthy(
        _get(booking, ["delivery_date"]),
        _get(booking, ["unloading_planned_date", "unloading_requested_date"]),
    )

    # referenser: antingen separata fält eller dict .references
    refs = getattr(booking, "references", None) or {}
    ref1 = _first_truthy(_get(booking, ["customer_reference"]), refs.get("reference1"))
    ref2 = _first_truthy(_get(booking, ["additional_reference"]), refs.get("reference2"))

    tbl2 = Table([[
        Paragraph(f"<b>4. Place & date of taking over</b><br/>{pickup_place or '-'}<br/>{_date_to_str(pickup_date) or '-'}", normal),
        Paragraph(f"<b>5. Place designated for delivery</b><br/>{delivery_place or '-'}<br/>{_date_to_str(delivery_date) or '-'}", normal),
        Paragraph(f"<b>6. References</b><br/>Ref1: {ref1 or '-'}<br/>Ref2: {ref2 or '-'}", normal),
    ]], colWidths=[70*mm, 70*mm, 45*mm])
    tbl2.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    elems.append(tbl2); elems.append(Spacer(1, 6))

    # Goods
    goods_desc = _get(booking, ["goods_description"], None)
    packages   = _get(booking, ["packages", "pallets"], None)
    gross_kg   = _get(booking, ["gross_weight_kg", "weight_kg"], None)
    volume_cbm = _get(booking, ["volume_cbm"], None)

    if goods_desc is None or packages is None or gross_kg is None:
        # härled från booking.goods (din struktur)
        goods_desc, packages, gross_kg, volume_cbm_calc = _summarize_goods(getattr(booking, "goods", None))
        if volume_cbm in (None, "", "-"):
            volume_cbm = volume_cbm_calc

    hs_code   = _get(booking, ["hs_code"], "-") or "-"
    dangerous = getattr(booking, "dangerous_goods", None)
    dangerous_str = "Yes" if dangerous else "No"

    goods_tbl = Table([
        [Paragraph("<b>7. Description of goods</b>", normal), Paragraph("<b>8. Packages</b>", normal),
         Paragraph("<b>9. Gross weight (kg)</b>", normal), Paragraph("<b>10. Volume (m³)</b>", normal),
         Paragraph("<b>11. HS code</b>", normal), Paragraph("<b>12. DG</b>", normal)],
        [Paragraph(str(goods_desc or "-"), normal), str(packages or "-"),
         str(gross_kg or "-"), str(volume_cbm or "-"), str(hs_code or "-"), dangerous_str]
    ], colWidths=[65*mm, 20*mm, 30*mm, 25*mm, 25*mm, 15*mm])
    goods_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#f3f3f3")),
        ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4)
    ]))
    elems.append(goods_tbl); elems.append(Spacer(1, 6))

    # Incoterms / instruktioner / tillval
    incoterms = _get(booking, ["incoterms"], "-") or "-"
    instructions = _get(booking, ["instructions"], "-") or "-"
    accessorials = []
    if bool(_get(booking, ["tail_lift"], False)): accessorials.append("Tail-lift")
    if bool(_get(booking, ["pre_notice"], False)): accessorials.append("Pre-notice")
    accessorials_str = ", ".join(accessorials) if accessorials else "-"

    info_tbl = Table([
        [Paragraph("<b>13. Incoterms</b>", normal),
         Paragraph("<b>14. Special instructions</b>", normal),
         Paragraph("<b>15. Accessorials</b>", normal)],
        [incoterms, Paragraph(str(instructions), normal), accessorials_str]
    ], colWidths=[35*mm, 95*mm, 55*mm])
    info_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#f3f3f3")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1),4), ("BOTTOMPADDING",(0,0),(-1,-1),4)
    ]))
    elems.append(info_tbl); elems.append(Spacer(1, 6))

    # Signaturer
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
    elems.append(sign_tbl); elems.append(Spacer(1, 4))

    # Footer
    elems.append(Paragraph(f"CMR/Booking No: <b>{cmr_number}</b>", normal))

    # Bygg PDF
    doc.build(elems)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
