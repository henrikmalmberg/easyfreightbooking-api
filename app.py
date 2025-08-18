from flask_cors import CORS
from flask import Flask, request, jsonify
from math import radians, cos, sin, sqrt, atan2, log
from datetime import datetime, timedelta, time
import pytz
import holidays
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.exc import IntegrityError
from models import Base, Address, Booking, Organization, User, PricingConfig
import re
from typing import Tuple, Dict, Any, List
from sqlalchemy import func as sa_func
import os, jwt, uuid
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import BadRequest
import smtplib, ssl
from email.message import EmailMessage
import xml.etree.ElementTree as ET
import requests
from xml.etree import ElementTree as XET
from sqlalchemy import or_
import logging
from models import OrgAddress
from flask import Response
from flask_jwt_extended import jwt_required, get_jwt_identity
from pdf_utils import generate_cmr_pdf_bytes
from flask import request
from flask import Response, jsonify
from flask_jwt_extended import jwt_required
from pdf_utils import generate_cmr_pdf_bytes

CARRIER_INFO = {
    "name": "Easy Freight Booking Logistics AB",
    "address": "Valenciagatan 2, SE-201 21 Malmö, Sweden",
    "orgno": "Org.nr 559477-6378",
    "phone": "+46 (0)40-123 456",
    "email": "operations@easyfreightbooking.com",
}



# =========================================================
# App + CORS
# =========================================================
from flask_cors import CORS

app = Flask(__name__)

CORS(app, resources={
    r"/*": {
        "origins": [
            "https://easyfreightbooking-dashboard.onrender.com",
            "https://easyfreightbooking.com",
        ],
        "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Disposition"],  # <-- korrekt plats
        "max_age": 86400,
    }
})


@app.before_request
def accept_jwt_query_param():
    # Om ingen Authorization-header finns, men ?jwt= finns i URL:en,
    # injicera den som Authorization-header så @jwt_required() fungerar.
    if "Authorization" in request.headers:
        return
    token = request.args.get("jwt")
    if token:
        request.headers.environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-prod")
JWT_HOURS = int(os.getenv("JWT_HOURS", "8"))


@app.get("/bookings/<int:booking_id>/cmr.pdf", endpoint="cmr_pdf_v2")
@jwt_required()
def cmr_pdf_v2(booking_id):
    b = db.session.get(Booking, booking_id)
    if not b:
        return jsonify({"error": "Not found"}), 404
    try:
        pdf = generate_cmr_pdf_bytes(b, CARRIER_INFO)
    except Exception:
        app.logger.exception("CMR PDF generation failed")
        return jsonify({"error": "PDF generation failed"}), 500

    return Response(
        pdf,
        status=200,
        headers={
            "Content-Type": "application/pdf",
            "Content-Disposition": f'attachment; filename="CMR_{b.booking_number or booking_id}.pdf"',
            "Cache-Control": "no-store",
        },
    )




# =========================================================
# Helpers
# =========================================================
# --- VAT helpers (replace your old normalize_vat / is_vat_format with this set) ---
_cc_pat   = re.compile(r"^[A-Z]{2}$")
_vat_num  = re.compile(r"^[A-Z0-9]{2,12}$")              # national part only (no CC)
_vat_full = re.compile(r"^([A-Z]{2})([A-Z0-9]{2,12})$")  # CC + national

def normalize_vat(v: str) -> str:
    """Uppercase och ta bort alla icke-alfa-num-tecken."""
    return re.sub(r"[^A-Za-z0-9]", "", (v or "")).upper()

def addr_key(src: dict) -> str:
    def norm(x): return (x or "").strip().lower()
    parts = [
        norm(src.get("business_name")), norm(src.get("address")), norm(src.get("postal")),
        norm(src.get("city")), norm(src.get("country")),
    ]
    return "|".join(parts)

def _require_org_and_role():
    # alla inloggade får läsa/skriva sin egen org
    return request.user["org_id"], request.user.get("role")

def parse_vat_and_cc(raw: str, cc_hint: str | None = None) -> tuple[str, str] | tuple[None, None, str]:
    """
    Accepterar 'SE556082087901' eller '556082087901' + cc_hint='SE'.
    Returnerar (CC, nationalNumber) eller (None, None, error).
    """
    v = normalize_vat(raw)
    m = _vat_full.match(v)
    if m:
        return m.group(1), m.group(2)
    if _vat_num.match(v):
        cc = (cc_hint or "").strip().upper()
        if not _cc_pat.fullmatch(cc):
            return None, None, "Country code required when VAT number has no prefix"
        return cc, v
    return None, None, "Invalid VAT format"


def generate_uuid() -> str:
    return uuid.uuid4().hex

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

            # superadmin passerar alltid
            if role and decoded.get("role") not in (role, "superadmin"):
                return jsonify({"error": "Forbidden"}), 403

            return f(*args, **kwargs)
        return wrapper
    return decorator

def upsert_org_address(db, org_id: int, src: dict, addr_type: str):
    key = addr_key({
        "business_name": src.get("business_name"),
        "address": src.get("address"),
        "postal": src.get("postal"),
        "city": src.get("city"),
        "country": src.get("country"),
    })
    exists = (db.query(OrgAddress)
                .filter(OrgAddress.org_id == org_id, OrgAddress.dedupe_key == key)
                .first())
    if exists: return
    row = OrgAddress(
        id=generate_uuid(), org_id=org_id, type=addr_type, dedupe_key=key,
        business_name=src.get("business_name"), address=src.get("address"),
        postal_code=src.get("postal"), city=src.get("city"), country_code=src.get("country"),
        contact_name=src.get("contact_name"), phone=src.get("phone"), email=src.get("email"),
        opening_hours=src.get("opening_hours"), instructions=src.get("instructions"),
    )
    db.add(row)





# =========================================================
# Public endpoints
# =========================================================
@app.get("/ping")
def ping():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})

# app.py
from flask import Response
from flask_jwt_extended import jwt_required, get_jwt_identity
from pdf_utils import generate_cmr_pdf_bytes

CARRIER_INFO = {
    "name": "Easy Freight Booking / Begoma",
    "address": "Stapelbäddsgatan 3, 211 75 Malmö, Sweden",
    "orgno": "Org.nr 556123-4567",
    "phone": "+46 (0)40-123 456",
    "email": "operations@easyfreightbooking.com",
}

@app.get("/bookings/<int:booking_id>/cmr.pdf")
@jwt_required()
def get_cmr_pdf(booking_id):
    # 1) Plocka bokningen och gör behörighetskontroll mot org/user
    b = db.session.get(Booking, booking_id)
    if not b:
        return jsonify({"error":"Not found"}), 404

    # (valfritt) kontrollera att nuvarande användare har rätt till denna booking
    # current_user_id = get_jwt_identity()
    # ...

    # 2) Generera PDF
    try:
        pdf = generate_cmr_pdf_bytes(b, CARRIER_INFO)
    except Exception as e:
        app.logger.exception("CMR PDF generation failed")
        return jsonify({"error":"PDF generation failed"}), 500

    # 3) Skicka som PDF
    headers = {
        "Content-Type": "application/pdf",
        "Content-Disposition": f'attachment; filename="CMR_{b.booking_number or booking_id}.pdf"',
        "Cache-Control": "no-store"
    }
    return Response(pdf, status=200, headers=headers)





@app.get("/addresses")
@require_auth()
def addresses_list():
    db = SessionLocal()
    try:
        org_id, _ = _require_org_and_role()
        q = db.query(OrgAddress).filter(OrgAddress.org_id == org_id)
        typ = (request.args.get("type") or "").strip()
        if typ in ("sender", "receiver"): q = q.filter(OrgAddress.type == typ)
        rows = q.order_by(OrgAddress.business_name.asc().nulls_last()).all()
        def to_dict(a: OrgAddress):
            return {
                "id": a.id, "label": a.label, "type": a.type,
                "business_name": a.business_name, "address": a.address,
                "postal_code": a.postal_code, "city": a.city, "country_code": a.country_code,
                "contact_name": a.contact_name, "phone": a.phone, "email": a.email,
                "opening_hours": a.opening_hours, "instructions": a.instructions,
            }
        return jsonify([to_dict(a) for a in rows])
    finally:
        db.close()

@app.post("/addresses")
@require_auth()
def addresses_create():
    db = SessionLocal()
    try:
        org_id, _ = _require_org_and_role()
        d = request.get_json(force=True) or {}
        key = addr_key({
            "business_name": d.get("business_name"),
            "address": d.get("address"),
            "postal": d.get("postal_code"),
            "city": d.get("city"),
            "country": d.get("country_code"),
        })
        row = OrgAddress(
            id=generate_uuid(), org_id=org_id, dedupe_key=key,
            label=d.get("label"), type=d.get("type"),
            business_name=d.get("business_name"), address=d.get("address"),
            postal_code=d.get("postal_code"), city=d.get("city"), country_code=d.get("country_code"),
            contact_name=d.get("contact_name"), phone=d.get("phone"), email=d.get("email"),
            opening_hours=d.get("opening_hours"), instructions=d.get("instructions"),
        )
        db.add(row); db.commit()
        return jsonify({"id": row.id}), 201
    except IntegrityError:
        db.rollback()
        # redan finns: OK att vara idempotent
        return jsonify({"ok": True, "duplicate": True}), 200
    finally:
        db.close()

@app.put("/addresses/<addr_id>")
@require_auth()
def addresses_update(addr_id):
    db = SessionLocal()
    try:
        org_id, _ = _require_org_and_role()
        a = db.query(OrgAddress).filter(OrgAddress.id == addr_id, OrgAddress.org_id == org_id).first()
        if not a: return jsonify({"error":"Not found"}), 404
        d = request.get_json(force=True) or {}
        # uppdatera fält
        for k in ["label","type","business_name","address","postal_code","city","country_code",
                  "contact_name","phone","email","opening_hours","instructions"]:
            if k in d: setattr(a, k, d[k])

        # uppdatera dedupe_key om något av basfälten ändrats
        a.dedupe_key = addr_key({
            "business_name": a.business_name, "address": a.address, "postal": a.postal_code,
            "city": a.city, "country": a.country_code
        })
        db.commit()
        return jsonify({"ok": True})
    except IntegrityError:
        db.rollback()
        return jsonify({"error":"Another address with same key exists"}), 409
    finally:
        db.close()

@app.delete("/addresses/<addr_id>")
@require_auth()
def addresses_delete(addr_id):
    db = SessionLocal()
    try:
        org_id, _ = _require_org_and_role()
        a = db.query(OrgAddress).filter(OrgAddress.id == addr_id, OrgAddress.org_id == org_id).first()
        if not a: return jsonify({"error":"Not found"}), 404
        db.delete(a); db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()

# Acceptera både PATCH och POST
# REMOVE any other /admin/bookings/.../reassign definitions

@app.route("/admin/bookings/<booking_id>/reassign", methods=["POST", "PATCH", "OPTIONS"])
@require_auth("superadmin")
def admin_booking_reassign(booking_id):
    # Let CORS preflight succeed
    if request.method == "OPTIONS":
        return ("", 204)

    db = SessionLocal()
    try:
        # Find booking by integer id OR by booking_number (e.g. GD-ABC-12345)
        b = None
        try:
            bid = int(booking_id)
            b = db.query(Booking).filter(Booking.id == bid).first()
        except ValueError:
            b = db.query(Booking).filter(Booking.booking_number == booking_id).first()

        if not b:
            return jsonify({"error": "Not found"}), 404

        payload = request.get_json(force=True) or {}

        # accept both keys from frontend
        org_id_val = payload.get("organization_id", payload.get("org_id"))
        if org_id_val is None:
            return jsonify({"error": "organization_id is required"}), 400

        try:
            new_org_id = int(org_id_val)
        except Exception:
            return jsonify({"error": "organization_id must be integer"}), 400

        org = db.query(Organization).get(new_org_id)
        if not org:
            return jsonify({"error": "Organization not found"}), 404

        # optional user_id handling
        new_user_id = payload.get("user_id", "__missing__")
        if new_user_id != "__missing__":
            if new_user_id is None:
                b.user_id = None
            else:
                try:
                    uid = int(new_user_id)
                except Exception:
                    return jsonify({"error": "user_id must be integer or null"}), 400
                u = db.query(User).get(uid)
                if not u:
                    return jsonify({"error": "User not found"}), 404
                if u.org_id != new_org_id:
                    return jsonify({"error": "user_id must belong to the target organization"}), 400
                b.user_id = u.id
        else:
            # if org changes and user_id not explicitly provided → clear it
            if b.org_id != new_org_id:
                b.user_id = None

        b.org_id = new_org_id
        db.commit()

        org_row = db.query(Organization).get(b.org_id) if b.org_id else None
        user_row = db.query(User).get(b.user_id) if b.user_id else None
        return jsonify(booking_to_dict(b, org_row, user_row))

    except BadRequest as e:
        db.rollback()
        return jsonify({"error": "Invalid JSON", "detail": str(e)}), 400
    except Exception as e:
        db.rollback()
        app.logger.exception("reassign failed")
        return jsonify({"error": "Server error", "detail": str(e)}), 500
    finally:
        db.close()



@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    # direkt efter: data = request.get_json(force=True)
    # --- sanitize: never trust client-sent user_id ---
    data.pop("user_id", None)
    if isinstance(data.get("booker"), dict):
        data["booker"].pop("user_id", None)

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

def is_vat_format(v: str) -> bool:
    """ Grov formatkoll: CC + 2–12 tecken. Detaljkontroller görs ev. mot VIES. """
    return bool(re.match(r"^[A-Z]{2}[A-Z0-9]{2,12}$", v or ""))

VIES_ENABLED = os.getenv("VIES_ENABLED", "true").lower() == "true"

def _vies_request_envelope(cc: str, number: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
                   xmlns:tns="urn:ec.europa.eu:taxud:vies:services:checkVat:types">
      <soap:Body>
        <tns:checkVat>
          <tns:countryCode>{cc}</tns:countryCode>
          <tns:vatNumber>{number}</tns:vatNumber>
        </tns:checkVat>
      </soap:Body>
    </soap:Envelope>""".encode("utf-8")

def _vies_parse(xml_text: str) -> dict:
    """
    Plockar ut valid/name/address/requestDate/countryCode/vatNumber ur SOAP-svaret.
    """
    # snabb str-sökning för valid; robust parse för övrigt
    out = {"valid": False, "name": None, "address": None, "requestDate": None,
           "countryCode": None, "vatNumber": None}
    try:
        root = XET.fromstring(xml_text)
        ns = {"s": "http://schemas.xmlsoap.org/soap/envelope/",
              "t": "urn:ec.europa.eu:taxud:vies:services:checkVat:types"}
        body = root.find("s:Body", ns)
        resp = body.find(".//t:checkVatResponse", ns)
        def txt(tag):
            el = resp.find(f"t:{tag}", ns)
            return (el.text or "").strip() if el is not None and el.text else None
        out["valid"]       = (txt("valid") == "true")
        out["name"]        = txt("name")
        out["address"] = " ".join((txt("address") or "").split())
        out["requestDate"] = txt("requestDate")
        out["countryCode"] = txt("countryCode")
        out["vatNumber"]   = txt("vatNumber")
    except Exception:
        pass
    return out

def vies_check(cc: str, number: str) -> tuple[bool, str | None]:
    """
    True/False-validering mot VIES. Vid nätfel: (True, None) (best effort).
    """
    if not _cc_pat.fullmatch(cc) or not _vat_num.fullmatch(number):
        return False, "Invalid VAT format"
    if not VIES_ENABLED:
        return True, None

    url = "https://ec.europa.eu/taxation_customs/vies/services/checkVatService"
    try:
        r = requests.post(url, data=_vies_request_envelope(cc, number),
                          headers={"Content-Type": "text/xml; charset=utf-8"},
                          timeout=8)
        r.raise_for_status()
        parsed = _vies_parse(r.text)
        return (True, None) if parsed.get("valid") else (False, "VAT not found in VIES")
    except Exception as e:
        app.logger.warning("VIES check skipped (%s)", e)
        return True, None

def vies_lookup(cc: str, number: str) -> tuple[dict | None, str | None]:
    """
    Returnerar metadata från VIES: {valid, name, address, requestDate, countryCode, vatNumber}
    Vid nätfel: (None, "network")  – hantera i UI.
    """
    if not _cc_pat.fullmatch(cc) or not _vat_num.fullmatch(number):
        return None, "Invalid VAT format"
    if not VIES_ENABLED:
        return {"valid": True, "name": None, "address": None,
                "requestDate": None, "countryCode": cc, "vatNumber": number}, None

    url = "https://ec.europa.eu/taxation_customs/vies/services/checkVatService"
    try:
        r = requests.post(url, data=_vies_request_envelope(cc, number),
                          headers={"Content-Type": "text/xml; charset=utf-8"},
                          timeout=8)
        r.raise_for_status()
        return _vies_parse(r.text), None
    except Exception as e:
        app.logger.warning("VIES lookup failed (%s)", e)
        return None, "network"

# ========= Admin: organizations =========

def _country_ok(cc: str | None) -> bool:
    if not cc: 
        return True
    cc = cc.strip().upper()
    return bool(re.match(r"^[A-Z]{2}$", cc))

@app.get("/vies/lookup")
def vies_lookup_endpoint():
    """
    Exempel:
      /vies/lookup?vat=556082087901&cc=SE
      /vies/lookup?vat=SE556082087901          (cc valfritt)
    Returnerar: { valid, name, address, countryCode, vatNumber, requestDate }
    """
    raw = (request.args.get("vat") or "").strip()
    cc_hint = (request.args.get("cc") or "").strip().upper() or None
    if not raw:
        return jsonify({"error":"Missing vat"}), 400

    cc, nat, err = parse_vat_and_cc(raw, cc_hint)
    if err:
        return jsonify({"error": err}), 400

    meta, err2 = vies_lookup(cc, nat)
    if err2 == "network":
        return jsonify({"error":"VIES network error, please try again"}), 502
    if not meta:
        return jsonify({"error":"Unknown error"}), 500

    return jsonify(meta)


@app.get("/admin/organizations")
@require_auth("superadmin")
def admin_orgs_list():
    db = SessionLocal()
    try:
        q = (request.args.get("search") or "").strip().lower()
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 25))
        qry = db.query(Organization)

        if q:
            like = f"%{q}%"
            qry = qry.filter(
                sa_func.lower(Organization.company_name).like(like) |
                sa_func.lower(Organization.vat_number).like(like) |
                sa_func.lower(Organization.invoice_email).like(like)
            )

        total = qry.count()
        rows = (qry.order_by(Organization.company_name.asc())
                    .offset((page-1)*page_size)
                    .limit(page_size)
                    .all())

        items = []
        for o in rows:
            items.append({
                "id": o.id,
                "company_name": o.company_name,
                "vat_number": o.vat_number,
                "address": o.address,
                "postal_code": o.postal_code,
                "country_code": o.country_code,
                "invoice_email": o.invoice_email,
                "payment_terms_days": o.payment_terms_days,
                "currency": o.currency,
            })
        return jsonify({"items": items, "total": total})
    finally:
        db.close()


@app.put("/admin/organizations/<int:org_id>")
@require_auth("superadmin")
def admin_orgs_update(org_id: int):
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org_id).first()
        if not o:
            return jsonify({"error": "Not found"}), 404

        data = request.get_json(force=True) or {}

        # Tillåtna fält från UI
        allowed = {
            "company_name", "vat_number", "address",
            "postal_code", "country_code",
            "invoice_email", "payment_terms_days", "currency"
        }
        payload = {k: v for k, v in data.items() if k in allowed}

        # Validera country_code om satt
        if "country_code" in payload and not _country_ok(payload["country_code"]):
            return jsonify({"error": "Invalid country code"}), 400

        # Validera payment_terms_days om satt
        if "payment_terms_days" in payload:
            try:
                v = int(payload["payment_terms_days"])
                if v < 0 or v > 120:
                    return jsonify({"error": "payment_terms_days out of range"}), 400
                payload["payment_terms_days"] = v
            except Exception:
                return jsonify({"error": "payment_terms_days must be integer"}), 400

        # Normalisera + validera VAT om användaren uppdaterar det
        if "vat_number" in payload:
            raw_vat = (payload.get("vat_number") or "").strip()
            cc_hint = (payload.get("country_code") or o.country_code or "").strip().upper() or None

            parsed = parse_vat_and_cc(raw_vat, cc_hint)
            cc, nat = parsed[0], parsed[1]
            err = parsed[2] if len(parsed) > 2 else None
            if err:
                return jsonify({"error": "Invalid VAT format", "detail": err}), 400

            ok, vmsg = vies_check(cc, nat)
            if not ok:
                return jsonify({"error": vmsg or "Invalid VAT"}), 400

            nv = f"{cc}{nat}"
            exists = (db.query(Organization)
                        .filter(Organization.vat_number == nv, Organization.id != o.id)
                        .first())
            if exists:
                return jsonify({"error": "VAT already used by another organization"}), 409

            payload["vat_number"] = nv  # spara normaliserad (CC + national)

        # Sätt alla fält på modellen
        for k, v in payload.items():
            setattr(o, k, v)

        db.commit()
        return jsonify({
            "id": o.id,
            "company_name": o.company_name,
            "vat_number": o.vat_number,
            "address": o.address,
            "postal_code": o.postal_code,
            "country_code": o.country_code,
            "invoice_email": o.invoice_email,
            "payment_terms_days": o.payment_terms_days,
            "currency": o.currency,
        })
    except BadRequest as e:
        db.rollback()
        return jsonify({"error": "Invalid JSON", "detail": str(e)}), 400
    except Exception as e:
        db.rollback()
        app.logger.exception("PUT /admin/organizations failed")
        return jsonify({"error": "Server error", "detail": str(e)}), 500
    finally:
        db.close()



# ========= Admin: organizations (create + delete) =========


@app.post("/admin/organizations")
@require_auth("superadmin")
def admin_orgs_create():
    db = SessionLocal()
    try:
        d = request.get_json(force=True) or {}

        # Kravfält
        required = ["company_name", "vat_number", "invoice_email"]
        miss = [k for k in required if not d.get(k)]
        if miss:
            return jsonify({"error": "Missing required fields", "fields": miss}), 400

        # Country code (valfritt, men validera om satt)
        cc_country = (d.get("country_code") or "").strip().upper() or None
        if cc_country and not re.fullmatch(r"[A-Z]{2}", cc_country):
            return jsonify({"error": "Invalid country code", "field": "country_code"}), 400

        # VAT: normalisera + VIES
        raw_vat = (d.get("vat_number") or "").strip()
        parsed = parse_vat_and_cc(raw_vat, cc_country)
        cc, nat = parsed[0], parsed[1]
        err = parsed[2] if len(parsed) > 2 else None
        if err:
            return jsonify({"error": err, "field": "vat_number"}), 400

        ok, vmsg = vies_check(cc, nat)
        if not ok:
            return jsonify({"error": vmsg or "Invalid VAT", "field": "vat_number"}), 400

        nv = f"{cc}{nat}"  # kanoniskt lagringsformat
        # Unik VAT
        if db.query(Organization).filter(Organization.vat_number == nv).first():
            return jsonify({"error": "Organization with this VAT already exists"}), 409

        # Payment terms
        try:
            payment_terms_days = int(d.get("payment_terms_days") or 10)
            if not (0 <= payment_terms_days <= 120):
                return jsonify({"error": "payment_terms_days out of range"}), 400
        except Exception:
            return jsonify({"error": "payment_terms_days must be integer"}), 400

        org = Organization(
            vat_number=nv,
            company_name=d["company_name"],
            address=d.get("address"),
            invoice_email=d["invoice_email"],
            payment_terms_days=payment_terms_days,
            currency=(d.get("currency") or "EUR").upper(),
            postal_code=(d.get("postal_code") or None),
            country_code=cc_country,
        )
        db.add(org); db.flush()

        # Valfritt: skapa admin-user direkt
        if d.get("admin"):
            adm = d["admin"] or {}
            if not all(adm.get(k) for k in ("name", "email", "password")):
                return jsonify({"error": "admin needs name/email/password"}), 400
            if db.query(User).filter(User.email == adm["email"]).first():
                return jsonify({"error": "Admin email already in use"}), 409
            user = User(
                org_id=org.id,
                name=adm["name"],
                email=adm["email"],
                password_hash=generate_password_hash(adm["password"]),
                role="admin",
            )
            db.add(user)

        db.commit()
        return jsonify({
            "id": org.id,
            "company_name": org.company_name,
            "vat_number": org.vat_number,
            "address": org.address,
            "postal_code": org.postal_code,
            "country_code": org.country_code,
            "invoice_email": org.invoice_email,
            "payment_terms_days": org.payment_terms_days,
            "currency": org.currency,
        }), 201
    except BadRequest as e:
        db.rollback()
        return jsonify({"error": "Invalid JSON", "detail": str(e)}), 400
    except Exception:
        db.rollback(); app.logger.exception("POST /admin/organizations failed")
        return jsonify({"error": "Server error"}), 500
    finally:
        db.close()


@app.delete("/admin/organizations/<int:org_id>")
@require_auth("superadmin")
def admin_orgs_delete(org_id: int):
    """
    Säkert delete: blockera om det finns users/bookings.
    Vill du tvinga, skicka ?force=1 (då raderas users och deras addresses,
    MEN bookings blockeras om schema kräver org_id – i så fall returnerar vi 409).
    """
    force = request.args.get("force") in ("1", "true", "yes")
    db = SessionLocal()
    try:
        o = db.query(Organization).filter(Organization.id == org_id).first()
        if not o: return jsonify({"error":"Not found"}), 404

        users_count = db.query(User).filter(User.org_id == org_id).count()
        bookings_count = db.query(Booking).filter(Booking.org_id == org_id).count()

        if bookings_count > 0:
            # Vi blockerar delete så att historiken inte förloras / FK inte bryts
            return jsonify({"error":"Organization has bookings; cannot delete", 
                            "bookings": bookings_count}), 409

        if users_count > 0 and not force:
            return jsonify({"error":"Organization has users; use force=1 to remove users too",
                            "users": users_count}), 409

        if force and users_count > 0:
            # Ta bort addresses som skapats av orgens users
            user_ids = [u.id for u in db.query(User.id).filter(User.org_id == org_id)]
            if user_ids:
                db.query(Address).filter(Address.user_id.in_(user_ids)).delete(synchronize_session=False)
                db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)

        db.delete(o)
        db.commit()
        return jsonify({"ok": True})
    except Exception:
        db.rollback(); app.logger.exception("DELETE /admin/organizations failed")
        return jsonify({"error":"Server error"}), 500
    finally:
        db.close()

# ========= Admin: users (create + delete) =========

@app.post("/admin/users")
@require_auth("superadmin")
def admin_users_create():
    db = SessionLocal()
    try:
        d = request.get_json(force=True) or {}
        required = ["org_id", "name", "email", "password"]
        miss = [k for k in required if not d.get(k)]
        if miss:
            return jsonify({"error":"Missing fields","fields":miss}), 400

        if db.query(User).filter(User.email == d["email"]).first():
            return jsonify({"error":"Email already exists"}), 409

        org = db.query(Organization).filter(Organization.id == int(d["org_id"])).first()
        if not org:
            return jsonify({"error":"Organization not found"}), 404

        role = d.get("role") or "user"
        if role not in ("user","admin","superadmin"):
            return jsonify({"error":"Invalid role"}), 400

        u = User(
            org_id = org.id,
            name   = d["name"],
            email  = d["email"],
            password_hash = generate_password_hash(d["password"]),
            role   = role,
            is_blocked = bool(d.get("is_blocked", False)),
        )
        db.add(u); db.commit()
        return jsonify({
            "id": u.id, "name": u.name, "email": u.email, "role": u.role,
            "organization_id": u.org_id, "is_blocked": u.is_blocked
        }), 201
    except BadRequest as e:
        db.rollback()
        return jsonify({"error":"Invalid JSON","detail":str(e)}), 400
    except Exception:
        db.rollback(); app.logger.exception("POST /admin/users failed")
        return jsonify({"error":"Server error"}), 500
    finally:
        db.close()


@app.delete("/admin/users/<int:user_id>")
@require_auth("superadmin")
def admin_users_delete(user_id: int):
    db = SessionLocal()
    try:
        me_id = request.user.get("user_id")
        if user_id == me_id:
            return jsonify({"error":"Cannot delete yourself"}), 400

        u = db.query(User).filter(User.id == user_id).first()
        if not u: return jsonify({"error":"Not found"}), 404
        if u.role == "superadmin":
            return jsonify({"error":"Cannot delete a superadmin"}), 403

        # Nolla FK i bookings (om kolumnen tillåter NULL – annars byt till reasignering)
        db.query(Booking).filter(Booking.user_id == user_id).update({Booking.user_id: None})
        # Ta bort addresses skapade av användaren
        db.query(Address).filter(Address.user_id == user_id).delete(synchronize_session=False)
        # Ta bort användaren
        db.delete(u)
        db.commit()
        return jsonify({"ok": True})
    except Exception:
        db.rollback(); app.logger.exception("DELETE /admin/users failed")
        return jsonify({"error":"Server error"}), 500
    finally:
        db.close()





@app.route("/register-organization", methods=["POST"])
def register_organization():
    db = SessionLocal()
    try:
        try:
            data = request.get_json(force=True)
        except BadRequest as e:
            app.logger.exception("JSON parse failed in /register-organization")
            return jsonify({"error": "Invalid JSON", "detail": str(e)}), 400

        required = ["vat_number", "company_name", "address", "invoice_email",
                    "name", "email", "password"]
        missing = [k for k in required if not data.get(k)]
        if missing:
            return jsonify({"error": "Missing required fields", "fields": missing}), 400

        # Frivilliga fält (validerade om satta)
        postal_code = (data.get("postal_code") or "").strip()
        country_code = (data.get("country_code") or "").strip().upper() or None
        if country_code and country_code not in ALLOWED_CC:
            return jsonify({"error": "Invalid country code", "field": "country_code"}), 400

        # VAT: normalisera, VIES-validera
        raw_vat = (data.get("vat_number") or "").strip()
        parsed = parse_vat_and_cc(raw_vat, country_code)
        cc, nat = parsed[0], parsed[1]
        err = parsed[2] if len(parsed) > 2 else None
        if err:
            return jsonify({"error": "Invalid VAT format", "detail": err, "field": "vat_number"}), 400

        valid, vmsg = vies_check(cc, nat)
        if not valid:
            return jsonify({"error": vmsg or "Invalid VAT", "field": "vat_number"}), 400

        nv = f"{cc}{nat}"

        # 1) Finns org redan på denna VAT?
        org = db.query(Organization).filter(Organization.vat_number == nv).first()
        if org:
            admin = (db.query(User)
                       .filter(User.org_id == org.id, User.role.in_(["admin", "superadmin"]))
                       .order_by(User.id.asc())
                       .first())
            return jsonify({
                "error": "Organization already exists",
                "admin": {"name": admin.name, "email": admin.email} if admin else None
            }), 409

        # 2) Finns e-post redan?
        if db.query(User).filter(User.email == data["email"]).first():
            return jsonify({"error": "Email already in use", "field": "email"}), 409

        # 3) Skapa org + admin
        org = Organization(
            vat_number=nv,
            company_name=data["company_name"],
            address=data["address"],
            invoice_email=data["invoice_email"],
            payment_terms_days=10,           # låst default
            currency="EUR",                  # låst default
            postal_code=postal_code or None,
            country_code=country_code,
        )
        db.add(org); db.flush()

        user = User(
            org_id=org.id,
            name=data["name"],
            email=data["email"],
            password_hash=generate_password_hash(data["password"]),
            role="admin",
        )
        db.add(user); db.commit()

        return jsonify({"message": "Organization and admin created", "org_id": org.id}), 201

    except Exception:
        db.rollback(); app.logger.exception("register-organization failed")
        return jsonify({"error": "Server error"}), 500
    finally:
        db.close()




# =========================================================
# DB setup
# =========================================================
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set in environment variables")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
try:
    Base.metadata.create_all(bind=engine)
except Exception as e:
    app.logger.exception("DB init failed: %s", e)

# =========================================================
# Config (Pricing) – seed + helpers
# =========================================================
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
def _compute_allowed_cc() -> set[str]:
    """
    Samlar alla landskoder som finns i available_zones
    över samtliga modes i den aktiva konfigurationen.
    """
    cfg = get_active_config(use="published") or {}
    acc: set[str] = set()
    for mode in cfg.values():
        az = (mode or {}).get("available_zones") or {}
        acc |= set(az.keys())
    return acc

# Bygg ALLOWED_CC vid uppstart så register_organization kan validera country_code
ALLOWED_CC = _compute_allowed_cc()
app.logger.info("ALLOWED_CC initialiserat: %s", sorted(ALLOWED_CC))

# =========================================================
# Validation of config
# =========================================================
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

# =========================================================
# Pricing calculation
# =========================================================
def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

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
        "asap_pickup": b.asap_pickup,
        "requested_pickup_date": b.requested_pickup_date.isoformat() if b.requested_pickup_date else None,
        "asap_delivery": b.asap_delivery,
        "requested_delivery_date": b.requested_delivery_date.isoformat() if b.requested_delivery_date else None,
        "loading_requested_date": b.loading_requested_date.isoformat() if b.loading_requested_date else None,
        "loading_requested_time": _fmt_time(b.loading_requested_time),
        "loading_planned_date": b.loading_planned_date.isoformat() if b.loading_planned_date else None,
        "loading_planned_time": _fmt_time(b.loading_planned_time),
        "loading_actual_date": b.loading_actual_date.isoformat() if b.loading_actual_date else None,
        "loading_actual_time": _fmt_time(b.loading_actual_time),
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
        "organization": org_to_public(org),
        "booked_by": user_to_public(user),
    }



# =========================================================
# Protected endpoints
# =========================================================
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

# ---------- Auth / Me (superadmin-kompatibel) ----------
@app.get("/auth/me")
@require_auth()
def auth_me():
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id == request.user["user_id"]).first()
        if not u:
            return jsonify({"error": "Not found"}), 404
        return jsonify({
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,                # "superadmin" | "admin" | "user"
            "organization_id": u.org_id,   # matcha frontend-fältet
        })
    finally:
        db.close()


# ---------- Organizations (superadmin only) ----------
@app.get("/organizations")
@require_auth("superadmin")
def organizations_list():
    db = SessionLocal()
    try:
        orgs = db.query(Organization).order_by(Organization.company_name.asc()).all()
        return jsonify([{"id": o.id, "name": o.company_name} for o in orgs])
    finally:
        db.close()


# ---------- Users list (superadmin only) ----------
@app.get("/admin/users")
@require_auth("superadmin")
def users_list():
    db = SessionLocal()
    try:
        q = (request.args.get("search") or "").strip().lower()
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 25))

        base = db.query(User, Organization.company_name.label("organization_name")) \
                 .join(Organization, Organization.id == User.org_id, isouter=True)

        if q:
            like = f"%{q}%"
            base = base.filter(or_(
                sa_func.lower(User.email).like(like),
                sa_func.lower(User.name).like(like),
                sa_func.lower(Organization.company_name).like(like)
            ))

        total = base.count()
        rows = (base
                .order_by(User.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all())

        items = []
        for u, org_name in rows:
            items.append({
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "organization_id": u.org_id,
                "organization_name": org_name,
                "is_blocked": getattr(u, "is_blocked", False),
                "created_at": getattr(u, "created_at", None).isoformat() if getattr(u, "created_at", None) else None,
            })
        return jsonify({"items": items, "total": total})
    finally:
        db.close()


# ---------- Update user (superadmin only) ----------
@app.put("/admin/users/<int:user_id>")
@require_auth("superadmin")
def users_update(user_id):
    db = SessionLocal()
    try:
        me = db.query(User).get(request.user["user_id"])
        u  = db.query(User).get(user_id)
        if not u:
            return jsonify({"error": "Not found"}), 404

        # Guardrails
        if u.role == "superadmin" and u.id != me.id:
            return jsonify({"error": "Cannot edit another superadmin"}), 403

        data = (request.get_json() or {})
        allowed = {"email", "name", "role", "organization_id", "is_blocked"}
        data = {k: v for k, v in data.items() if k in allowed}

        # egen modell heter org_id (inte organization_id)
        if "organization_id" in data:
            u.org_id = data.pop("organization_id")

        # block / roll
        if "is_blocked" in data:
            setattr(u, "is_blocked", bool(data.pop("is_blocked")))
        if "role" in data:
            new_role = data.pop("role")
            if u.id == me.id and new_role != "superadmin":
                return jsonify({"error": "Cannot demote yourself"}), 400
            u.role = new_role

        # övrigt
        for k, v in data.items():
            setattr(u, k, v)

        db.commit()

        org = db.query(Organization).get(u.org_id) if u.org_id else None
        return jsonify({
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,
            "organization_id": u.org_id,
            "organization_name": org.company_name if org else None,
            "is_blocked": getattr(u, "is_blocked", False),
        })
    except Exception as e:
        db.rollback()
        app.logger.exception("users_update failed")
        return jsonify({"error": "Server error", "detail": str(e)}), 500
    finally:
        db.close()


# ---------- Send password reset (superadmin only) ----------
def issue_password_reset_token(user: User) -> str:
    # enkel JWT som gäller 60 min
    return jwt.encode(
        {
            "sub": "pwd_reset",
            "user_id": user.id,
            "exp": datetime.utcnow() + timedelta(minutes=60),
        },
        SECRET_KEY,
        algorithm="HS256",
    )

def send_reset_email(to_email: str, token: str):
    # Använd din befintliga send_email()
    reset_url = f"https://easyfreightbooking.com/reset?token={token}"
    body = f"""Hello,

A password reset was requested for your account.
If you initiated this, click the link below to reset your password:

{reset_url}

If you didn't request this, you can ignore this email.
"""
    send_email(to=to_email, subject="Reset your Easy Freight Booking password", body=body, attachments=[])


@app.post("/admin/users/<int:user_id>/send-reset")
@require_auth("superadmin")
def send_reset(user_id):
    db = SessionLocal()
    try:
        u = db.query(User).get(user_id)
        if not u:
            return jsonify({"error": "Not found"}), 404
        token = issue_password_reset_token(u)
        send_reset_email(u.email, token)
        return jsonify({"ok": True})
    finally:
        db.close()


@app.route("/invite-user", methods=["POST"])
@require_auth()
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
        else:
            rows = q.filter(Booking.org_id == request.user["org_id"]).all()

        org_ids = {b.org_id for b in rows if b.org_id}
        user_ids = {b.user_id for b in rows if b.user_id}
        orgs  = {o.id: o for o in db.query(Organization).filter(Organization.id.in_(org_ids)).all()} if org_ids else {}
        users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

        return jsonify([booking_to_dict(b, orgs.get(b.org_id), users.get(b.user_id)) for b in rows])
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

        org = db.query(Organization).get(b.org_id) if b.org_id else None
        user = db.query(User).get(b.user_id) if b.user_id else None
        return jsonify(booking_to_dict(b, org, user))
    finally:
        db.close()


# =========================================================
# Calculate
# =========================================================
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
    # Zoner
    if not (is_zone_allowed(pickup_country, pickup_postal, mode_config["available_zones"]) and
            is_zone_allowed(delivery_country, delivery_postal, mode_config["available_zones"])):
        return {"available": False, "status": "Not available for this request"}

    # Viktgränser
    min_allowed = mode_config.get("min_allowed_weight_kg", 0)
    max_allowed = mode_config.get("max_allowed_weight_kg", 999999)
    if weight < min_allowed or weight > max_allowed:
        return {"available": False, "status": "Weight not allowed", "error": f"Allowed weight range: {min_allowed}–{max_allowed} kg"}

    # Avstånd (aldrig 0 → undvik log(0) senare)
    distance_km = max(1, int(round(haversine(pickup_coord, delivery_coord) * 1.2)))

    balance_key = f"{pickup_country}-{delivery_country}"
    balance_factor = float(mode_config.get("balance_factors", {}).get(balance_key, 1.0) or 1.0)
    km_price = float(mode_config.get("km_price_eur", 0) or 0)
    ftl_price = max(1, int(round(distance_km * km_price * balance_factor)))

    # Kurvparametrar
    try:
        p1  = float(mode_config["p1"]);   price_p1 = float(mode_config["price_p1"])
        p2  = float(mode_config["p2"]);   p2k = float(mode_config["p2k"]);  p2m = float(mode_config["p2m"])
        p3  = float(mode_config["p3"]);   p3k = float(mode_config["p3k"]);  p3m = float(mode_config["p3m"])
        bp  = float(mode_config["default_breakpoint"])
        maxw = float(mode_config["max_weight_kg"])
    except Exception:
        return {"available": False, "status": "Bad pricing config (missing numbers)"}

    # Monotonicitet + positive krav
    if not (0 < p1 < p2 < p3 < bp <= maxw):
        return {"available": False, "status": "Bad pricing config (need 0<p1<p2<p3<breakpoint≤max_weight)"}
    if price_p1 <= 0 or km_price <= 0:
        return {"available": False, "status": "Bad pricing config (non-positive price)"}

    # y-värden måste vara > 0
    y1 = price_p1 / p1
    y2 = (p2k * ftl_price + p2m) / p2
    y3 = (p3k * ftl_price + p3m) / p3
    y4 = ftl_price / bp

    EPS = 1e-9
    if min(y1, y2, y3, y4) <= 0:
        return {"available": False, "status": "Bad pricing config (y <= 0 leads to log-domain error)"}

    # Exponenter (skydd mot log-domain/0-division)
    try:
        n1 = (log(y2) - log(y1)) / (log(p2) - log(p1)); a1 = y1 / (p1 ** n1)
        n2 = (log(y3) - log(y2)) / (log(p3) - log(p2)); a2 = y2 / (p2 ** n2)
        n3 = (log(y4) - log(y3)) / (log(bp) - log(p3)); a3 = y3 / (p3 ** n3)
    except Exception:
        return {"available": False, "status": "Bad pricing config (log/ratio failure)"}

    # Prissättning
    if weight < p1:
        total_price = round(ftl_price * weight / maxw)
    elif p1 <= weight < p2:
        total_price = round(min(a1 * (weight ** n1) * weight, ftl_price))
    elif p2 <= weight < p3:
        total_price = round(min(a2 * (weight ** n2) * weight, ftl_price))
    elif p3 <= weight <= bp:
        total_price = round(min(a3 * (weight ** n3) * weight, ftl_price))
    elif bp < weight <= maxw:
        total_price = int(ftl_price)
    else:
        return {"available": False, "status": "Weight exceeds max weight"}

    # Transit
    speed = float(mode_config.get("transit_speed_kmpd", 500) or 500)
    base_transit = max(1, int(round(distance_km / max(speed, 1))))
    transit_time_days = [base_transit, base_transit + 1]

    # Tidigaste hämtning
    try:
        now_utc = datetime.utcnow()
        tz_name = pytz.country_timezones[pickup_country.upper()][0]
        now_local = now_utc.replace(tzinfo=pytz.utc).astimezone(pytz.timezone(tz_name))
    except Exception:
        now_local = datetime.utcnow()

    cutoff_hour = int(mode_config.get("cutoff_hour", 10) or 10)
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

    pickup_date += timedelta(days=int(mode_config.get("extra_pickup_days", 0) or 0))
    earliest_pickup_date = pickup_date.isoformat()

    co2_grams = max(0, int(round((distance_km * weight / 1000.0) * float(mode_config.get("co2_per_ton_km", 0) or 0) * 1000)))

    return {
        "available": True, "status": "success",
        "total_price_eur": int(total_price), "ftl_price_eur": int(ftl_price),
        "distance_km": distance_km, "transit_time_days": transit_time_days,
        "earliest_pickup_date": earliest_pickup_date, "currency": "EUR",
        "co2_emissions_grams": co2_grams, "description": mode_config.get("description", "")
    }


# Koppla Flask-loggningen till Gunicorns logger (så allt syns i Render)
gunicorn_logger = logging.getLogger("gunicorn.error")
if gunicorn_logger.handlers:
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

@app.route("/calculate", methods=["POST"])
def calculate():
    debug_id = uuid.uuid4().hex[:8]  # kort korrelations-ID
    data = request.json or {}
    try:
        pickup_coord = data["pickup_coordinate"]
        pickup_country = data["pickup_country"]
        pickup_postal = data["pickup_postal_prefix"]
        delivery_coord = data["delivery_coordinate"]
        delivery_country = data["delivery_country"]
        delivery_postal = data["delivery_postal_prefix"]
        weight = float(data["chargeable_weight"])
    except (KeyError, ValueError) as e:
        app.logger.warning("CALC %s bad input: %s | payload=%s", debug_id, e, data)
        return jsonify({"error": "Missing or invalid input", "debug_id": debug_id}), 400

    active_cfg = get_active_config(use="published") or {}
    app.logger.info(
        "CALC %s start %s-%s %s -> %s-%s %s kg",
        debug_id, pickup_country, pickup_postal, pickup_coord, delivery_country, delivery_postal, weight
    )

    results = {}
    for mode, cfg in active_cfg.items():
        try:
            r = calculate_for_mode(
                cfg, pickup_coord, delivery_coord,
                pickup_country, pickup_postal, delivery_country, delivery_postal,
                weight, mode_name=mode
            )
            results[mode] = r
            if r.get("available"):
                app.logger.info("CALC %s %s ok price=%s dist=%s", debug_id, mode, r.get("total_price_eur"), r.get("distance_km"))
            else:
                app.logger.info("CALC %s %s not-available: %s", debug_id, mode, r.get("status"))
        except Exception:
            app.logger.exception("CALC %s %s crashed in calculate_for_mode", debug_id, mode)
            results[mode] = {"available": False, "status": "error", "error": "internal", "mode": mode}

    app.logger.info("CALC %s done", debug_id)
    return jsonify({"debug_id": debug_id, **results})



# =========================================================
# Booking number generator + /book
# =========================================================
LETTERS = "ABCDEFGHJKMNPQRSTVWXYZ"
DIGITS = "0123456789"

def generate_booking_number() -> str:
    import secrets
    p1 = "".join(secrets.choice(LETTERS) for _ in range(2))
    p2 = "".join(secrets.choice(LETTERS) for _ in range(3))
    p3 = "".join(secrets.choice(DIGITS)  for _ in range(5))
    return f"{p1}-{p2}-{p3}"

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
        data = request.get_json(force=True) or {}
        app.logger.info("BOOK payload received")

        # 1) Resolve user + org (ignorera client-sent user_id om inte superadmin uttryckligen sätter den)
        org_id = request.user["org_id"]
        user_id = request.user["user_id"]

        if request.user.get("role") == "superadmin":
            override_uid = (data.get("booker") or {}).get("user_id") or data.get("user_id")
            if override_uid is not None:
                try:
                    override_uid = int(override_uid)
                    u_row = db.query(User).get(override_uid)
                    if not u_row:
                        return jsonify({"ok": False, "error": "Override user_id not found"}), 400
                    if data.get("organization_id") and int(data["organization_id"]) != u_row.org_id:
                        return jsonify({"ok": False, "error": "Override user_id does not belong to organization_id"}), 400
                    user_id = u_row.id
                    org_id  = u_row.org_id
                except Exception:
                    return jsonify({"ok": False, "error": "Invalid override user_id"}), 400

        # dubbelkolla att användaren finns (skydd mot föråldrad JWT)
        if not db.query(User.id).filter(User.id == user_id).first():
            return jsonify({"ok": False, "error": "Authenticated user not found"}), 401

        # 2) Fältalias: acceptera sender/receiver OCH pickup/delivery (+ postal_code/country_code)
        def pick_addr(src: dict | None) -> dict:
            src = src or {}
            return {
                "business_name": src.get("business_name"),
                "address":       src.get("address"),
                "postal":        src.get("postal") or src.get("postal_code"),
                "city":          src.get("city"),
                "country":       src.get("country") or src.get("country_code"),
                "contact_name":  src.get("contact_name"),
                "phone":         src.get("phone"),
                "email":         src.get("email"),
                "opening_hours": src.get("opening_hours"),
                "instructions":  src.get("instructions"),
            }

        body_sender   = pick_addr(data.get("sender")   or data.get("pickup"))
        body_receiver = pick_addr(data.get("receiver") or data.get("delivery"))

        def mk_addr(src: dict, addr_type: str) -> Address:
            return Address(
                user_id=user_id,
                type=addr_type,
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

        # 3) Spara adresser (ingen commit än)
        sender   = mk_addr(body_sender or {}, "sender")
        receiver = mk_addr(body_receiver or {}, "receiver")
        db.add(sender); db.add(receiver)

        # in i orgens adressbok (idempotent)
        upsert_org_address(db, org_id, body_sender or {},   "sender")
        upsert_org_address(db, org_id, body_receiver or {}, "receiver")

        db.flush()  # => sender.id / receiver.id

        # 4) Datum/tider (acceptera alias)
        loading_req_date   = parse_yyyy_mm_dd(data.get("requested_pickup_date")   or data.get("loading_requested_date"))
        loading_req_time   = parse_hh_mm     (data.get("requested_pickup_time")   or data.get("loading_requested_time"))
        unloading_req_date = parse_yyyy_mm_dd(data.get("requested_delivery_date") or data.get("unloading_requested_date"))
        unloading_req_time = parse_hh_mm     (data.get("requested_delivery_time") or data.get("unloading_requested_time"))

        # 5) Skapa booking – försök med upp till 7 unika bokningsnummer
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
                co2_emissions=float(data.get("co2_emissions_grams") or 0.0) / 1000.0,
                sender_address_id=sender.id,
                receiver_address_id=receiver.id,
                goods=data.get("goods"),
                references=data.get("references"),
                addons=data.get("addons"),
                asap_pickup=bool(data.get("asap_pickup")) if data.get("asap_pickup") is not None else True,
                requested_pickup_date=loading_req_date,
                asap_delivery=bool(data.get("asap_delivery")) if data.get("asap_delivery") is not None else True,
                requested_delivery_date=unloading_req_date,
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
                # kollision på booking_number → börja om (addresses rullas också tillbaka)
                db.rollback()
                sender   = mk_addr(body_sender or {}, "sender")
                receiver = mk_addr(body_receiver or {}, "receiver")
                db.add(sender); db.add(receiver)
                upsert_org_address(db, org_id, body_sender or {},   "sender")
                upsert_org_address(db, org_id, body_receiver or {}, "receiver")
                db.flush()

        if not booking_obj:
            raise RuntimeError("Could not allocate a unique booking number after several attempts")

        booking_id = booking_obj.id
        booking_number = booking_obj.booking_number

        # 6) Bygg XML på en payload som säkert har pickup/delivery med 'postal'/'country'
        xml_payload = dict(data)  # shallow copy räcker (bara läsning i build_booking_xml)
        xml_payload["pickup"]   = body_sender
        xml_payload["delivery"] = body_receiver
        xml_bytes = build_booking_xml(xml_payload)
        app.logger.info("XML built, %d bytes", len(xml_bytes))

        # 7) E-post (confirmation + intern)
        to_confirm = set()
        if (data.get("booker") or {}).get("email"):
            to_confirm.add(data["booker"]["email"])
        uc_email = (data.get("update_contact") or {}).get("email")
        if uc_email and uc_email.lower() not in {e.lower() for e in to_confirm}:
            to_confirm.add(uc_email)

        subject_conf = f"EFB Booking confirmation – {booking_number}"
        body_conf = render_text_confirmation(xml_payload)  # använder samma aliaserade payload
        if EMAIL_ENABLED:
            for rcpt in to_confirm:
                try:
                    app.logger.info("Sending confirmation to %s", rcpt)
                    send_email(to=rcpt, subject=subject_conf, body=body_conf, attachments=[])
                except Exception as e:
                    app.logger.warning("Failed sending confirmation to %s: %s", rcpt, e)

        subject_internal = f"EFB NEW BOOKING – {booking_number}"
        body_internal = render_text_internal(xml_payload)
        if EMAIL_ENABLED:
            try:
                app.logger.info("Sending internal booking email to %s", INTERNAL_BOOKING_EMAIL)
                send_email(
                    to=INTERNAL_BOOKING_EMAIL,
                    subject=subject_internal,
                    body=body_internal,
                    attachments=[("booking.xml", "application/xml", xml_bytes)],
                )
            except Exception as e:
                app.logger.warning("Failed sending internal email: %s", e)

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


# =========================================================
# PATCH booking (plan/utfall/status)
# =========================================================
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
        return jsonify(booking_to_dict(b))
    except BadRequest as e:
        db.rollback()
        return jsonify({"error": "Invalid JSON", "detail": str(e)}), 400
    except Exception as e:
        db.rollback()
        app.logger.exception("PATCH /bookings failed")
        return jsonify({"error": "Server error", "detail": str(e)}), 500
    finally:
        db.close()

# =========================================================
# Admin: pricing config
# =========================================================
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
    effective_at = None

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

# =========================================================
# Email & XML helpers
# =========================================================
def send_email(to: str, subject: str, body: str, attachments: List[Tuple[str, str, bytes]]):
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

    locs = ET.SubElement(booking, "locations")
    for loc_type, src in [(1, d.get("pickup", {}) or {}), (2, d.get("delivery", {}) or {})]:
        loc = ET.SubElement(locs, "location")
        ET.SubElement(loc, "locationType").text = str(loc_type)
        ET.SubElement(loc, "locationName").text = src.get("business_name", "") or ""
        ET.SubElement(loc, "streetAddress").text = src.get("address", "") or ""
        ET.SubElement(loc, "city").text = src.get("city", "") or ""
        ET.SubElement(loc, "countryCode").text = src.get("country", "") or ""
        zipcode = src.get("postal", "") or ""
        ET.SubElement(loc, "zipcode").text = f"{src.get('country','')}-{zipcode}"
        if loc_type == 1:
            # Använd requested pickup om satt, annars earliest från offerten
            chosen = d.get("requested_pickup_date") or d.get("earliest_pickup")
            ET.SubElement(loc, "planningDateUTC").text = to_utc_iso(chosen)

    goods_specs = ET.SubElement(booking, "goodsSpecifications")
    for g in (d.get("goods") or []):
        row = ET.SubElement(goods_specs, "goodsSpecification")
        ET.SubElement(row, "goodsMarks").text = str(g.get("marks", "") or "")
        ET.SubElement(row, "goodsPhgType").text = str(g.get("type", "") or "")
        ET.SubElement(row, "goodsLength").text = str(g.get("length", "") or "")
        ET.SubElement(row, "goodsWidth").text = str(g.get("width", "") or "")
        ET.SubElement(row, "goodsHeight").text = str(g.get("height", "") or "")
        qty = int(float(g.get("quantity") or 1))
        ET.SubElement(row, "goodsQty").text = str(qty)
        cbm = cm_to_m(g.get("length", 0)) * cm_to_m(g.get("width", 0)) * cm_to_m(g.get("height", 0)) * qty
        ET.SubElement(row, "goodsCBM").text = f"{cbm:.3f}"
        ET.SubElement(row, "goodsLDM").text = f"{float(g.get('ldm', 0) or 0):.2f}"
        ET.SubElement(row, "goodsWeight").text = str(g.get("weight", "") or "")
        ET.SubElement(row, "goodsChgWeight").text = str(int(round(d.get("chargeable_weight", 0) or 0)))

    refs_node = ET.SubElement(booking, "references")
    refs = d.get("references") or {}
    ET.SubElement(refs_node, "loadingReference").text = refs.get("reference1", "") or ""
    ET.SubElement(refs_node, "unloadingReference").text = refs.get("reference2", "") or ""
    ET.SubElement(refs_node, "invoiceReference").text = d.get("invoice_reference", "") or ""

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

# =========================================================
# Teardown
# =========================================================
@app.teardown_appcontext
def remove_session(exception=None):
    SessionLocal.remove()

# =========================================================
# Main (dev only)
# =========================================================
if __name__ == "__main__":
    app.run(debug=True)
