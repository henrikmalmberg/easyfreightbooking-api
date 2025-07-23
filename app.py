from flask import Flask, request, jsonify
from math import radians, sin, cos, sqrt, atan2, log

app = Flask(__name__)

# Haversine-formeln för att räkna avstånd i km
def calculate_distance_km(coord1, coord2):
    R = 6371.0  # Jordens radie i km
    lat1, lon1 = radians(coord1[0]), radians(coord1[1])
    lat2, lon2 = radians(coord2[0]), radians(coord2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

# Segmentformelns konstanter
def segment_constants(x1, y1, x2, y2):
    n = (log(y2) - log(y1)) / (log(x2) - log(x1))
    a = y1 / (x1 ** n)
    return a, n

@app.route("/calculate", methods=["POST"])
def calculate():
    data = request.json

    pickup_coord = data.get("pickup_coordinate")  # [lat, lon]
    delivery_coord = data.get("delivery_coordinate")  # [lat, lon]
    weight = data.get("weight")  # i kg
    breakpoint = data.get("breakpoint", 20000)
    maxweight = 25160

    if not pickup_coord or not delivery_coord or weight is None:
        return jsonify({"error": "Missing pickup, delivery or weight"}), 400

    # Avståndsberäkning
    base_distance_km = calculate_distance_km(pickup_coord, delivery_coord)
    distance_km = base_distance_km * 1.2  # 20% tillägg

    # Dummy-priser
    km_price = 1.0
    balance_factor = 1.0

    ftl_price = distance_km * km_price * balance_factor

    # Magiska trösklar
    p1 = 150
    price_p1 = 0.9 * ftl_price / p1

    p2 = 1000
    p2k = 0.6
    p2m = 100

    p3 = 5000
    p3k = 0.4
    p3m = 200

    y1 = price_p1
    y2 = (p2k * ftl_price + p2m) / p2
    y3 = (p3k * ftl_price + p3m) / p3
    y4 = ftl_price / breakpoint

    a1, n1 = segment_constants(p1, y1, p2, y2)
    a2, n2 = segment_constants(p2, y2, p3, y3)
    a3, n3 = segment_constants(p3, y3, breakpoint, y4)

    # Pris för vikt
    if weight < p2:
        total_price = min(weight * (a1 * weight ** n1), ftl_price)
    elif weight < p3:
        total_price = min(weight * (a2 * weight ** n2), ftl_price)
    elif weight <= breakpoint:
        total_price = min(weight * (a3 * weight ** n3), ftl_price)
    else:
        total_price = ftl_price

    return jsonify({
        "pickup_coordinate": pickup_coord,
        "delivery_coordinate": delivery_coord,
        "distance_km": round(distance_km, 2),
        "weight_kg": weight,
        "ftl_price_eur": round(ftl_price, 2),
        "total_price_eur": round(total_price, 2),
        "currency": "EUR",
        "source": "calculated"
    })

if __name__ == "__main__":
    app.run(debug=True)
