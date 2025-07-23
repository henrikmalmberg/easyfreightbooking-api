from flask import Flask, request, jsonify
import math

app = Flask(__name__)

def calculate_partial_load_price(
    price_ftl: float,
    chargeable_weight: float,
    p1: float,
    pricep1: float,
    p2: float,
    p2k: float,
    p2m: float,
    p3: float,
    p3k: float,
    p3m: float,
    breakpoint: float,
    maxweight: float,
    adjustment: float
):
    x1, y1 = p1, (pricep1 * adjustment) / p1
    x2, y2 = p2, ((p2k * price_ftl + p2m) * adjustment) / p2
    x3, y3 = p3, ((p3k * price_ftl + p3m) * adjustment) / p3
    x4, y4 = breakpoint, price_ftl / breakpoint

    def calc_constants(xa, ya, xb, yb):
        n = (math.log(yb) - math.log(ya)) / (math.log(xb) - math.log(xa))
        a = ya / (xa ** n)
        return a, n

    a1, n1 = calc_constants(x1, y1, x2, y2)
    a2, n2 = calc_constants(x2, y2, x3, y3)
    a3, n3 = calc_constants(x3, y3, x4, y4)

    if chargeable_weight < p1:
        price_per_kg = a1 * chargeable_weight ** n1
    elif chargeable_weight < p2:
        price_per_kg = a1 * chargeable_weight ** n1
    elif chargeable_weight < p3:
        price_per_kg = a2 * chargeable_weight ** n2
    elif chargeable_weight <= breakpoint:
        price_per_kg = a3 * chargeable_weight ** n3
    elif chargeable_weight <= maxweight:
        price_per_kg = price_ftl / chargeable_weight
    else:
        raise ValueError("Chargeable weight exceeds maximum allowed weight")

    total_price = min(chargeable_weight * price_per_kg, price_ftl)
    return round(total_price, 2)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    # Inputdata
    pickup_zip = data.get("pickup_zip")
    delivery_zip = data.get("delivery_zip")
    goods_type = data.get("goods_type")
    chargeable_weight = data.get("chargeable_weight", 1000)

    # FTL-pris (kan senare räknas fram baserat på avstånd, zip m.m.)
    price_ftl = 1000

    # Parametrar med defaultvärden
    p1 = data.get("p1", 500)
    pricep1 = data.get("pricep1", 50)
    p2 = data.get("p2", 1000)
    p2k = data.get("p2k", 0.4)
    p2m = data.get("p2m", 100)
    p3 = data.get("p3", 1500)
    p3k = data.get("p3k", 0.6)
    p3m = data.get("p3m", 200)
    breakpoint = data.get("breakpoint", 3000)
    maxweight = data.get("maxweight", 4000)
    adjustment = data.get("adjustment", 0.9)

    try:
        price = calculate_partial_load_price(
            price_ftl,
            chargeable_weight,
            p1, pricep1,
            p2, p2k, p2m,
            p3, p3k, p3m,
            breakpoint,
            maxweight,
            adjustment
        )

        return jsonify({
            "price_eur": price,
            "currency": "EUR",
            "source": "magic-formula"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 400

if __name__ == "__main__":
    app.run(debug=True)


