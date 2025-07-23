from flask import Flask, request, jsonify
import math

app = Flask(__name__)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    # Input
    pickup_zip = data.get("pickup_zip")
    delivery_zip = data.get("delivery_zip")
    goods_type = data.get("goods_type", "Pallets")
    kg = float(data.get("chargeable_weight", 1000))
    breakpoint = float(data.get("breakpoint", 20000))
    maxweight = float(data.get("maxweight", 30000))

    # Simulerat FTL-pris
    ftl_price = 950.0

    # Tröskelvärden och parametrar
    p1 = 100
    pricep1 = 200

    p2 = 1000
    p2k = 0.7
    p2m = 50

    p3 = 3000
    p3k = 0.4
    p3m = 200

    adjustment = 1.0  # Reserv för framtida anpassningar

    # Beräkna punkter på kurvan
    x1 = p1
    y1 = (pricep1 * adjustment) / p1

    x2 = p2
    y2 = ((p2k * ftl_price + p2m) * adjustment) / p2

    x3 = p3
    y3 = ((p3k * ftl_price + p3m) * adjustment) / p3

    x4 = breakpoint
    y4 = ftl_price / breakpoint

    # Segmentkonstanter
    n1 = (math.log(y2) - math.log(y1)) / (math.log(x2) - math.log(x1))
    a1 = y1 / (x1 ** n1)

    n2 = (math.log(y3) - math.log(y2)) / (math.log(x3) - math.log(x2))
    a2 = y2 / (x2 ** n2)

    n3 = (math.log(y4) - math.log(y3)) / (math.log(x4) - math.log(x3))
    a3 = y3 / (x3 ** n3)

    # Räkna ut pris per kg
    if kg >= p1 and kg < p2:
        price_per_kg = a1 * (kg ** n1)
    elif kg >= p2 and kg < p3:
        price_per_kg = a2 * (kg ** n2)
    elif kg >= p3 and kg <= breakpoint:
        price_per_kg = a3 * (kg ** n3)
    elif kg > breakpoint and kg <= maxweight:
        price_per_kg = ftl_price / kg
    else:
        return jsonify({
            "error": f"Vikt utanför tillåtet intervall: 100 kg – {maxweight} kg"
        }), 400

    # Totalt pris, ej mer än FTL-priset
    calculated_price = min(kg * price_per_kg, ftl_price)

    return jsonify({
        "price_eur": round(calculated_price, 2),
        "ftl_price_eur": round(ftl_price, 2),
        "chargeable_weight_kg": kg,
        "currency": "EUR",
        "source": "magic-formula"
    })

if __name__ == "__main__":
    app.run(debug=True)
