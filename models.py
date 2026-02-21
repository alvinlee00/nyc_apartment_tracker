"""Neighborhood data and user-listing matching logic."""

from __future__ import annotations

import re
from apartment_tracker import NEIGHBORHOOD_ALIASES, parse_price


# ---------------------------------------------------------------------------
# Valid neighborhoods — every StreetEasy slug we support
# ---------------------------------------------------------------------------

VALID_NEIGHBORHOODS: dict[str, str] = {
    # Manhattan
    "battery-park-city": "Battery Park City",
    "carnegie-hill": "Carnegie Hill",
    "chelsea": "Chelsea",
    "chinatown": "Chinatown",
    "civic-center": "Civic Center",
    "east-village": "East Village",
    "financial-district": "Financial District",
    "flatiron": "Flatiron",
    "fulton-seaport": "Fulton / Seaport",
    "gramercy-park": "Gramercy Park",
    "greenwich-village": "Greenwich Village",
    "hells-kitchen": "Hell's Kitchen",
    "hudson-yards": "Hudson Yards",
    "kips-bay": "Kips Bay",
    "lenox-hill": "Lenox Hill",
    "les": "Lower East Side",
    "little-italy": "Little Italy",
    "manhattan-valley": "Manhattan Valley",
    "midtown": "Midtown",
    "midtown-east": "Midtown East",
    "midtown-south": "Midtown South",
    "midtown-west": "Midtown West",
    "murray-hill": "Murray Hill",
    "noho": "NoHo",
    "nolita": "Nolita",
    "nomad": "NoMad",
    "soho": "SoHo",
    "stuyvesant-town": "Stuyvesant Town",
    "tribeca": "Tribeca",
    "two-bridges": "Two Bridges",
    "upper-east-side": "Upper East Side",
    "upper-west-side": "Upper West Side",
    "west-village": "West Village",
    "yorkville": "Yorkville",
    # Brooklyn
    "bay-ridge": "Bay Ridge",
    "bed-stuy": "Bedford-Stuyvesant",
    "boerum-hill": "Boerum Hill",
    "brooklyn-heights": "Brooklyn Heights",
    "bushwick": "Bushwick",
    "carroll-gardens": "Carroll Gardens",
    "clinton-hill": "Clinton Hill",
    "cobble-hill": "Cobble Hill",
    "crown-heights": "Crown Heights",
    "downtown-brooklyn": "Downtown Brooklyn",
    "dumbo": "DUMBO",
    "flatbush": "Flatbush",
    "fort-greene": "Fort Greene",
    "gowanus": "Gowanus",
    "greenpoint": "Greenpoint",
    "kensington": "Kensington",
    "park-slope": "Park Slope",
    "prospect-heights": "Prospect Heights",
    "red-hook": "Red Hook",
    "sunset-park": "Sunset Park",
    "williamsburg": "Williamsburg",
    "windsor-terrace": "Windsor Terrace",
    # Queens
    "astoria": "Astoria",
    "flushing": "Flushing",
    "forest-hills": "Forest Hills",
    "jackson-heights": "Jackson Heights",
    "long-island-city": "Long Island City",
    "ridgewood": "Ridgewood",
    "sunnyside": "Sunnyside",
    "woodside": "Woodside",
    # Upper Manhattan
    "east-harlem": "East Harlem",
    "hamilton-heights": "Hamilton Heights",
    "harlem": "Harlem",
    "inwood": "Inwood",
    "morningside-heights": "Morningside Heights",
    "washington-heights": "Washington Heights",
}


# Reverse lookup: display name -> set of slugs that cover it
_DISPLAY_NAME_TO_SLUGS: dict[str, set[str]] = {}
for _slug, _display in VALID_NEIGHBORHOODS.items():
    _DISPLAY_NAME_TO_SLUGS.setdefault(_display, set()).add(_slug)


def _get_slugs_for_display_name(display_name: str) -> set[str]:
    """Return slugs whose NEIGHBORHOOD_ALIASES include this display name.

    For example, "Manhattan Valley" -> {"upper-west-side"} because
    NEIGHBORHOOD_ALIASES["upper-west-side"] contains "Manhattan Valley".
    """
    slugs = set()
    for slug, aliases in NEIGHBORHOOD_ALIASES.items():
        if display_name in aliases:
            slugs.add(slug)
    return slugs


def listing_matches_user(listing: dict, user_prefs: dict) -> bool:
    """Check if a listing matches a user's filter preferences.

    Args:
        listing: Dict with keys like address, price, neighborhood, beds, latitude, longitude.
                 `neighborhood` is the display name (e.g. "East Village").
        user_prefs: User preferences document from MongoDB with a `filters` sub-dict.

    Returns:
        True if the listing passes all active filters.
    """
    filters = user_prefs.get("filters", {})

    # --- Neighborhood filter ---
    user_neighborhoods = filters.get("neighborhoods", [])
    if user_neighborhoods:
        listing_hood = listing.get("neighborhood", "")
        if not listing_hood:
            return False

        # Check if listing neighborhood matches any user-subscribed slug.
        # A listing in "Manhattan Valley" matches user subscription to "upper-west-side"
        # because NEIGHBORHOOD_ALIASES["upper-west-side"] includes "Manhattan Valley".
        matched = False
        for slug in user_neighborhoods:
            aliases = NEIGHBORHOOD_ALIASES.get(slug, set())
            if listing_hood in aliases:
                matched = True
                break
            # Also match exact display name from VALID_NEIGHBORHOODS
            display = VALID_NEIGHBORHOODS.get(slug, "")
            if display and listing_hood == display:
                matched = True
                break
        if not matched:
            return False

    # --- Price filter ---
    min_price = filters.get("min_price", 0) or 0
    max_price = filters.get("max_price", 0) or 0
    if max_price > 0:
        listing_price = parse_price(listing.get("price", ""))
        if listing_price is not None:
            if listing_price > max_price:
                return False
            if min_price > 0 and listing_price < min_price:
                return False

    # --- Bed type filter ---
    user_beds = filters.get("bed_rooms", [])
    if user_beds:
        listing_beds = listing.get("beds", "").lower()
        if not listing_beds or listing_beds == "n/a":
            pass  # Don't filter out listings with unknown bed count
        else:
            matched_bed = False
            for bed_type in user_beds:
                if bed_type.lower() == "studio" and "studio" in listing_beds:
                    matched_bed = True
                    break
                # Match "1" with "1 bed", "1 bedroom", etc.
                bed_num = re.search(r"(\d+)", bed_type)
                if bed_num:
                    listing_num = re.search(r"(\d+)", listing_beds)
                    if listing_num and listing_num.group(1) == bed_num.group(1):
                        matched_bed = True
                        break
            if not matched_bed:
                return False

    # --- No-fee filter ---
    if filters.get("no_fee"):
        # If user wants no-fee only, we can't determine fee status from listing data.
        # This is enforced at the scrape URL level instead. Pass through here.
        pass

    # --- Geo bounds filter ---
    geo_bounds = filters.get("geo_bounds")
    if geo_bounds:
        listing_lon = listing.get("longitude")
        if listing_lon is not None:
            west = geo_bounds.get("west_longitude")
            east = geo_bounds.get("east_longitude")
            if west is not None and east is not None:
                if not (west <= listing_lon <= east):
                    return False

    return True


# ---------------------------------------------------------------------------
# Default preferences for new subscribers
# ---------------------------------------------------------------------------

DEFAULT_FILTERS: dict = {
    "neighborhoods": [],
    "min_price": 0,
    "max_price": 5000,
    "bed_rooms": [],
    "no_fee": False,
    "geo_bounds": None,
}

DEFAULT_NOTIFICATION_SETTINGS: dict = {
    "new_listings": True,
    "price_drops": True,
    "daily_digest": True,
}
