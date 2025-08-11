from flask_cors import CORS
from flask import Flask, request, jsonify
from math import radians, cos, sin, sqrt, atan2, log
from datetime import datetime, timedelta, time
import pytz
import holidays
import json
import os
import jwt
import re
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func as sa_func
from typing import Tuple, Dict, Any
from uuid import uuid4

from models import Base, Address, Booking, Organization, User, PricingConfig

# ---------------------- helpers: ids ----------------------
def generate_uuid() -> str:
    return uuid4().hex

# ---------------------- app & CORS ------------------------
app = Flask(__name__)
CORS(
    app,
    resources={
        r"/*": {
            "origins": [
                "https://easyfreightbooking.com",
                "https://easyfreightbooking-dashboard.onrender.com",
            ],
            "allow_headers": ["Content-Type", "Authorization"],
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        }
    },
)

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-prod")
JWT_HOURS = int(os.getenv("JWT_HOURS", "8"))

def require_auth(role=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            token = request.headers.get("Authorization", "").replace("Bearer ", "")
            if not token:
                return jsonify({"error": "Missing token"}), 401
            try:
                decoded = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            except jwt.ExpiredSignatureError:
                return jsonify({"error": "Token expired"}), 401
            except Exception:
                return jsonify({"error": "Invalid token"}), 401

            request.user = decoded  # { user_id, org_id, role }

            # superadmin får alltid passera
            if role and decoded.get("role") not in (role, "superadmin"):
                return jsonify({"error": "Forbidden"}), 403

            return f(*args, **kwargs)
        return wrapper
    return decorator

from werkzeug.exceptions import BadRequest

# ---------------------- parse helpers ---------------------
def parse_yyyy_mm_dd(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None

def parse_hh_mm(s: str | None):
    if not s:
        return None
    try:
        hh, mm = s.split(":")
        return time(int(hh), int(mm))
    except Exception:
        return None

# ---- E-post & XML ----
import smtplib, ssl
from email.message import EmailMessage
import xml.etree.ElementTree as ET

# ---------------------- /me -------------------------------
@app.get("/me")
@require_auth()
def me():
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == request.user["user_id"]).first()
        if not u:
            return jsonify({"error": "Not found"}), 404
        org = db.query(Organization).filter(Organization.id == u.org_id).first()
        return jsonify({
            "user": {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "role": u.role,
            },
            "organization": {
                "id": org.id if org else None,
                "company_name": org.company_name if org else "",
                "vat_number": org.vat_number if org else "",
            }
        })
    finally:
        db.close()

# ---------------------- register org ----------------------
@app.route("/register-organization", methods=["POST"])
def register_organization():
    db = SessionLocal()
    try:
        try:
            data = request.get_json(force=True)
        except BadRequest as e:
            app.logger.exception("JSON parse failed in /register-organization")
            return jsonify({"error": "Invalid JSON", "detail": str(e)}), 400

        required = ["vat_number", "company_name", "address", "invoice_email", "name", "email", "password"]
        missing = [k for k in required if not data.get(k)]
        if missing:
            return jsonify({"error": "Missing required fields", "fields": missing}), 400

        app.logger.info("Register org start vat=%s email=%s", data["vat_number"], data["email"])

        org = Organization(
            vat_number=data["vat_number"],
            company_name=data["company_name"],
            address=data["address"],
            invoice_email=data["invoice_email"],
            payment_terms_days=int(data.get("payment_terms_days", 10)),
            currency=data.get("currency", "EUR"),
        )
        db.add(org)
        db.flush()  # get org.id

        user = User(
            org_id=org.id,
            name=data["name"],
            email=data["email"],
            password_hash=generate_password_hash(data["password"]),
            role="admin",
        )
        db.add(user)
        db.commit()
        app.logger.info("Register org OK org_id=%s user_id=%s", org.id, user.id)
        return jsonify({"message": "Organization and admin created", "org_id": org.id}), 201

    except IntegrityError:
        db.rollback()
        app.logger.warning("IntegrityError on register (duplicate VAT or email)")
        return jsonify({"error": "VAT number or email already exists"}), 400
    except Exception as e:
        db.rollback()
        app.logger.exception("Register organization failed")
        return jsonify({"error": "Server error", "detail": str(e)}), 500
    finally:
        db.close()

# ---------------------- DB setup --------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set in environment variables")

engine = create_engine(DATABASE_URL)
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base.metadata.create_all(bind=engine)

# ---------- CONFIG: seed + fetch + validering -------------
def seed_published_config_from_file_if_empty():
    """Om ingen publicerad config finns i DB: läs config.json och publicera v1."""
    db = SessionLocal()
    try:
        existing = (db.query(PricingConfig)
                    .filter(PricingConfig.status == "published")
                    .order_by(PricingConfig.version.desc())
                    .first())
        if existing:
            return
        with open("config.json", "r", encoding="utf-8") as f:
            file_cfg = json.load(f)
        row = PricingConfig(
            id=generate_uuid(),
            status="published",
            version=1,
            data=file_cfg,
            created_by=None,
            comment="Seed from config.json"
        )
        db.add(row)
        db.commit()
        app.logger.info("Seeded published pricing config v1 from config.json")
    except Exception:
        db.rollback()
        app.logger.exception("Failed to seed published config")
    finally:
        db.close()

seed_published_config_from_file_if_empty()

def get_active_config(use: str = "published") -> Dict[str, Any]:
    """
    use='published' (default) → senaste publicerade
    use='draft' → aktuell draft om finns, annars senaste publicerade
    """
    db = SessionLocal()
    try:
        if use == "draft":
            draft = (db.query(PricingConfig)
                     .filter(PricingConfig.status == "draft")
                     .order_by(PricingConfig.created_at.desc())
                     .first())
            if draft:
                return draft.data
        pub = (db.query(PricingConfig)
               .filter(PricingConfig.status == "published")
               .order_by(PricingConfig.version.desc())
               .first())
        return pub.data if pub else {}
    finally:
        db.close()

# ------ Validering av config ------
_range_pat = re.compile(r"^\d{2}(-\d{2})?$")
_cc_pat = re.compile(r"^[A-Z]{2}$")
_pair_pat = re.compile(r"^[A-Z]{2}-[A-Z]{2}$")

def _num(x, name, errors, minv=None, maxv=None):
    if not isinstance(x, (int, float)):
        errors.append(f"{name} must be number")
        return
    if minv is not None and x < minv: errors.append(f"{name} must be >= {minv}")
    if maxv is not None and x > maxv: errors.append(f"{name} must be <= {maxv}")

def validate_config(cfg: Dict[str, Any]) -> Tuple[bool, list]:
    errors = []
    if not isinstance(cfg, dict) or not cfg:
        return False, ["Config root must be a non-empty object"]

    for mode_key, mode in cfg.items():
        if not isinstance(mode, dict):
            errors.append(f"{mode_key}: must be object")
            continue

        required = [
            "label","km_price_eur","co2_per_ton_km","max_weight_kg","default_breakpoint",
            "min_allowed_weight_kg","max_allowed_weight_kg","p1","price_p1","p2","p2k","p2m",
            "p3","p3k","p3m","transit_speed_kmpd","cutoff_hour","extra_pickup_days",
            "available_zones","balance_factors"
        ]
        for r in required:
            if r not in mode:
                errors.append(f"{mode_key}.{r} missing")

        # Numbers
        for n in ["km_price_eur","co2_per_ton_km","max_weight_kg","default_breakpoint",
                  "min_allowed_weight_kg","max_allowed_weight_kg","p1","price_p1","p2","p2k","p2m",
                  "p3","p3k","p3m","transit_speed_kmpd","cutoff_hour","extra_pickup_days"]:
            if n in mode:
                _num(mode[n], f"{mode_key}.{n}", errors, minv=0)

        # Relations
        if all(k in mode for k in ["min_allowed_weight_kg","max_allowed_weight_kg"]):
            if mode["min_allowed_weight_kg"] > mode["max_allowed_weight_kg"]:
                errors.append(f"{mode_key}: min_allowed_weight_kg > max_allowed_weight_kg")
        if all(k in mode for k in ["default_breakpoint","max_weight_kg"]):
            if mode["default_breakpoint"] > mode["max_weight_kg"]:
                errors.append(f"{mode_key}: default_breakpoint > max_weight_kg")

        # available_zones
        az = mode.get("available_zones", {})
        if not isinstance(az, dict) or not az:
            errors.append(f"{mode_key}.available_zones must be object")
        else:
            for cc, ranges in az.items():
                if not _cc_pat.match(cc or ""):
                    errors.append(f"{mode_key}.available_zones[{cc}] invalid country")
                if not isinstance(ranges, list) or not ranges:
                    errors.append(f"{mode_key}.available_zones[{cc}] must be non-empty list")
                else:
                    for r in ranges:
                        if not _range_pat.match(str(r)):
                            errors.append(f"{mode_key}.available_zones[{cc}] bad range '{r}'")

        # balance_factors
        bf = mode.get("balance_factors", {})
        if not isinstance(bf, dict):
            errors.append(f"{mode_key}.balance_factors must be object")
        else:
            for pair, val in bf.items():
                if not _pair_pat.match(pair or ""):
                    errors.append(f"{mode_key}.balance_factors key '{pair}' must be CC-CC")
                if not isinstance(val, (int, float)) or val <= 0:
                    errors.append(f"{mode_key}.balance_factors[{pair}] must be > 0")

    return (len(errors) == 0), errors

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

# --- helpers ---
def _fmt_time(t):
    try:
        return t.strftime("%H:%M") if t else None
    except Exception:
        return None

def address_to_dict(a):
    if not a:
        return None
    return {
        "id": a.id,
        "business_name": a.business_name,
        "address": a.address,
        "postal_code": a.postal_code,
        "city": a.city,
        "country_code": a.country_code,
        "contact_name": a.contact_name,
        "phone": a.phone,
        "email": a.email,
        "opening_hours": a.opening_hours,
        "instructions": a.instructions,
    }

def user_to_public(u):
    if not u: return None
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role}

def org_to_public(o):
    if not o: return None
    return {"id": o.id, "company_name": o.company_name, "vat_number": o.vat_number}

def booking_to_dict(b, org=None, user=None):
    return {
        "id": b.id,
        "booking_number": getattr(b, "booking_number", None),
        "booking_date": b.booking_date.isoformat() if getattr(b, "booking_date", None) else None,
        "status": getattr(b, "status", None),

        "user_id": b.user_id,
        "selected_mode": b.selected_mode,
        "price_eur": b.price_eur,
        "pickup_date": b.pickup_date.isoformat() if b.pickup_date else None,
        "transit_time_days": b.transit_time_days,
        "co2_emissions": b.co2_emissions,

        # Legacy request (bakåtkompatibelt svar)
        "asap_pickup": b.asap_pickup,
        "requested_pickup_date": b.requested_pickup_date.isoformat() if b.requested_pickup_date else None,
        "asap_delivery": b.asap_delivery,
        "requested_delivery_date": b.requested_delivery_date.isoformat() if b.requested_delivery_date else None,

        # Nya datum/tidsfält – lastning
        "loading_requested_date": b.loading_requested_date.isoformat() if b.loading_requested_date else None,
        "loading_requested_time": _fmt_time(b.loading_requested_time),
        "loading_planned_date": b.loading_planned_date.isoformat() if b.loading_planned_date else None,
        "loading_planned_time": _fmt_time(b.loading_planned_time),
        "loading_actual_date": b.loading_actual_date.isoformat() if b.loading_actual_date else None,
        "loading_actual_time": _fmt_time(b.loading_actual_time),

        # Nya datum/tidsfält – lossning
        "unloading_requested_date": b.unloading_requested_date.isoformat() if b.unloading_requested_date else None,
        "unloading_requested_time": _fmt_time(b.unloading_requested_time),
        "unloading_planned_date": b.unloading_planned_date.isoformat() if b.unloading_planned_date else None,
        "unloading_planned_time": _fmt_time(b.unloading_planned_time),
        "unloading_actual_date": b.unloading_actual_date.isoformat() if b.unloading_actual_date else None,
        "unloading_actual_time": _fmt_time(b.unloading_actual_time),

        "goods": b.goods,
        "references": b.references,
        "addons": b.addons,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "sender_address": address_to_dict(b.sender_address),
        "receiver_address": address_to_dict(b.receiver_address),

        # Enrichment for admin UI
        "organization": org_to_public(org),
        "booked_by": user_to_public(user),
    }

# ---------------------- bookings list --------------------
@app.route("/bookings", methods=["GET"])
@require_auth()
def get_bookings():
    db = SessionLocal()
    try:
        q = db.query(Booking).order_by(Booking.created_at.desc())

        if request.user["role"] == "superadmin":
            org_id = request.args.get("org_id", type=int)
            user_id = request.args.get("user_id", type=int)
            if org_id:
                q = q.filter(Booking.org_id == org_id)
            if user_id:
                q = q.filter(Booking.user_id == user_id)

            rows = q.all()

            # Prefetch orgs/users to avoid N+1
            org_ids = {b.org_id for b in rows if b.org_id}
            user_ids = {b.user_id for b in rows if b.user_id}

            orgs = {o.id: o for o in db.query(Organization).filter(Organization.id.in_(org_ids)).all()} if org_ids else {}
            users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

            return jsonify([booking_to_dict(b, orgs.get(b.org_id), users.get(b.user_id)) for b in rows])
        else:
            rows = q.filter(Booking.org_id == request.user["org_id"]).all()
            # För icke-superadmin behövs inte enrichment
            return jsonify([booking_to_dict(b) for b in rows])
    finally:
        db.close()

# --- Validering av bokningsnummer (XX-LLL-#####) ---
BOOKING_REGEX = re.compile(r"^[A-HJ-NP-TV-Z]{2}-[A-HJ-NP-TV-Z]{3}-\d{5}$")

@app.get("/bookings/<booking_number>")
@require_auth()
def get_booking_by_number(booking_number: str):
    code = (booking_number or "").upper()
    if not BOOKING_REGEX.fullmatch(code):
        return jsonify({"error": "Invalid booking number format"}), 400

    db = SessionLocal()
    try:
        q = db.query(Booking).filter(Booking.booking_number == code)
        if request.user["role"] != "superadmin":
            q = q.filter(Booking.org_id == request.user["org_id"])
        b = q.first()
        if not b:
            return jsonify({"error": "Not found"}), 404

        org = db.query(Organization).filter(Organization.id == b.org_id).first()
        user = db.query(User).filter(User.id == b.user_id).first()
        return jsonify(booking_to_dict(b, org, user))
    finally:
        db.close()

# ---------------------- login ----------------------------
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == data["email"]).first()
        if not user or not check_password_hash(user.password_hash, data["password"]):
            return jsonify({"error": "Invalid credentials"}), 401

        token = jwt.encode(
            {
                "user_id": user.id,
                "org_id": user.org_id,
                "role": user.role,
                "exp": datetime.utcnow() + timedelta(hours=JWT_HOURS),
            },
            SECRET_KEY,
            algorithm="HS256",
        )
        return jsonify({"token": token})
    finally:
        db.close()

@app.get("/ping")
def ping():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})

# ---------------------- invite user ----------------------
@app.route("/invite-user", methods=["POST"])
@require_auth()  # låt både admin & superadmin
def invite_user():
    data = request.get_json(force=True)
    db = SessionLocal()
    try:
        if request.user["role"] == "superadmin":
            target_org_id = data.get("org_id") or request.user["org_id"]
        else:
            target_org_id = request.user["org_id"]

        user = User(
            org_id=target_org_id,
            name=data["name"],
            email=data["email"],
            password_hash=generate_password_hash(data["password"]),
            role=data.get("role", "user"),
        )
        db.add(user)
        db.commit()
        return jsonify({"message": "User invited", "user_id": user.id}), 201
    except IntegrityError:
        db.rollback()
        return jsonify({"error": "Email already exists"}), 400
    finally:
        db.close()

# ---------- Pris-endpoint ----------
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
        return {"available": False, "status": "Not available for this request"}

    min_allowed = mode_config.get("min_allowed_weight_kg", 0)
    max_allowed = mode_config.get("max_allowed_weight_kg", 999999)
    if weight < min_allowed or weight > max_allowed:
        return {"available": False, "status": "Weight not allowed", "error": f"Allowed weight range: {min_allowed}–{max_allowed} kg"}

    distance_km = round(haversine(pickup_coord, delivery_coord) * 1.2)
    balance_key = f"{pickup_country}-{delivery_country}"
    balance_factor = mode_config.get("balance_factors", {}).get(balance_key, 1.0)
    ftl_price = round(distance_km * mode_config["km_price_eur"] * balance_factor)

    p1 = mode_config["p1"]; price_p1 = mode_config["price_p1"]
    p2 = mode_config["p2"]; p2k = mode_config["p2k"]; p2m = mode_config["p2m"]
    p3 = mode_config["p3"]; p3k = mode_config["p3k"]; p3m = mode_config["p3m"]
    breakpoint = mode_config["default_breakpoint"]; maxweight = mode_config["max_weight_kg"]

    y1 = price_p1 / p1
    y2 = (p2k * ftl_price + p2m) / p2
    y3 = (p3k * ftl_price + p3m) / p3
    y4 = ftl_price / breakpoint

    n1 = (log(y2) - log(y1)) / (log(p2) - log(p1)); a1 = y1 / (p1 ** n1)
    n2 = (log(y3) - log(y2)) / (log(p3) - log(p2)); a2 = y2 / (p2 ** n2)
    n3 = (log(y4) - log(y3)) / (log(breakpoint) - log(p3)); a3 = y3 / (p3 ** n3)

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
        return {"available": False, "status": "Weight exceeds max weight"}

    # ⏱ Transit time
    speed = mode_config.get("transit_speed_kmpd", 500)
    base_transit = max(1, round(distance_km / speed))
    transit_time_days = [base_transit, base_transit + 1]

    # 📆 Earliest pickup
    try:
        now_utc = datetime.utcnow()
        tz_name = pytz.country_timezones[pickup_country.upper()][0]
        now_local = now_utc.replace(tzinfo=pytz.utc).astimezone(pytz.timezone(tz_name))
    except Exception:
        now_local = datetime.utcnow()

    cutoff_hour = mode_config.get("cutoff_hour", 10)
    cutoff = now_local.replace(hour=cutoff_hour, minute=0, second=0, microsecond=0)
    days_to_add = 1 if now_local < cutoff else 2

    try:
        country_holidays = holidays.country_holidays(pickup_country.upper())
    except Exception:
        country_holidays = []

    pickup_date = now_local.date()
    added_days = 0
    while added_days < days_to_add:
        pickup_date += timedelta(days=1)
        if pickup_date.weekday() < 5 and pickup_date not in country_holidays:
            added_days += 1

    pickup_date += timedelta(days=mode_config.get("extra_pickup_days", 0))
    earliest_pickup_date = pickup_date.isoformat()

    co2_grams = round((distance_km * weight / 1000) * mode_config.get("co2_per_ton_km", 0) * 1000)

    return {
        "available": True, "status": "success",
        "total_price_eur": total_price, "ftl_price_eur": ftl_price,
        "distance_km": distance_km, "transit_time_days": transit_time_days,
        "earliest_pickup_date": earliest_pickup_date, "currency": "EUR",
        "co2_emissions_grams": co2_grams, "description": mode_config.get("description", "")
    }

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

    active_cfg = get_active_config(use="published")

    results = {}
    for mode in active_cfg:
        results[mode] = calculate_for_mode(
            active_cfg[mode], pickup_coord, delivery_coord,
            pickup_country, pickup_postal, delivery_country, delivery_postal,
            weight, mode_name=mode
        )
    return jsonify(results)

# ---------- Bokningsnummer-generator ----------
LETTERS = "ABCDEFGHJKMNPQRSTVWXYZ"
DIGITS = "0123456789"

def generate_booking_number() -> str:
    import secrets
    p1 = "".join(secrets.choice(LETTERS) for _ in range(2))
    p2 = "".join(secrets.choice(LETTERS) for _ in range(3))
    p3 = "".join(secrets.choice(DIGITS)  for _ in range(5))
    return f"{p1}-{p2}-{p3}"

# ---------- Booking endpoint ----------
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
FROM_EMAIL = os.getenv("FROM_EMAIL", "no-reply@easyfreightbooking.com")
INTERNAL_BOOKING_EMAIL = os.getenv("INTERNAL_BOOKING_EMAIL", "henrik.malmberg@begoma.se")
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "true").lower() == "true"

@app.post("/book")
@require_auth()
def book():
    db = SessionLocal()
    try:
        data = request.get_json(force=True)
        app.logger.info("BOOK payload received")

        # 1) Bygg XML
        xml_bytes = build_booking_xml(data)
        app.logger.info("XML built, %d bytes", len(xml_bytes))

        # 2) Förbered data
        user_id = (data.get("booker") or {}).get("user_id") or data.get("user_id") or request.user["user_id"]
        try:
            user_id = int(user_id)
        except Exception:
            user_id = request.user["user_id"]

        def mk_addr(src: dict, addr_type: str) -> Address:
            return Address(
                user_id=user_id,
                type=addr_type,  # "sender" / "receiver"
                business_name=src.get("business_name"),
                address=src.get("address"),
                postal_code=src.get("postal"),
                city=src.get("city"),
                country_code=src.get("country"),
                contact_name=src.get("contact_name"),
                phone=src.get("phone"),
                email=src.get("email"),
                opening_hours=src.get("opening_hours"),
                instructions=src.get("instructions"),
            )

        # 3) Spara adresser först
        sender = mk_addr(data.get("pickup", {}) or {}, "sender")
        receiver = mk_addr(data.get("delivery", {}) or {}, "receiver")
        db.add(sender); db.add(receiver)
        db.commit()  # adresserna får id

        # Hämta org_id från token
        org_id = request.user["org_id"]

        # Läs requested datum/tider
        loading_req_date = parse_yyyy_mm_dd(data.get("requested_pickup_date"))
        loading_req_time = parse_hh_mm(data.get("requested_pickup_time"))
        unloading_req_date = parse_yyyy_mm_dd(data.get("requested_delivery_date"))
        unloading_req_time = parse_hh_mm(data.get("requested_delivery_time"))

        # 4) Skapa bokning med unikt nummer
        booking_obj = None
        for _ in range(7):
            bn = generate_booking_number()
            b = Booking(
                booking_number=bn,
                user_id=user_id,
                org_id=org_id,
                selected_mode=data.get("selected_mode"),
                price_eur=float(data.get("price_eur") or 0.0),
                pickup_date=None,
                transit_time_days=str(data.get("transit_time_days") or ""),
                co2_emissions=float(data.get("co2_emissions_grams") or 0.0) / 1000.0,  # g -> kg

                sender_address_id=sender.id,
                receiver_address_id=receiver.id,
                goods=data.get("goods"),
                references=data.get("references"),
                addons=data.get("addons"),

                asap_pickup=bool(data.get("asap_pickup")) if data.get("asap_pickup") is not None else True,
                requested_pickup_date=parse_yyyy_mm_dd(data.get("requested_pickup_date")),
                asap_delivery=bool(data.get("asap_delivery")) if data.get("asap_delivery") is not None else True,
                requested_delivery_date=parse_yyyy_mm_dd(data.get("requested_delivery_date")),

                loading_requested_date=loading_req_date,
                loading_requested_time=loading_req_time,
                unloading_requested_date=unloading_req_date,
                unloading_requested_time=unloading_req_time,
            )
            db.add(b)
            try:
                db.commit()
                booking_obj = b
                break
            except IntegrityError:
                db.rollback()
                continue

        if not booking_obj:
            raise RuntimeError("Could not allocate a unique booking number after several attempts")

        booking_id = booking_obj.id
        booking_number = booking_obj.booking_number

        # 5) E-post (valfritt)
        to_confirm = set()
        if data.get("booker", {}).get("email"):
            to_confirm.add(data["booker"]["email"])
        uc_email = (data.get("update_contact") or {}).get("email")
        if uc_email and uc_email.lower() not in {e.lower() for e in to_confirm}:
            to_confirm.add(uc_email)

        subject_conf = f"EFB Booking confirmation – {booking_number}"
        body_conf = render_text_confirmation(data)
        if EMAIL_ENABLED:
            for rcpt in to_confirm:
                app.logger.info("Sending confirmation to %s", rcpt)
                send_email(to=rcpt, subject=subject_conf, body=body_conf, attachments=[])

        subject_internal = f"EFB NEW BOOKING – {booking_number}"
        body_internal = render_text_internal(data)
        if EMAIL_ENABLED:
            app.logger.info("Sending internal booking email to %s", INTERNAL_BOOKING_EMAIL)
            send_email(
                to=INTERNAL_BOOKING_EMAIL,
                subject=subject_internal,
                body=body_internal,
                attachments=[("booking.xml", "application/xml", xml_bytes)],
            )

        saved = {
            "booking_id": booking_id,
            "booking_number": booking_number,
            "asap_pickup": booking_obj.asap_pickup,
            "requested_pickup_date": booking_obj.requested_pickup_date.isoformat() if booking_obj.requested_pickup_date else None,
            "asap_delivery": booking_obj.asap_delivery,
            "requested_delivery_date": booking_obj.requested_delivery_date.isoformat() if booking_obj.requested_delivery_date else None,
        }
        return jsonify({"ok": True, "email_enabled": EMAIL_ENABLED, **saved})
    except Exception as e:
        db.rollback()
        app.logger.exception("BOOK failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()

# ---------- PATCH: uppdatera plan/utfall/status ----------
@app.patch("/bookings/<bid>")
@require_auth(role="admin")
def update_booking(bid):
    db = SessionLocal()
    try:
        b = db.query(Booking).filter(Booking.id == bid).first()
        if not b:
            return jsonify({"error": "Not found"}), 404
        if request.user["role"] != "superadmin" and b.org_id != request.user["org_id"]:
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json(force=True) or {}

        if "booking_date" in data:
            data.pop("booking_date", None)

        def set_date(attr, key):
            if key in data:
                setattr(b, attr, parse_yyyy_mm_dd(data.get(key)) if data.get(key) else None)

        def set_time(attr, key):
            if key in data:
                setattr(b, attr, parse_hh_mm(data.get(key)) if data.get(key) else None)

        # Loading
        set_date("loading_requested_date", "loading_requested_date")
        set_time("loading_requested_time", "loading_requested_time")
        set_date("loading_planned_date", "loading_planned_date")
        set_time("loading_planned_time", "loading_planned_time")
        set_date("loading_actual_date", "loading_actual_date")
        set_time("loading_actual_time", "loading_actual_time")

        # Unloading
        set_date("unloading_requested_date", "unloading_requested_date")
        set_time("unloading_requested_time", "unloading_requested_time")
        set_date("unloading_planned_date", "unloading_planned_date")
        set_time("unloading_planned_time", "unloading_planned_time")
        set_date("unloading_actual_date", "unloading_actual_date")
        set_time("unloading_actual_time", "unloading_actual_time")

        # Auto-statusregler
        manual_status = data.get("status")
        if not manual_status or manual_status not in {"CANCELLED", "EXCEPTION"}:
            if b.loading_planned_date or b.loading_planned_time:
                if b.status in (None, "NEW", "CONFIRMED"):
                    b.status = "PICKUP_PLANNED"
            if b.loading_actual_date and b.loading_actual_time:
                if b.status not in ("CANCELLED", "EXCEPTION"):
                    b.status = "PICKED_UP"
            if b.unloading_planned_date or b.unloading_planned_time:
                if b.status not in ("DELIVERED", "COMPLETED", "CANCELLED", "EXCEPTION"):
                    b.status = "DELIVERY_PLANNED"
            if b.unloading_actual_date and b.unloading_actual_time:
                if b.status not in ("CANCELLED", "EXCEPTION"):
                    b.status = "DELIVERED"

        if manual_status:
            allowed = {
                "NEW","CONFIRMED","PICKUP_PLANNED","PICKED_UP","IN_TRANSIT",
                "DELIVERY_PLANNED","DELIVERED","COMPLETED","ON_HOLD","CANCELLED","EXCEPTION"
            }
            if manual_status not in allowed:
                return jsonify({"error": "Invalid status"}), 400
            b.status = manual_status

        # Basvalideringar (datumordning)
        if b.loading_planned_date and b.loading_actual_date:
            if b.loading_actual_date < b.loading_planned_date:
                return jsonify({"error": "Actual loading cannot be before planned loading"}), 400
        if b.unloading_planned_date and b.unloading_actual_date:
            if b.unloading_actual_date < b.unloading_planned_date:
                return jsonify({"error": "Actual unloading cannot be before planned unloading"}), 400
        if b.unloading_actual_date and b.loading_actual_date:
            if b.unloading_actual_date < b.loading_actual_date:
                return jsonify({"error": "Actual unloading cannot be before actual loading"}), 400

        db.commit()

        org = db.query(Organization).filter(Organization.id == b.org_id).first()
        user = db.query(User).filter(User.id == b.user_id).first()
        return jsonify(booking_to_dict(b, org, user))
    except BadRequest as e:
        db.rollback()
        return jsonify({"error": "Invalid JSON", "detail": str(e)}), 400
    except Exception as e:
        db.rollback()
        app.logger.exception("PATCH /bookings failed")
        return jsonify({"error": "Server error", "detail": str(e)}), 500
    finally:
        db.close()

# ---------- ADMIN: Pricing config (superadmin only) ----------
@app.get("/admin/config")
@require_auth("superadmin")
def admin_get_config():
    db = SessionLocal()
    try:
        pub = (db.query(PricingConfig)
               .filter(PricingConfig.status=="published")
               .order_by(PricingConfig.version.desc())
               .first())
        draft = (db.query(PricingConfig)
                 .filter(PricingConfig.status=="draft")
                 .order_by(PricingConfig.created_at.desc())
                 .first())
        return jsonify({
            "published": {"version": pub.version if pub else None, "data": pub.data if pub else None},
            "draft": {"version": draft.version if draft else None, "data": draft.data if draft else None}
        })
    finally:
        db.close()

@app.put("/admin/config/draft")
@require_auth("superadmin")
def admin_put_draft():
    payload = request.get_json(force=True)
    cfg = payload if isinstance(payload, dict) else payload.get("data")
    ok, errs = validate_config(cfg)
    if not ok:
        return jsonify({"ok": False, "errors": errs}), 400

    db = SessionLocal()
    try:
        draft = db.query(PricingConfig).filter(PricingConfig.status=="draft").first()
        if draft:
            draft.data = cfg
        else:
            draft = PricingConfig(
                id=generate_uuid(),
                status="draft",
                version=None,
                data=cfg,
                created_by=request.user.get("user_id")
            )
            db.add(draft)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        app.logger.exception("put draft failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()

@app.post("/admin/config/validate")
@require_auth("superadmin")
def admin_validate():
    payload = request.get_json(silent=True) or {}
    cfg = payload.get("data")
    if cfg is None:
        cfg = get_active_config(use="draft") or get_active_config(use="published")
    ok, errs = validate_config(cfg)
    return jsonify({"ok": ok, "errors": errs})

@app.post("/admin/config/publish")
@require_auth("superadmin")
def admin_publish():
    payload = request.get_json(silent=True) or {}
    comment = payload.get("comment")
    effective_at = None  # publicera direkt

    db = SessionLocal()
    try:
        draft = db.query(PricingConfig).filter(PricingConfig.status=="draft").first()
        if not draft:
            return jsonify({"ok": False, "error": "No draft to publish"}), 400

        ok, errs = validate_config(draft.data)
        if not ok:
            return jsonify({"ok": False, "errors": errs}), 400

        max_v = db.query(sa_func.max(PricingConfig.version)).filter(PricingConfig.status=="published").scalar() or 0
        new_pub = PricingConfig(
            id=generate_uuid(),
            status="published",
            version=max_v + 1,
            data=draft.data,
            created_by=request.user.get("user_id"),
            comment=comment,
            effective_at=effective_at
        )
        db.add(new_pub)
        db.delete(draft)
        db.commit()
        return jsonify({"ok": True, "version": new_pub.version})
    except Exception as e:
        db.rollback()
        app.logger.exception("publish failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()

@app.get("/admin/config/history")
@require_auth("superadmin")
def admin_history():
    db = SessionLocal()
    try:
        rows = (db.query(PricingConfig)
                .filter(PricingConfig.status=="published")
                .order_by(PricingConfig.version.desc())
                .all())
        return jsonify([{
            "id": r.id, "version": r.version, "created_at": r.created_at.isoformat(),
            "created_by": r.created_by, "comment": r.comment
        } for r in rows])
    finally:
        db.close()

@app.post("/admin/config/rollback/<int:version>")
@require_auth("superadmin")
def admin_rollback(version: int):
    """Skapar/ersätter draft som kopia av en publicerad version."""
    db = SessionLocal()
    try:
        src = (db.query(PricingConfig)
               .filter(PricingConfig.status=="published", PricingConfig.version==version)
               .first())
        if not src:
            return jsonify({"ok": False, "error": "Version not found"}), 404
        draft = db.query(PricingConfig).filter(PricingConfig.status=="draft").first()
        if draft:
            draft.data = src.data
            draft.created_by = request.user.get("user_id")
        else:
            draft = PricingConfig(
                id=generate_uuid(),
                status="draft",
                version=None,
                data=src.data,
                created_by=request.user.get("user_id")
            )
            db.add(draft)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        app.logger.exception("rollback failed")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()

@app.post("/admin/calculate")
@require_auth("superadmin")
def admin_calculate_preview():
    """Som /calculate men använder draft om den finns."""
    data = request.json or {}
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

    cfg = get_active_config(use="draft") or get_active_config(use="published")
    results = {mode: calculate_for_mode(
        cfg[mode], pickup_coord, delivery_coord,
        pickup_country, pickup_postal, delivery_country, delivery_postal,
        weight, mode_name=mode
    ) for mode in cfg}
    return jsonify(results)

# ---------- E-post & XML-hjälpare ----------
def send_email(to: str, subject: str, body: str, attachments: list[tuple[str, str, bytes]]):
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        raise RuntimeError("SMTP credentials not configured (SMTP_HOST/SMTP_USER/SMTP_PASS).")

    msg = EmailMessage()
    msg["From"] = FROM_EMAIL
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    for filename, mime, content in (attachments or []):
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
        except Exception:
            return 0.0

    root = ET.Element("CreateBooking")
    booking = ET.SubElement(root, "booking")
    ET.SubElement(booking, "customerBookingId").text = safe_ref(d)

    # Locations: 1 = pickup, 2 = delivery
    locs = ET.SubElement(booking, "locations")
    for loc_type, src in [(1, d.get("pickup", {})), (2, d.get("delivery", {}))]:
        loc = ET.SubElement(locs, "location")
        ET.SubElement(loc, "locationType").text = str(loc_type)
        ET.SubElement(loc, "locationName").text = src.get("business_name", "")
        ET.SubElement(loc, "streetAddress").text = src.get("address", "")
        ET.SubElement(loc, "city").text = src.get("city", "")
        ET.SubElement(loc, "countryCode").text = src.get("country", "")
        zipcode = src.get("postal", "")
        ET.SubElement(loc, "zipcode").text = f"{src.get('country','')}-{zipcode}"
        if loc_type == 1:
            chosen = d.get("requested_pickup_date") or d.get("earliest_pickup")
            ET.SubElement(loc, "planningDateUTC").text = to_utc_iso(chosen)

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

    refs_node = ET.SubElement(booking, "references")
    refs = d.get("references") or {}
    ET.SubElement(refs_node, "loadingReference").text = refs.get("reference1", "")
    ET.SubElement(refs_node, "unloadingReference").text = refs.get("reference2", "")
    ET.SubElement(refs_node, "invoiceReference").text = d.get("invoice_reference", "")

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)

def safe_ref(d: dict) -> str:
    p = d.get("pickup", {}); q = d.get("delivery", {})
    ref = f"{p.get('country','')}{p.get('postal','')}→{q.get('country','')}{q.get('postal','')} {d.get('earliest_pickup','')}"
    return ref.strip()

def to_utc_iso(date_str: str | None) -> str:
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
    requested = d.get("requested_pickup_date")
    asap = d.get("asap_pickup")
    lines = [
        "Thank you for your booking with Easy Freight Booking.",
        "",
        f"Route: {p.get('country','')} {p.get('postal','')} {p.get('city','')} → {q.get('country','')} {q.get('postal','')} {q.get('city','')}",
        f"Mode: {d.get('selected_mode','')}",
        f"Price: {d.get('price_eur','')} EUR excl. VAT",
        f"Earliest pickup (offer): {d.get('earliest_pickup','')}",
        f"Requested pickup: {'ASAP' if asap else (requested or '—')}",
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
    requested = d.get("requested_pickup_date")
    asap = d.get("asap_pickup")
    lines = [
        "NEW BOOKING",
        f"Booker: {b.get('name','')} <{b.get('email','')}> {b.get('phone','')}",
        f"Update contact: {uc.get('name','')} <{uc.get('email','')}> {uc.get('phone','')}",
        "",
        f"Route: {p.get('country','')} {p.get('postal','')} {p.get('city','')} → {q.get('country','')} {q.get('postal','')} {q.get('city','')}",
        f"Mode: {d.get('selected_mode','')}",
        f"Price: {d.get('price_eur','')} EUR excl. VAT",
        f"Earliest pickup (offer): {d.get('earliest_pickup','')}",
        f"Requested pickup: {'ASAP' if asap else (requested or '—')}",
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

@app.teardown_appcontext
def remove_session(exception=None):
    SessionLocal.remove()
