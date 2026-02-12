#!/usr/bin/env python3
"""One-time script to build subway station data from the MTA Stations CSV.

Downloads the MTA Stations.csv, groups by Complex ID to merge routes,
and writes data/subway_stations.json with ~300 unique station complexes.

Usage:
    python scripts/build_subway_data.py
"""

import csv
import io
import json
from pathlib import Path

import requests

MTA_CSV_URL = "https://data.ny.gov/api/views/39hk-dx4f/rows.csv?accessType=DOWNLOAD"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "subway_stations.json"


def download_csv() -> str:
    """Download the MTA Stations CSV."""
    print(f"Downloading MTA Stations CSV from {MTA_CSV_URL}...")
    resp = requests.get(MTA_CSV_URL, timeout=30)
    resp.raise_for_status()
    print(f"Downloaded {len(resp.text):,} bytes")
    return resp.text


def parse_stations(csv_text: str) -> list[dict]:
    """Parse CSV and group stations by Complex ID, merging routes."""
    reader = csv.DictReader(io.StringIO(csv_text))

    complexes: dict[str, dict] = {}

    for row in reader:
        complex_id = row.get("Complex ID", "").strip()
        if not complex_id:
            continue

        station_name = row.get("Stop Name", "").strip()
        lat = row.get("GTFS Latitude", "").strip()
        lon = row.get("GTFS Longitude", "").strip()
        line = row.get("Daytime Routes", "").strip()

        if not (lat and lon and station_name):
            continue

        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except ValueError:
            continue

        if complex_id not in complexes:
            complexes[complex_id] = {
                "name": station_name,
                "latitude": lat_f,
                "longitude": lon_f,
                "routes": set(),
            }

        # Merge routes (space-separated in CSV, e.g. "N Q R W")
        if line:
            for route in line.split():
                complexes[complex_id]["routes"].add(route)

        # Use the first station name, but average coordinates for complexes
        # with multiple entries for better accuracy
        existing = complexes[complex_id]
        existing["latitude"] = (existing["latitude"] + lat_f) / 2
        existing["longitude"] = (existing["longitude"] + lon_f) / 2

    # Convert to list, sort routes for determinism
    stations = []
    for complex_id, data in complexes.items():
        stations.append({
            "complex_id": complex_id,
            "name": data["name"],
            "latitude": round(data["latitude"], 6),
            "longitude": round(data["longitude"], 6),
            "routes": sorted(data["routes"]),
        })

    # Sort by complex_id for deterministic output
    stations.sort(key=lambda s: int(s["complex_id"]))
    return stations


def main():
    csv_text = download_csv()
    stations = parse_stations(csv_text)
    print(f"Parsed {len(stations)} unique station complexes")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(stations, f, indent=2)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
