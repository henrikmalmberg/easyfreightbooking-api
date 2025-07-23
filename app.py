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
        chargeable_weight = float(data["chargeable_weight"])
        pickup_country = data["pickup_country"]
        pickup_zip = str(data["pickup_zip"])
        delivery_country = data["delivery_country"]
        delivery_zip = str(data["delivery_zip"])
    except (KeyError, ValueError):
        return jsonify({"error": "Missing or invalid input"}), 400

    # Ladda parametrar
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

    # Kontrollera förbjudna zoner
    forbidden = config.get("forbidden_zones", {})
    if pickup_country in forbidden and pickup_zip[:2] in forbidden[pickup_country]:
        return jsonify({"error": f"Pickup location {pickup_country}-{pickup_zip} is restricted"}), 403
    if delivery_country in forbidden and delivery_zip[:2] in forbidden[delivery_country]:
        return jsonify({"error": f"Delivery location {delivery_country}-{delivery_zip} is restricted"}), 403

    # Storcirkelberäkning
    def haversine(coord1, coord2):
        R = 6371
        lat1, lon1 = map(radians, coord1)
        lat2, lon2 = map(radians, coord2)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    distance_km = round(haversine(pickup_coord, delivery_coord) * 1.2)

    # Beräkna FTL-pris
    base_ftl_price = distance_km * km_price

    # Balansfaktor
    relation = f"{pickup_country}-{delivery_country}"
    balance = config.get("balance_factors", {}).get(relation, 1.0)
    ftl_price = round(base_ftl_price * balance)

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

    if chargeable_weight < p1:
        total_price = round(ftl_price * chargeable_weight / maxweight)
    elif p1 <= chargeable_weight < p2:
        total_price = round(min(a1 * chargeable_weight ** n1 * chargeable_weight, ftl_price))
    elif p2 <= chargeable_weight < p3:
        total_price = round(min(a2 * chargeable_weight ** n2 * chargeable_weight, ftl_price))
    elif p3 <= chargeable_weight <= breakpoint:
        total_price = round(min(a3 * chargeable_weight ** n3 * chargeable_weight, ftl_price))
    elif breakpoint < chargeable_weight <= maxweight:
        total_price = ftl_price
    else:
        return jsonify({"error": "Chargeable weight exceeds max weight"}), 400

    return jsonify({
        "ftl_price_eur": ftl_price,
        "total_price_eur": total_price,
        "distance_km": distance_km,
        "currency": "EUR"
    })


if __name__ == "__main__":
    app.run(debug=True)
