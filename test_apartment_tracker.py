"""Tests for apartment_tracker.py — filtering, parsing, and core logic."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from bs4 import BeautifulSoup

import apartment_tracker as at


# ---------------------------------------------------------------------------
# Helpers to build fake HTML listing cards
# ---------------------------------------------------------------------------

def make_listing_card(
    address="123 Test Street #4A",
    url="/building/123-test-street/4a",
    price="$3,000",
    neighborhood="East Village",
    beds="1 bed",
    baths="1 bath",
    sqft="650 ft²",
    featured=False,
):
    """Build a minimal HTML listing card matching StreetEasy's structure."""
    featured_param = "?featured=1" if featured else ""
    return f"""
    <div data-testid="listing-card">
        <a class="addressTextAction" href="{url}{featured_param}">{address}</a>
        <span class="PriceInfo-module__price">{price}</span>
        <p class="ListingDescription-module__title">Studio in {neighborhood}</p>
        <span class="BedsBathsSqft-mod">{beds}</span>
        <span class="BedsBathsSqft-mod">{baths}</span>
        <span class="BedsBathsSqft-mod">{sqft}</span>
        <img src="https://img.example.com/photo.jpg" />
    </div>
    """


def make_search_page(cards_html: list[str], max_page: int = 1) -> BeautifulSoup:
    """Wrap listing cards in a page with optional pagination."""
    pagination = ""
    if max_page > 1:
        links = "".join(
            f'<a href="/for-rent/test?page={p}">Page {p}</a>'
            for p in range(1, max_page + 1)
        )
        pagination = f'<div class="paginationContainer">{links}</div>'
    html = f"<html><body>{''.join(cards_html)}{pagination}</body></html>"
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# parse_price
# ---------------------------------------------------------------------------

class TestParsePrice:
    def test_normal_price(self):
        assert at.parse_price("$3,200") == 3200

    def test_no_comma(self):
        assert at.parse_price("$900") == 900

    def test_large_price(self):
        assert at.parse_price("$12,500") == 12500

    def test_na(self):
        assert at.parse_price("N/A") is None

    def test_empty(self):
        assert at.parse_price("") is None

    def test_price_with_text(self):
        assert at.parse_price("From $2,800/mo") == 2800


# ---------------------------------------------------------------------------
# build_search_url
# ---------------------------------------------------------------------------

class TestBuildSearchUrl:
    def test_single_bed(self):
        config = {
            "search": {"max_price": 3600, "min_price": 0, "bed_rooms": ["1"]},
        }
        url = at.build_search_url("east-village", config)
        assert "for-rent/east-village/" in url
        assert "price:-3600" in url
        assert "beds:1" in url

    def test_multi_bed(self):
        config = {
            "search": {"max_price": 3600, "min_price": 0, "bed_rooms": ["studio", "1"]},
        }
        url = at.build_search_url("chelsea", config)
        assert "beds:studio-1" in url

    def test_min_price(self):
        config = {
            "search": {"max_price": 5000, "min_price": 2000, "bed_rooms": ["1"]},
        }
        url = at.build_search_url("soho", config)
        assert "price:2000-5000" in url

    def test_no_min_price(self):
        config = {
            "search": {"max_price": 3600, "bed_rooms": ["studio"]},
        }
        url = at.build_search_url("tribeca", config)
        assert "price:-3600" in url


# ---------------------------------------------------------------------------
# parse_single_card
# ---------------------------------------------------------------------------

class TestParseSingleCard:
    def test_basic_card(self):
        html = make_listing_card()
        card = BeautifulSoup(html, "lxml").find("div", attrs={"data-testid": "listing-card"})
        result = at.parse_single_card(card)
        assert result is not None
        assert result["address"] == "123 Test Street #4A"
        assert result["url"] == "https://streeteasy.com/building/123-test-street/4a"
        assert result["price"] == "$3,000"
        assert result["neighborhood"] == "East Village"
        assert result["beds"] == "1 bed"
        assert result["baths"] == "1 bath"

    def test_featured_url_cleaned(self):
        html = make_listing_card(featured=True)
        card = BeautifulSoup(html, "lxml").find("div", attrs={"data-testid": "listing-card"})
        result = at.parse_single_card(card)
        assert "?featured" not in result["url"]

    def test_no_address_link_returns_none(self):
        html = '<div data-testid="listing-card"><span>No link here</span></div>'
        card = BeautifulSoup(html, "lxml").find("div")
        assert at.parse_single_card(card) is None

    def test_empty_sqft_filtered(self):
        html = make_listing_card(sqft="- ft²")
        card = BeautifulSoup(html, "lxml").find("div", attrs={"data-testid": "listing-card"})
        result = at.parse_single_card(card)
        assert result["sqft"] == "N/A"

    def test_valid_sqft_kept(self):
        html = make_listing_card(sqft="800 ft²")
        card = BeautifulSoup(html, "lxml").find("div", attrs={"data-testid": "listing-card"})
        result = at.parse_single_card(card)
        assert result["sqft"] == "800 ft²"


# ---------------------------------------------------------------------------
# parse_listings
# ---------------------------------------------------------------------------

class TestParseListings:
    def test_multiple_cards(self):
        cards = [
            make_listing_card(address="Apt A", url="/building/a/1"),
            make_listing_card(address="Apt B", url="/building/b/2"),
        ]
        soup = make_search_page(cards)
        listings = at.parse_listings(soup)
        assert len(listings) == 2

    def test_empty_page(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert at.parse_listings(soup) == []


# ---------------------------------------------------------------------------
# get_max_page
# ---------------------------------------------------------------------------

class TestGetMaxPage:
    def test_no_pagination(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert at.get_max_page(soup) == 1

    def test_multiple_pages(self):
        soup = make_search_page([], max_page=4)
        assert at.get_max_page(soup) == 4


# ---------------------------------------------------------------------------
# Neighborhood filtering (THE critical bug fix)
# ---------------------------------------------------------------------------

class TestNeighborhoodFiltering:
    """Test that scrape_neighborhood correctly filters sponsored/unrelated listings."""

    CONFIG = {
        "search": {"max_price": 3600, "min_price": 0, "bed_rooms": ["studio", "1"]},
        "scraper": {"request_delay_seconds": 0},
    }

    def _make_listings_and_run_filter(self, listings: list[dict], neighborhood: str) -> list[dict]:
        """Run the filtering logic from scrape_neighborhood on a list of fake listings."""
        # Replicate the filtering logic from scrape_neighborhood
        max_price = self.CONFIG["search"]["max_price"]

        # Price filter
        filtered = []
        for listing in listings:
            price_val = at.parse_price(listing["price"])
            if price_val is not None and price_val > max_price:
                continue
            filtered.append(listing)

        # Neighborhood filter
        allowed = at.NEIGHBORHOOD_ALIASES.get(neighborhood)
        if allowed:
            filtered = [l for l in filtered if l["neighborhood"] in allowed]

        return filtered

    def test_correct_neighborhood_passes(self):
        listings = [
            {"url": "/a", "address": "A", "price": "$3,000", "neighborhood": "East Village"},
        ]
        result = self._make_listings_and_run_filter(listings, "east-village")
        assert len(result) == 1

    def test_wrong_neighborhood_rejected(self):
        """A sponsored UES listing appearing on the East Village page should be rejected."""
        listings = [
            {"url": "/a", "address": "A", "price": "$3,000", "neighborhood": "Upper East Side"},
        ]
        result = self._make_listings_and_run_filter(listings, "east-village")
        assert len(result) == 0

    def test_empty_neighborhood_rejected(self):
        """Listings with empty neighborhood MUST be rejected (this was the bug)."""
        listings = [
            {"url": "/a", "address": "A", "price": "$3,000", "neighborhood": ""},
        ]
        result = self._make_listings_and_run_filter(listings, "east-village")
        assert len(result) == 0

    def test_sub_neighborhood_passes(self):
        """Manhattan Valley is a sub-neighborhood of UWS and should pass."""
        listings = [
            {"url": "/a", "address": "A", "price": "$3,000", "neighborhood": "Manhattan Valley"},
        ]
        result = self._make_listings_and_run_filter(listings, "upper-west-side")
        assert len(result) == 1

    def test_sponsored_above_max_price_rejected(self):
        listings = [
            {"url": "/a", "address": "A", "price": "$5,000", "neighborhood": "East Village"},
        ]
        result = self._make_listings_and_run_filter(listings, "east-village")
        assert len(result) == 0

    def test_mixed_listings_filtered_correctly(self):
        """Simulate a real page with valid + sponsored + empty-neighborhood listings."""
        listings = [
            {"url": "/good1", "address": "Good 1", "price": "$3,000", "neighborhood": "Gramercy Park"},
            {"url": "/good2", "address": "Good 2", "price": "$3,500", "neighborhood": "Gramercy"},
            {"url": "/good3", "address": "Good 3", "price": "$2,800", "neighborhood": "Kips Bay"},
            {"url": "/bad_ues", "address": "Bad UES", "price": "$3,200", "neighborhood": "Upper East Side"},
            {"url": "/bad_empty", "address": "Bad Empty", "price": "$3,100", "neighborhood": ""},
            {"url": "/bad_price", "address": "Bad Price", "price": "$8,000", "neighborhood": "Gramercy Park"},
            {"url": "/bad_bk", "address": "Bad BK", "price": "$2,500", "neighborhood": "Greenpoint"},
        ]
        result = self._make_listings_and_run_filter(listings, "gramercy-park")
        urls = {l["url"] for l in result}
        assert urls == {"/good1", "/good2", "/good3"}

    def test_ues_sponsored_on_uws_page_rejected(self):
        """UES listing appearing as sponsored on UWS search page gets filtered."""
        listings = [
            {"url": "/uws", "address": "UWS Apt", "price": "$3,000", "neighborhood": "Upper West Side"},
            {"url": "/ues", "address": "UES Sponsored", "price": "$3,000", "neighborhood": "Upper East Side"},
        ]
        result = self._make_listings_and_run_filter(listings, "upper-west-side")
        assert len(result) == 1
        assert result[0]["url"] == "/uws"

    def test_lincoln_square_passes_for_uws(self):
        listings = [
            {"url": "/ls", "address": "LS Apt", "price": "$3,000", "neighborhood": "Lincoln Square"},
        ]
        result = self._make_listings_and_run_filter(listings, "upper-west-side")
        assert len(result) == 1

    def test_unknown_slug_no_filter(self):
        """If neighborhood slug has no NEIGHBORHOOD_ALIASES entry, no filtering happens."""
        listings = [
            {"url": "/a", "address": "A", "price": "$3,000", "neighborhood": "Randomville"},
        ]
        result = self._make_listings_and_run_filter(listings, "unknown-neighborhood")
        assert len(result) == 1

    def test_les_aliases(self):
        """LES should allow Lower East Side, Two Bridges, Chinatown."""
        for hood in ["Lower East Side", "Two Bridges", "Chinatown"]:
            listings = [{"url": "/a", "address": "A", "price": "$2,000", "neighborhood": hood}]
            result = self._make_listings_and_run_filter(listings, "les")
            assert len(result) == 1, f"{hood} should be allowed for 'les'"

    def test_chelsea_aliases(self):
        for hood in ["Chelsea", "West Chelsea"]:
            listings = [{"url": "/a", "address": "A", "price": "$3,000", "neighborhood": hood}]
            result = self._make_listings_and_run_filter(listings, "chelsea")
            assert len(result) == 1, f"{hood} should be allowed for 'chelsea'"


# ---------------------------------------------------------------------------
# URL deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_duplicate_urls_removed(self):
        cards = [
            make_listing_card(address="Same Apt", url="/building/same/1"),
            make_listing_card(address="Same Apt", url="/building/same/1"),
            make_listing_card(address="Different Apt", url="/building/diff/2"),
        ]
        soup = make_search_page(cards)
        listings = at.parse_listings(soup)
        # Dedup logic from scrape_neighborhood
        seen_urls = set()
        unique = []
        for l in listings:
            if l["url"] not in seen_urls:
                seen_urls.add(l["url"])
                unique.append(l)
        assert len(unique) == 2


# ---------------------------------------------------------------------------
# Seen listings persistence
# ---------------------------------------------------------------------------

class TestSeenListings:
    def test_save_and_load(self, tmp_path):
        seen_file = tmp_path / "seen.json"
        seen = {
            "https://streeteasy.com/building/test/1": {
                "first_seen": "2026-02-11T00:00:00+00:00",
                "address": "Test #1",
                "price": "$3,000",
            }
        }
        with patch.object(at, "SEEN_PATH", seen_file):
            at.save_seen(seen)
            loaded = at.load_seen()
            assert loaded == seen

    def test_empty_file_returns_empty(self, tmp_path):
        seen_file = tmp_path / "nonexistent.json"
        with patch.object(at, "SEEN_PATH", seen_file):
            assert at.load_seen() == {}

    def test_migrate_list_format(self, tmp_path):
        seen_file = tmp_path / "seen.json"
        seen_file.write_text(json.dumps(["https://streeteasy.com/building/test/1"]))
        with patch.object(at, "SEEN_PATH", seen_file):
            loaded = at.load_seen()
            assert "https://streeteasy.com/building/test/1" in loaded
            assert "first_seen" in loaded["https://streeteasy.com/building/test/1"]


# ---------------------------------------------------------------------------
# Integration: scrape_neighborhood with mocked fetch
# ---------------------------------------------------------------------------

class TestScrapeNeighborhoodIntegration:
    """Test scrape_neighborhood end-to-end with mocked HTTP responses."""

    CONFIG = {
        "search": {"max_price": 3600, "min_price": 0, "bed_rooms": ["studio", "1"]},
        "scraper": {"request_delay_seconds": 0},
    }

    def test_filters_sponsored_listings(self):
        """End-to-end: a page with 3 valid + 2 sponsored listings returns only 3."""
        cards = [
            make_listing_card(address="Good 1", url="/building/g1/1", neighborhood="East Village"),
            make_listing_card(address="Good 2", url="/building/g2/2", neighborhood="East Village"),
            make_listing_card(address="Good 3", url="/building/g3/3", neighborhood="East Village"),
            make_listing_card(address="Sponsored UES", url="/building/s1/1", neighborhood="Upper East Side"),
            make_listing_card(address="Sponsored Empty", url="/building/s2/2", neighborhood=""),
        ]
        soup = make_search_page(cards)

        class FakeSession:
            def fetch(self, url):
                return soup

        result = at.scrape_neighborhood(FakeSession(), "east-village", self.CONFIG)
        assert len(result) == 3
        addresses = {l["address"] for l in result}
        assert "Sponsored UES" not in addresses
        assert "Sponsored Empty" not in addresses

    def test_filters_above_max_price(self):
        cards = [
            make_listing_card(address="Affordable", url="/building/a/1", price="$3,000", neighborhood="Chelsea"),
            make_listing_card(address="Expensive", url="/building/e/1", price="$5,000", neighborhood="Chelsea"),
        ]
        soup = make_search_page(cards)

        class FakeSession:
            def fetch(self, url):
                return soup

        result = at.scrape_neighborhood(FakeSession(), "chelsea", self.CONFIG)
        assert len(result) == 1
        assert result[0]["address"] == "Affordable"

    def test_deduplicates_across_pages(self):
        """Same listing appearing on page 1 and page 2 only counted once."""
        page1_cards = [
            make_listing_card(address="Apt A", url="/building/a/1", neighborhood="Flatiron"),
            make_listing_card(address="Apt B", url="/building/b/1", neighborhood="Flatiron"),
        ]
        page2_cards = [
            make_listing_card(address="Apt A", url="/building/a/1", neighborhood="Flatiron"),  # duplicate
            make_listing_card(address="Apt C", url="/building/c/1", neighborhood="Flatiron"),
        ]
        soup1 = make_search_page(page1_cards, max_page=2)
        soup2 = make_search_page(page2_cards)

        call_count = 0

        class FakeSession:
            def fetch(self, url):
                nonlocal call_count
                call_count += 1
                return soup1 if call_count == 1 else soup2

        result = at.scrape_neighborhood(FakeSession(), "flatiron", self.CONFIG)
        assert len(result) == 3  # A, B, C — no duplicate A

    def test_handles_empty_page(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")

        class FakeSession:
            def fetch(self, url):
                return soup

        result = at.scrape_neighborhood(FakeSession(), "chelsea", self.CONFIG)
        assert result == []

    def test_handles_fetch_failure(self):
        class FakeSession:
            def fetch(self, url):
                return None

        result = at.scrape_neighborhood(FakeSession(), "chelsea", self.CONFIG)
        assert result == []


# ---------------------------------------------------------------------------
# NEIGHBORHOOD_ALIASES completeness
# ---------------------------------------------------------------------------

class TestNeighborhoodAliases:
    def test_all_config_neighborhoods_have_aliases(self):
        """Every neighborhood in config.json should have an entry in NEIGHBORHOOD_ALIASES."""
        config = at.load_config()
        for hood in config["search"]["neighborhoods"]:
            assert hood in at.NEIGHBORHOOD_ALIASES, (
                f"Neighborhood '{hood}' in config.json but missing from NEIGHBORHOOD_ALIASES"
            )

    def test_aliases_are_sets_of_strings(self):
        for slug, aliases in at.NEIGHBORHOOD_ALIASES.items():
            assert isinstance(aliases, set), f"Aliases for '{slug}' should be a set"
            for a in aliases:
                assert isinstance(a, str), f"Alias '{a}' for '{slug}' should be a string"
                assert len(a) > 0, f"Empty alias found for '{slug}'"


# ---------------------------------------------------------------------------
# _parse_address_for_geoclient
# ---------------------------------------------------------------------------

class TestParseAddressForGeoclient:
    def test_standard_address(self):
        result = at._parse_address_for_geoclient("337 East 21st Street #3H")
        assert result == ("337", "East 21st Street")

    def test_address_with_apt(self):
        result = at._parse_address_for_geoclient("100 West 10th Street Apt 4B")
        assert result == ("100", "West 10th Street")

    def test_address_with_unit(self):
        result = at._parse_address_for_geoclient("200 Broadway, Unit 5C")
        assert result == ("200", "Broadway")

    def test_address_no_unit(self):
        result = at._parse_address_for_geoclient("45 Christopher Street")
        assert result == ("45", "Christopher Street")

    def test_unparseable_address(self):
        assert at._parse_address_for_geoclient("No Number Here") is None

    def test_empty_string(self):
        assert at._parse_address_for_geoclient("") is None


# ---------------------------------------------------------------------------
# _format_cross_streets
# ---------------------------------------------------------------------------

class TestFormatCrossStreets:
    def test_basic_format(self):
        assert at._format_cross_streets("1 AVENUE", "2 AVENUE") == "between 1 Avenue & 2 Avenue"

    def test_titlecases_and_collapses_whitespace(self):
        result = at._format_cross_streets("BROADWAY", "WEST   4 STREET")
        assert result == "between Broadway & West 4 Street"


# ---------------------------------------------------------------------------
# geoclient_lookup (Geoclient API)
# ---------------------------------------------------------------------------

class TestGeoclientLookup:
    def test_returns_cross_streets_and_coordinates(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "address": {
                "lowCrossStreetName1": "2 AVENUE",
                "highCrossStreetName1": "1 AVENUE",
                "latitude": 40.7357,
                "longitude": -73.9823,
            }
        }
        mock_response.raise_for_status = MagicMock()
        with patch("apartment_tracker.requests.get", return_value=mock_response) as mock_get:
            result = at.geoclient_lookup("337 East 21st Street #3H", "fake-key")
            assert result is not None
            assert result["cross_streets"] == "between 2 Avenue & 1 Avenue"
            assert result["latitude"] == 40.7357
            assert result["longitude"] == -73.9823
            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args
            assert call_kwargs[1]["params"]["houseNumber"] == "337"
            assert call_kwargs[1]["params"]["street"] == "East 21st Street"

    def test_returns_none_cross_streets_on_missing_fields(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "address": {"latitude": 40.73, "longitude": -73.98}
        }
        mock_response.raise_for_status = MagicMock()
        with patch("apartment_tracker.requests.get", return_value=mock_response):
            result = at.geoclient_lookup("337 East 21st Street #3H", "fake-key")
            assert result is not None
            assert result["cross_streets"] is None
            assert result["latitude"] == 40.73

    def test_returns_none_on_unparseable_address(self):
        result = at.geoclient_lookup("No Number Here", "fake-key")
        assert result is None

    def test_returns_none_on_api_error(self):
        with patch("apartment_tracker.requests.get", side_effect=at.requests.RequestException("timeout")):
            result = at.geoclient_lookup("337 East 21st Street #3H", "fake-key")
            assert result is None

    def test_handles_missing_coordinates(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "address": {
                "lowCrossStreetName1": "BROADWAY",
                "highCrossStreetName1": "5 AVENUE",
            }
        }
        mock_response.raise_for_status = MagicMock()
        with patch("apartment_tracker.requests.get", return_value=mock_response):
            result = at.geoclient_lookup("200 West 23rd Street", "fake-key")
            assert result is not None
            assert result["cross_streets"] == "between Broadway & 5 Avenue"
            assert result["latitude"] is None
            assert result["longitude"] is None


# ---------------------------------------------------------------------------
# Discord embed includes cross streets
# ---------------------------------------------------------------------------

class TestDiscordCrossStreets:
    CONFIG = {"discord": {"username": "Test Bot"}}

    def test_embed_includes_cross_streets(self):
        listing = {
            "price": "$3,000", "address": "123 Test St", "beds": "1 bed",
            "baths": "1 bath", "sqft": "650 ft²", "neighborhood": "East Village",
            "url": "https://streeteasy.com/building/test/1",
            "cross_streets": "between 1st Ave & 2nd Ave",
        }
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_notification("https://discord.com/webhook", listing, self.CONFIG)
            payload = mock_post.call_args[1]["json"]
            fields = payload["embeds"][0]["fields"]
            cross_field = [f for f in fields if "Cross Streets" in f["name"]]
            assert len(cross_field) == 1
            assert cross_field[0]["value"] == "between 1st Ave & 2nd Ave"

    def test_embed_omits_cross_streets_when_none(self):
        listing = {
            "price": "$3,000", "address": "123 Test St", "beds": "1 bed",
            "baths": "1 bath", "sqft": "650 ft²", "neighborhood": "East Village",
            "url": "https://streeteasy.com/building/test/1",
        }
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_notification("https://discord.com/webhook", listing, self.CONFIG)
            payload = mock_post.call_args[1]["json"]
            fields = payload["embeds"][0]["fields"]
            cross_field = [f for f in fields if "Cross Streets" in f["name"]]
            assert len(cross_field) == 0

    def test_embed_includes_subway_info(self):
        listing = {
            "price": "$3,000", "address": "123 Test St", "beds": "1 bed",
            "baths": "1 bath", "sqft": "650 ft²", "neighborhood": "East Village",
            "url": "https://streeteasy.com/building/test/1",
            "subway_info": "L at 1st Ave (0.2 mi)\n6 at Astor Pl (0.3 mi)",
        }
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_notification("https://discord.com/webhook", listing, self.CONFIG)
            payload = mock_post.call_args[1]["json"]
            fields = payload["embeds"][0]["fields"]
            subway_field = [f for f in fields if "Subway" in f["name"]]
            assert len(subway_field) == 1
            assert "L at 1st Ave" in subway_field[0]["value"]
            assert subway_field[0]["inline"] is False


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point_is_zero(self):
        assert at._haversine(40.7128, -74.0060, 40.7128, -74.0060) == 0.0

    def test_known_distance(self):
        # Times Square to Empire State Building ~0.6 miles
        dist = at._haversine(40.7580, -73.9855, 40.7484, -73.9857)
        assert 0.5 < dist < 0.8

    def test_short_distance(self):
        # Two very close points in Manhattan
        dist = at._haversine(40.7300, -73.9900, 40.7305, -73.9905)
        assert dist < 0.1


# ---------------------------------------------------------------------------
# Subway station loading
# ---------------------------------------------------------------------------

class TestLoadSubwayStations:
    def test_loads_real_data(self):
        # Reset cache
        at._subway_stations_cache = None
        stations = at._load_subway_stations()
        assert len(stations) > 100
        # Check structure
        first = stations[0]
        assert "name" in first
        assert "latitude" in first
        assert "longitude" in first
        assert "routes" in first
        assert isinstance(first["routes"], list)
        # Reset cache for other tests
        at._subway_stations_cache = None

    def test_returns_empty_on_missing_file(self):
        at._subway_stations_cache = None
        with patch.object(at, "SUBWAY_DATA_PATH", Path("/nonexistent/path.json")):
            stations = at._load_subway_stations()
            assert stations == []
        at._subway_stations_cache = None

    def test_caches_after_first_call(self):
        at._subway_stations_cache = None
        stations1 = at._load_subway_stations()
        stations2 = at._load_subway_stations()
        assert stations1 is stations2
        at._subway_stations_cache = None


# ---------------------------------------------------------------------------
# find_nearby_stations
# ---------------------------------------------------------------------------

class TestFindNearbyStations:
    SAMPLE_STATIONS = [
        {"name": "1st Ave", "latitude": 40.7307, "longitude": -73.9817, "routes": ["L"]},
        {"name": "Astor Pl", "latitude": 40.7291, "longitude": -73.9910, "routes": ["6"]},
        {"name": "Far Away Station", "latitude": 41.0, "longitude": -74.5, "routes": ["A"]},
    ]

    def test_finds_nearby_stations(self):
        # Point near 1st Ave L station
        results = at.find_nearby_stations(40.7310, -73.9820, self.SAMPLE_STATIONS)
        assert len(results) >= 1
        assert results[0]["name"] == "1st Ave"

    def test_respects_max_miles(self):
        results = at.find_nearby_stations(40.7310, -73.9820, self.SAMPLE_STATIONS, max_miles=0.01)
        # Only very close stations should match
        names = [r["name"] for r in results]
        assert "Far Away Station" not in names

    def test_respects_max_stations(self):
        results = at.find_nearby_stations(40.7310, -73.9820, self.SAMPLE_STATIONS, max_stations=1, max_miles=1.0)
        assert len(results) <= 1

    def test_excludes_far_stations(self):
        results = at.find_nearby_stations(40.7310, -73.9820, self.SAMPLE_STATIONS, max_miles=0.5)
        names = [r["name"] for r in results]
        assert "Far Away Station" not in names

    def test_returns_empty_for_no_nearby(self):
        results = at.find_nearby_stations(41.5, -74.5, self.SAMPLE_STATIONS, max_miles=0.5)
        assert results == []

    def test_results_sorted_by_distance(self):
        results = at.find_nearby_stations(40.7310, -73.9820, self.SAMPLE_STATIONS, max_miles=1.0)
        distances = [r["distance_mi"] for r in results]
        assert distances == sorted(distances)

    def test_result_structure(self):
        results = at.find_nearby_stations(40.7310, -73.9820, self.SAMPLE_STATIONS, max_miles=1.0)
        assert len(results) > 0
        r = results[0]
        assert "name" in r
        assert "routes" in r
        assert "distance_mi" in r
        assert isinstance(r["distance_mi"], float)


# ---------------------------------------------------------------------------
# _format_subway_field
# ---------------------------------------------------------------------------

class TestFormatSubwayField:
    def test_single_station(self):
        stations = [{"name": "1st Ave", "routes": ["L"], "distance_mi": 0.2}]
        result = at._format_subway_field(stations)
        assert result == "L at 1st Ave (0.2 mi)"

    def test_multiple_stations(self):
        stations = [
            {"name": "1st Ave", "routes": ["L"], "distance_mi": 0.2},
            {"name": "Astor Pl", "routes": ["6"], "distance_mi": 0.3},
        ]
        result = at._format_subway_field(stations)
        assert "L at 1st Ave (0.2 mi)" in result
        assert "6 at Astor Pl (0.3 mi)" in result
        assert "\n" in result

    def test_multi_route_station(self):
        stations = [{"name": "Union Sq", "routes": ["4", "5", "6", "L", "N", "Q", "R", "W"], "distance_mi": 0.1}]
        result = at._format_subway_field(stations)
        assert "4, 5, 6, L, N, Q, R, W at Union Sq (0.1 mi)" == result


# ---------------------------------------------------------------------------
# Daily digest
# ---------------------------------------------------------------------------

class TestDailyDigest:
    CONFIG = {"discord": {"username": "Test Bot"}}

    def test_send_discord_digest_success(self):
        listings = [
            {"url": "/a", "address": "Apt A", "price": "$3,000", "neighborhood": "East Village"},
            {"url": "/b", "address": "Apt B", "price": "$3,500", "neighborhood": "East Village"},
            {"url": "/c", "address": "Apt C", "price": "$2,800", "neighborhood": "Chelsea"},
        ]
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            result = at.send_discord_digest("https://discord.com/webhook", listings, self.CONFIG)
            assert result is True
            payload = mock_post.call_args[1]["json"]
            embed = payload["embeds"][0]
            assert "Daily Digest" in embed["title"]
            assert "3 new listing(s)" in embed["description"]
            assert "East Village" in embed["description"]
            assert "Chelsea" in embed["description"]
            assert embed["color"] == 0x3498DB

    def test_send_discord_digest_empty_listings(self):
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            result = at.send_discord_digest("https://discord.com/webhook", [], self.CONFIG)
            assert result is True
            payload = mock_post.call_args[1]["json"]
            assert "0 new listing(s)" in payload["embeds"][0]["description"]

    def test_run_digest_filters_recent(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=6)).isoformat()
        old = (now - timedelta(hours=30)).isoformat()
        seen = {
            "https://streeteasy.com/a": {
                "first_seen": recent, "address": "Recent Apt",
                "price": "$3,000", "neighborhood": "Chelsea",
            },
            "https://streeteasy.com/b": {
                "first_seen": old, "address": "Old Apt",
                "price": "$2,500", "neighborhood": "SoHo",
            },
        }
        with patch.object(at, "load_config", return_value={"discord": {}}), \
             patch.object(at, "load_seen", return_value=seen), \
             patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/webhook"}), \
             patch("apartment_tracker.send_discord_digest") as mock_digest:
            at.run_digest()
            mock_digest.assert_called_once()
            listings_arg = mock_digest.call_args[0][1]
            assert len(listings_arg) == 1
            assert listings_arg[0]["address"] == "Recent Apt"

    def test_run_digest_still_sends_when_no_recent(self):
        """Digest always sends (analytics are valuable even with 0 new listings)."""
        old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        seen = {
            "https://streeteasy.com/a": {
                "first_seen": old, "address": "Old Apt",
                "price": "$2,500", "neighborhood": "SoHo",
            },
        }
        with patch.object(at, "load_config", return_value={"discord": {}}), \
             patch.object(at, "load_seen", return_value=seen), \
             patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/webhook"}), \
             patch("apartment_tracker.send_discord_digest") as mock_digest:
            at.run_digest()
            mock_digest.assert_called_once()
            # Recent listings arg should be empty
            listings_arg = mock_digest.call_args[0][1]
            assert len(listings_arg) == 0
            # Analytics should still be provided (as keyword arg)
            analytics_arg = mock_digest.call_args[1].get("analytics")
            if analytics_arg is None and len(mock_digest.call_args[0]) > 3:
                analytics_arg = mock_digest.call_args[0][3]
            assert analytics_arg is not None


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_default_no_digest(self):
        with patch("sys.argv", ["apartment_tracker.py"]):
            args = at.parse_args()
            assert args.digest is False

    def test_digest_flag(self):
        with patch("sys.argv", ["apartment_tracker.py", "--digest"]):
            args = at.parse_args()
            assert args.digest is True


# ---------------------------------------------------------------------------
# No-fee filter
# ---------------------------------------------------------------------------

class TestNoFeeFilter:
    def test_no_fee_disabled(self):
        config = {
            "search": {"max_price": 3600, "min_price": 0, "bed_rooms": ["1"], "no_fee": False},
        }
        url = at.build_search_url("east-village", config)
        assert "no_fee" not in url

    def test_no_fee_enabled(self):
        config = {
            "search": {"max_price": 3600, "min_price": 0, "bed_rooms": ["1"], "no_fee": True},
        }
        url = at.build_search_url("east-village", config)
        assert "no_fee:1" in url

    def test_no_fee_absent_from_config(self):
        config = {
            "search": {"max_price": 3600, "min_price": 0, "bed_rooms": ["1"]},
        }
        url = at.build_search_url("east-village", config)
        assert "no_fee" not in url


# ---------------------------------------------------------------------------
# Google Maps URL
# ---------------------------------------------------------------------------

class TestGoogleMapsUrl:
    def test_basic_address(self):
        url = at.build_google_maps_url("123 East 10th Street")
        assert "google.com/maps/search" in url
        assert "123" in url
        assert "New+York" in url or "New%20York" in url

    def test_special_characters_encoded(self):
        url = at.build_google_maps_url("45 West 4th Street #2A")
        assert "google.com/maps/search" in url
        # The # should be encoded
        assert "#" not in url.split("query=")[1] or "%23" in url

    def test_embed_includes_map_field(self):
        listing = {
            "price": "$3,000", "address": "123 Test St", "beds": "1 bed",
            "baths": "1 bath", "sqft": "650 ft²", "neighborhood": "East Village",
            "url": "https://streeteasy.com/building/test/1",
        }
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_notification("https://discord.com/webhook", listing, {"discord": {}})
            payload = mock_post.call_args[1]["json"]
            fields = payload["embeds"][0]["fields"]
            map_field = [f for f in fields if "Map" in f["name"]]
            assert len(map_field) == 1
            assert "google.com/maps" in map_field[0]["value"]


# ---------------------------------------------------------------------------
# Price drop detection
# ---------------------------------------------------------------------------

class TestPriceDropDetection:
    def test_detect_price_drop(self):
        seen_entry = {"price": "$3,000"}
        result = at.detect_price_change(seen_entry, 2800)
        assert result is not None
        assert result["old_price"] == 3000
        assert result["new_price"] == 2800
        assert result["savings"] == 200
        assert result["pct"] == pytest.approx(6.7, abs=0.1)

    def test_no_change_when_price_same(self):
        seen_entry = {"price": "$3,000"}
        result = at.detect_price_change(seen_entry, 3000)
        assert result is None

    def test_no_change_when_price_increased(self):
        seen_entry = {"price": "$3,000"}
        result = at.detect_price_change(seen_entry, 3200)
        assert result is None

    def test_no_change_when_old_price_missing(self):
        seen_entry = {"price": "N/A"}
        result = at.detect_price_change(seen_entry, 3000)
        assert result is None

    def test_no_change_when_current_price_none(self):
        seen_entry = {"price": "$3,000"}
        result = at.detect_price_change(seen_entry, None)
        assert result is None

    def test_update_price_history(self):
        seen_entry = {"price": "$3,000"}
        at.update_price_history(seen_entry, 2800)
        assert "price_history" in seen_entry
        assert len(seen_entry["price_history"]) == 1
        assert seen_entry["price_history"][0]["price"] == 2800
        assert seen_entry["price"] == "$2,800"

    def test_update_price_history_appends(self):
        seen_entry = {"price": "$3,000", "price_history": [{"price": 2900, "date": "2026-01-01"}]}
        at.update_price_history(seen_entry, 2800)
        assert len(seen_entry["price_history"]) == 2
        assert seen_entry["price_history"][-1]["price"] == 2800

    def test_send_discord_price_drop(self):
        listing = {"address": "123 Test St", "url": "https://streeteasy.com/test", "neighborhood": "Chelsea"}
        change = {"old_price": 3000, "new_price": 2800, "savings": 200, "pct": 6.7}
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            result = at.send_discord_price_drop("https://discord.com/webhook", listing, change, {"discord": {}})
            assert result is True
            payload = mock_post.call_args[1]["json"]
            embed = payload["embeds"][0]
            assert "Price Drop" in embed["title"]
            assert embed["color"] == 0xFF8C00
            fields = {f["name"]: f["value"] for f in embed["fields"]}
            assert "~~$3,000~~" in fields["💰 Price"]
            assert "**$2,800**" in fields["💰 Price"]
            assert "$200" in fields["💵 Savings"]


# ---------------------------------------------------------------------------
# Listing staleness
# ---------------------------------------------------------------------------

class TestStaleness:
    def test_compute_days_on_market(self):
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        result = at.compute_days_on_market(two_days_ago)
        assert result == 2

    def test_compute_days_on_market_30_plus(self):
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        result = at.compute_days_on_market(old)
        assert result == 45

    def test_compute_days_on_market_none_input(self):
        assert at.compute_days_on_market(None) is None

    def test_compute_days_on_market_empty_string(self):
        assert at.compute_days_on_market("") is None

    def test_compute_days_on_market_invalid(self):
        assert at.compute_days_on_market("not-a-date") is None

    def test_days_tracked_in_notification(self):
        listing = {
            "price": "$3,000", "address": "123 Test St", "beds": "1 bed",
            "baths": "1 bath", "sqft": "650 ft²", "neighborhood": "East Village",
            "url": "https://streeteasy.com/building/test/1",
        }
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_notification("https://discord.com/webhook", listing, {"discord": {}},
                                         days_on_market=35)
            payload = mock_post.call_args[1]["json"]
            fields = {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}
            assert "35 days" in fields["📅 Days Tracked"]
            assert "negotiable" in fields["📅 Days Tracked"]

    def test_days_tracked_no_hint_under_30(self):
        listing = {
            "price": "$3,000", "address": "123 Test St", "beds": "1 bed",
            "baths": "1 bath", "sqft": "650 ft²", "neighborhood": "East Village",
            "url": "https://streeteasy.com/building/test/1",
        }
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_notification("https://discord.com/webhook", listing, {"discord": {}},
                                         days_on_market=10)
            payload = mock_post.call_args[1]["json"]
            fields = {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}
            assert "10 days" in fields["📅 Days Tracked"]
            assert "negotiable" not in fields["📅 Days Tracked"]

    def test_days_tracked_in_price_drop(self):
        listing = {"address": "123 Test St", "url": "https://streeteasy.com/test", "neighborhood": "Chelsea"}
        change = {"old_price": 3000, "new_price": 2800, "savings": 200, "pct": 6.7}
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_price_drop("https://discord.com/webhook", listing, change,
                                       {"discord": {}}, days_on_market=40)
            payload = mock_post.call_args[1]["json"]
            fields = {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}
            assert "40 days" in fields["📅 Days Tracked"]
            assert "negotiable" in fields["📅 Days Tracked"]


# ---------------------------------------------------------------------------
# Value score system
# ---------------------------------------------------------------------------

class TestValueScore:
    def test_compute_neighborhood_medians(self):
        seen = {
            "a": {"price": "$3,000", "neighborhood": "Chelsea"},
            "b": {"price": "$3,200", "neighborhood": "Chelsea"},
            "c": {"price": "$2,800", "neighborhood": "SoHo"},
        }
        medians = at.compute_neighborhood_medians(seen)
        assert medians["Chelsea"] == 3100.0  # median of 3000, 3200
        assert medians["SoHo"] == 2800.0

    def test_compute_neighborhood_medians_empty(self):
        assert at.compute_neighborhood_medians({}) == {}

    def test_value_score_below_median_scores_high(self):
        medians = {"Chelsea": 3500.0}
        listing = {"price": "$2,800", "neighborhood": "Chelsea", "sqft": "N/A"}
        vs = at.compute_value_score(listing, medians)
        assert vs is not None
        assert vs["score"] > 5.0

    def test_value_score_above_median_scores_low(self):
        medians = {"Chelsea": 2500.0}
        listing = {"price": "$3,500", "neighborhood": "Chelsea", "sqft": "N/A"}
        vs = at.compute_value_score(listing, medians)
        assert vs is not None
        assert vs["score"] < 5.0

    def test_value_score_with_subway(self):
        medians = {"Chelsea": 3000.0}
        listing = {"price": "$3,000", "neighborhood": "Chelsea", "sqft": "N/A"}
        nearby = [{"name": "23rd St", "routes": ["1"], "distance_mi": 0.1}]
        vs = at.compute_value_score(listing, medians, nearby)
        assert vs is not None
        assert vs["score"] > 5.0  # Close subway boosts score

    def test_value_score_returns_none_for_unparseable_price(self):
        medians = {"Chelsea": 3000.0}
        listing = {"price": "N/A", "neighborhood": "Chelsea", "sqft": "N/A"}
        assert at.compute_value_score(listing, medians) is None

    def test_value_score_grade_a(self):
        medians = {"Chelsea": 4000.0}
        listing = {"price": "$2,500", "neighborhood": "Chelsea", "sqft": "500 ft²"}
        nearby = [{"name": "23rd St", "routes": ["1"], "distance_mi": 0.05}]
        vs = at.compute_value_score(listing, medians, nearby)
        assert vs is not None
        assert vs["grade"] == "A"
        assert vs["color"] == 0x2ECC71

    def test_value_score_in_notification(self):
        listing = {
            "price": "$3,000", "address": "123 Test St", "beds": "1 bed",
            "baths": "1 bath", "sqft": "650 ft²", "neighborhood": "East Village",
            "url": "https://streeteasy.com/building/test/1",
        }
        vs = {"score": 7.5, "grade": "B", "color": 0x27AE60}
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_notification("https://discord.com/webhook", listing, {"discord": {}},
                                         value_score=vs)
            payload = mock_post.call_args[1]["json"]
            embed = payload["embeds"][0]
            assert embed["color"] == 0x27AE60
            fields = {f["name"]: f["value"] for f in embed["fields"]}
            assert "7.5/10" in fields["📊 Value Score"]
            assert "Grade: B" in fields["📊 Value Score"]


# ---------------------------------------------------------------------------
# Digest analytics
# ---------------------------------------------------------------------------

class TestDigestAnalytics:
    def _make_seen(self):
        now = datetime.now(timezone.utc)
        return {
            "https://streeteasy.com/a": {
                "first_seen": (now - timedelta(hours=6)).isoformat(),
                "address": "Apt A", "price": "$3,000", "neighborhood": "Chelsea",
            },
            "https://streeteasy.com/b": {
                "first_seen": (now - timedelta(days=10)).isoformat(),
                "address": "Apt B", "price": "$3,200", "neighborhood": "Chelsea",
            },
            "https://streeteasy.com/c": {
                "first_seen": (now - timedelta(days=35)).isoformat(),
                "address": "Apt C", "price": "$2,800", "neighborhood": "SoHo",
            },
            "https://streeteasy.com/d": {
                "first_seen": (now - timedelta(days=5)).isoformat(),
                "address": "Apt D", "price": "$2,500", "neighborhood": "SoHo",
            },
        }

    def test_avg_by_hood(self):
        seen = self._make_seen()
        analytics = at.compute_digest_analytics(seen, [])
        assert "Chelsea" in analytics["avg_by_hood"]
        assert "SoHo" in analytics["avg_by_hood"]
        assert analytics["avg_by_hood"]["Chelsea"] == 3100  # avg of 3000, 3200

    def test_overall_avg(self):
        seen = self._make_seen()
        analytics = at.compute_digest_analytics(seen, [])
        assert analytics["overall_avg"] > 0
        assert analytics["total_tracked"] == 4

    def test_top_deals(self):
        seen = self._make_seen()
        analytics = at.compute_digest_analytics(seen, [])
        assert len(analytics["top_deals"]) <= 5
        for deal in analytics["top_deals"]:
            assert "score" in deal
            assert "grade" in deal

    def test_stale_listings(self):
        seen = self._make_seen()
        analytics = at.compute_digest_analytics(seen, [])
        assert len(analytics["stale_listings"]) >= 1
        # Apt C is 35 days old
        stale_addrs = [s["address"] for s in analytics["stale_listings"]]
        assert "Apt C" in stale_addrs

    def test_digest_includes_analytics(self):
        seen = self._make_seen()
        analytics = at.compute_digest_analytics(seen, [])
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_digest("https://discord.com/webhook", [], {"discord": {}}, analytics=analytics)
            payload = mock_post.call_args[1]["json"]
            desc = payload["embeds"][0]["description"]
            assert "Market Summary" in desc
            assert "Avg Price by Neighborhood" in desc

    def test_digest_description_limit(self):
        """Digest description should not exceed 4096 characters."""
        # Create a large seen dict
        seen = {}
        now = datetime.now(timezone.utc)
        for i in range(200):
            seen[f"https://streeteasy.com/{i}"] = {
                "first_seen": (now - timedelta(days=i % 60)).isoformat(),
                "address": f"Apt {i} at {'A' * 50} Street",
                "price": f"${2000 + i * 10:,}",
                "neighborhood": f"Neighborhood{i % 20}",
            }
        analytics = at.compute_digest_analytics(seen, [])
        with patch("apartment_tracker.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp
            at.send_discord_digest("https://discord.com/webhook", [], {"discord": {}}, analytics=analytics)
            payload = mock_post.call_args[1]["json"]
            desc = payload["embeds"][0]["description"]
            assert len(desc) <= 4096


# ---------------------------------------------------------------------------
# Geographic bounds filtering
# ---------------------------------------------------------------------------

class TestGeoBounds:
    CONFIG_WITH_BOUNDS = {
        "search": {
            "geo_bounds": {
                "west_longitude": -74.001,
                "east_longitude": -73.983,
            }
        }
    }
    CONFIG_NO_BOUNDS = {"search": {}}

    def test_inside_bounds(self):
        assert at.is_within_geo_bounds(-73.990, self.CONFIG_WITH_BOUNDS) is True

    def test_outside_east(self):
        """Longitude east of 1st Ave (e.g. Avenue A) should be rejected."""
        assert at.is_within_geo_bounds(-73.980, self.CONFIG_WITH_BOUNDS) is False

    def test_outside_west(self):
        """Longitude west of 8th Ave (e.g. 9th Ave) should be rejected."""
        assert at.is_within_geo_bounds(-74.005, self.CONFIG_WITH_BOUNDS) is False

    def test_on_east_boundary(self):
        assert at.is_within_geo_bounds(-73.983, self.CONFIG_WITH_BOUNDS) is True

    def test_on_west_boundary(self):
        assert at.is_within_geo_bounds(-74.001, self.CONFIG_WITH_BOUNDS) is True

    def test_no_bounds_configured(self):
        assert at.is_within_geo_bounds(-73.980, self.CONFIG_NO_BOUNDS) is True

    def test_no_longitude(self):
        assert at.is_within_geo_bounds(None, self.CONFIG_WITH_BOUNDS) is True


# ---------------------------------------------------------------------------
# check_listing_status
# ---------------------------------------------------------------------------

class TestCheckListingStatus:
    def test_404_returns_gone(self):
        session = MagicMock()
        session.fetch_with_status.return_value = (None, 404)
        assert at.check_listing_status(session, "https://streeteasy.com/x") == "gone"

    def test_403_returns_unknown(self):
        session = MagicMock()
        session.fetch_with_status.return_value = (None, 403)
        assert at.check_listing_status(session, "https://streeteasy.com/x") == "unknown"

    def test_network_error_returns_unknown(self):
        session = MagicMock()
        session.fetch_with_status.return_value = (None, None)
        assert at.check_listing_status(session, "https://streeteasy.com/x") == "unknown"

    def test_500_returns_unknown(self):
        session = MagicMock()
        session.fetch_with_status.return_value = (None, 500)
        assert at.check_listing_status(session, "https://streeteasy.com/x") == "unknown"

    def test_200_no_longer_available_returns_gone(self):
        soup = BeautifulSoup("<html><body><p>This listing is no longer available</p></body></html>", "lxml")
        session = MagicMock()
        session.fetch_with_status.return_value = (soup, 200)
        assert at.check_listing_status(session, "https://streeteasy.com/x") == "gone"

    def test_200_off_market_returns_gone(self):
        soup = BeautifulSoup("<html><body><p>This unit is off market</p></body></html>", "lxml")
        session = MagicMock()
        session.fetch_with_status.return_value = (soup, 200)
        assert at.check_listing_status(session, "https://streeteasy.com/x") == "gone"

    def test_200_normal_listing_returns_active(self):
        soup = BeautifulSoup("<html><body><span class='price'>$3,000</span></body></html>", "lxml")
        session = MagicMock()
        session.fetch_with_status.return_value = (soup, 200)
        assert at.check_listing_status(session, "https://streeteasy.com/x") == "active"


# ---------------------------------------------------------------------------
# cleanup_stale_listings
# ---------------------------------------------------------------------------

class TestCleanupStaleListings:
    CONFIG = {
        "scraper": {"request_delay_seconds": 0},
        "search": {},
    }

    def test_stale_gone_entry_removed(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        seen = {
            "https://streeteasy.com/gone": {
                "address": "Gone Apt", "price": "$3,000",
                "last_scraped": old_ts,
            },
        }
        session = MagicMock()
        session.fetch_with_status.return_value = (None, 404)
        removed = at.cleanup_stale_listings(session, seen, self.CONFIG, "")
        assert removed == 1
        assert "https://streeteasy.com/gone" not in seen

    def test_stale_active_entry_updated(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        seen = {
            "https://streeteasy.com/active": {
                "address": "Active Apt", "price": "$3,000",
                "last_scraped": old_ts,
            },
        }
        soup = BeautifulSoup("<html><body><span class='price'>$3,000</span></body></html>", "lxml")
        session = MagicMock()
        session.fetch_with_status.return_value = (soup, 200)
        removed = at.cleanup_stale_listings(session, seen, self.CONFIG, "")
        assert removed == 0
        assert "https://streeteasy.com/active" in seen
        # last_scraped should have been updated
        new_ts = seen["https://streeteasy.com/active"]["last_scraped"]
        assert new_ts > old_ts

    def test_fresh_entry_not_checked(self):
        fresh_ts = datetime.now(timezone.utc).isoformat()
        seen = {
            "https://streeteasy.com/fresh": {
                "address": "Fresh Apt", "price": "$3,000",
                "last_scraped": fresh_ts,
            },
        }
        session = MagicMock()
        removed = at.cleanup_stale_listings(session, seen, self.CONFIG, "")
        assert removed == 0
        session.fetch_with_status.assert_not_called()

    def test_respects_max_checks(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        seen = {}
        for i in range(20):
            seen[f"https://streeteasy.com/{i}"] = {
                "address": f"Apt {i}", "price": "$3,000",
                "last_scraped": old_ts,
            }
        session = MagicMock()
        session.fetch_with_status.return_value = (None, 404)
        removed = at.cleanup_stale_listings(session, seen, self.CONFIG, "", max_checks=5)
        assert removed == 5
        assert session.fetch_with_status.call_count == 5

    def test_missing_last_scraped_treated_as_stale(self):
        seen = {
            "https://streeteasy.com/old": {
                "address": "Old Apt", "price": "$3,000",
                # no last_scraped
            },
        }
        session = MagicMock()
        session.fetch_with_status.return_value = (None, 404)
        removed = at.cleanup_stale_listings(session, seen, self.CONFIG, "")
        assert removed == 1

    def test_geo_backfill_during_cleanup(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        seen = {
            "https://streeteasy.com/apt": {
                "address": "123 East 10th Street", "price": "$3,000",
                "last_scraped": old_ts,
                # no latitude/longitude
            },
        }
        soup = BeautifulSoup("<html><body><span class='price'>$3,000</span></body></html>", "lxml")
        session = MagicMock()
        session.fetch_with_status.return_value = (soup, 200)
        with patch("apartment_tracker.geoclient_lookup") as mock_geo:
            mock_geo.return_value = {"cross_streets": None, "latitude": 40.73, "longitude": -73.99}
            removed = at.cleanup_stale_listings(session, seen, self.CONFIG, "fake-key")
        assert removed == 0
        assert seen["https://streeteasy.com/apt"]["latitude"] == 40.73
        assert seen["https://streeteasy.com/apt"]["longitude"] == -73.99


# ---------------------------------------------------------------------------
# Lazy geo backfill in run_scraper
# ---------------------------------------------------------------------------

class TestLazyGeoBackfill:
    CONFIG = {
        "search": {
            "max_price": 3600, "min_price": 0, "bed_rooms": ["studio", "1"],
            "neighborhoods": ["east-village"],
            "geo_bounds": {"west_longitude": -74.001, "east_longitude": -73.983},
        },
        "scraper": {"request_delay_seconds": 0},
        "discord": {},
    }

    def test_seen_listing_no_coords_gets_backfill(self):
        """Seen listing with no lat/lon gets geoclient lookup and coords stored."""
        seen = {
            "https://streeteasy.com/building/test/1": {
                "first_seen": "2026-01-01T00:00:00+00:00",
                "address": "123 East 10th Street",
                "price": "$3,000",
                "neighborhood": "East Village",
                # no latitude/longitude
            },
        }
        listing = {
            "url": "https://streeteasy.com/building/test/1",
            "address": "123 East 10th Street",
            "price": "$3,000",
            "neighborhood": "East Village",
        }
        with patch("apartment_tracker.geoclient_lookup") as mock_geo:
            mock_geo.return_value = {"cross_streets": None, "latitude": 40.73, "longitude": -73.99}
            with patch("apartment_tracker.is_within_geo_bounds", return_value=True):
                url = listing["url"]
                # Simulate the seen-listing path
                seen[url]["last_scraped"] = datetime.now(timezone.utc).isoformat()
                geoclient_key = "fake-key"
                if geoclient_key and "latitude" not in seen[url]:
                    geo = at.geoclient_lookup(seen[url].get("address", ""), geoclient_key)
                    if geo and geo["latitude"] and geo["longitude"]:
                        seen[url]["latitude"] = geo["latitude"]
                        seen[url]["longitude"] = geo["longitude"]

        assert seen[url]["latitude"] == 40.73
        assert seen[url]["longitude"] == -73.99

    def test_seen_listing_outside_bounds_removed(self):
        """Seen listing that resolves to outside geo bounds gets removed."""
        seen = {
            "https://streeteasy.com/building/test/1": {
                "first_seen": "2026-01-01T00:00:00+00:00",
                "address": "500 East 10th Street",
                "price": "$3,000",
                "neighborhood": "East Village",
            },
        }
        url = "https://streeteasy.com/building/test/1"
        geoclient_key = "fake-key"
        config = self.CONFIG
        with patch("apartment_tracker.geoclient_lookup") as mock_geo:
            # Longitude east of bounds (Ave A/B territory)
            mock_geo.return_value = {"cross_streets": None, "latitude": 40.73, "longitude": -73.978}
            seen[url]["last_scraped"] = datetime.now(timezone.utc).isoformat()
            if geoclient_key and "latitude" not in seen[url]:
                geo = at.geoclient_lookup(seen[url].get("address", ""), geoclient_key)
                if geo and geo["latitude"] and geo["longitude"]:
                    seen[url]["latitude"] = geo["latitude"]
                    seen[url]["longitude"] = geo["longitude"]
                if geo and not at.is_within_geo_bounds(geo.get("longitude"), config):
                    del seen[url]

        assert url not in seen

    def test_geoclient_failure_no_removal(self):
        """If geoclient fails, listing is not removed and proceeds normally."""
        seen = {
            "https://streeteasy.com/building/test/1": {
                "first_seen": "2026-01-01T00:00:00+00:00",
                "address": "123 East 10th Street",
                "price": "$3,000",
                "neighborhood": "East Village",
            },
        }
        url = "https://streeteasy.com/building/test/1"
        geoclient_key = "fake-key"
        config = self.CONFIG
        with patch("apartment_tracker.geoclient_lookup") as mock_geo:
            mock_geo.return_value = None
            seen[url]["last_scraped"] = datetime.now(timezone.utc).isoformat()
            if geoclient_key and "latitude" not in seen[url]:
                geo = at.geoclient_lookup(seen[url].get("address", ""), geoclient_key)
                if geo and geo["latitude"] and geo["longitude"]:
                    seen[url]["latitude"] = geo["latitude"]
                    seen[url]["longitude"] = geo["longitude"]
                if geo and not at.is_within_geo_bounds(geo.get("longitude"), config):
                    del seen[url]

        assert url in seen
