from flask import Flask, request, jsonify
from math import radians, cos, sin, sqrt, atan2, log
import json

app = Flask(__name__)

# Läs in konfigurationsdata
with open("config.json", "r") as f:
    config = json.load(f)

TRANSPORT_MODES = [
    "road_freight",
    "intermodal_rail_freight",
    "conventional_rail_freight",
    "ocean_freight"
]

def haversine(coord1, coord2):
    R = 6371
    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json
    results = {}

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

    distance_km = round(haversine(pickup_coord, delivery_coord) * 1.2)

    for mode in TRANSPORT_MODES:
        mode_config = config.get(mode)

        if not mode_config:
            results[mode] = "Not available for this request"
            continue

        # Kontrollera förbjudna zoner
        forbidden_zones = mode_config.get("forbidden_zones", {})
        if pickup_country in forbidden_zones and pickup_postal in forbidden_zones[pickup_country]:
            results[mode] = "Not available for this request"
            continue
        if delivery_country in forbidden_zones and delivery_postal in forbidden_zones[delivery_country]:
            results[mode] = "Not available for this request"
            continue

        # Balansfaktor
        balance_key = f"{pickup_country}-{delivery_country}"
        balance_factor = mode_config.get("balance_factors", {}).get(balance_key, 1.0)

        # Läs in parametrar
        try:
            km_price = mode_config["km_price_eur"]
            maxweight = mode_config["max_weight_kg"]
            breakpoint = mode_config["default_breakpoint"]
            p1 = mode_config["p1"]
            price_p1 = mode_config["price_p1"]
            p2 = mode_config["p2"]
            p2k = mode_config["p2k"]
            p2m = mode_config["p2m"]
            p3 = mode_config["p3"]
            p3k = mode_config["p3k"]
            p3m = mode_config["p3m"]
        except KeyError:
            results[mode] = "Missing parameters in config"
            continue

        ftl_price = round(distance_km * km_price * balance_factor)

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
            results[mode] = "Weight exceeds limit"
            continue

        results[mode] = {
            "pricing_successful": True,
            "total_price_eur": total_price,
            "ftl_price_eur": ftl_price,
            "chargeable_weight_kg": weight,
            "distance_km": distance_km,
            "currency": "EUR"
        }

    return jsonify(results)

if __name__ == "__main__":
    app.run(debug=True)
