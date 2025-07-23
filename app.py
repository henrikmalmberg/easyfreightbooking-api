from flask import Flask, request, jsonify
from math import radians, cos, sin, sqrt, atan2, log
import json

app = Flask(__name__)

# Läs in konfigurationsdata
with open("config.json", "r") as f:
    config = json.load(f)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    try:
        pickup_coord = data["pickup_coordinate"]
        delivery_coord = data["delivery_coordinate"]
        weight = float(data["weight"])
    except (KeyError, ValueError):
        return jsonify({"error": "Missing or invalid input"}), 400

    # Läs in parametrar från config
    km_price = config["km_price_eur"]
    maxweight = config["max_weight_kg"]
    breakpoint = config["default_breakpoint"]

    p1 = config["p1"]
    price_p1 = config["price_p1"]
    p2 = config["p2"]
    p2k = config["p2k"]
    p2m = config["p2m"]
    p3 = config["p3"]
    p3k = config["p3k"]
    p3m = config["p3m"]

    # Storcirkelberäkning
    def haversine(coord1, coord2):
        R = 6371  # km
        lat1, lon1 = map(radians, coord1)
        lat2, lon2 = map(radians, coord2)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    distance_km = round(haversine(pickup_coord, delivery_coord) * 1.2)

    # Beräkna FTL-pris
    ftl_price = round(distance_km * km_price)

    # Magisk formel
    y1 = (price_p1) / p1
    y2 = ((p2k * ftl_price + p2m)) / p2
    y3 = ((p3k * ftl_price + p3m)) / p3
    y4 = ftl_price / breakpoint

    n1 = (log(y2) - log(y1)) / (log(p2) - log(p1))
    a1 = y1 / (p1 ** n1)

    n2 = (log(y3) - log(y2)) / (log(p3) - log(p2))
    a2 = y2 / (p2 ** n2)

    n3 = (log(y4) - log(y3)) / (log(breakpoint) - log(p3))
    a3 = y3 / (p3 ** n3)

    if weight < p1:
        total_price = round(ftl_price * weight / maxweight)
        source = "below-p1"
    elif p1 <= weight < p2:
        total_price = round(min(a1 * weight ** n1 * weight, ftl_price))
        source = "magic-formula"
    elif p2 <= weight < p3:
        total_price = round(min(a2 * weight ** n2 * weight, ftl_price))
        source = "magic-formula"
    elif p3 <= weight <= breakpoint:
        total_price = round(min(a3 * weight ** n3 * weight, ftl_price))
        source = "magic-formula"
    elif breakpoint < weight <= maxweight:
        total_price = ftl_price
        source = "capped-to-ftl"
    else:
        return jsonify({"error": "Weight exceeds max weight for road transport"}), 400

    return jsonify({
        "ftl_price_eur": ftl_price,
        "total_price_eur": total_price,
        "distance_km": distance_km,
        "currency": "EUR",
        "pricing_method": source
    })

if __name__ == "__main__":
    app.run(debug=True)
