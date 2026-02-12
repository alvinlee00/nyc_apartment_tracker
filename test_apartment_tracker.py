"""Tests for apartment_tracker.py — filtering, parsing, and core logic."""

import json
import tempfile
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
# fetch_cross_streets (Geoclient API)
# ---------------------------------------------------------------------------

class TestFetchCrossStreets:
    def test_returns_formatted_cross_streets(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "address": {
                "lowCrossStreetName1": "2 AVENUE",
                "highCrossStreetName1": "1 AVENUE",
            }
        }
        mock_response.raise_for_status = MagicMock()
        with patch("apartment_tracker.requests.get", return_value=mock_response) as mock_get:
            result = at.fetch_cross_streets("337 East 21st Street #3H", "fake-key")
            assert result == "between 2 Avenue & 1 Avenue"
            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args
            assert call_kwargs[1]["params"]["houseNumber"] == "337"
            assert call_kwargs[1]["params"]["street"] == "East 21st Street"

    def test_returns_none_on_missing_fields(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"address": {}}
        mock_response.raise_for_status = MagicMock()
        with patch("apartment_tracker.requests.get", return_value=mock_response):
            result = at.fetch_cross_streets("337 East 21st Street #3H", "fake-key")
            assert result is None

    def test_returns_none_on_unparseable_address(self):
        result = at.fetch_cross_streets("No Number Here", "fake-key")
        assert result is None

    def test_returns_none_on_api_error(self):
        with patch("apartment_tracker.requests.get", side_effect=at.requests.RequestException("timeout")):
            result = at.fetch_cross_streets("337 East 21st Street #3H", "fake-key")
            assert result is None


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
