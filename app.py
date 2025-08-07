from flask_cors import CORS
from flask import Flask, request, jsonify
from math import radians, cos, sin, sqrt, atan2, log
from datetime import datetime, timedelta
import pytz
import holidays
import json

app = Flask(__name__)
CORS(app, origins=[
    "https://easyfreightbooking.com",
    "https://easyfreightbooking-dashboard.onrender.com",
    "https://easyfreightbooking-dashboard.onrender.com/new-booking"
])

# Läs in konfigurationsdata
with open("config.json", "r") as f:
    config = json.load(f)

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
        return {"status": "Not available for this request"}
    
    min_allowed = mode_config.get("min_allowed_weight_kg", 0)
    max_allowed = mode_config.get("max_allowed_weight_kg", 999999)

    if weight < min_allowed or weight > max_allowed:
        return {
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
        return {"status": "Weight exceeds max weight"}

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
    co2_grams = round((distance_km * weight / 1000) * mode_config.get("co2_per_ton_km", 0)*1000)

    return {
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

if __name__ == "__main__":
    app.run(debug=True)
