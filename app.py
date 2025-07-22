from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/calculate", methods=["POST"])
def calculate_price():
    data = request.json

    pickup_zip = data.get("pickup_zip")
    delivery_zip = data.get("delivery_zip")
    goods_type = data.get("goods_type")
    chargeable_weight = data.get("chargeable_weight", 1000)

    if goods_type == "FTL":
        price = 950
    elif goods_type == "Bulk":
        price = chargeable_weight * 0.05
    else:
        price = chargeable_weight * 0.08

    return jsonify({
        "price_eur": round(price, 2),
        "currency": "EUR",
        "source": "dummy-calculation"
    })

if __name__ == "__main__":
    app.run(debug=True)
Add app.py
