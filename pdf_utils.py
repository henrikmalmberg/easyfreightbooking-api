# pdf_utils.py
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    Table, TableStyle, SimpleDocTemplate, Paragraph, Spacer, Image
)
from reportlab.lib.utils import ImageReader
import io
import json

# QR är valfritt – om modulen saknas hoppar vi över
try:
    import qrcode
except Exception:
    qrcode = None


def _pick(obj, *names, default=""):
    """Returnera första existerande attribut/nyckel som har icke-tomt värde."""
    if obj is None:
        return default
    for n in names:
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
    """Formatera Address enligt dina modeller."""
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


def _normalize_goods(goods_raw):
    """
    Tar emot goods som lista/dict/sträng och returnerar lista av dicts.
    """
    if goods_raw is None:
        return []
    if isinstance(goods_raw, str):
        try:
            parsed = json.loads(goods_raw)
        except Exception:
            return []
        goods_raw = parsed
    if isinstance(goods_raw, dict):
        return [goods_raw]
    if isinstance(goods_raw, list):
        return goods_raw
    return []


def generate_cmr_pdf_bytes(booking, carrier_info: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=14*mm, bottomMargin=14*mm,
        leftMargin=14*mm, rightMargin=14*mm
    )
    styles = getSampleStyleSheet()
    normal = styles["Normal"]; normal.alignment = TA_LEFT
    elems = []

    # Rubrik
    elems.append(Paragraph("<b>CMR / International Consignment Note</b>", styles["Title"]))
    elems.append(Spacer(1, 6))

    # CMR/Booking nr + QR (om möjligt)
    cmr_number = getattr(booking, "booking_number", None) or getattr(booking, "id", "N/A")
    qr_flowable = None
    if qrcode:
        try:
            qr_img = qrcode.make(str(cmr_number))
            qr_buf = io.BytesIO()
            qr_img.save(qr_buf, format="PNG")
            qr_buf.seek(0)
            # Platypus Image (säkert i Table)
            qr_flowable = Image(qr_buf, 18*mm, 18*mm)
        except Exception:
            qr_flowable = None

    # Adresser
    shipper_addr   = getattr(booking, "sender_address", None)   or getattr(booking, "shipper_address", None)
    consignee_addr = getattr(booking, "receiver_address", None) or getattr(booking, "consignee_address", None)
    shipper  = _fmt_addr(shipper_addr)
    consignee = _fmt_addr(consignee_addr)

    carrier_lines = [carrier_info.get("name",""), carrier_info.get("address","")]
    orgline = " ".join([x for x in [
        carrier_info.get("orgno",""), carrier_info.get("phone",""), carrier_info.get("email","")
    ] if x])
    if orgline: carrier_lines.append(orgline)
    carrier_text = "\n".join([l for l in carrier_lines if l])

    hdr_cells = [
        Paragraph("<b>1. Shipper / Avsändare</b><br/>" + (shipper or "-"), normal),
        Paragraph("<b>2. Consignee / Mottagare</b><br/>" + (consignee or "-"), normal),
        Paragraph("<b>3. Carrier / Transportör</b><br/>" + (carrier_text or "-"), normal),
    ]
    if qr_flowable is not None:
        hdr_cells.append(qr_flowable)
        col_widths = [60*mm, 60*mm, 45*mm, 20*mm]
    else:
        col_widths = [70*mm, 70*mm, 45*mm]

    hdr_tbl = Table([hdr_cells], colWidths=col_widths)
    hdr_tbl.setStyle(TableStyle([
        ("BOX", (0,0), (-1,-1), 0.6, colors.black),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",(0,0),(-1,-1),4),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    elems.append(hdr_tbl)
    elems.append(Spacer(1, 6))

    # Pickup/Delivery + datum
    pickup_place   = _pick(shipper_addr, "city")
    delivery_place = _pick(consignee_addr, "city")
    pickup_date    = _pick(booking, "loading_planned_date", "loading_requested_date", "pickup_date", default="")
    delivery_date  = _pick(booking, "unloading_planned_date", "unloading_requested_date", "delivery_date", default="")
    if hasattr(pickup_date, "isoformat"):   pickup_date = pickup_date.isoformat()
    if hasattr(delivery_date, "isoformat"): delivery_date = delivery_date.isoformat()

    # Referenser
    refs = getattr(booking, "references", None) or {}
    if not isinstance(refs, dict):
        try:
            refs = json.loads(refs)
        except Exception:
            refs = {}
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
    elems.append(tbl2)
    elems.append(Spacer(1, 6))

    # Goods
    goods_list = _normalize_goods(getattr(booking, "goods", None))

    def _goods_desc(gs):
        parts = []
        for g in gs:
            if not isinstance(g, dict): 
                continue
            qty = g.get("quantity") or g.get("qty") or 1
            typ = g.get("type") or g.get("description") or ""
            dims = "x".join(str(g.get(k) or "") for k in ("length","width","height")).strip("x")
            wt   = g.get("weight") or ""
            seg = f"{qty}× {typ}".strip()
            if dims: seg += f" {dims}cm"
            if wt:   seg += f", {wt} kg"
            parts.append(seg)
        return "\n".join(parts) if parts else "-"

    goods_desc   = _goods_desc(goods_list)
    try:
        packages     = sum(int(float(g.get("quantity") or 0)) for g in goods_list) or "-"
    except Exception:
        packages = "-"
    try:
        gross_weight = sum(float(g.get("weight") or 0.0) for g in goods_list) or "-"
    except Exception:
        gross_weight = "-"
    try:
        volume_cbm   = sum(float(g.get("cbm") or 0.0) for g in goods_list) or "-"
    except Exception:
        volume_cbm = "-"

    hs_code      = "-"
    dangerous_str= "Yes" if any((isinstance(g, dict) and (g.get("dg") or g.get("dangerous"))) for g in goods_list) else "No"

    goods_tbl = Table([
        [Paragraph("<b>7. Description of goods</b>", normal), Paragraph("<b>8. Packages</b>", normal),
         Paragraph("<b>9. Gross weight (kg)</b>", normal), Paragraph("<b>10. Volume (m³)</b>", normal),
         Paragraph("<b>11. HS code</b>", normal), Paragraph("<b>12. DG</b>", normal)],
        [Paragraph(goods_desc, normal), str(packages), str(int(gross_weight) if isinstance(gross_weight, (int,float)) else gross_weight),
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
    elems.append(goods_tbl)
    elems.append(Spacer(1, 6))

    # Incoterms / Instructions / Addons
    incoterms     = _pick(booking, "incoterms", default="-")
    instructions  = _pick(booking, "instructions", default="-")
    addons = getattr(booking, "addons", None)
    if isinstance(addons, str):
        try:
            addons = json.loads(addons)
        except Exception:
            addons = {}
    if not isinstance(addons, dict):
        addons = {}
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
    elems.append(info_tbl)
    elems.append(Spacer(1, 6))

    # Signatur + footer
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
    elems.append(Paragraph(f"CMR/Booking No: <b>{cmr_number}</b>", normal))

    doc.build(elems)
    pdf_bytes = buf.getvalue()
    buf.close()
    return pdf_bytes
