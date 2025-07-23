from flask import Flask, request, jsonify
import math

app = Flask(__name__)

MAX_WEIGHT_ROAD = 25160  # kg

def calculate_road_price(ftl_price, weight, breakpoint=20000):
    # Prispunkter (dessa kan göras dynamiska)
    p1, pricep1 = 500, 0.7 * ftl_price / 500
    p2, p2k, p2m = 2000, 0.4, 100
    p3, p3k, p3m = 8000, 0.25, 200

    adjustment = 1.0  # t.ex. rabatt kan appliceras här

    # Prispunktsvärden
    x1 = p1
    y1 = pricep1
    x2 = p2
    y2 = (p2k * ftl_price + p2m) * adjustment / p2
    x3 = p3
    y3 = (p3k * ftl_price + p3m) * adjustment / p3
    x4 = breakpoint
    y4 = ftl_price / breakpoint

    # Räknar ut konstanter
    n1 = (math.log(y2) - math.log(y1)) / (math.log(x2) - math.log(x1))
    a1 = y1 / (x1 ** n1)

    n2 = (math.log(y3) - math.log(y2)) / (math.log(x3) - math.log(x2))
    a2 = y2 / (x2 ** n2)

    n3 = (math.log(y4) - math.log(y3)) / (math.log(x4) - math.log(x3))
    a3 = y3 / (x3 ** n3)

    # Beräkna pris
    if weight < p2:
        price_per_kg = a1 * weight ** n1
    elif weight < p3:
        price_per_kg = a2 * weight ** n2
    elif weight <= breakpoint:
        price_per_kg = a3 * weight ** n3
    elif weight <= MAX_WEIGHT_ROAD:
        return ftl_price  # Helt bilpris
    else:
        raise ValueError("Weight exceeds maximum allowed for road transport")

    return min(weight * price_per_kg, ftl_price)


def calculate_intermodal_rail(ftl_price, weight):
    raise NotImplementedError("Intermodal rail pricing not implemented yet")


def calculate_conventional_rail(ftl_price, weight):
    raise NotImplementedError("Conventional rail pricing not implemented yet")


def calculate_ocean(ftl_price, weight):
    raise NotImplementedError("Ocean freight pricing not implemented yet")


@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    pickup_zip = data.get("pickup_zip")
    delivery_zip = data.get("delivery_zip")
    transport_type = data.get("transport_type")
    weight = float(data.get("weight", 1000))
    ftl_price = float(data.get("ftl_price", 950))
    breakpoint = float(data.get("breakpoint", 20000))

    if not transport_type:
        return jsonify({"error": "transport_type is required"}), 400

    try:
        if transport_type == "road":
            price = calculate_road_price(ftl_price, weight, breakpoint)
        elif transport_type == "intermodal_rail":
            price = calculate_intermodal_rail(ftl_price, weight)
        elif transport_type == "conventional_rail":
            price = calculate_conventional_rail(ftl_price, weight)
        elif transport_type == "ocean":
            price = calculate_ocean(ftl_price, weight)
        else:
            return jsonify({"error": f"Unknown transport_type: {transport_type}"}), 400

        return jsonify({
            "total_price_eur": round(price, 2),
            "ftl_price_eur": round(ftl_price, 2),
            "weight_kg": weight,
            "transport_type": transport_type,
            "source": "magic-formula" if transport_type == "road" else "not-implemented"
        })

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except NotImplementedError as e:
        return jsonify({"error": str(e)}), 501


if __name__ == "__main__":
    app.run(debug=True)
