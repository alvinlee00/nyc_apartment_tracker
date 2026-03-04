#!/usr/bin/env python3
"""One-off script: send a test LIC listing DM to a user based on their subway prefs."""

import os
import sys

# Require env vars
if not os.environ.get("MONGODB_URI") or not os.environ.get("DISCORD_BOT_TOKEN"):
    print("ERROR: Set MONGODB_URI and DISCORD_BOT_TOKEN env vars")
    sys.exit(1)

import db as db_module
from apartment_tracker import (
    _load_subway_stations, find_nearby_stations, _format_subway_field,
    _get_subway_prefs_for_listing, compute_subway_pref_score,
    build_listing_embed, compute_value_score, send_discord_dm,
)

# Find the "alvin" user
users = db_module.get_all_users()
alvin = None
for u in users:
    if "alvin" in u.get("discord_username", "").lower():
        alvin = u
        break

if not alvin:
    print("Could not find an 'alvin' user. Users found:")
    for u in users:
        print(f"  {u['discord_user_id']}: {u.get('discord_username', '?')}")
    sys.exit(1)

user_id = alvin["discord_user_id"]
print(f"Found user: {alvin.get('discord_username')} (ID: {user_id})")
print(f"Filters: {alvin.get('filters', {})}")

# Fake LIC listing (realistic coordinates near Court Sq)
listing = {
    "url": "https://streeteasy.com/building/test-lic-listing/1a",
    "address": "27-01 Queens Plaza North #12F",
    "price": "$3,200",
    "beds": "1 bed",
    "baths": "1 bath",
    "sqft": "680 ft²",
    "neighborhood": "Long Island City",
    "image_url": "",
    "latitude": 40.7490,
    "longitude": -73.9440,
    "source": "streeteasy",
}

# Compute subway info
stations = _load_subway_stations()
nearby = find_nearby_stations(listing["latitude"], listing["longitude"], stations)

# Check for user subway prefs
user_subway_prefs = alvin.get("filters", {}).get("subway_preferences")
if user_subway_prefs:
    sprefs = _get_subway_prefs_for_listing(listing, {"subway_preferences": user_subway_prefs})
    if sprefs and nearby:
        listing["subway_info"] = _format_subway_field(nearby, sprefs)
        spref_score = compute_subway_pref_score(
            listing["latitude"], listing["longitude"], stations, sprefs)
        if spref_score:
            listing["subway_pref_score"] = spref_score
        print(f"Using user subway prefs: {sprefs}")
        print(f"Subway match score: {spref_score}")
    else:
        listing["subway_info"] = _format_subway_field(nearby)
        print("User has subway_preferences but no match for LIC")
else:
    listing["subway_info"] = _format_subway_field(nearby)
    print("No user subway preferences set — using defaults")

print(f"Subway info:\n{listing.get('subway_info', 'N/A')}")

# Build embed and send
vs = compute_value_score(listing, {"Long Island City": 3400.0}, nearby)
embed = build_listing_embed(listing, value_score=vs)
embed["title"] = "🧪 TEST: " + embed["title"]

bot_token = os.environ["DISCORD_BOT_TOKEN"]
success = send_discord_dm(bot_token, user_id, embed)
print(f"\nDM sent: {success}")
