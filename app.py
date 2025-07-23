from flask import Flask, request, jsonify
import math

app = Flask(__name__)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    pickup_zip = data.get("pickup_zip")
    delivery_zip = data.get("delivery_zip")
    goods_type = data.get("goods_type", "LTL")
    chargeable_weight = data.get("chargeable_weight", 1000)
    ftl_price = data.get("ftl_price_eur", 950)  # fallback om inget skickas in

    # Parametrar till magisk formel
    p1 = 100
    pricep1 = 80
    p2 = 500
    p2k = 0.5
    p2m = 100
    p3 = 1500
    p3k = 0.35
    p3m = 200
    breakpoint = 2500
    maxweight = 26000
    adjustment = 1.0

    # Pris per kg för varje intervall
    x1, y1 = p1, (pricep1 * adjustment) / p1
    x2, y2 = p2, ((p2k * ftl_price + p2m) * adjustment) / p2
    x3, y3 = p3, ((p3k * ftl_price + p3m) * adjustment) / p3
    x4, y4 = breakpoint, ftl_price / breakpoint

    # Räkna ut konstanter för varje segment
    n1 = (math.log(y2) - math.log(y1)) / (math.log(x2) - math.log(x1))
    a1 = y1 / (x1 ** n1)

    n2 = (math.log(y3) - math.log(y2)) / (math.log(x3) - math.log(x2))
    a2 = y2 / (x2 ** n2)

    n3 = (math.log(y4) - math.log(y3)) / (math.log(x4) - math.log(x3))
    a3 = y3 / (x3 ** n3)

    # Räkna ut pris
    kg = chargeable_weight

    if kg >= p1 and kg < p2:
        price_per_kg = a1 * kg ** n1
    elif kg >= p2 and kg < p3:
        price_per_kg = a2 * kg ** n2
    elif kg >= p3 and kg <= breakpoint:
        price_per_kg = a3 * kg ** n3
    elif kg > breakpoint and kg <= maxweight:
        total_price = ftl_price
        price_per_kg = ftl_price / kg
    else:
        return jsonify({"error": "Vikt utanför tillåtna gränser"}), 400

    total_price = min(kg * price_per_kg, ftl_price)

    return jsonify({
        "price_eur": round(total_price, 2),
        "currency": "EUR",
        "source": "magic-formula"
    })

if __name__ == "__main__":
    app.run(debug=True)
