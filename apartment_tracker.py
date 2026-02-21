#!/usr/bin/env python3
"""NYC Apartment Tracker - Scrapes StreetEasy and sends Discord notifications."""

import argparse
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

# curl_cffi session reused across requests (Chrome TLS fingerprint)
_cffi_session: cffi_requests.Session | None = None

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("apartment_tracker")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
SEEN_PATH = BASE_DIR / "seen_listings.json"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _use_mongodb() -> bool:
    """Check if MongoDB backend is configured."""
    return bool(os.environ.get("MONGODB_URI"))


def load_seen() -> dict:
    if _use_mongodb():
        import db as db_module
        return db_module.load_seen_from_mongo()
    if SEEN_PATH.exists():
        with open(SEEN_PATH) as f:
            data = json.load(f)
            if isinstance(data, list):
                # Migrate from old list format to dict format
                return {url: {"first_seen": datetime.now(timezone.utc).isoformat()} for url in data}
            return data
    return {}


def save_seen(seen: dict) -> None:
    if _use_mongodb():
        import db as db_module
        db_module.save_seen_to_mongo(seen)
        return
    with open(SEEN_PATH, "w") as f:
        json.dump(seen, f, indent=2)

# ---------------------------------------------------------------------------
# StreetEasy scraping
# ---------------------------------------------------------------------------

STREETEASY_BASE = "https://streeteasy.com"

# Maps search slugs to valid neighborhood names that StreetEasy returns.
# Sub-neighborhoods (e.g. Manhattan Valley for UWS) are included.
# Sponsored listings from unrelated areas (e.g. Greenpoint) get filtered out.
NEIGHBORHOOD_ALIASES: dict[str, set[str]] = {
    "east-village": {"East Village"},
    "west-village": {"West Village"},
    "upper-west-side": {"Upper West Side", "Manhattan Valley", "Lincoln Square"},
    "chelsea": {"Chelsea", "West Chelsea"},
    "les": {"Lower East Side", "Two Bridges", "Chinatown"},
    "upper-east-side": {"Upper East Side", "Yorkville", "Carnegie Hill", "Lenox Hill"},
    "hells-kitchen": {"Hell's Kitchen", "Midtown West"},
    "murray-hill": {"Murray Hill", "Kips Bay"},
    "gramercy-park": {"Gramercy Park", "Gramercy", "Kips Bay"},
    "flatiron": {"Flatiron", "NoMad"},
    "kips-bay": {"Kips Bay"},
    "greenwich-village": {"Greenwich Village"},
    "soho": {"SoHo"},
    "tribeca": {"Tribeca"},
    "financial-district": {"Financial District", "FiDi"},
    "williamsburg": {"Williamsburg", "East Williamsburg"},
    "greenpoint": {"Greenpoint"},
    "park-slope": {"Park Slope"},
    "bushwick": {"Bushwick"},
    "bed-stuy": {"Bedford-Stuyvesant", "Bed-Stuy"},
    "astoria": {"Astoria"},
    "long-island-city": {"Long Island City"},
}


def parse_price(price_str: str) -> int | None:
    """Extract integer price from a string like '$3,200'. Returns None if unparseable."""
    match = re.search(r"[\d,]+", price_str.replace(",", ""))
    if match:
        try:
            return int(match.group(0))
        except ValueError:
            return None
    return None


def build_search_url(neighborhood: str, config: dict) -> str:
    """Build a StreetEasy rental search URL from config."""
    search = config["search"]
    max_price = search["max_price"]
    min_price = search.get("min_price", 0)

    beds = search["bed_rooms"]
    if len(beds) == 1:
        beds_param = beds[0]
    else:
        beds_param = f"{beds[0]}-{beds[-1]}"

    price_filter = f"price:{min_price}-{max_price}" if min_price else f"price:-{max_price}"
    beds_filter = f"beds:{beds_param}"
    filters = f"{price_filter}|{beds_filter}"

    if search.get("no_fee"):
        filters += "|no_fee:1"

    return f"{STREETEASY_BASE}/for-rent/{neighborhood}/{quote(filters, safe=':|-')}"


class ScraperSession:
    """Lightweight wrapper around curl_cffi with Chrome TLS impersonation."""

    def __init__(self):
        global _cffi_session
        _cffi_session = cffi_requests.Session(impersonate="chrome")
        self._session = _cffi_session

    def close(self):
        self._session.close()

    _HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    def fetch(self, url: str) -> BeautifulSoup | None:
        """Fetch URL with Chrome TLS fingerprint and return parsed soup."""
        try:
            resp = self._session.get(url, headers=self._HEADERS, timeout=30)
            if resp.status_code == 403:
                log.warning("Got 403 for %s — may be rate-limited or blocked", url)
                return None
            if resp.status_code >= 400:
                log.error("HTTP %d for %s", resp.status_code, url)
                return None
            return BeautifulSoup(resp.text, "lxml")
        except Exception as e:
            log.error("Failed to fetch %s: %s", url, e)
            return None

    def fetch_with_status(self, url: str) -> tuple[BeautifulSoup | None, int | None]:
        """Fetch URL and return (parsed_soup, http_status_code).
        Returns (None, None) on network/connection errors.
        """
        try:
            resp = self._session.get(url, headers=self._HEADERS, timeout=30)
            if resp.status_code >= 400:
                return None, resp.status_code
            return BeautifulSoup(resp.text, "lxml"), resp.status_code
        except Exception as e:
            log.error("Failed to fetch %s: %s", url, e)
            return None, None


def get_session(config: dict) -> ScraperSession:
    """Create a scraper session with Chrome TLS impersonation."""
    return ScraperSession()


def fetch_page(session: ScraperSession, url: str) -> BeautifulSoup | None:
    """Fetch a page and return parsed soup, or None on failure."""
    return session.fetch(url)


def parse_listings(soup: BeautifulSoup) -> list[dict]:
    """Extract listing data from a search results page."""
    listings = []

    cards = soup.find_all("div", attrs={"data-testid": "listing-card"})
    if not cards:
        # Fallback: try class-based selector
        cards = soup.find_all("div", class_=lambda c: c and "ListingCard-module__cardContainer" in c)

    for card in cards:
        try:
            listing = parse_single_card(card)
            if listing and listing.get("url"):
                listings.append(listing)
        except Exception as e:
            log.debug("Failed to parse a card: %s", e)
            continue

    return listings


def parse_single_card(card) -> dict | None:
    """Parse a single listing card element."""
    # Address and URL
    addr_link = card.find("a", class_=lambda c: c and "addressTextAction" in c)
    if not addr_link:
        addr_link = card.find("a", href=lambda h: h and "/building/" in h)
    if not addr_link:
        return None

    url = addr_link.get("href", "")
    if url and not url.startswith("http"):
        url = STREETEASY_BASE + url
    address = addr_link.get_text(strip=True)

    # Remove tracking params like ?featured=1
    clean_url = re.sub(r'\?.*$', '', url)

    # Price
    price_el = card.find("span", class_=lambda c: c and "price" in c.lower() and "PriceInfo" in c)
    if not price_el:
        price_el = card.find("span", class_=lambda c: c and "price" in c.lower())
    price = price_el.get_text(strip=True) if price_el else "N/A"

    # Type and neighborhood from title
    title_el = card.find("p", class_=lambda c: c and "title" in c.lower() and "ListingDescription" in c)
    if not title_el:
        title_el = card.find("p", class_=lambda c: c and "title" in c.lower())
    title_text = title_el.get_text(strip=True) if title_el else ""
    neighborhood = ""
    match = re.search(r"in\s+(.+?)(?:\s+at|$)", title_text)
    if match:
        neighborhood = match.group(1).strip()

    # Beds, baths, sqft
    detail_spans = card.find_all("span", class_=lambda c: c and "BedsBathsSqft" in c)
    beds = "N/A"
    baths = "N/A"
    sqft = "N/A"
    for span in detail_spans:
        text = span.get_text(strip=True).lower()
        if "bed" in text or "studio" in text:
            beds = span.get_text(strip=True)
        elif "bath" in text:
            baths = span.get_text(strip=True)
        elif "ft" in text:
            raw = span.get_text(strip=True)
            # Filter out empty sqft like "-ft²" or "- ft²"
            if re.search(r"\d", raw):
                sqft = raw

    # Image
    img = card.find("img")
    image_url = img.get("src", "") if img else ""

    return {
        "url": clean_url,
        "address": address,
        "price": price,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "neighborhood": neighborhood,
        "image_url": image_url,
    }


def get_max_page(soup: BeautifulSoup) -> int:
    """Get the max page number from pagination."""
    pagination = soup.find("div", class_=lambda c: c and "paginationContainer" in c)
    if not pagination:
        return 1
    page_links = pagination.find_all("a", href=re.compile(r"page=\d+"))
    max_page = 1
    for link in page_links:
        match = re.search(r"page=(\d+)", link.get("href", ""))
        if match:
            max_page = max(max_page, int(match.group(1)))
    return max_page


def scrape_neighborhood(session: ScraperSession, neighborhood: str, config: dict) -> list[dict]:
    """Scrape all pages of listings for a neighborhood."""
    base_url = build_search_url(neighborhood, config)
    delay = config["scraper"]["request_delay_seconds"]
    raw_listings = []

    log.info("Scraping %s → %s", neighborhood, base_url)
    soup = fetch_page(session, base_url)
    if not soup:
        return []

    listings = parse_listings(soup)
    raw_listings.extend(listings)
    log.info("  Page 1: found %d listings", len(listings))

    max_page = get_max_page(soup)
    # Cap at 5 pages to avoid excessive requests
    max_page = min(max_page, 5)

    for page in range(2, max_page + 1):
        time.sleep(delay)
        page_url = f"{base_url}?page={page}"
        soup = fetch_page(session, page_url)
        if not soup:
            break
        listings = parse_listings(soup)
        if not listings:
            break
        raw_listings.extend(listings)
        log.info("  Page %d: found %d listings", page, len(listings))

    # Deduplicate by URL (featured listings appear on multiple pages)
    seen_urls = set()
    unique_listings = []
    for listing in raw_listings:
        if listing["url"] not in seen_urls:
            seen_urls.add(listing["url"])
            unique_listings.append(listing)

    # Filter out sponsored listings above max price
    max_price = config["search"]["max_price"]
    filtered = []
    for listing in unique_listings:
        price_val = parse_price(listing["price"])
        if price_val is not None and price_val > max_price:
            log.debug("Filtered out %s (%s) — above max $%d",
                      listing["address"], listing["price"], max_price)
            continue
        filtered.append(listing)

    # Filter out sponsored listings from unrelated neighborhoods.
    # Listings with empty neighborhood are also rejected — they're likely sponsored
    # placements where StreetEasy doesn't show the standard neighborhood label.
    allowed = NEIGHBORHOOD_ALIASES.get(neighborhood)
    if allowed:
        before_count = len(filtered)
        kept = []
        for l in filtered:
            if l["neighborhood"] in allowed:
                kept.append(l)
            else:
                log.debug("  Rejected: %s — neighborhood '%s' not in %s",
                          l["address"], l["neighborhood"], neighborhood)
        filtered = kept
        removed = before_count - len(filtered)
        if removed:
            log.info("  Filtered %d sponsored/unrelated listing(s)", removed)

    log.info("  %s: %d raw → %d unique → %d after filters",
             neighborhood, len(raw_listings), len(unique_listings), len(filtered))

    return filtered


# ---------------------------------------------------------------------------
# Cross street lookup via NYC Geoclient API
# ---------------------------------------------------------------------------

GEOCLIENT_BASE = "https://api.nyc.gov/geoclient/v2/address"


def _parse_address_for_geoclient(address: str) -> tuple[str, str] | None:
    """Extract (house_number, street) from a StreetEasy address like '337 East 21st Street #3H'.

    Returns None if the address can't be parsed.
    """
    # Strip unit/apt suffixes like "#3H", "Apt 4B", ", Unit 5"
    cleaned = re.sub(r"[,\s]*(?:#|apt\.?|unit)\s*\S+$", "", address, flags=re.IGNORECASE).strip()
    match = re.match(r"^(\d+[\w-]*)\s+(.+)$", cleaned)
    if not match:
        return None
    return match.group(1), match.group(2)


def _format_cross_streets(low: str, high: str) -> str:
    """Format cross street names into a readable string."""
    low_clean = " ".join(low.split()).title()
    high_clean = " ".join(high.split()).title()
    return f"between {low_clean} & {high_clean}"


def geoclient_lookup(address: str, geoclient_key: str) -> dict | None:
    """Look up cross streets and coordinates for a NYC address via the Geoclient API.

    Returns {'cross_streets': str|None, 'latitude': float|None, 'longitude': float|None}
    or None if the address can't be parsed.
    """
    parsed = _parse_address_for_geoclient(address)
    if not parsed:
        log.debug("Could not parse address for Geoclient: %s", address)
        return None

    house_number, street = parsed
    try:
        resp = requests.get(
            GEOCLIENT_BASE,
            params={
                "houseNumber": house_number,
                "street": street,
                "borough": "Manhattan",
            },
            headers={"Ocp-Apim-Subscription-Key": geoclient_key},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("address", {})

        # Cross streets
        low = data.get("lowCrossStreetName1", "").strip()
        high = data.get("highCrossStreetName1", "").strip()
        cross_streets = _format_cross_streets(low, high) if low and high else None

        # Coordinates
        lat = data.get("latitude")
        lon = data.get("longitude")
        latitude = float(lat) if lat is not None else None
        longitude = float(lon) if lon is not None else None

        return {
            "cross_streets": cross_streets,
            "latitude": latitude,
            "longitude": longitude,
        }
    except requests.RequestException as e:
        log.warning("Geoclient API error for '%s': %s", address, e)
        return None


# ---------------------------------------------------------------------------
# Subway station proximity
# ---------------------------------------------------------------------------

SUBWAY_DATA_PATH = BASE_DIR / "data" / "subway_stations.json"
_subway_stations_cache: list[dict] | None = None


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _load_subway_stations() -> list[dict]:
    """Load subway station data from JSON, caching after first call."""
    global _subway_stations_cache
    if _subway_stations_cache is not None:
        return _subway_stations_cache
    try:
        with open(SUBWAY_DATA_PATH) as f:
            _subway_stations_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning("Could not load subway station data: %s", e)
        _subway_stations_cache = []
    return _subway_stations_cache


def find_nearby_stations(
    lat: float, lon: float, stations: list[dict],
    max_stations: int = 3, max_miles: float = 0.5,
) -> list[dict]:
    """Find the closest subway stations within max_miles.

    Returns up to max_stations results, each with keys:
        name, routes, distance_mi
    """
    results = []
    for s in stations:
        dist = _haversine(lat, lon, s["latitude"], s["longitude"])
        if dist <= max_miles:
            results.append({
                "name": s["name"],
                "routes": s["routes"],
                "distance_mi": round(dist, 2),
            })
    results.sort(key=lambda r: r["distance_mi"])
    return results[:max_stations]


def _format_subway_field(nearby_stations: list[dict]) -> str:
    """Format nearby stations for a Discord embed field value."""
    lines = []
    for s in nearby_stations:
        routes = ", ".join(s["routes"])
        lines.append(f"{routes} at {s['name']} ({s['distance_mi']} mi)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Google Maps URL
# ---------------------------------------------------------------------------

def is_within_geo_bounds(longitude: float | None, config: dict) -> bool:
    """Check if a longitude falls within configured geo bounds.

    Returns True if within bounds, no bounds configured, or no longitude provided.
    """
    if longitude is None:
        return True
    bounds = config.get("search", {}).get("geo_bounds")
    if not bounds:
        return True
    west = bounds.get("west_longitude")
    east = bounds.get("east_longitude")
    if west is None or east is None:
        return True
    return west <= longitude <= east


def build_google_maps_url(address: str) -> str:
    """Build a Google Maps search URL for an NYC address."""
    query = quote(f"{address}, New York, NY")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


# ---------------------------------------------------------------------------
# Listing staleness
# ---------------------------------------------------------------------------

def compute_days_on_market(first_seen_str: str | None) -> int | None:
    """Compute the number of days since a listing was first seen."""
    if not first_seen_str:
        return None
    try:
        first_seen = datetime.fromisoformat(first_seen_str)
        now = datetime.now(timezone.utc)
        # Handle naive datetimes by assuming UTC
        if first_seen.tzinfo is None:
            first_seen = first_seen.replace(tzinfo=timezone.utc)
        return (now - first_seen).days
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Listing status check + stale cleanup
# ---------------------------------------------------------------------------

def check_listing_status(session: ScraperSession, url: str) -> str:
    """Check if a StreetEasy listing is still active.
    Returns 'active', 'gone', or 'unknown'.
    """
    soup, status = session.fetch_with_status(url)
    if status is None:
        return "unknown"      # network error
    if status == 404:
        return "gone"
    if status == 403:
        return "unknown"      # rate-limited, retry later
    if status >= 400:
        return "unknown"
    # 200 OK — check page content
    if soup:
        text = soup.get_text(separator=" ", strip=True).lower()
        if "no longer available" in text or "off market" in text:
            return "gone"
    return "active"


def cleanup_stale_listings(
    session: ScraperSession, seen: dict, config: dict,
    geoclient_key: str, max_checks: int = 10,
) -> int:
    """Check stale listings and remove rented/gone ones. Returns count removed."""
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=7)
    delay = config.get("scraper", {}).get("request_delay_seconds", 2)

    # Collect stale entries (missing last_scraped or older than 7 days)
    stale = []
    for url, entry in seen.items():
        ls = entry.get("last_scraped")
        if not ls:
            stale.append((url, None))
        else:
            try:
                ts = datetime.fromisoformat(ls)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < stale_cutoff:
                    stale.append((url, ts))
            except (ValueError, TypeError):
                stale.append((url, None))

    # Sort by staleness (oldest / missing first — most likely gone)
    stale.sort(key=lambda x: x[1] or datetime.min.replace(tzinfo=timezone.utc))

    removed = 0
    for i, (url, _) in enumerate(stale[:max_checks]):
        if i > 0:
            time.sleep(delay)

        entry = seen.get(url)
        if entry is None:
            continue  # already removed by geo backfill below

        # Geo backfill during cleanup if missing coordinates
        if geoclient_key and "latitude" not in entry:
            geo = geoclient_lookup(entry.get("address", ""), geoclient_key)
            if geo and geo["latitude"] and geo["longitude"]:
                entry["latitude"] = geo["latitude"]
                entry["longitude"] = geo["longitude"]
            if geo and not is_within_geo_bounds(geo.get("longitude"), config):
                log.info("REMOVING (geo bounds during cleanup): %s", entry.get("address"))
                del seen[url]
                removed += 1
                continue

        status = check_listing_status(session, url)
        if status == "gone":
            log.info("REMOVING (rented/gone): %s — %s", entry.get("address", "?"), url)
            del seen[url]
            removed += 1
        elif status == "active":
            entry["last_scraped"] = now.isoformat()
        # "unknown" → skip, try again next run

    return removed


# ---------------------------------------------------------------------------
# Value score system
# ---------------------------------------------------------------------------

def _median(values: list[float]) -> float:
    """Compute median of a sorted list of values."""
    n = len(values)
    if n == 0:
        return 0.0
    s = sorted(values)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]


def compute_neighborhood_medians(seen: dict) -> dict[str, float]:
    """Compute median price per neighborhood from all tracked listings."""
    prices_by_hood: dict[str, list[float]] = {}
    for entry in seen.values():
        hood = entry.get("neighborhood", "")
        if not hood:
            continue
        price = parse_price(entry.get("price", ""))
        if price is not None:
            prices_by_hood.setdefault(hood, []).append(float(price))
    return {hood: _median(prices) for hood, prices in prices_by_hood.items()}


def compute_value_score(listing: dict, medians: dict[str, float],
                        nearby_stations: list[dict] | None = None) -> dict | None:
    """Compute a weighted value score (0-10) for a listing.

    Components:
      - Price vs neighborhood median (40%): below median = higher score
      - Price per sqft (30%): lower $/sqft = higher score
      - Subway proximity (30%): closer = higher score

    Returns dict with score, grade, color, or None if price not parseable.
    """
    price = parse_price(listing.get("price", ""))
    if price is None:
        return None

    # --- Price vs neighborhood median (40%) ---
    hood = listing.get("neighborhood", "")
    median = medians.get(hood)
    if median and median > 0:
        ratio = price / median
        # ratio < 0.8 → 10, ratio = 1.0 → 5, ratio > 1.2 → 0
        price_score = max(0, min(10, (1.2 - ratio) / 0.04))
    else:
        price_score = 5.0  # neutral if no median data

    # --- Price per sqft (30%) ---
    sqft_str = listing.get("sqft", "N/A")
    sqft_match = re.search(r"([\d,]+)", sqft_str.replace(",", ""))
    if sqft_match:
        sqft_val = int(sqft_match.group(1))
        if sqft_val > 0:
            ppsf = price / sqft_val
            # ppsf < $3 → 10, ppsf = $5 → 5, ppsf > $7 → 0
            sqft_score = max(0, min(10, (7 - ppsf) / 0.4))
        else:
            sqft_score = 5.0
    else:
        sqft_score = 5.0  # neutral if no sqft data

    # --- Subway proximity (30%) ---
    if nearby_stations:
        closest = nearby_stations[0]["distance_mi"]
        # 0 mi → 10, 0.25 mi → 5, 0.5 mi → 0
        subway_score = max(0, min(10, (0.5 - closest) / 0.05))
    else:
        subway_score = 5.0  # neutral if no subway data

    score = round(price_score * 0.4 + sqft_score * 0.3 + subway_score * 0.3, 1)

    # Letter grade
    if score >= 8:
        grade = "A"
        color = 0x2ECC71  # Green
    elif score >= 6:
        grade = "B"
        color = 0x27AE60  # Dark green
    elif score >= 4:
        grade = "C"
        color = 0xF39C12  # Yellow/Orange
    elif score >= 2:
        grade = "D"
        color = 0xE67E22  # Orange
    else:
        grade = "F"
        color = 0xE74C3C  # Red

    return {"score": score, "grade": grade, "color": color}


# ---------------------------------------------------------------------------
# Price drop detection
# ---------------------------------------------------------------------------

def detect_price_change(seen_entry: dict, current_price: int) -> dict | None:
    """Compare stored price vs current price. Returns change info or None."""
    old_price_str = seen_entry.get("price", "")
    old_price = parse_price(old_price_str)
    if old_price is None or current_price is None:
        return None
    if current_price >= old_price:
        return None
    savings = old_price - current_price
    pct = round((savings / old_price) * 100, 1)
    return {"old_price": old_price, "new_price": current_price, "savings": savings, "pct": pct}


def update_price_history(seen_entry: dict, new_price: int) -> None:
    """Append a price change to the seen entry's price_history list."""
    if "price_history" not in seen_entry:
        seen_entry["price_history"] = []
    seen_entry["price_history"].append({
        "price": new_price,
        "date": datetime.now(timezone.utc).isoformat(),
    })
    seen_entry["price"] = f"${new_price:,}"


def send_discord_price_drop(webhook_url: str, listing: dict, price_change: dict,
                            config: dict, days_on_market: int | None = None) -> bool:
    """Send an orange Discord embed for a price drop alert."""
    discord_config = config.get("discord", {})
    address = listing.get("address", "Unknown")
    url = listing.get("url", "")
    neighborhood = listing.get("neighborhood", "N/A")
    old_price = price_change["old_price"]
    new_price = price_change["new_price"]
    savings = price_change["savings"]
    pct = price_change["pct"]

    fields = [
        {"name": "💰 Price", "value": f"~~${old_price:,}~~ → **${new_price:,}**", "inline": True},
        {"name": "💵 Savings", "value": f"${savings:,}/mo ({pct}% off)", "inline": True},
        {"name": "📍 Neighborhood", "value": neighborhood, "inline": True},
    ]

    maps_url = build_google_maps_url(address)
    fields.append({"name": "🗺️ Map", "value": f"[View on Google Maps]({maps_url})", "inline": True})

    if days_on_market is not None:
        dom_value = f"{days_on_market} days"
        if days_on_market >= 30:
            dom_value += " (may be negotiable!)"
        fields.append({"name": "📅 Days Tracked", "value": dom_value, "inline": True})

    embed = {
        "title": f"📉 Price Drop! {address}",
        "url": url,
        "color": 0xFF8C00,  # Orange
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "NYC Apartment Tracker • Price Drop"},
    }

    payload = {
        "username": discord_config.get("username", "NYC Apartment Tracker"),
        "avatar_url": discord_config.get("avatar_url", ""),
        "embeds": [embed],
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 5)
            log.warning("Discord rate limit hit, waiting %.1fs", retry_after)
            time.sleep(retry_after)
            resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Failed to send Discord price drop notification: %s", e)
        return False


# ---------------------------------------------------------------------------
# Discord notifications
# ---------------------------------------------------------------------------

def build_listing_embed(listing: dict, days_on_market: int | None = None,
                        value_score: dict | None = None) -> dict:
    """Build a Discord embed dict for a listing (reused by webhook and DM paths)."""
    price = listing.get("price", "N/A")
    address = listing.get("address", "Unknown")
    beds = listing.get("beds", "N/A")
    baths = listing.get("baths", "N/A")
    sqft = listing.get("sqft", "N/A")
    neighborhood = listing.get("neighborhood", "N/A")
    url = listing.get("url", "")
    image_url = listing.get("image_url", "")

    cross_streets = listing.get("cross_streets")

    fields = [
        {"name": "💰 Price", "value": price or "N/A", "inline": True},
        {"name": "🛏️ Beds", "value": beds or "N/A", "inline": True},
        {"name": "🚿 Baths", "value": baths or "N/A", "inline": True},
        {"name": "📐 Size", "value": sqft or "N/A", "inline": True},
        {"name": "📍 Neighborhood", "value": neighborhood or "N/A", "inline": True},
    ]

    maps_url = build_google_maps_url(address)
    fields.append({"name": "🗺️ Map", "value": f"[View on Google Maps]({maps_url})", "inline": True})

    if cross_streets:
        fields.append({"name": "🚦 Cross Streets", "value": cross_streets, "inline": True})

    subway_info = listing.get("subway_info")
    if subway_info:
        fields.append({"name": "🚇 Nearby Subway", "value": subway_info, "inline": False})

    if days_on_market is not None:
        dom_value = f"{days_on_market} days"
        if days_on_market >= 30:
            dom_value += " (may be negotiable!)"
        fields.append({"name": "📅 Days Tracked", "value": dom_value, "inline": True})

    embed_color = 0x00B4D8
    if value_score is not None:
        score = value_score["score"]
        grade = value_score["grade"]
        fields.append({"name": "📊 Value Score", "value": f"{score}/10 (Grade: {grade})", "inline": True})
        embed_color = value_score.get("color", embed_color)

    embed = {
        "title": f"🏠 {address}",
        "url": url,
        "color": embed_color,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "NYC Apartment Tracker • StreetEasy"},
    }

    if image_url and image_url.startswith("http"):
        embed["image"] = {"url": image_url}

    return embed


def send_discord_notification(webhook_url: str, listing: dict, config: dict,
                              days_on_market: int | None = None,
                              value_score: dict | None = None) -> bool:
    """Send a Discord embed for a new listing."""
    discord_config = config.get("discord", {})

    embed = build_listing_embed(listing, days_on_market, value_score)

    payload = {
        "username": discord_config.get("username", "NYC Apartment Tracker"),
        "avatar_url": discord_config.get("avatar_url", ""),
        "embeds": [embed],
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 5)
            log.warning("Discord rate limit hit, waiting %.1fs", retry_after)
            time.sleep(retry_after)
            resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 400:
            log.error("Discord 400 Bad Request for %s — response: %s", listing.get("address", "?"), resp.text)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Failed to send Discord notification: %s", e)
        return False


def send_discord_summary(webhook_url: str, new_listings: list[dict], config: dict) -> bool:
    """Send a single summary notification for the first run instead of flooding."""
    discord_config = config.get("discord", {})

    # Count by neighborhood
    by_neighborhood: dict[str, int] = {}
    for l in new_listings:
        n = l.get("neighborhood", "Unknown")
        by_neighborhood[n] = by_neighborhood.get(n, 0) + 1

    neighborhood_lines = "\n".join(
        f"• **{name}**: {count} listings"
        for name, count in sorted(by_neighborhood.items(), key=lambda x: -x[1])
    )

    # Price range
    prices = []
    for l in new_listings:
        p = parse_price(l["price"])
        if p:
            prices.append(p)
    price_range = f"${min(prices):,} – ${max(prices):,}" if prices else "N/A"

    embed = {
        "title": "🚀 Apartment Tracker Started",
        "description": (
            f"Found **{len(new_listings)} listings** matching your criteria. "
            f"These have been saved — you'll only be notified about **new** listings from now on.\n\n"
            f"**Price range:** {price_range}\n\n"
            f"**By neighborhood:**\n{neighborhood_lines}"
        ),
        "color": 0x2ECC71,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "NYC Apartment Tracker • First Run Summary"},
    }

    payload = {
        "username": discord_config.get("username", "NYC Apartment Tracker"),
        "avatar_url": discord_config.get("avatar_url", ""),
        "embeds": [embed],
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Failed to send Discord summary: %s", e)
        return False


# ---------------------------------------------------------------------------
# Discord DM sending (via bot token, not webhook)
# ---------------------------------------------------------------------------

DISCORD_API_BASE = "https://discord.com/api/v10"


def send_discord_dm(bot_token: str, user_id: str, embed: dict) -> bool:
    """Send a DM to a Discord user via the bot token REST API.

    1. Create/get DM channel with the user
    2. Send the embed message to that channel
    """
    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type": "application/json",
    }

    # Step 1: Create DM channel
    try:
        resp = requests.post(
            f"{DISCORD_API_BASE}/users/@me/channels",
            json={"recipient_id": user_id},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        channel_id = resp.json()["id"]
    except requests.RequestException as e:
        log.error("Failed to create DM channel for user %s: %s", user_id, e)
        return False

    # Step 2: Send message
    try:
        resp = requests.post(
            f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
            json={"embeds": [embed]},
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 5)
            log.warning("Discord DM rate limit, waiting %.1fs", retry_after)
            time.sleep(retry_after)
            resp = requests.post(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                json={"embeds": [embed]},
                headers=headers,
                timeout=10,
            )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Failed to send DM to user %s: %s", user_id, e)
        return False


def get_neighborhoods_to_scrape(config: dict) -> list[str]:
    """Return union of config neighborhoods + all user-subscribed neighborhoods."""
    neighborhoods = set(config["search"]["neighborhoods"])

    if _use_mongodb():
        import db as db_module
        users = db_module.get_all_subscribed_users()
        for user in users:
            user_hoods = user.get("filters", {}).get("neighborhoods", [])
            neighborhoods.update(user_hoods)

    return sorted(neighborhoods)


def send_personalized_notifications(
    new_listings: list[dict],
    price_drops: list[dict],
    seen: dict,
    medians: dict,
    bot_token: str,
) -> int:
    """Send personalized DMs to each subscribed user based on their filters.

    Args:
        new_listings: List of new listing dicts (with enrichment data).
        price_drops: List of dicts with keys: listing, price_change, days_on_market.
        seen: Current seen listings dict.
        medians: Neighborhood median prices.
        bot_token: Discord bot token for sending DMs.

    Returns:
        Total number of DMs sent.
    """
    import db as db_module
    from models import listing_matches_user

    users = db_module.get_all_subscribed_users()
    if not users:
        log.info("No subscribed users — skipping personalized notifications")
        return 0

    total_sent = 0

    for user in users:
        user_id = user["discord_user_id"]
        notif_settings = user.get("notification_settings", {})

        # --- New listing DMs ---
        if notif_settings.get("new_listings", True):
            for listing in new_listings:
                if not listing_matches_user(listing, user):
                    continue
                # Dedup check
                if db_module.was_notification_sent(user_id, listing["url"], "new_listing"):
                    continue

                nearby = None
                if listing.get("latitude") and listing.get("longitude"):
                    stations = _load_subway_stations()
                    nearby = find_nearby_stations(listing["latitude"], listing["longitude"], stations)

                vs = compute_value_score(listing, medians, nearby)
                embed = build_listing_embed(listing, value_score=vs)

                success = send_discord_dm(bot_token, user_id, embed)
                db_module.log_notification(user_id, listing["url"], "new_listing", success)
                if success:
                    total_sent += 1
                time.sleep(1)

        # --- Price drop DMs ---
        if notif_settings.get("price_drops", True):
            for drop_info in price_drops:
                listing = drop_info["listing"]
                if not listing_matches_user(listing, user):
                    continue
                listing_url = listing.get("url", "")
                if db_module.was_notification_sent(user_id, listing_url, "price_drop"):
                    continue

                price_change = drop_info["price_change"]
                dom = drop_info.get("days_on_market")
                # Build price drop embed inline
                address = listing.get("address", "Unknown")
                old_price = price_change["old_price"]
                new_price = price_change["new_price"]
                savings = price_change["savings"]
                pct = price_change["pct"]
                neighborhood = listing.get("neighborhood", "N/A")

                fields = [
                    {"name": "💰 Price", "value": f"~~${old_price:,}~~ → **${new_price:,}**", "inline": True},
                    {"name": "💵 Savings", "value": f"${savings:,}/mo ({pct}% off)", "inline": True},
                    {"name": "📍 Neighborhood", "value": neighborhood, "inline": True},
                ]
                maps_url = build_google_maps_url(address)
                fields.append({"name": "🗺️ Map", "value": f"[View on Google Maps]({maps_url})", "inline": True})
                if dom is not None:
                    dom_value = f"{dom} days"
                    if dom >= 30:
                        dom_value += " (may be negotiable!)"
                    fields.append({"name": "📅 Days Tracked", "value": dom_value, "inline": True})

                embed = {
                    "title": f"📉 Price Drop! {address}",
                    "url": listing_url,
                    "color": 0xFF8C00,
                    "fields": fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": "NYC Apartment Tracker • Price Drop"},
                }

                success = send_discord_dm(bot_token, user_id, embed)
                db_module.log_notification(user_id, listing_url, "price_drop", success)
                if success:
                    total_sent += 1
                time.sleep(1)

    log.info("Sent %d personalized DMs to %d users", total_sent, len(users))
    return total_sent


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_scraper():
    """Main scraper flow — scrape StreetEasy and send Discord notifications."""
    log.info("=" * 60)
    log.info("NYC Apartment Tracker starting at %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    # Load config
    config = load_config()
    search = config["search"]

    # Scrape union of config neighborhoods + all user-subscribed neighborhoods
    neighborhoods = get_neighborhoods_to_scrape(config)
    log.info("Config: %d neighborhoods, max $%s, beds=%s",
             len(neighborhoods), search["max_price"], search["bed_rooms"])

    # Load seen listings
    seen = load_seen()
    log.info("Previously seen: %d listings", len(seen))

    # Discord webhook
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        log.warning("DISCORD_WEBHOOK_URL not set — will scrape but skip notifications")

    # NYC Geoclient API key (for cross street lookups)
    geoclient_key = os.environ.get("NYC_GEOCLIENT_KEY", "")
    if not geoclient_key:
        log.info("NYC_GEOCLIENT_KEY not set — cross streets will be omitted")

    # Detect first run (empty seen_listings.json)
    is_first_run = len(seen) == 0

    if is_first_run:
        log.info("First run detected — will send summary instead of individual notifications")

    # Discord bot token (for per-user DMs)
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if bot_token and _use_mongodb():
        log.info("Per-user DM notifications enabled (bot token + MongoDB)")

    # Scrape
    session = get_session(config)
    delay = config["scraper"]["request_delay_seconds"]
    new_count = 0
    price_drop_count = 0
    total_found = 0
    new_listings = []
    price_drops = []  # Collected for per-user DMs

    # Pre-compute neighborhood medians for value scoring
    medians = compute_neighborhood_medians(seen)

    for i, neighborhood in enumerate(neighborhoods):
        if i > 0:
            time.sleep(delay)

        listings = scrape_neighborhood(session, neighborhood, config)
        total_found += len(listings)

        for listing in listings:
            url = listing["url"]
            current_price = parse_price(listing["price"])

            # --- Price drop detection for seen listings ---
            if url in seen:
                # Update last_scraped timestamp
                seen[url]["last_scraped"] = datetime.now(timezone.utc).isoformat()

                # Lazy geo backfill for old entries missing coordinates
                if geoclient_key and "latitude" not in seen[url]:
                    geo = geoclient_lookup(seen[url].get("address", ""), geoclient_key)
                    if geo and geo["latitude"] and geo["longitude"]:
                        seen[url]["latitude"] = geo["latitude"]
                        seen[url]["longitude"] = geo["longitude"]
                    if geo and not is_within_geo_bounds(geo.get("longitude"), config):
                        log.info("REMOVING (geo bounds): %s", seen[url].get("address"))
                        del seen[url]
                        continue

                if current_price is not None and not is_first_run:
                    change = detect_price_change(seen[url], current_price)
                    if change:
                        price_drop_count += 1
                        log.info("PRICE DROP: %s — $%d → $%d (%s%%)",
                                 seen[url].get("address", "?"),
                                 change["old_price"], change["new_price"], change["pct"])
                        dom = compute_days_on_market(seen[url].get("first_seen"))
                        # Build listing-like dict for the notification
                        drop_listing = {
                            "address": seen[url].get("address", "Unknown"),
                            "url": url,
                            "neighborhood": seen[url].get("neighborhood", "N/A"),
                        }
                        # Send webhook notification
                        if webhook_url:
                            send_discord_price_drop(webhook_url, drop_listing, change,
                                                    config, days_on_market=dom)
                            time.sleep(1)
                        # Collect for per-user DMs
                        price_drops.append({
                            "listing": drop_listing,
                            "price_change": change,
                            "days_on_market": dom,
                        })
                        update_price_history(seen[url], current_price)
                continue

            # New listing — geoclient lookup for geo filtering + enrichment
            geo = None
            nearby = None
            if geoclient_key:
                try:
                    geo = geoclient_lookup(listing["address"], geoclient_key)
                except Exception as e:
                    log.warning("Failed geoclient lookup for %s: %s", listing["address"], e)

            # Geographic bounds filter — skip listings outside the bounding box
            longitude = geo["longitude"] if geo else None
            if not is_within_geo_bounds(longitude, config):
                log.info("FILTERED (geo bounds): %s — lon=%.4f outside [%.3f, %.3f]",
                         listing["address"], longitude,
                         config["search"]["geo_bounds"]["west_longitude"],
                         config["search"]["geo_bounds"]["east_longitude"])
                continue

            new_count += 1
            new_listings.append(listing)
            log.info("NEW: %s — %s — %s", listing["price"], listing["address"], listing["neighborhood"])

            seen_entry = {
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "last_scraped": datetime.now(timezone.utc).isoformat(),
                "address": listing["address"],
                "price": listing["price"],
                "neighborhood": listing.get("neighborhood", ""),
            }

            # Enrich with geo data
            if geo:
                listing["cross_streets"] = geo["cross_streets"]
                if geo["latitude"] and geo["longitude"]:
                    seen_entry["latitude"] = geo["latitude"]
                    seen_entry["longitude"] = geo["longitude"]
                    listing["latitude"] = geo["latitude"]
                    listing["longitude"] = geo["longitude"]
                    stations = _load_subway_stations()
                    nearby = find_nearby_stations(geo["latitude"], geo["longitude"], stations)
                    if nearby:
                        listing["subway_info"] = _format_subway_field(nearby)

            seen[url] = seen_entry

            # Send Discord webhook notification for non-first-run listings
            if not is_first_run and webhook_url:
                vs = compute_value_score(listing, medians, nearby)
                send_discord_notification(webhook_url, listing, config,
                                          days_on_market=None, value_score=vs)
                time.sleep(1)  # Rate-limit Discord messages

    # On first run, send a single summary instead
    if is_first_run and webhook_url and new_listings:
        send_discord_summary(webhook_url, new_listings, config)
        log.info("Sent first-run summary notification (%d listings)", len(new_listings))

    # Send personalized DMs to subscribed users
    if not is_first_run and bot_token and _use_mongodb():
        dm_count = send_personalized_notifications(
            new_listings, price_drops, seen, medians, bot_token,
        )
        log.info("Sent %d personalized DMs", dm_count)

    # Cleanup stale listings (before closing session)
    removed = cleanup_stale_listings(session, seen, config, geoclient_key)
    if removed:
        log.info("Cleaned up %d stale/rented listing(s)", removed)

    session.close()

    # Save seen listings
    save_seen(seen)

    log.info("-" * 60)
    log.info("Done. Found %d total listings, %d new, %d price drops.", total_found, new_count, price_drop_count)
    log.info("Tracking %d listings total.", len(seen))

    # Set GitHub Actions output
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"new_listings={new_count}\n")
            f.write(f"total_found={total_found}\n")


def compute_digest_analytics(seen: dict, recent_listings: list[dict]) -> dict:
    """Compute analytics for the daily digest.

    Returns dict with:
      - avg_by_hood: {neighborhood: avg_price}
      - price_trends: {neighborhood: "up"/"down"/"stable"}
      - top_deals: list of top 5 best deals by value score
      - stale_listings: listings tracked 30+ days (negotiation targets)
      - total_tracked: total listings being tracked
      - overall_avg: overall average price
    """
    now = datetime.now(timezone.utc)
    cutoff_7d = (now - timedelta(days=7)).isoformat()
    cutoff_14d = (now - timedelta(days=14)).isoformat()

    # Average price by neighborhood (all tracked)
    prices_by_hood: dict[str, list[float]] = {}
    all_prices: list[float] = []
    for entry in seen.values():
        hood = entry.get("neighborhood", "")
        if not hood:
            continue
        price = parse_price(entry.get("price", ""))
        if price is not None:
            prices_by_hood.setdefault(hood, []).append(float(price))
            all_prices.append(float(price))

    avg_by_hood = {}
    for hood, prices in sorted(prices_by_hood.items()):
        avg_by_hood[hood] = round(sum(prices) / len(prices))

    overall_avg = round(sum(all_prices) / len(all_prices)) if all_prices else 0

    # Price trends: compare last 7 days vs previous 7 days
    recent_by_hood: dict[str, list[float]] = {}
    prev_by_hood: dict[str, list[float]] = {}
    for entry in seen.values():
        hood = entry.get("neighborhood", "")
        first_seen = entry.get("first_seen", "")
        price = parse_price(entry.get("price", ""))
        if not hood or price is None or not first_seen:
            continue
        if first_seen >= cutoff_7d:
            recent_by_hood.setdefault(hood, []).append(float(price))
        elif first_seen >= cutoff_14d:
            prev_by_hood.setdefault(hood, []).append(float(price))

    price_trends = {}
    all_hoods = set(recent_by_hood.keys()) | set(prev_by_hood.keys())
    for hood in all_hoods:
        recent_avg = sum(recent_by_hood.get(hood, [])) / len(recent_by_hood[hood]) if recent_by_hood.get(hood) else None
        prev_avg = sum(prev_by_hood.get(hood, [])) / len(prev_by_hood[hood]) if prev_by_hood.get(hood) else None
        if recent_avg is not None and prev_avg is not None and prev_avg > 0:
            change_pct = ((recent_avg - prev_avg) / prev_avg) * 100
            if change_pct > 2:
                price_trends[hood] = "up"
            elif change_pct < -2:
                price_trends[hood] = "down"
            else:
                price_trends[hood] = "stable"

    # Top 5 best deals by value score
    medians = compute_neighborhood_medians(seen)
    scored_listings = []
    for url, entry in seen.items():
        price = parse_price(entry.get("price", ""))
        if price is None:
            continue
        fake_listing = {
            "price": entry.get("price", ""),
            "neighborhood": entry.get("neighborhood", ""),
            "sqft": "N/A",
        }
        vs = compute_value_score(fake_listing, medians)
        if vs:
            scored_listings.append({
                "url": url,
                "address": entry.get("address", "Unknown"),
                "price": entry.get("price", "N/A"),
                "neighborhood": entry.get("neighborhood", ""),
                "score": vs["score"],
                "grade": vs["grade"],
            })
    scored_listings.sort(key=lambda x: -x["score"])
    top_deals = scored_listings[:5]

    # Stale listings (30+ days)
    stale_listings = []
    for url, entry in seen.items():
        dom = compute_days_on_market(entry.get("first_seen"))
        if dom is not None and dom >= 30:
            stale_listings.append({
                "url": url,
                "address": entry.get("address", "Unknown"),
                "price": entry.get("price", "N/A"),
                "neighborhood": entry.get("neighborhood", ""),
                "days": dom,
            })
    stale_listings.sort(key=lambda x: -x["days"])

    return {
        "avg_by_hood": avg_by_hood,
        "price_trends": price_trends,
        "top_deals": top_deals,
        "stale_listings": stale_listings[:10],
        "total_tracked": len(seen),
        "overall_avg": overall_avg,
    }


def send_discord_digest(webhook_url: str, listings: list[dict], config: dict,
                        analytics: dict | None = None) -> bool:
    """Send a daily digest embed summarizing listings found in the last 24 hours."""
    discord_config = config.get("discord", {})

    # Group by neighborhood
    by_neighborhood: dict[str, list[dict]] = {}
    for entry in listings:
        hood = entry.get("neighborhood", "Unknown") or "Unknown"
        by_neighborhood.setdefault(hood, []).append(entry)

    # Build neighborhood summary lines
    hood_lines = []
    for hood in sorted(by_neighborhood, key=lambda h: -len(by_neighborhood[h])):
        entries = by_neighborhood[hood]
        prices = [parse_price(e.get("price", "")) for e in entries]
        prices = [p for p in prices if p]
        if prices:
            price_str = f"${min(prices):,}–${max(prices):,}" if len(prices) > 1 else f"${prices[0]:,}"
        else:
            price_str = "N/A"
        hood_lines.append(f"• **{hood}**: {len(entries)} listing(s) — {price_str}")

    today_str = datetime.now(timezone.utc).strftime("%b %d, %Y")
    new_listings_desc = "\n".join(hood_lines) if hood_lines else "No new listings today."

    # Build full description with analytics
    sections = [
        f"**{len(listings)} new listing(s)** found in the last 24 hours.\n",
        new_listings_desc,
    ]

    if analytics:
        # Market summary
        total = analytics.get("total_tracked", 0)
        avg = analytics.get("overall_avg", 0)
        if total and avg:
            sections.append(f"\n**Market Summary**: {total} listings tracked, avg ${avg:,}/mo")

        # Average by neighborhood
        avg_by_hood = analytics.get("avg_by_hood", {})
        trends = analytics.get("price_trends", {})
        if avg_by_hood:
            avg_lines = []
            for hood, avg_price in sorted(avg_by_hood.items()):
                trend = trends.get(hood, "")
                trend_icon = {"up": " \u2191", "down": " \u2193", "stable": " \u2192"}.get(trend, "")
                avg_lines.append(f"• {hood}: ${avg_price:,}{trend_icon}")
            sections.append("\n**Avg Price by Neighborhood:**\n" + "\n".join(avg_lines))

        # Top deals
        top_deals = analytics.get("top_deals", [])
        if top_deals:
            deal_lines = []
            for d in top_deals:
                deal_lines.append(
                    f"• [{d['address']}]({d['url']}) — {d['price']} ({d['grade']}, {d['score']}/10)"
                )
            sections.append("\n**Top 5 Best Deals:**\n" + "\n".join(deal_lines))

        # Stale listings
        stale = analytics.get("stale_listings", [])
        if stale:
            stale_lines = []
            for s in stale[:5]:
                stale_lines.append(
                    f"• [{s['address']}]({s['url']}) — {s['price']} ({s['days']}d)"
                )
            sections.append("\n**Negotiation Targets (30+ days):**\n" + "\n".join(stale_lines))

    description = "\n".join(sections)
    # Discord embed description limit is 4096 chars
    if len(description) > 4096:
        description = description[:4093] + "..."

    embed = {
        "title": f"\U0001f4ca Daily Digest \u2014 {today_str}",
        "description": description,
        "color": 0x3498DB,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "NYC Apartment Tracker \u2022 Daily Digest"},
    }

    payload = {
        "username": discord_config.get("username", "NYC Apartment Tracker"),
        "avatar_url": discord_config.get("avatar_url", ""),
        "embeds": [embed],
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.error("Failed to send Discord digest: %s", e)
        return False


def run_digest():
    """Daily digest — summarize listings found in the last 24 hours."""
    log.info("=" * 60)
    log.info("NYC Apartment Tracker — Daily Digest at %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    config = load_config()
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "")

    if not webhook_url and not (bot_token and _use_mongodb()):
        log.error("No notification method configured — cannot send digest")
        return

    seen = load_seen()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    recent = []
    for url, entry in seen.items():
        first_seen = entry.get("first_seen", "")
        if first_seen >= cutoff:
            recent.append({
                "url": url,
                "address": entry.get("address", "Unknown"),
                "price": entry.get("price", "N/A"),
                "neighborhood": entry.get("neighborhood", "Unknown"),
            })

    log.info("Found %d listings in the last 24 hours (out of %d total)", len(recent), len(seen))

    # Always send digest — analytics are valuable even with 0 new listings
    analytics = compute_digest_analytics(seen, recent)

    # Send webhook digest (original behavior)
    if webhook_url:
        send_discord_digest(webhook_url, recent, config, analytics=analytics)
        log.info("Sent daily digest with %d new listings and analytics", len(recent))

    # Send per-user digest DMs
    if bot_token and _use_mongodb():
        import db as db_module
        from models import listing_matches_user

        users = db_module.get_all_subscribed_users()
        dm_count = 0
        for user in users:
            notif_settings = user.get("notification_settings", {})
            if not notif_settings.get("daily_digest", True):
                continue

            # Filter recent listings to those matching user preferences
            user_recent = [l for l in recent if listing_matches_user(l, user)]
            user_analytics = compute_digest_analytics(seen, user_recent)

            # Build digest embed for this user
            from datetime import datetime as _dt
            today_str = _dt.now(timezone.utc).strftime("%b %d, %Y")

            by_hood: dict[str, list[dict]] = {}
            for entry in user_recent:
                hood = entry.get("neighborhood", "Unknown") or "Unknown"
                by_hood.setdefault(hood, []).append(entry)

            hood_lines = []
            for hood in sorted(by_hood, key=lambda h: -len(by_hood[h])):
                entries = by_hood[hood]
                prices = [parse_price(e.get("price", "")) for e in entries]
                prices = [p for p in prices if p]
                if prices:
                    price_str = f"${min(prices):,}–${max(prices):,}" if len(prices) > 1 else f"${prices[0]:,}"
                else:
                    price_str = "N/A"
                hood_lines.append(f"• **{hood}**: {len(entries)} listing(s) — {price_str}")

            desc = f"**{len(user_recent)} new listing(s)** matching your filters in the last 24 hours.\n\n"
            desc += "\n".join(hood_lines) if hood_lines else "No matching listings today."

            if user_analytics.get("total_tracked"):
                desc += f"\n\n**Total tracked**: {user_analytics['total_tracked']} listings"

            if len(desc) > 4096:
                desc = desc[:4093] + "..."

            embed = {
                "title": f"\U0001f4ca Daily Digest — {today_str}",
                "description": desc,
                "color": 0x3498DB,
                "timestamp": _dt.now(timezone.utc).isoformat(),
                "footer": {"text": "NYC Apartment Tracker • Daily Digest"},
            }

            user_id = user["discord_user_id"]
            if db_module.was_notification_sent(user_id, f"digest-{today_str}", "daily_digest"):
                continue
            success = send_discord_dm(bot_token, user_id, embed)
            db_module.log_notification(user_id, f"digest-{today_str}", "daily_digest", success)
            if success:
                dm_count += 1
            time.sleep(1)

        log.info("Sent %d per-user digest DMs", dm_count)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NYC Apartment Tracker")
    parser.add_argument("--digest", action="store_true", help="Send daily digest instead of scraping")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.digest:
        run_digest()
    else:
        run_scraper()


if __name__ == "__main__":
    main()
