"""RentHop scraper — parallel source to StreetEasy for NYC apartment listings.

RentHop serves fully server-rendered HTML (no JS rendering needed).
Listing cards use class 'search-listing' with id 'listing-{id}'.
URL format: /apartments-for-rent/{area-slug}?max_price=...&bedrooms[]=...&sort=hoppiness
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlencode, quote

from bs4 import BeautifulSoup

log = logging.getLogger("apartment_tracker.renthop")

RENTHOP_BASE = "https://www.renthop.com"

# Maps StreetEasy neighborhood slugs → RentHop area URL slugs
# Format is typically {neighborhood}-{borough}-ny
# (Manhattan uses "new-york", Brooklyn uses "brooklyn", Queens uses "queens")
RENTHOP_AREA_MAP: dict[str, str] = {
    # Manhattan
    "east-village":        "east-village-new-york-ny",
    "west-village":        "west-village-new-york-ny",
    "upper-west-side":     "upper-west-side-new-york-ny",
    "chelsea":             "chelsea-new-york-ny",
    "les":                 "lower-east-side-new-york-ny",
    "upper-east-side":     "upper-east-side-new-york-ny",
    "hells-kitchen":       "hells-kitchen-new-york-ny",
    "murray-hill":         "murray-hill-new-york-ny",
    "gramercy-park":       "gramercy-park-new-york-ny",
    "flatiron":            "flatiron-district-new-york-ny",
    "kips-bay":            "kips-bay-new-york-ny",
    "greenwich-village":   "greenwich-village-new-york-ny",
    "soho":                "soho-new-york-ny",
    "tribeca":             "tribeca-new-york-ny",
    "financial-district":  "financial-district-new-york-ny",
    "harlem":              "harlem-new-york-ny",
    "washington-heights":  "washington-heights-new-york-ny",
    "inwood":              "inwood-new-york-ny",
    "morningside-heights": "morningside-heights-new-york-ny",
    "noho":                "noho-new-york-ny",
    "nolita":              "nolita-new-york-ny",
    "midtown":             "midtown-new-york-ny",
    "battery-park-city":   "battery-park-city-new-york-ny",
    "stuyvesant-town":     "stuyvesant-town-new-york-ny",
    "east-harlem":         "east-harlem-new-york-ny",
    "hamilton-heights":    "hamilton-heights-new-york-ny",
    # Brooklyn
    "williamsburg":        "williamsburg-brooklyn-ny",
    "greenpoint":          "greenpoint-brooklyn-ny",
    "park-slope":          "park-slope-brooklyn-ny",
    "bushwick":            "bushwick-brooklyn-ny",
    "bed-stuy":            "bedford-stuyvesant-brooklyn-ny",
    "brooklyn-heights":    "brooklyn-heights-brooklyn-ny",
    "cobble-hill":         "cobble-hill-brooklyn-ny",
    "carroll-gardens":     "carroll-gardens-brooklyn-ny",
    "boerum-hill":         "boerum-hill-brooklyn-ny",
    "fort-greene":         "fort-greene-brooklyn-ny",
    "clinton-hill":        "clinton-hill-brooklyn-ny",
    "crown-heights":       "crown-heights-brooklyn-ny",
    "prospect-heights":    "prospect-heights-brooklyn-ny",
    "dumbo":               "dumbo-brooklyn-ny",
    "red-hook":            "red-hook-brooklyn-ny",
    "sunset-park":         "sunset-park-brooklyn-ny",
    "bay-ridge":           "bay-ridge-brooklyn-ny",
    "gowanus":             "gowanus-brooklyn-ny",
    "downtown-brooklyn":   "downtown-brooklyn-brooklyn-ny",
    # Queens
    "astoria":             "astoria-queens-ny",
    "long-island-city":    "long-island-city-queens-ny",
    "jackson-heights":     "jackson-heights-queens-ny",
    "sunnyside":           "sunnyside-queens-ny",
    "woodside":            "woodside-queens-ny",
    "ridgewood":           "ridgewood-queens-ny",
    "flushing":            "flushing-queens-ny",
    "forest-hills":        "forest-hills-queens-ny",
}

# Keep backward compat alias
RENTHOP_NEIGHBORHOOD_MAP = RENTHOP_AREA_MAP


def build_renthop_search_url(neighborhood_slug: str, config: dict, page: int = 1) -> str | None:
    """Build a RentHop search URL for a neighborhood with price/beds filters.

    Returns None if the neighborhood slug is not supported by RentHop.

    URL format:
        https://www.renthop.com/apartments-for-rent/{area}?max_price=...&bedrooms[]=...&page=N&sort=hoppiness
    """
    area = RENTHOP_AREA_MAP.get(neighborhood_slug)
    if not area:
        return None

    search = config.get("search", {})
    min_price = search.get("min_price", 0) or 0
    max_price = search.get("max_price", 0) or 0
    beds_list = search.get("bed_rooms", [])
    no_fee = search.get("no_fee", False)

    params: list[tuple[str, str]] = []

    if min_price:
        params.append(("min_price", str(min_price)))
    if max_price:
        params.append(("max_price", str(max_price)))

    # RentHop uses bedrooms[]=0 for studio, 1 for 1BR, etc.
    for bed in beds_list:
        if str(bed).lower() == "studio":
            params.append(("bedrooms[]", "0"))
        else:
            num = re.search(r"(\d+)", str(bed))
            if num:
                params.append(("bedrooms[]", num.group(1)))

    if page > 1:
        params.append(("page", str(page)))

    params.append(("sort", "hoppiness"))

    if no_fee:
        params.append(("no_fee", "1"))

    return f"{RENTHOP_BASE}/apartments-for-rent/{area}?{urlencode(params)}"


def _extract_unit_from_renthop_url(href: str) -> str:
    """Extract the unit identifier from a RentHop listing URL, or '' if none.

    URL format: /listings/{street-slug}/{unit}/{listing_id}
    Example: /listings/east-10th-street-new-york-ny-10009/2b/75908135 → "2B"
    Example: /listings/east-14th-street/10/75132142 → "" (10 is numeric, skip)
    Example: /listings/246-west-22nd-street/na/75... → "" (na means no unit)
    """
    path = href.replace(RENTHOP_BASE, "")
    m = re.match(r"/listings/(.+)", path)
    if not m:
        return ""
    parts = m.group(1).rstrip("/").split("/")
    # parts[0]=address slug, parts[1]=unit (optional), parts[-1]=listing_id (numeric)
    if len(parts) < 3:
        return ""
    unit_candidate = parts[1].strip().lower()
    # Skip non-units: purely numeric (apt number without letter), "na", empty
    if not unit_candidate or unit_candidate == "na" or unit_candidate.isdigit():
        return ""
    return unit_candidate.upper()


def _parse_renthop_card(card, neighborhood_slug: str) -> dict | None:
    """Parse a single RentHop search-listing card.

    Card structure (confirmed from live HTML):
      <div class="search-listing" id="listing-{id}" listing_id="{id}"
           latitude="{lat}" longitude="{lon}">
        <a href="/listings/{slug}">...</a>
        <div id="listing-{id}-price">$3,995</div>
        <div id="listing-{id}-neighborhoods">Alphabet City, East Village, ...</div>
        <div id="listing-{id}-info">... 1 Bed | 1 Bath ...</div>
        <img src="...">
      </div>
    """
    listing_id = card.get("listing_id") or card.get("id", "").replace("listing-", "")
    if not listing_id:
        return None

    # URL — canonical listing page
    link = card.find("a", href=re.compile(r"/listings/"))
    if not link:
        return None
    href = link.get("href", "")
    if not href.startswith("http"):
        href = RENTHOP_BASE + href
    # Strip query params
    clean_url = re.sub(r"\?.*$", "", href)

    # Address: best source is the title link text (already properly cased by RentHop)
    # e.g. "East 10th Street, New York, NY..." or "East 14th Street"
    title_link = card.find("a", id=f"listing-{listing_id}-title")
    if not title_link:
        title_link = link
    address = title_link.get_text(separator=" ", strip=True)
    # Strip ", New York, NY..." / ", Brooklyn, NY..." suffix and trailing ellipsis
    address = re.sub(r",\s*(New York|Brooklyn|Queens|Bronx|Staten Island),?\s*NY.*$", "", address, flags=re.I).strip()
    address = address.rstrip(".").strip()

    # Append unit from URL only if address doesn't already include one
    unit = _extract_unit_from_renthop_url(href)
    if unit and not re.search(r"(?:#|apt\.?|unit)\s*\S+$", address, re.I):
        address = f"{address} #{unit}"

    if not address:
        return None

    # Price
    price = "N/A"
    price_el = card.find("div", id=f"listing-{listing_id}-price")
    if price_el:
        price_text = price_el.get_text(strip=True)
        if "$" in price_text:
            price = price_text

    # Neighborhood — use our canonical slug's display name so listing_matches_user() works
    from models import VALID_NEIGHBORHOODS
    neighborhood_display = VALID_NEIGHBORHOODS.get(neighborhood_slug, neighborhood_slug.replace("-", " ").title())

    # Beds and baths: in 'font-size-10 d-inline-block align-bottom' divs scattered
    # through the card (outside the info div). Collect all such div texts.
    beds = "N/A"
    baths = "N/A"
    detail_divs = card.find_all("div", class_=lambda c: c and "font-size-10" in c and "align-bottom" in c)
    for div in detail_divs:
        text = div.get_text(strip=True)
        if re.search(r"\bbed\b", text, re.I):
            m = re.search(r"(\d+)\s*bed", text, re.I)
            beds = f"{m.group(1)} bed" if m else "Studio"
        elif re.search(r"\bstudio\b", text, re.I):
            beds = "Studio"
        elif re.search(r"\bbath\b", text, re.I):
            m = re.search(r"(\d+(?:\.\d+)?)\s*bath", text, re.I)
            if m:
                baths = f"{m.group(1)} bath"

    # Sqft — RentHop rarely shows this in search results but check just in case
    sqft = "N/A"
    card_text = card.get_text(separator=" ", strip=True)
    sqft_match = re.search(r"([\d,]+)\s*(?:sq\.?\s*ft|ft²|sqft)", card_text, re.I)
    if sqft_match:
        sqft = f"{sqft_match.group(1)} ft²"

    # Latitude / longitude — available directly as attributes
    lat_str = card.get("latitude")
    lon_str = card.get("longitude")
    latitude = float(lat_str) if lat_str else None
    longitude = float(lon_str) if lon_str else None

    # Image
    image_url = ""
    img = card.find("img", class_="search-thumb")
    if not img:
        img = card.find("img")
    if img:
        image_url = img.get("src", img.get("data-src", ""))

    result = {
        "url": clean_url,
        "address": address,
        "price": price,
        "beds": beds,
        "baths": baths,
        "sqft": sqft,
        "neighborhood": neighborhood_display,
        "image_url": image_url,
        "source": "renthop",
    }
    if latitude is not None:
        result["latitude"] = latitude
    if longitude is not None:
        result["longitude"] = longitude

    return result


def _get_max_page(soup: BeautifulSoup, area: str) -> int:
    """Extract the maximum page number from RentHop pagination links."""
    max_page = 1
    for link in soup.find_all("a", href=re.compile(rf"/apartments-for-rent/{re.escape(area)}")):
        m = re.search(r"[?&]page=(\d+)", link.get("href", ""))
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


def scrape_renthop_neighborhood(
    session,  # ScraperSession from apartment_tracker
    neighborhood_slug: str,
    config: dict,
) -> list[dict]:
    """Scrape RentHop for a neighborhood. Returns canonical listing dicts with source='renthop'.

    Returns empty list if the neighborhood is unsupported or on fetch error.
    """
    area = RENTHOP_AREA_MAP.get(neighborhood_slug)
    if not area:
        log.debug("RentHop does not support neighborhood slug: %s", neighborhood_slug)
        return []

    base_url = build_renthop_search_url(neighborhood_slug, config, page=1)
    delay = config.get("scraper", {}).get("request_delay_seconds", 2)

    log.info("Scraping RentHop %s → %s", neighborhood_slug, base_url)
    soup = session.fetch(base_url)
    if not soup:
        log.warning("RentHop: no response for %s", neighborhood_slug)
        return []

    # Validate we got the right neighborhood page (not the generic NYC fallback)
    title = soup.find("title")
    title_text = title.get_text().lower() if title else ""
    area_display = area.replace("-new-york-ny", "").replace("-brooklyn-ny", "").replace("-queens-ny", "").replace("-", " ")
    if area_display not in title_text and "apartment" not in title_text:
        log.warning("RentHop: unexpected page title for %s: %s", neighborhood_slug, title_text[:80])

    raw_listings: list[dict] = []

    cards = soup.find_all("div", class_="search-listing")
    log.info("  RentHop %s page 1: %d cards", neighborhood_slug, len(cards))
    for card in cards:
        try:
            listing = _parse_renthop_card(card, neighborhood_slug)
            if listing and listing.get("url"):
                raw_listings.append(listing)
        except Exception as e:
            log.debug("Failed to parse RentHop card: %s", e)

    # Pagination — cap at 3 pages to avoid excessive requests
    max_page = min(_get_max_page(soup, area), 3)
    for page in range(2, max_page + 1):
        time.sleep(delay)
        page_url = build_renthop_search_url(neighborhood_slug, config, page=page)
        soup = session.fetch(page_url)
        if not soup:
            break
        cards = soup.find_all("div", class_="search-listing")
        if not cards:
            break
        log.info("  RentHop %s page %d: %d cards", neighborhood_slug, page, len(cards))
        for card in cards:
            try:
                listing = _parse_renthop_card(card, neighborhood_slug)
                if listing and listing.get("url"):
                    raw_listings.append(listing)
            except Exception as e:
                log.debug("Failed to parse RentHop card page %d: %s", page, e)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for listing in raw_listings:
        if listing["url"] not in seen_urls:
            seen_urls.add(listing["url"])
            unique.append(listing)

    # Filter above max price
    max_price = config.get("search", {}).get("max_price", 0)
    if max_price:
        from apartment_tracker import parse_price
        filtered = []
        for listing in unique:
            price_val = parse_price(listing["price"])
            if price_val is not None and price_val > max_price:
                log.debug("RentHop filtered (price): %s (%s)", listing["address"], listing["price"])
                continue
            filtered.append(listing)
        unique = filtered

    log.info("  RentHop %s: %d raw → %d after dedup/filter",
             neighborhood_slug, len(raw_listings), len(unique))
    return unique


def check_renthop_listing_status(session, url: str) -> str:
    """Check if a RentHop listing is still active.

    Returns 'active', 'gone', or 'unknown'.
    """
    soup, status = session.fetch_with_status(url)
    if status is None:
        return "unknown"
    if status == 404:
        return "gone"
    if status == 403:
        return "unknown"  # rate-limited
    if status >= 400:
        return "unknown"
    if soup:
        text = soup.get_text(separator=" ", strip=True).lower()
        if any(phrase in text for phrase in [
            "no longer available",
            "listing has been removed",
            "this listing is no longer",
            "apartment has been rented",
        ]):
            return "gone"
    return "active"
