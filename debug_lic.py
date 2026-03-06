#!/usr/bin/env python3
"""Debug script: check why LIC listings aren't being sent."""

import os
import sys
from datetime import datetime, timedelta, timezone

if not os.environ.get("MONGODB_URI"):
    print("ERROR: Set MONGODB_URI env var")
    sys.exit(1)

import db as db_module
from models import VALID_NEIGHBORHOODS

# 1. Check all users and their LIC subscription
print("=" * 60)
print("1. USER PREFERENCES")
print("=" * 60)
users = db_module.get_all_users()
for u in users:
    uid = u["discord_user_id"]
    name = u.get("discord_username", "?")
    subscribed = u.get("subscribed", False)
    filters = u.get("filters", {})
    hoods = filters.get("neighborhoods", [])
    max_price = filters.get("max_price", 0)
    beds = filters.get("bed_rooms", [])
    subway_prefs = filters.get("subway_preferences")

    print(f"\n  User: {name} (ID: {uid})")
    print(f"  Subscribed: {subscribed}")
    print(f"  Neighborhoods: {hoods}")
    print(f"  Has LIC: {'long-island-city' in hoods}")
    print(f"  Max price: ${max_price:,}")
    print(f"  Beds: {beds}")
    print(f"  Geo bounds: {filters.get('geo_bounds')}")
    print(f"  Subway prefs: {subway_prefs}")

# 2. Check seen_listings for LIC entries
print("\n" + "=" * 60)
print("2. SEEN LIC LISTINGS (from MongoDB)")
print("=" * 60)
seen = db_module.load_seen_from_mongo()
now = datetime.now(timezone.utc)
cutoff_7d = (now - timedelta(days=7)).isoformat()

lic_listings = []
for url, entry in seen.items():
    hood = entry.get("neighborhood", "")
    if hood == "Long Island City":
        lic_listings.append((url, entry))

print(f"\nTotal seen listings: {len(seen)}")
print(f"LIC listings in DB: {len(lic_listings)}")

if lic_listings:
    # Sort by first_seen descending
    lic_listings.sort(key=lambda x: x[1].get("first_seen", ""), reverse=True)
    print("\nMost recent LIC listings:")
    for url, entry in lic_listings[:10]:
        first_seen = entry.get("first_seen", "?")
        price = entry.get("price", "?")
        address = entry.get("address", "?")
        source = entry.get("source", "?")
        print(f"  {first_seen[:16]}  {price:>8}  {address}  [{source}]")
else:
    print("\n  *** NO LIC LISTINGS IN DATABASE ***")
    print("  This means LIC is not being scraped.")
    print("  Check that 'long-island-city' is in your neighborhood filters.")

# 3. Check notification log for LIC DMs
print("\n" + "=" * 60)
print("3. RECENT LIC NOTIFICATION LOG")
print("=" * 60)
notif_col = db_module._notif_col()
lic_notifs = list(notif_col.find({
    "listing_url": {"$regex": "long-island-city|lic", "$options": "i"}
}).sort("sent_at", -1).limit(10))
if not lic_notifs:
    # Also check by looking at listing URLs from seen LIC entries
    lic_urls = [url for url, _ in lic_listings[:20]]
    if lic_urls:
        lic_notifs = list(notif_col.find({
            "listing_url": {"$in": lic_urls}
        }).sort("sent_at", -1).limit(10))

if lic_notifs:
    print(f"\nFound {len(lic_notifs)} LIC notification(s):")
    for n in lic_notifs:
        print(f"  {n.get('sent_at', '?')}  user={n.get('discord_user_id', '?')}  "
              f"type={n.get('notification_type', '?')}  success={n.get('success', '?')}")
        print(f"    URL: {n.get('listing_url', '?')}")
else:
    print("\n  No LIC notifications found in log")

# 4. Check get_neighborhoods_to_scrape
print("\n" + "=" * 60)
print("4. NEIGHBORHOODS BEING SCRAPED")
print("=" * 60)
from apartment_tracker import load_config, get_neighborhoods_to_scrape
config = load_config()
all_hoods = get_neighborhoods_to_scrape(config)
print(f"\nConfig neighborhoods: {config['search']['neighborhoods']}")
print(f"All neighborhoods (config + users): {all_hoods}")
print(f"LIC in scrape list: {'long-island-city' in all_hoods}")
