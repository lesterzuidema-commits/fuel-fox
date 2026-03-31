from flask import Flask, render_template, request
from fuel_logic import get_fuel_results

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    fuel_type = "ulp91"
    litres = 70
    max_distance = 20
    start_address = ""
    fuel_consumption = 11.6

    results = None

    if request.method == "POST":
        fuel_type = request.form.get("fuel_type", "ulp91")
        litres = int(request.form.get("litres", 70))
        max_distance = int(request.form.get("max_distance", 20))
        start_address = request.form.get("start_address", "")
        fuel_consumption = float(request.form.get("fuel_consumption", 11.6))

        results = get_fuel_results(
            start_address=start_address,
            fuel_type=fuel_type,
            litres_to_buy=litres,
            max_distance_km=max_distance,
            fuel_consumption=fuel_consumption
        )

    return render_template(
        "index.html",
        fuel_type=fuel_type,
        litres=litres,
        max_distance=max_distance,
        start_address=start_address,
        fuel_consumption=fuel_consumption,
        results=results
    )

if __name__ == "__main__":
    app.run(debug=True)
