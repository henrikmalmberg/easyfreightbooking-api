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
        pickup_coordinate = data["pickup_coordinate"]
        pickup_country = data["pickup_country"]
        pickup_zip = str(data["pickup_zip"])

        delivery_coordinate = data["delivery_coordinate"]
        delivery_country = data["delivery_country"]
        delivery_zip = str(data["delivery_zip"])

        chargeable_weight = float(data["chargeable_weight"])
    except (KeyError, ValueError):
        return jsonify({"error": "Missing or invalid input"}), 400

    # Förbjudna länder eller postnummer
    forbidden = config.get("forbidden", {})
    if pickup_country in forbidden.get("countries", []) or delivery_country in forbidden.get("countries", []):
        return jsonify({"error": "One of the countries is not allowed"}), 400

    if pickup_zip[:2] in forbidden.get("zip_prefixes", []) or delivery_zip[:2] in forbidden.get("zip_prefixes", []):
        return jsonify({"error": "One of the zip code regions is not allowed"}), 400

    # Läs in övriga parametrar
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

    # Hämta balansfaktor
    balance = config.get("balance_factors", {}).get(pickup_country, {}).get(delivery_country, 1.0)

    # Storcirkelberäkning
    def haversine(coord1, coord2):
        R = 6371
        lat1, lon1 = map(radians, coord1)
        lat2, lon2 = map(radians, coord2)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    distance_km = round(haversine(pickup_coord, delivery_coord) * 1.2)

    # Beräkna FTL-pris
    ftl_price = round(distance_km * km_price * balance)

    # Magiska formeln
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

    # Prissättning enligt vikt
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
        return jsonify({"error": "Weight exceeds max weight for road transport"}), 400

    return jsonify({
        "ftl_price_eur": ftl_price,
        "total_price_eur": total_price,
        "distance_km": distance_km,
        "currency": "EUR"
    })

if __name__ == "__main__":
    app.run(debug=True)
