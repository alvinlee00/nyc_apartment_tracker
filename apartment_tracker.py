#!/usr/bin/env python3
"""NYC Apartment Tracker - Scrapes StreetEasy and sends Discord notifications."""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Browser, Page

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


def load_seen() -> dict:
    if SEEN_PATH.exists():
        with open(SEEN_PATH) as f:
            data = json.load(f)
            if isinstance(data, list):
                # Migrate from old list format to dict format
                return {url: {"first_seen": datetime.now(timezone.utc).isoformat()} for url in data}
            return data
    return {}


def save_seen(seen: dict) -> None:
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

    return f"{STREETEASY_BASE}/for-rent/{neighborhood}/{quote(filters, safe=':|-')}"


class BrowserSession:
    """Manages a Playwright browser for scraping."""

    def __init__(self):
        self._pw = sync_playwright().start()
        self._browser: Browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        self._page: Page = self._context.new_page()

    def close(self):
        self._context.close()
        self._browser.close()
        self._pw.stop()

    def fetch(self, url: str) -> BeautifulSoup | None:
        """Navigate to URL and return parsed soup."""
        try:
            resp = self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if resp and resp.status == 403:
                log.warning("Got 403 for %s — may be rate-limited", url)
                return None
            if resp and resp.status >= 400:
                log.error("HTTP %d for %s", resp.status, url)
                return None
            # Wait for listing cards to appear (or timeout after 5s)
            try:
                self._page.wait_for_selector('[data-testid="listing-card"]', timeout=5000)
            except Exception:
                pass  # Page may have loaded but no cards (e.g. 0 results)
            html = self._page.content()
            return BeautifulSoup(html, "lxml")
        except Exception as e:
            log.error("Failed to fetch %s: %s", url, e)
            return None


def get_session(config: dict) -> BrowserSession:
    """Create a browser session for scraping."""
    return BrowserSession()


def fetch_page(session: BrowserSession, url: str) -> BeautifulSoup | None:
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


def scrape_neighborhood(session: BrowserSession, neighborhood: str, config: dict) -> list[dict]:
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

    # Filter out sponsored listings from unrelated neighborhoods
    allowed = NEIGHBORHOOD_ALIASES.get(neighborhood)
    if allowed:
        before_count = len(filtered)
        filtered = [
            l for l in filtered
            if not l["neighborhood"] or l["neighborhood"] in allowed
        ]
        removed = before_count - len(filtered)
        if removed:
            log.info("  Filtered %d sponsored listing(s) from other neighborhoods", removed)

    if len(raw_listings) != len(filtered):
        log.info("  %s: %d raw → %d unique → %d after filters",
                 neighborhood, len(raw_listings), len(unique_listings), len(filtered))

    return filtered

# ---------------------------------------------------------------------------
# Discord notifications
# ---------------------------------------------------------------------------

def send_discord_notification(webhook_url: str, listing: dict, config: dict) -> bool:
    """Send a Discord embed for a new listing."""
    discord_config = config.get("discord", {})

    price = listing["price"]
    address = listing["address"]
    beds = listing["beds"]
    baths = listing["baths"]
    sqft = listing["sqft"]
    neighborhood = listing["neighborhood"]
    url = listing["url"]
    image_url = listing.get("image_url", "")

    embed = {
        "title": f"🏠 {address}",
        "url": url,
        "color": 0x00B4D8,
        "fields": [
            {"name": "💰 Price", "value": price, "inline": True},
            {"name": "🛏️ Beds", "value": beds, "inline": True},
            {"name": "🚿 Baths", "value": baths, "inline": True},
            {"name": "📐 Size", "value": sqft, "inline": True},
            {"name": "📍 Neighborhood", "value": neighborhood or "N/A", "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "NYC Apartment Tracker • StreetEasy"},
    }

    if image_url:
        embed["image"] = {"url": image_url}

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
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("NYC Apartment Tracker starting at %s", datetime.now(timezone.utc).isoformat())
    log.info("=" * 60)

    # Load config
    config = load_config()
    search = config["search"]
    neighborhoods = search["neighborhoods"]
    log.info("Config: %d neighborhoods, max $%s, beds=%s",
             len(neighborhoods), search["max_price"], search["bed_rooms"])

    # Load seen listings
    seen = load_seen()
    log.info("Previously seen: %d listings", len(seen))

    # Discord webhook
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        log.warning("DISCORD_WEBHOOK_URL not set — will scrape but skip notifications")

    # Detect first run (empty seen_listings.json)
    is_first_run = len(seen) == 0

    if is_first_run:
        log.info("First run detected — will send summary instead of individual notifications")

    # Scrape
    session = get_session(config)
    delay = config["scraper"]["request_delay_seconds"]
    new_count = 0
    total_found = 0
    new_listings = []

    for i, neighborhood in enumerate(neighborhoods):
        if i > 0:
            time.sleep(delay)

        listings = scrape_neighborhood(session, neighborhood, config)
        total_found += len(listings)

        for listing in listings:
            url = listing["url"]
            if url in seen:
                continue

            # New listing!
            new_count += 1
            new_listings.append(listing)
            log.info("NEW: %s — %s — %s", listing["price"], listing["address"], listing["neighborhood"])

            seen[url] = {
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "address": listing["address"],
                "price": listing["price"],
            }

            # Send individual notifications only on subsequent runs
            if not is_first_run and webhook_url:
                send_discord_notification(webhook_url, listing, config)
                time.sleep(1)  # Rate-limit Discord messages

    # On first run, send a single summary instead
    if is_first_run and webhook_url and new_listings:
        send_discord_summary(webhook_url, new_listings, config)
        log.info("Sent first-run summary notification (%d listings)", len(new_listings))

    # Close browser
    session.close()

    # Save seen listings
    save_seen(seen)

    log.info("-" * 60)
    log.info("Done. Found %d total listings, %d new.", total_found, new_count)
    log.info("Tracking %d listings total.", len(seen))

    # Set GitHub Actions output
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"new_listings={new_count}\n")
            f.write(f"total_found={total_found}\n")


if __name__ == "__main__":
    main()
