from flask import Flask, request, jsonify
import math

app = Flask(__name__)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    chargeable_weight = float(data.get("chargeable_weight", 1000))
    ftl_price = 950.0  # EUR

    # Magiska parametrar (kan hämtas från databas eller config senare)
    p1 = 100
    price_p1 = 200
    p2 = 1000
    p2k = 0.6
    p2m = 80
    p3 = 3000
    p3k = 0.45
    p3m = 120
    breakpoint = 20000
    maxweight = 24000
    adjustment = 1.0  # 1 - rabatt (t.ex. 0.95 för 5 % rabatt)

    x1 = p1
    y1 = (price_p1 * adjustment) / p1
    x2 = p2
    y2 = ((p2k * ftl_price + p2m) * adjustment) / p2
    x3 = p3
    y3 = ((p3k * ftl_price + p3m) * adjustment) / p3
    x4 = breakpoint
    y4 = ftl_price / breakpoint

    n1 = (math.log(y2) - math.log(y1)) / (math.log(x2) - math.log(x1))
    a1 = y1 / (x1 ** n1)

    n2 = (math.log(y3) - math.log(y2)) / (math.log(x3) - math.log(x2))
    a2 = y2 / (x2 ** n2)

    n3 = (math.log(y4) - math.log(y3)) / (math.log(x4) - math.log(x3))
    a3 = y3 / (x3 ** n3)

    if chargeable_weight < p1:
        price = price_p1 * adjustment
    elif p1 <= chargeable_weight < p2:
        price = a1 * chargeable_weight ** n1 * chargeable_weight
    elif p2 <= chargeable_weight < p3:
        price = a2 * chargeable_weight ** n2 * chargeable_weight
    elif p3 <= chargeable_weight <= breakpoint:
        price = a3 * chargeable_weight ** n3 * chargeable_weight
    elif chargeable_weight <= maxweight:
        price = ftl_price
    else:
        return jsonify({
            "error": "Weight exceeds max allowed"
        }), 400

    price = round(price, 2)

    return jsonify({
        "total_price_eur": price,
        "ftl_price_eur": round(ftl_price, 2),
        "chargeable_weight": chargeable_weight,
        "currency": "EUR",
        "source": "magic-formula"
    })

if __name__ == "__main__":
    app.run(debug=True)
