from flask import Flask, request, jsonify
from math import radians, sin, cos, sqrt, atan2, log

app = Flask(__name__)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    pickup_coord = data.get("pickup_coordinate")  # [lat, lon]
    delivery_coord = data.get("delivery_coordinate")  # [lat, lon]
    weight = data.get("weight", 1000)  # kg
    breakpoint = data.get("breakpoint", 20000)  # Optional

    # === Kontrollera att koordinater finns
    if not (pickup_coord and delivery_coord):
        return jsonify({"error": "pickup_coordinate and delivery_coordinate are required"}), 400

    # === Storcirkel + 20 % tillägg
    def haversine_distance(coord1, coord2):
        R = 6371  # Jordens radie i km
        lat1, lon1 = radians(coord1[0]), radians(coord1[1])
        lat2, lon2 = radians(coord2[0]), radians(coord2[1])
        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    base_distance_km = haversine_distance(pickup_coord, delivery_coord)
    distance_km = base_distance_km * 1.2  # 20 % påslag

    # === FTL-pris (0,95 €/km)
    ftl_price = round(distance_km * 0.95)

    # === Magisk formel-konstanter från bilden
    p1 = 30
    pp1 = 50
    p2 = 740
    p2k = 0.1
    p2m = 100
    p3 = 1850
    p3k = 0.12
    p3m = 120
    maxweight = 25160

    # === Punktvärden
    x1 = p1
    y1 = pp1 / p1
    x2 = p2
    y2 = (p2k * ftl_price + p2m) / p2
    x3 = p3
    y3 = (p3k * ftl_price + p3m) / p3
    x4 = breakpoint
    y4 = ftl_price / breakpoint

    # === Logaritmisk interpolation
    def get_constants(xa, ya, xb, yb):
        n = (log(yb) - log(ya)) / (log(xb) - log(xa))
        a = ya / (xa ** n)
        return a, n

    a1, n1 = get_constants(x1, y1, x2, y2)
    a2, n2 = get_constants(x2, y2, x3, y3)
    a3, n3 = get_constants(x3, y3, x4, y4)

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
        "distance_km": round(distance_km, 1),
        "weight_kg": weight,
        "ftl_price_eur": ftl_price,
        "total_price_eur": total_price,
        "source": source
    })

if __name__ == "__main__":
    app.run(debug=True)
