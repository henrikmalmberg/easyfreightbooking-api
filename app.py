from flask import Flask, request, jsonify
from math import log

app = Flask(__name__)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    try:
        # Input
        weight = float(data.get("weight_kg"))
        origin = data.get("origin")
        destination = data.get("destination")

        # Storcirkelberäkning
        lat1, lon1 = origin["lat"], origin["lon"]
        lat2, lon2 = destination["lat"], destination["lon"]
        distance_km = haversine(lat1, lon1, lat2, lon2) * 1.2

        # Prissättning
        km_rate = 1.1  # €/km
        ftl_price = round(distance_km * km_rate)
        maxweight = 25160
        conversion_factor = 1000  # referensvikt i VBA

        # Tröskelvärden
        p1 = 30
        pp1 = 50
        p2 = 740
        p2k = 0.1
        p2m = 100
        p3 = 1850
        p3k = 0.12
        p3m = 120
        breakpoint = 20000

        # Formelparametrar
        y1 = pp1 / p1
        y2 = (p2k * ftl_price + p2m) / p2
        y3 = (p3k * ftl_price + p3m) / p3
        y4 = ftl_price / breakpoint

        n1 = (log(y2) - log(y1)) / (log(p2) - log(p1))
        a1 = y1 / (p1 ** n1)

        n2 = (log(y3) - log(y2)) / (log(p3) - log(p2))
        a2 = y2 / (p2 ** n2)

        n3 = (log(y4) - log(y3)) / (log(breakpoint) - log(p3))
        a3 = y3 / (p3 ** n3)

        # Prisberäkning
        if weight < p1:
            total_price = round(ftl_price * weight / maxweight)
            source = "below-p1"
        elif p1 <= weight < p2:
            price_per_kg = a1 * weight ** n1
            total_price = round(min(weight * price_per_kg, ftl_price) / (weight / conversion_factor))
            source = "magic-formula"
        elif p2 <= weight < p3:
            price_per_kg = a2 * weight ** n2
            total_price = round(min(weight * price_per_kg, ftl_price) / (weight / conversion_factor))
            source = "magic-formula"
        elif p3 <= weight <= breakpoint:
            price_per_kg = a3 * weight ** n3
            total_price = round(min(weight * price_per_kg, ftl_price) / (weight / conversion_factor))
            source = "magic-formula"
        elif breakpoint < weight <= maxweight:
            total_price = ftl_price
            source = "capped-to-ftl"
        else:
            return jsonify({"error": "Weight exceeds max weight for road transport"}), 400

        return jsonify({
            "total_price_eur": total_price,
            "ftl_price_eur": ftl_price,
            "weight_kg": round(weight, 1),
            "source": source,
            "currency": "EUR"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def haversine(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, sqrt, atan2
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    lat1 = radians(lat1)
    lat2 = radians(lat2)
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))

if __name__ == "__main__":
    app.run(debug=True)
