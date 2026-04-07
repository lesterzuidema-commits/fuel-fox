import os
import requests
import xmltodict
import urllib.parse
from bs4 import BeautifulSoup
import json
import time
import math

API_KEY = os.getenv("API_KEY")

FUEL_CODES = {
    "ulp91": 1,
    "ulp95": 2,
    "ulp98": 6,
    "diesel": 4,
}

# ---------------------------------------------------------
# JSON CACHE FOR DISTANCE MATRIX
# ---------------------------------------------------------
CACHE_FILE = "distance_cache.json"
CACHE_TTL = 60 * 60 * 24  # 24 hours


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except:
        pass


def make_cache_key(origin, lat, lng):
    return f"{origin.lower()}::{lat}::{lng}"


def get_cached_distance(origin, lat, lng):
    cache = load_cache()
    key = make_cache_key(origin, lat, lng)

    if key not in cache:
        return None

    entry = cache[key]
    if time.time() - entry["timestamp"] > CACHE_TTL:
        return None  # expired

    return entry["distance_km"]


def set_cached_distance(origin, lat, lng, distance_km):
    cache = load_cache()
    key = make_cache_key(origin, lat, lng)

    cache[key] = {
        "distance_km": distance_km,
        "timestamp": time.time()
    }

    save_cache(cache)


# ---------------------------------------------------------
# Helper functions
# ---------------------------------------------------------
def chunk_list(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def safe_xml_items(channel):
    if "item" not in channel:
        return []
    items = channel["item"]
    return items if isinstance(items, list) else [items]


# ---------------------------------------------------------
# Haversine (straight-line distance)
# ---------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(a))


# ---------------------------------------------------------
# Fetch stations that have reported being OUT OF FUEL
# ---------------------------------------------------------
def get_unavailable_stations():
    url = "https://www.fuelwatch.wa.gov.au/fuelwatch/pages/fuelAvailability.jsp"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(response.text, "html.parser")

    unavailable = set()

    rows = soup.select("table tr")
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 3:
            continue

        station_name = cols[0].get_text(strip=True)
        suburb = cols[2].get_text(strip=True)

        unavailable.add((station_name.lower(), suburb.lower()))

    return unavailable


# ---------------------------------------------------------
# MAIN FUNCTION
# ---------------------------------------------------------
def get_fuel_results(start_address, fuel_type="ulp91", litres_to_buy=70,
                     max_distance_km=20, fuel_consumption=11.6):

    print("API KEY LOADED:", API_KEY is not None)

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
            "lat": float(lat),
            "lng": float(lng)
        })

    # -----------------------------
    # 3. Filter unavailable stations
    # -----------------------------
    unavailable = get_unavailable_stations()

    filtered_station_list = []
    for s in station_list:
        key = (s["name"].lower(), s["suburb"].lower())
        if key not in unavailable:
            filtered_station_list.append(s)

    station_list = filtered_station_list

    print("Unavailable stations:", len(unavailable))
    print("Stations after filtering:", len(station_list))

    # -----------------------------
    # 4. PREFILTER using haversine
    # -----------------------------
    # Get origin coordinates via Geocoding API
    geo_url = (
        "https://maps.googleapis.com/maps/api/geocode/json"
        f"?address={urllib.parse.quote(start_address)}&key={API_KEY}"
    )
    geo_data = requests.get(geo_url).json()

    if geo_data.get("status") != "OK":
        print("Geocoding failed:", geo_data)
        return None

    origin_lat = geo_data["results"][0]["geometry"]["location"]["lat"]
    origin_lng = geo_data["results"][0]["geometry"]["location"]["lng"]

    buffer_km = max_distance_km + 10  # safety buffer

    pre_filtered = []
    for s in station_list:
        dist = haversine(origin_lat, origin_lng, s["lat"], s["lng"])
        if dist <= buffer_km:
            pre_filtered.append(s)

    print("Stations after prefiltering:", len(pre_filtered))

    station_list = pre_filtered

    # -----------------------------
    # 5. Distance Matrix with caching
    # -----------------------------
    distances_km = []
    encoded_origin = urllib.parse.quote(start_address)

    for chunk in chunk_list(station_list, 25):

        cached_distances = []
        uncached_destinations = []
        uncached_stations = []

        # Check cache first
        for s in chunk:
            cached = get_cached_distance(start_address, s["lat"], s["lng"])
            if cached is not None:
                cached_distances.append(cached)
            else:
                uncached_destinations.append(f"{s['lat']},{s['lng']}")
                uncached_stations.append(s)

        # If everything was cached
        if len(uncached_destinations) == 0:
            distances_km.extend(cached_distances)
            continue

        # Call Google for uncached stations
        dm_url = (
            "https://maps.googleapis.com/maps/api/distancematrix/json"
            f"?origins={encoded_origin}&destinations={'|'.join(uncached_destinations)}"
            f"&mode=driving&key={API_KEY}"
        )

        dm_data = requests.get(dm_url).json()
        print("DM response:", dm_data)

        if dm_data.get("status") != "OK" or "rows" not in dm_data or not dm_data["rows"]:
            distances_km.extend(cached_distances + [None] * len(uncached_destinations))
            continue

        elements = dm_data["rows"][0]["elements"]

        new_distances = []
        for s, e in zip(uncached_stations, elements):
            if e.get("status") == "OK":
                dist_km = e["distance"]["value"] / 1000
                new_distances.append(dist_km)
                set_cached_distance(start_address, s["lat"], s["lng"], dist_km)
            else:
                new_distances.append(None)

        distances_km.extend(cached_distances + new_distances)

    # -----------------------------
    # 6. Combine station + distance
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
    # 7. TODAY results
    # -----------------------------
    today_sorted = sorted(results, key=lambda x: x["total_cost_today"])
    today_top5 = today_sorted[:5]

    today_nearby = [r for r in today_sorted if r["distance_km"] <= 5]
    today_cheapest_near = today_nearby[0] if today_nearby else None

    # -----------------------------
    # 8. TOMORROW results
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
    # 9. Return final structured result
    # -----------------------------
    return {
        "today_top5": today_top5,
        "today_near": today_cheapest_near,
        "tomorrow_top5": tomorrow_top5,
        "tomorrow_near": tomorrow_cheapest_near
    }
