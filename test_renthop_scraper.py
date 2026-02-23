"""Tests for renthop_scraper.py — URL building, HTML parsing, status checking."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from bs4 import BeautifulSoup

import renthop_scraper as rh


# ---------------------------------------------------------------------------
# build_renthop_search_url
# ---------------------------------------------------------------------------

class TestBuildRentHopSearchUrl:
    def _config(self, **overrides):
        base = {
            "search": {
                "max_price": 4000,
                "min_price": 0,
                "bed_rooms": ["1"],
                "no_fee": False,
            }
        }
        base["search"].update(overrides)
        return base

    def test_supported_neighborhood(self):
        url = rh.build_renthop_search_url("east-village", self._config())
        assert url is not None
        # New URL format uses /apartments-for-rent/{area}
        assert "renthop.com/apartments-for-rent/" in url
        assert "east-village-new-york-ny" in url

    def test_unsupported_neighborhood_returns_none(self):
        url = rh.build_renthop_search_url("nonexistent-hood", self._config())
        assert url is None

    def test_max_price_in_url(self):
        url = rh.build_renthop_search_url("chelsea", self._config(max_price=3500))
        assert url is not None
        assert "max_price=3500" in url

    def test_min_price_in_url_when_nonzero(self):
        url = rh.build_renthop_search_url("chelsea", self._config(min_price=1500))
        assert url is not None
        assert "min_price=1500" in url

    def test_min_price_absent_when_zero(self):
        url = rh.build_renthop_search_url("chelsea", self._config(min_price=0))
        assert url is not None
        assert "min_price" not in url

    def test_studio_bedroom_param(self):
        url = rh.build_renthop_search_url("east-village", self._config(bed_rooms=["studio"]))
        assert url is not None
        # Studio maps to bedrooms[]=0
        assert "bedrooms%5B%5D=0" in url or "bedrooms[]=0" in url

    def test_one_bedroom_param(self):
        url = rh.build_renthop_search_url("east-village", self._config(bed_rooms=["1"]))
        assert url is not None
        assert "bedrooms%5B%5D=1" in url or "bedrooms[]=1" in url

    def test_multi_bedroom_params(self):
        url = rh.build_renthop_search_url("east-village", self._config(bed_rooms=["studio", "1", "2"]))
        assert url is not None
        # All three bed types should be present
        assert "0" in url  # studio
        assert "1" in url
        assert "2" in url

    def test_no_fee_flag(self):
        url = rh.build_renthop_search_url("east-village", self._config(no_fee=True))
        assert url is not None
        assert "no_fee=1" in url

    def test_no_fee_absent_when_false(self):
        url = rh.build_renthop_search_url("east-village", self._config(no_fee=False))
        assert url is not None
        assert "no_fee" not in url

    def test_sort_param_present(self):
        url = rh.build_renthop_search_url("williamsburg", self._config())
        assert url is not None
        assert "sort=hoppiness" in url

    def test_page_2_in_url(self):
        url = rh.build_renthop_search_url("east-village", self._config(), page=2)
        assert url is not None
        assert "page=2" in url

    def test_page_1_not_in_url(self):
        # Page 1 should not include page param
        url = rh.build_renthop_search_url("east-village", self._config(), page=1)
        assert url is not None
        assert "page=" not in url

    def test_brooklyn_neighborhood_uses_brooklyn_area(self):
        url = rh.build_renthop_search_url("williamsburg", self._config())
        assert url is not None
        assert "williamsburg-brooklyn-ny" in url

    def test_queens_neighborhood_uses_queens_area(self):
        url = rh.build_renthop_search_url("astoria", self._config())
        assert url is not None
        assert "astoria-queens-ny" in url

    def test_all_neighborhoods_in_map_return_url(self):
        config = self._config()
        for slug in rh.RENTHOP_AREA_MAP:
            url = rh.build_renthop_search_url(slug, config)
            assert url is not None, f"Expected URL for slug: {slug}"
            assert "renthop.com" in url


# ---------------------------------------------------------------------------
# HTML parsing fixtures — matching actual RentHop search-listing card structure
# ---------------------------------------------------------------------------

def _make_card(listing_id: str, href: str, title: str, neighborhoods: str,
               price: str, beds: str, baths: str,
               lat: str = "40.7261", lon: str = "-73.9784",
               img_src: str = "https://photos.renthop.com/test.webp") -> str:
    """Build a realistic RentHop search-listing card HTML snippet."""
    return f"""
<div class="d-block d-md-flex search-listing my-3 my-md-0 py-0 py-md-4"
     id="listing-{listing_id}" latitude="{lat}" listing_id="{listing_id}" longitude="{lon}">
  <div class="search-photo d-block align-top">
    <a href="{href}" style="text-decoration: none">
      <div class="search-thumbs">
        <img alt="{title} - Photo 1" class="search-thumb align-top"
             id="listing-{listing_id}-img" src="{img_src}"/>
      </div>
    </a>
  </div>
  <div class="search-info d-block align-top">
    <div class="search-info-title">
      <a class="font-size-12 b" href="{href}"
         id="listing-{listing_id}-title" style="text-decoration: none; line-height: 100%;">
        {title}
      </a>
      <div class="font-size-8" id="listing-{listing_id}-neighborhoods">
        {neighborhoods}
      </div>
      <div class="font-size-9">10009</div>
    </div>
    <div id="listing-{listing_id}-info" style="margin-top: 10px;">
      <div class="d-inline-block align-middle b font-size-20" id="listing-{listing_id}-price">
        {price}
      </div>
    </div>
    <div class="font-size-10 d-inline-block align-bottom" style="margin-top: 12px;">
      <img alt="bedrooms" src="/images/bedrooms-icon.svg"/>
    </div>
    <div class="font-size-10 d-inline-block align-bottom" style="margin-left: 3px;">
      {beds}
    </div>
    <div class="font-size-12 d-inline-block font-gray-4">|</div>
    <img alt="bathrooms" class="font-size-10 d-inline-block align-bottom" src="/images/bathrooms-icon.svg"/>
    <div class="font-size-10 d-inline-block align-bottom" style="margin-left: 1px;">
      {baths}
    </div>
  </div>
</div>
"""


# Two realistic cards for East Village
_CARD1 = _make_card(
    listing_id="75908135",
    href="https://www.renthop.com/listings/east-10th-street-new-york-ny-10009/2b/75908135",
    title="East 10th Street, New York, NY...",
    neighborhoods="Alphabet City, East Village, Downtown Manhattan, Manhattan",
    price="$3,200",
    beds="1 Bed",
    baths="1 Bath",
    lat="40.7261", lon="-73.9784",
)
_CARD2 = _make_card(
    listing_id="55678901",
    href="https://www.renthop.com/listings/200-e-7th-street-new-york-ny-10009/1a/55678901",
    title="200 E 7th Street, New York, NY...",
    neighborhoods="East Village, Downtown Manhattan, Manhattan",
    price="$2,800",
    beds="Studio",
    baths="1 Bath",
    lat="40.7238", lon="-73.9786",
)

RENTHOP_LISTING_HTML = f"<html><body>{_CARD1}{_CARD2}</body></html>"

RENTHOP_GONE_HTML = """
<html><body>
  <h1>This listing is no longer available</h1>
  <p>The apartment has been rented. Browse similar listings below.</p>
</body></html>
"""

RENTHOP_ACTIVE_HTML = """
<html><body>
  <h1>337 East 21st Street, Apt 3H</h1>
  <div class="price">$3,200 / mo</div>
  <div class="description">Beautiful 1 bedroom apartment in Gramercy Park.</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# scrape_renthop_neighborhood (HTML parsing)
# ---------------------------------------------------------------------------

class TestScrapeRentHopNeighborhood:
    def _make_session(self, html: str):
        session = MagicMock()
        soup = BeautifulSoup(html, "lxml")
        session.fetch.return_value = soup
        return session

    def _config(self, max_price: int = 4000):
        return {
            "search": {
                "max_price": max_price,
                "min_price": 0,
                "bed_rooms": ["studio", "1"],
                "no_fee": False,
            },
            "scraper": {"request_delay_seconds": 0},
        }

    def test_returns_listings(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        assert len(listings) == 2
        for l in listings:
            assert "url" in l
            assert "address" in l
            assert "price" in l
            assert l["source"] == "renthop"

    def test_source_field_is_renthop(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        assert len(listings) == 2
        for l in listings:
            assert l.get("source") == "renthop"

    def test_urls_are_absolute(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        assert len(listings) == 2
        for l in listings:
            assert l["url"].startswith("https://www.renthop.com"), f"URL not absolute: {l['url']}"

    def test_no_query_params_in_url(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        for l in listings:
            assert "?" not in l["url"], f"URL has query params: {l['url']}"

    def test_address_parsed_correctly(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        addresses = [l["address"] for l in listings]
        # First card: title "East 10th Street, New York, NY..." → "East 10th Street #2B"
        assert any("East 10th Street" in a for a in addresses)

    def test_price_parsed_correctly(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        prices = [l["price"] for l in listings]
        assert "$3,200" in prices
        assert "$2,800" in prices

    def test_beds_parsed_correctly(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        beds = [l["beds"] for l in listings]
        assert "1 bed" in beds
        assert "Studio" in beds

    def test_baths_parsed_correctly(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        for l in listings:
            assert l["baths"] == "1 bath"

    def test_lat_lon_extracted(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        for l in listings:
            assert l.get("latitude") is not None
            assert l.get("longitude") is not None

    def test_neighborhood_is_canonical(self):
        """Neighborhood field should use VALID_NEIGHBORHOODS display name, not RentHop name."""
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        from models import VALID_NEIGHBORHOODS
        expected = VALID_NEIGHBORHOODS["east-village"]
        for l in listings:
            assert l["neighborhood"] == expected

    def test_unsupported_neighborhood_returns_empty(self):
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "nonexistent-hood", self._config())
        assert listings == []
        session.fetch.assert_not_called()

    def test_fetch_failure_returns_empty(self):
        session = MagicMock()
        session.fetch.return_value = None
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        assert listings == []

    def test_price_filter_applied(self):
        # $3,200 card should be filtered out with max_price=3000; $2,800 card stays
        session = self._make_session(RENTHOP_LISTING_HTML)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config(max_price=3000))
        from apartment_tracker import parse_price
        for l in listings:
            p = parse_price(l["price"])
            if p is not None:
                assert p <= 3000
        assert len(listings) == 1
        assert "$2,800" in listings[0]["price"]

    def test_deduplicates_by_url(self):
        # Inject a duplicate card (same listing_id and URL) at the end
        dup_card = _make_card(
            listing_id="75908135",
            href="https://www.renthop.com/listings/east-10th-street-new-york-ny-10009/2b/75908135",
            title="East 10th Street, New York, NY...",
            neighborhoods="East Village",
            price="$3,200",
            beds="1 Bed",
            baths="1 Bath",
        )
        html = f"<html><body>{_CARD1}{_CARD2}{dup_card}</body></html>"
        session = self._make_session(html)
        listings = rh.scrape_renthop_neighborhood(session, "east-village", self._config())
        urls = [l["url"] for l in listings]
        assert len(urls) == len(set(urls)), "Duplicate URLs found"
        assert len(listings) == 2  # dup removed, 2 unique


# ---------------------------------------------------------------------------
# check_renthop_listing_status
# ---------------------------------------------------------------------------

class TestCheckRentHopListingStatus:
    def _make_session(self, html: str, status_code: int):
        session = MagicMock()
        soup = BeautifulSoup(html, "lxml") if status_code < 400 else None
        session.fetch_with_status.return_value = (soup, status_code)
        return session

    def test_404_returns_gone(self):
        session = self._make_session("", 404)
        assert rh.check_renthop_listing_status(session, "https://renthop.com/listings/1") == "gone"

    def test_403_returns_unknown(self):
        session = self._make_session("", 403)
        assert rh.check_renthop_listing_status(session, "https://renthop.com/listings/1") == "unknown"

    def test_network_error_returns_unknown(self):
        session = MagicMock()
        session.fetch_with_status.return_value = (None, None)
        assert rh.check_renthop_listing_status(session, "https://renthop.com/listings/1") == "unknown"

    def test_200_active_returns_active(self):
        session = self._make_session(RENTHOP_ACTIVE_HTML, 200)
        assert rh.check_renthop_listing_status(session, "https://renthop.com/listings/1") == "active"

    def test_200_no_longer_available_returns_gone(self):
        session = self._make_session(RENTHOP_GONE_HTML, 200)
        assert rh.check_renthop_listing_status(session, "https://renthop.com/listings/1") == "gone"

    def test_500_returns_unknown(self):
        session = self._make_session("", 500)
        assert rh.check_renthop_listing_status(session, "https://renthop.com/listings/1") == "unknown"
