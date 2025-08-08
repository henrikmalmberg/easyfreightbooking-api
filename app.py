from flask_cors import CORS
from flask import Flask, request, jsonify
from math import radians, cos, sin, sqrt, atan2, log
from datetime import datetime, timedelta
import pytz
import holidays
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base
import os

# ---- Nytt: e-post & XML ----
import smtplib, ssl
from email.message import EmailMessage
import xml.etree.ElementTree as ET

app = Flask(__name__)
CORS(app, origins=[
    "https://easyfreightbooking.com",
    "https://easyfreightbooking-dashboard.onrender.com",
    "https://easyfreightbooking-dashboard.onrender.com/new-booking"
])

# Läs in konfigurationsdata
with open("config.json", "r") as f:
    config = json.load(f)

# 🔌 Skapa databasanslutning
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set in environment variables")

engine = create_engine(DATABASE_URL)
from sqlalchemy.orm import scoped_session
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

# 🏗️ Skapa tabeller om de inte redan finns
Base.metadata.create_all(bind=engine)


# ---------- Hjälpfunktioner: pris, tider, mm ----------
def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

def is_zone_allowed(country, postal_prefix, available_zones):
    if country not in available_zones:
        return False
    try:
        prefix = int(postal_prefix)
    except ValueError:
        return False
    for zone in available_zones[country]:
        if "-" in zone:
            start, end = map(int, zone.split("-"))
            if start <= prefix <= end:
                return True
        else:
            if int(zone) == prefix:
                return True
    return False

def calculate_for_mode(mode_config, pickup_coord, delivery_coord, pickup_country, pickup_postal, delivery_country, delivery_postal, weight, mode_name=None):
    if not (is_zone_allowed(pickup_country, pickup_postal, mode_config["available_zones"]) and
            is_zone_allowed(delivery_country, delivery_postal, mode_config["available_zones"])):
        return {
            "available": False,
            "status": "Not available for this request"
        }

    min_allowed = mode_config.get("min_allowed_weight_kg", 0)
    max_allowed = mode_config.get("max_allowed_weight_kg", 999999)

    if weight < min_allowed or weight > max_allowed:
        return {
            "available": False,
            "status": "Weight not allowed",
            "error": f"Allowed weight range: {min_allowed}–{max_allowed} kg"
        }

    distance_km = round(haversine(pickup_coord, delivery_coord) * 1.2)
    balance_key = f"{pickup_country}-{delivery_country}"
    balance_factor = mode_config.get("balance_factors", {}).get(balance_key, 1.0)
    ftl_price = round(distance_km * mode_config["km_price_eur"] * balance_factor)

    p1 = mode_config["p1"]
    price_p1 = mode_config["price_p1"]
    p2 = mode_config["p2"]
    p2k = mode_config["p2k"]
    p2m = mode_config["p2m"]
    p3 = mode_config["p3"]
    p3k = mode_config["p3k"]
    p3m = mode_config["p3m"]
    breakpoint = mode_config["default_breakpoint"]
    maxweight = mode_config["max_weight_kg"]

    y1 = price_p1 / p1
    y2 = (p2k * ftl_price + p2m) / p2
    y3 = (p3k * ftl_price + p3m) / p3
    y4 = ftl_price / breakpoint

    n1 = (log(y2) - log(y1)) / (log(p2) - log(p1))
    a1 = y1 / (p1 ** n1)

    n2 = (log(y3) - log(y2)) / (log(p3) - log(p2))
    a2 = y2 / (p2 ** n2)

    n3 = (log(y4) - log(y3)) / (log(breakpoint) - log(p3))
    a3 = y3 / (p3 ** n3)

    if weight < p1:
        total_price = round(ftl_price * weight / maxweight)
    elif p1 <= weight < p2:
        total_price = round(min(a1 * weight ** n1 * weight, ftl_price))
    elif p2 <= weight < p3:
        total_price = round(min(a2 * weight ** n2 * weight, ftl_price))
    elif p3 <= weight <= breakpoint:
        total_price = round(min(a3 * weight ** n3 * weight, ftl_price))
    elif breakpoint < weight <= maxweight:
        total_price = ftl_price
    else:
        return {
            "available": False,
            "status": "Weight exceeds max weight"
        }

    # ⏱ Transit time från konfig
    speed = mode_config.get("transit_speed_kmpd", 500)
    base_transit = max(1, round(distance_km / speed))
    transit_time_days = [base_transit, base_transit + 1]

    # 📆 Earliest pickup
    try:
        now_utc = datetime.utcnow()
        tz_name = pytz.country_timezones[pickup_country.lower()][0]
        now_local = now_utc.replace(tzinfo=pytz.utc).astimezone(pytz.timezone(tz_name))
    except:
        now_local = datetime.utcnow()

    cutoff_hour = mode_config.get("cutoff_hour", 10)
    cutoff = now_local.replace(hour=cutoff_hour, minute=0, second=0, microsecond=0)
    days_to_add = 1 if now_local < cutoff else 2

    try:
        country_holidays = holidays.country_holidays(pickup_country.upper())
    except:
        country_holidays = []

    pickup_date = now_local.date()
    added_days = 0
    while added_days < days_to_add:
        pickup_date += timedelta(days=1)
        if pickup_date.weekday() < 5 and pickup_date not in country_holidays:
            added_days += 1

    pickup_date += timedelta(days=mode_config.get("extra_pickup_days", 0))
    earliest_pickup_date = pickup_date.isoformat()

    # CO₂-utsläpp (gram)
    co2_grams = round((distance_km * weight / 1000) * mode_config.get("co2_per_ton_km", 0) * 1000)

    return {
        "available": True,
        "status": "success",
        "total_price_eur": total_price,
        "ftl_price_eur": ftl_price,
        "distance_km": distance_km,
        "transit_time_days": transit_time_days,
        "earliest_pickup_date": earliest_pickup_date,
        "currency": "EUR",
        "co2_emissions_grams": co2_grams,
        "description": mode_config.get("description", "")
    }

# ---------- Pris-endpoint ----------
@app.route("/calculate", methods=["POST"])
def calculate():
    data = request.json
    try:
        pickup_coord = data["pickup_coordinate"]
        pickup_country = data["pickup_country"]
        pickup_postal = data["pickup_postal_prefix"]
        delivery_coord = data["delivery_coordinate"]
        delivery_country = data["delivery_country"]
        delivery_postal = data["delivery_postal_prefix"]
        weight = float(data["chargeable_weight"])
    except (KeyError, ValueError):
        return jsonify({"error": "Missing or invalid input"}), 400

    results = {}
    for mode in config:
        results[mode] = calculate_for_mode(
            config[mode],
            pickup_coord,
            delivery_coord,
            pickup_country,
            pickup_postal,
            delivery_country,
            delivery_postal,
            weight,
            mode_name=mode
        )

    return jsonify(results)


# ---------- NYTT: Booking endpoint ----------
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
FROM_EMAIL = os.getenv("FROM_EMAIL", "no-reply@easyfreightbooking.com")
INTERNAL_BOOKING_EMAIL = os.getenv("INTERNAL_BOOKING_EMAIL", "henrik.malmberg@begoma.se")

@app.post("/book")
def book():
    try:
        data = request.get_json(force=True)

        # 1) Bygg XML
        xml_bytes = build_booking_xml(data)

        # 2) Bekräftelser till bokare + update_contact (om olika)
        to_confirm = set()
        if data.get("booker", {}).get("email"):
            to_confirm.add(data["booker"]["email"])
        uc_email = (data.get("update_contact") or {}).get("email")
        if uc_email and uc_email.lower() not in {e.lower() for e in to_confirm}:
            to_confirm.add(uc_email)

        subject_conf = f"EFB Booking confirmation – {safe_ref(data)}"
        body_conf = render_text_confirmation(data)
        for rcpt in to_confirm:
            send_email(
                to=rcpt,
                subject=subject_conf,
                body=body_conf,
                attachments=[]
            )

        # 3) Internt bokningsmejl med XML
        subject_internal = f"EFB NEW BOOKING – {safe_ref(data)}"
        body_internal = render_text_internal(data)
        send_email(
            to=INTERNAL_BOOKING_EMAIL,
            subject=subject_internal,
            body=body_internal,
            attachments=[("booking.xml", "application/xml", xml_bytes)]
        )

        return jsonify({"ok": True})

    except Exception as e:
        app.logger.exception("BOOK failed")
        return jsonify({"ok": False, "error": str(e)}), 500



# ---------- E-post & XML-hjälpare ----------
def send_email(to: str, subject: str, body: str, attachments: list[tuple[str, str, bytes]]):
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("SMTP credentials not configured (SMTP_HOST/SMTP_USER/SMTP_PASS).")

    msg = EmailMessage()
    msg["From"] = FROM_EMAIL
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    for filename, mime, content in attachments or []:
        maintype, subtype = mime.split("/")
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(context=context)
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def build_booking_xml(d: dict) -> bytes:
    def cm_to_m(x):
        try:
            return float(x) / 100.0
        except:
            return 0.0

    root = ET.Element("CreateBooking")
    booking = ET.SubElement(root, "booking")

    ET.SubElement(booking, "customerBookingId").text = safe_ref(d)
    # Om du har kundkort-id kan du lägga till:
    # ET.SubElement(booking, "customerCardId").text = d.get("customer_card_id","")

    # Locations: 1 = pickup, 2 = delivery
    locs = ET.SubElement(booking, "locations")
    for loc_type, src in [
        (1, d.get("pickup", {})),
        (2, d.get("delivery", {})),
    ]:
        loc = ET.SubElement(locs, "location")
        ET.SubElement(loc, "locationType").text = str(loc_type)
        ET.SubElement(loc, "locationName").text = src.get("business_name", "")
        ET.SubElement(loc, "streetAddress").text = src.get("address", "")
        ET.SubElement(loc, "city").text = src.get("city", "")
        ET.SubElement(loc, "countryCode").text = src.get("country", "")
        zipcode = src.get("postal", "")
        ET.SubElement(loc, "zipcode").text = f"{src.get('country','')}-{zipcode}"
        if loc_type == 1:
            ET.SubElement(loc, "planningDateUTC").text = to_utc_iso(d.get("earliest_pickup"))

    # Goods
    goods_specs = ET.SubElement(booking, "goodsSpecifications")
    for g in d.get("goods") or []:
        row = ET.SubElement(goods_specs, "goodsSpecification")
        ET.SubElement(row, "goodsMarks").text = g.get("marks", "")
        ET.SubElement(row, "goodsPhgType").text = g.get("type", "")
        ET.SubElement(row, "goodsLength").text = str(g.get("length", ""))
        ET.SubElement(row, "goodsWidth").text = str(g.get("width", ""))
        ET.SubElement(row, "goodsHeight").text = str(g.get("height", ""))
        qty = int(float(g.get("quantity") or 1))
        ET.SubElement(row, "goodsQty").text = str(qty)
        cbm = cm_to_m(g.get("length", 0)) * cm_to_m(g.get("width", 0)) * cm_to_m(g.get("height", 0)) * qty
        ET.SubElement(row, "goodsCBM").text = f"{cbm:.3f}"
        ET.SubElement(row, "goodsLDM").text = f"{float(g.get('ldm', 0) or 0):.2f}"
        ET.SubElement(row, "goodsWeight").text = str(g.get("weight", ""))
        ET.SubElement(row, "goodsChgWeight").text = str(int(round(d.get("chargeable_weight", 0))))

    # References
    refs_node = ET.SubElement(booking, "references")
    refs = d.get("references") or {}
    ET.SubElement(refs_node, "loadingReference").text = refs.get("reference1", "")
    ET.SubElement(refs_node, "unloadingReference").text = refs.get("reference2", "")
    ET.SubElement(refs_node, "invoiceReference").text = d.get("invoice_reference", "")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

def safe_ref(d: dict) -> str:
    p = d.get("pickup", {})
    q = d.get("delivery", {})
    ref = f"{p.get('country','')}{p.get('postal','')}→{q.get('country','')}{q.get('postal','')} {d.get('earliest_pickup','')}"
    return ref.strip()

def to_utc_iso(date_str: str | None) -> str:
    # Input "YYYY-MM-DD" -> "YYYY-MM-DDT09:00:00Z" (default 09:00 UTC)
    try:
        dt_local = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        dt_local = datetime.utcnow()
    return dt_local.replace(hour=9, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

def format_transit(tt):
    if isinstance(tt, (list, tuple)) and len(tt) == 2:
        return f"{tt[0]}–{tt[1]} days"
    return str(tt or "")

def render_text_confirmation(d: dict) -> str:
    p, q = d.get("pickup", {}), d.get("delivery", {})
    uc = d.get("update_contact", {}) or {}
    lines = [
        "Thank you for your booking with Easy Freight Booking.",
        "",
        f"Route: {p.get('country','')} {p.get('postal','')} {p.get('city','')} → {q.get('country','')} {q.get('postal','')} {q.get('city','')}",
        f"Mode: {d.get('selected_mode','')}",
        f"Price: {d.get('price_eur','')} EUR excl. VAT",
        f"Earliest pickup: {d.get('earliest_pickup','')}",
        f"Transit time: {format_transit(d.get('transit_time_days'))}",
        "",
        f"Update contact: {uc.get('name','')} <{uc.get('email','')}> {uc.get('phone','')}",
        "",
        "We’ll get back if anything needs clarification.",
    ]
    return "\n".join(lines)

def render_text_internal(d: dict) -> str:
    p, q = d.get("pickup", {}), d.get("delivery", {})
    b = d.get("booker", {}) or {}
    uc = d.get("update_contact", {}) or {}
    lines = [
        "NEW BOOKING",
        f"Booker: {b.get('name','')} <{b.get('email','')}> {b.get('phone','')}",
        f"Update contact: {uc.get('name','')} <{uc.get('email','')}> {uc.get('phone','')}",
        "",
        f"Route: {p.get('country','')} {p.get('postal','')} {p.get('city','')} → {q.get('country','')} {q.get('postal','')} {q.get('city','')}",
        f"Mode: {d.get('selected_mode','')}",
        f"Price: {d.get('price_eur','')} EUR excl. VAT",
        f"Earliest pickup: {d.get('earliest_pickup','')}",
        f"Transit time: {format_transit(d.get('transit_time_days'))}",
        f"Chargeable weight: {int(round(d.get('chargeable_weight',0)))} kg",
        "",
        "Goods:"
    ]
    for g in d.get("goods") or []:
        lines.append(f" - {g.get('quantity','1')}× {g.get('type','')} {g.get('length','')}x{g.get('width','')}x{g.get('height','')}cm, {g.get('weight','')} kg")
    lines.append("")
    lines.append("XML attached: booking.xml")
    return "\n".join(lines)


# ---------- Main ----------
if __name__ == "__main__":
    app.run(debug=True)
