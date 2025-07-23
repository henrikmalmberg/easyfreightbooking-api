from flask import Flask, request, jsonify
import math

app = Flask(__name__)

def calculate_chargeable_weight(weight, ldm, cbm):
    return max(weight, ldm * 1850, cbm * 333)

def apply_magic_formula(price_ftl, chargeable_weight):
    # Tröskelvärden och parametrar
    p1 = 1000
    pricep1 = 400
    p2 = 3000
    p2k = 0.45
    p2m = 180
    p3 = 6000
    p3k = 0.35
    p3m = 300
    breakpoint = 10000
    maxweight = 24000
    adjustment = 1.0

    # Beräkna punkter
    x1, y1 = p1, (pricep1 * adjustment) / p1
    x2, y2 = p2, ((p2k * price_ftl + p2m) * adjustment) / p2
    x3, y3 = p3, ((p3k * price_ftl + p3m) * adjustment) / p3
    x4, y4 = breakpoint, price_ftl / breakpoint

    # Segment 1
    n1 = (math.log(y2) - math.log(y1)) / (math.log(x2) - math.log(x1))
    a1 = y1 / (x1 ** n1)

    # Segment 2
    n2 = (math.log(y3) - math.log(y2)) / (math.log(x3) - math.log(x2))
    a2 = y2 / (x2 ** n2)

    # Segment 3
    n3 = (math.log(y4) - math.log(y3)) / (math.log(x4) - math.log(x3))
    a3 = y3 / (x3 ** n3)

    # Prisberäkning
    if chargeable_weight >= p1 and chargeable_weight < p2:
        price_per_kg = a1 * chargeable_weight ** n1
    elif chargeable_weight >= p2 and chargeable_weight < p3:
        price_per_kg = a2 * chargeable_weight ** n2
    elif chargeable_weight >= p3 and chargeable_weight <= breakpoint:
        price_per_kg = a3 * chargeable_weight ** n3
    elif chargeable_weight > breakpoint and chargeable_weight <= maxweight:
        return price_ftl  # Flat rate

    return min(chargeable_weight * price_per_kg, price_ftl)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    # Inparametrar
    pickup_coordinate = data.get("pickup_coordinate")
    delivery_coordinate = data.get("delivery_coordinate")
    pickup_country = data.get("pickup_country")
    delivery_country = data.get("delivery_country")
    pickup_zip = data.get("pickup_zip")
    delivery_zip = data.get("delivery_zip")
    goods_type = data.get("goods_type", "Pallets")
    weight = float(data.get("weight", 1000))
    ldm = float(data.get("ldm", 0))
    cbm = float(data.get("cbm", 0))

    # 1. Beräkna chargeable weight
    chargeable_weight = calculate_chargeable_weight(weight, ldm, cbm)

    # 2. Dummy FTL-pris (kan bytas mot framtida distansbaserad beräkning)
    price_ftl = 950

    # 3. Räkna pris med magiska formeln
    price = apply_magic_formula(price_ftl, chargeable_weight)

    return jsonify({
        "price_eur": round(price, 2),
        "currency": "EUR",
        "source": "magic-formula",
        "chargeable_weight": round(chargeable_weight, 1),
        "goods_type": goods_type
    })

if __name__ == "__main__":
    app.run(debug=True)
