from flask import Flask, request, jsonify
from math import radians, sin, cos, sqrt, atan2, log

app = Flask(__name__)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    try:
        # Input
        lat1 = float(data["origin"]["lat"])
        lon1 = float(data["origin"]["lon"])
        lat2 = float(data["destination"]["lat"])
        lon2 = float(data["destination"]["lon"])
        weight = float(data["weight_kg"])
    except (KeyError, ValueError):
        return jsonify({"error": "Invalid input data"}), 400

    # Parametrar för storcirkel
    R = 6371.0  # Jordens radie i km

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance_km = round(R * c * 1.2)  # +20% för faktisk vägsträcka

    # Dynamiska faktorer
    km_price = 1.15  # €/km, exempel
    ftl_price = round(distance_km * km_price)

    # Magiska formelns parametrar
    p1, pp1 = 30, 50
    p2, p2k, p2m = 740, 0.1, 100
    p3, p3k, p3m = 1850, 0.12, 120
    breakpoint = 20000
    maxweight = 25160

    # Logaritmisk formel
    try:
        y1 = (pp1) / p1
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

    except Exception as e:
        return jsonify({"error": f"Calculation error: {str(e)}"}), 500

    return jsonify({
        "total_price_eur": total_price,
        "ftl_price_eur": ftl_price,
        "weight_kg": round(weight),
        "distance_km": distance_km
    })

if __name__ == "__main__":
    app.run(debug=True)
