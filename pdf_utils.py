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

def _pick(obj, *names, default=""):
    """Returnera första existerande attribut/nyckel i ordning, annars default."""
    if obj is None:
        return default
    for n in names:
        # stöd både objekt-attribut och dict
        if hasattr(obj, n):
            v = getattr(obj, n)
        elif isinstance(obj, dict) and n in obj:
            v = obj.get(n)
        else:
            continue
        if v is not None:
            s = str(v).strip()
            if s:
                return s
    return default

def _fmt_addr(addr):
    """Formatera Address för dina modeller: business_name/address/postal_code/city/country_code."""
    if not addr:
        return ""
    lines = [
        _pick(addr, "business_name", "company_name", "company", "name"),
        _pick(addr, "address", "address_line1"),
        _pick(addr, "address_line2"),
        f"{_pick(addr, 'postal_code', 'postal')} {_pick(addr, 'city')}".strip(),
        _pick(addr, "country_code", "country"),
    ]
    lines = [l for l in lines if l]
    return "\n".join(lines)

def generate_cmr_pdf_bytes(booking, carrier_info: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=14*mm, bottomMargin=14*mm, leftMargin=14*mm, rightMargin=14*mm)
    styles = getSampleStyleSheet()
    normal = styles["Normal"]; normal.alignment = TA_LEFT
    elems = []

    elems.append(Paragraph("<b>CMR / International Consignment Note</b>", styles["Title"]))
    elems.append(Spacer(1, 6))

    cmr_number = getattr(booking, "booking_number", None) or getattr(booking, "id", "N/A")
    qr = qrcode.make(str(cmr_number))
    qbuf = io.BytesIO(); qr.save(qbuf, format="PNG"); qbuf.seek(0)
    qr_img = ImageReader(qbuf)

    # DINA fält: sender_address / receiver_address (fall back till shipper/consignee om de skulle finnas)
    shipper_addr   = getattr(booking, "sender_address", None)   or getattr(booking, "shipper_address", None)
    consignee_addr = getattr(booking, "receiver_address", None) or getattr(booking, "consignee_address", None)

    shipper  = _fmt_addr(shipper_addr)
    consignee = _fmt_addr(consignee_addr)

    carrier_lines = [carrier_info.get("name",""), carrier_info.get("address","")]
    orgline = " ".join([x for x in [carrier_info.get("orgno",""), carrier_info.get("phone",""), carrier_info.get("email","")] if x])
    if orgline: carrier_lines.append(orgline)
    carrier_text = "\n".join([l for l in carrier_lines if l])

    hdr_data = [[
        Paragraph("<b>1. Shipper / Avsändare</b><br/>" + (shipper or "-"), normal),
        Paragraph("<b>2. Consignee / Mottagare</b><br/>" + (consignee or "-"), normal),
        Paragraph("<b>3. Carrier / Transportör</b><br/>" + (carrier_text or "-"), normal),
        qr_img
    ]]
    hdr_tbl = Table(hdr_data, colWidths=[60*mm, 60*mm, 45*mm, 20*mm])
    hdr_tbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.6, colors.black),
        ("VALIGN", (0,0), (-2,-1), "TOP"),
        ("ALIGN", (-1,0), (-1,0), "CENTER"),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    elems.append(hdr_tbl); elems.append(Spacer(1, 6))

    pickup_place  = _pick(shipper_addr,   "city")
    delivery_place= _pick(consignee_addr, "city")
    pickup_date   = _pick(booking, "loading_planned_date", "loading_requested_date", "pickup_date", default="")
    delivery_date = _pick(booking, "unloading_planned_date", "unloading_requested_date", "delivery_date", default="")

    # konvertera datumobjekt till ISO om de inte redan är strängar
    if hasattr(pickup_date, "isoformat"):   pickup_date = pickup_date.isoformat()
    if hasattr(delivery_date, "isoformat"): delivery_date = delivery_date.isoformat()

    # referenser: din Booking har .references (dict) – plocka ut 1 & 2 om de finns
    refs = getattr(booking, "references", None) or {}
    if not isinstance(refs, dict): refs = {}
    ref1 = refs.get("reference1") or refs.get("loadingReference") or ""
    ref2 = refs.get("reference2") or refs.get("unloadingReference") or ""

    row2 = [[
        Paragraph(f"<b>4. Place & date of taking over</b><br/>{pickup_place or '-'}<br/>{pickup_date or '-'}", normal),
        Paragraph(f"<b>5. Place designated for delivery</b><br/>{delivery_place or '-'}<br/>{delivery_date or '-'}", normal),
        Paragraph(f"<b>6. References</b><br/>Ref1: {ref1 or '-'}<br/>Ref2: {ref2 or '-'}", normal),
    ]]
    tbl2 = Table(row2, colWidths=[70*mm, 70*mm, 45*mm])
    tbl2.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4)
    ]))
    elems.append(tbl2); elems.append(Spacer(1, 6))

    # goods: din Booking har normalt listan b.goods
    goods_list = getattr(booking, "goods", None) or []
    if isinstance(goods_list, dict):  # om någon råkat spara dict
        goods_list = [goods_list]

    # Summera “snyggt” till en sträng
    def _goods_desc(gs):
        parts = []
        for g in gs:
            qty = g.get("quantity") or g.get("qty") or 1
            typ = g.get("type") or g.get("description") or ""
            dims = "x".join(str(g.get(k) or "") for k in ("length","width","height")).strip("x")
            wt   = g.get("weight") or ""
            seg = f"{qty}× {typ}"
            if dims: seg += f" {dims}cm"
            if wt:   seg += f", {wt} kg"
            parts.append(seg)
        return "\n".join(parts) if parts else "-"

    goods_desc   = _goods_desc(goods_list)
    packages     = sum(int(float(g.get("quantity") or 0)) for g in goods_list) or "-"
    gross_weight = sum(float(g.get("weight") or 0.0) for g in goods_list) or "-"
    volume_cbm   = sum(float(g.get("cbm") or 0.0) for g in goods_list) or "-"
    hs_code      = "-"
    dangerous_str= "Yes" if any(g.get("dg") or g.get("dangerous") for g in goods_list) else "No"

    goods_tbl = Table([
        [Paragraph("<b>7. Description of goods</b>", normal), Paragraph("<b>8. Packages</b>", normal),
         Paragraph("<b>9. Gross weight (kg)</b>", normal), Paragraph("<b>10. Volume (m³)</b>", normal),
         Paragraph("<b>11. HS code</b>", normal), Paragraph("<b>12. DG</b>", normal)],
        [Paragraph(goods_desc, normal), str(packages), str(int(gross_weight) if gross_weight != "-" else "-"),
         str(volume_cbm), str(hs_code), dangerous_str]
    ], colWidths=[65*mm, 20*mm, 30*mm, 25*mm, 25*mm, 15*mm])
    goods_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#f3f3f3")),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4)
    ]))
    elems.append(goods_tbl); elems.append(Spacer(1, 6))

    incoterms     = _pick(booking, "incoterms", default="-")
    instructions  = _pick(booking, "instructions", default="-")
    addons = getattr(booking, "addons", None) or {}
    if not isinstance(addons, dict): addons = {}
    accessorials = [k.replace("_"," ").title() for k, v in addons.items() if v]
    accessorials_str = ", ".join(accessorials) if accessorials else "-"

    info_tbl = Table([
        [Paragraph("<b>13. Incoterms</b>", normal),
         Paragraph("<b>14. Special instructions</b>", normal),
         Paragraph("<b>15. Accessorials</b>", normal)],
        [incoterms, Paragraph(instructions, normal), accessorials_str]
    ], colWidths=[35*mm, 95*mm, 55*mm])
    info_tbl.setStyle(TableStyle([
        ("BOX",(0,0),(-1,-1),0.6,colors.black),
        ("GRID",(0,0),(-1,-1),0.3,colors.grey),
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#f3f3f3")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4)
    ]))
    elems.append(info_tbl); elems.append(Spacer(1, 6))

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
    elems.append(Paragraph(f"CMR/Booking No: <b>{cmr_number}</b>", normal))

    doc.build(elems)
    out = buf.getvalue()
    buf.close()
    return out
