import os
API_KEY = os.getenv("AIzaSyD1gxDGlhnzfhVaqzIKq8R-F5uDnRttYsw")

import requests
import xmltodict
import math
import urllib.parse

API_KEY = "AIzaSyD1gxDGlhnzfhVaqzIKq8R-F5uDnRttYsw"

FUEL_CODES = {
    "ulp91": 1,
    "ulp95": 2,
    "ulp98": 4,
    "lpg": 5,
    "diesel": 6,
    "e85": 10
}

def chunk_list(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]

def safe_xml_items(channel):
    """Return channel['item'] as a list, or empty list if missing."""
    if "item" not in channel:
        return []
    items = channel["item"]
    return items if isinstance(items, list) else [items]

def get_fuel_results(start_address, fuel_type="ulp91", litres_to_buy=70, max_distance_km=20, fuel_consumption=11.6):

    product_code = FUEL_CODES[fuel_type]
    headers = {"User-Agent": "Mozilla/5.0"}

    # -----------------------------
    # 1. Fetch FuelWatch data
    # -----------------------------
    fw_today_url = f"https://www.fuelwatch.wa.gov.au/fuelwatch/fuelWatchRSS?Product={product_code}&Region=0"
    fw_today = xmltodict.parse(requests.get(fw_today_url, headers=headers).text)
    stations_today = safe_xml_items(fw_today["rss"]["channel"])

    fw_tom_url = f"https://www.fuelwatch.wa.gov.au/fuelwatch/fuelWatchRSS?Product={product_code}&Region=0&Day=Tomorrow"
    fw_tom = xmltodict.parse(requests.get(fw_tom_url, headers=headers).text)
    stations_tomorrow = safe_xml_items(fw_tom["rss"]["channel"])

    # Build lookup for tomorrow prices
    tomorrow_lookup = {}
    for s in stations_tomorrow:
        key = (s.get("trading-name"), s.get("location"))
        try:
            tomorrow_lookup[key] = float(s.get("price", 0))
        except:
            pass

    # -----------------------------
    # 2. Extract coordinates
    # -----------------------------
    station_list = []
    for s in stations_today:
        lat = s.get("latitude")
        lng = s.get("longitude")
        if not lat or not lng:
            continue

        try:
            price_today = float(s.get("price", 0))
        except:
            continue

        station_list.append({
            "name": s.get("trading-name"),
            "suburb": s.get("location"),
            "price_today": price_today,
            "lat": lat,
            "lng": lng
        })

    # -----------------------------
    # 3. Distance Matrix batching
    # -----------------------------
    destinations = [f"{s['lat']},{s['lng']}" for s in station_list]
    distances_km = []

    encoded_origin = urllib.parse.quote(start_address)

    for chunk in chunk_list(destinations, 25):
        dm_url = (
            "https://maps.googleapis.com/maps/api/distancematrix/json"
            f"?origins={encoded_origin}&destinations={'|'.join(chunk)}"
            f"&mode=driving&key={API_KEY}"
        )

        dm_data = requests.get(dm_url).json()
        print("DM response:", dm_data)

        if dm_data.get("status") != "OK" or "rows" not in dm_data or not dm_data["rows"]:
            distances_km.extend([None] * len(chunk))
            continue

        elements = dm_data["rows"][0]["elements"]

        for e in elements:
            if e.get("status") == "OK":
                distances_km.append(e["distance"]["value"] / 1000)
            else:
                distances_km.append(None)

    # -----------------------------
    # 4. Combine station + distance
    # -----------------------------
    results = []
    for station, dist in zip(station_list, distances_km):

        if dist is None or dist > max_distance_km:
            continue

        round_trip_km = dist * 2
        litres_used = (round_trip_km / 100) * fuel_consumption
        trip_cost = litres_used * (station["price_today"] / 100)

        fuel_cost_today = (station["price_today"] / 100) * litres_to_buy
        total_cost_today = fuel_cost_today + trip_cost

        key = (station["name"], station["suburb"])
        price_tomorrow = tomorrow_lookup.get(key)

        results.append({
            **station,
            "distance_km": dist,
            "trip_cost": trip_cost,
            "fuel_cost_today": fuel_cost_today,
            "total_cost_today": total_cost_today,
            "price_tomorrow": price_tomorrow
        })

    # -----------------------------
    # 5. TODAY results
    # -----------------------------
    today_sorted = sorted(results, key=lambda x: x["total_cost_today"])
    today_top5 = today_sorted[:5]

    today_nearby = [r for r in today_sorted if r["distance_km"] <= 5]
    today_cheapest_near = today_nearby[0] if today_nearby else None

    # -----------------------------
    # 6. TOMORROW results
    # -----------------------------
    tomorrow_results = []
    for r in results:
        if r["price_tomorrow"] is None:
            continue

        fuel_cost_tomorrow = (r["price_tomorrow"] / 100) * litres_to_buy
        total_cost_tomorrow = fuel_cost_tomorrow + r["trip_cost"]

        tomorrow_results.append({
            **r,
            "fuel_cost_tomorrow": fuel_cost_tomorrow,
            "total_cost_tomorrow": total_cost_tomorrow
        })

    tomorrow_sorted = sorted(tomorrow_results, key=lambda x: x["total_cost_tomorrow"])
    tomorrow_top5 = tomorrow_sorted[:5]

    tomorrow_nearby = [r for r in tomorrow_sorted if r["distance_km"] <= 5]
    tomorrow_cheapest_near = tomorrow_nearby[0] if tomorrow_nearby else None

    # -----------------------------
    # 7. Return final structured result
    # -----------------------------
    return {
        "today_top5": today_top5,
        "today_near": today_cheapest_near,
        "tomorrow_top5": tomorrow_top5,
        "tomorrow_near": tomorrow_cheapest_near
    }
