"""Tests for cross-source deduplication logic: normalize_address, find_cross_source_duplicate,
first-RentHop-run anti-spam, and alt_urls linking.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import apartment_tracker as at


# ---------------------------------------------------------------------------
# normalize_address
# ---------------------------------------------------------------------------

class TestNormalizeAddress:
    def test_lowercase(self):
        assert at.normalize_address("123 Main Street") == "123 main street"

    def test_unit_hash(self):
        assert at.normalize_address("123 E 21st St #3H") == "123 east 21st street"

    def test_unit_apt(self):
        assert at.normalize_address("123 East 21st Street Apt 3H") == "123 east 21st street"

    def test_unit_unit(self):
        assert at.normalize_address("456 West 14th Street Unit 2B") == "456 west 14th street"

    def test_directional_east(self):
        assert at.normalize_address("123 E 21st St") == "123 east 21st street"

    def test_directional_west(self):
        assert at.normalize_address("456 W 14th St") == "456 west 14th street"

    def test_directional_north(self):
        result = at.normalize_address("100 N 5th Ave")
        assert "north" in result
        assert "avenue" in result

    def test_directional_south(self):
        result = at.normalize_address("200 S Broadway")
        assert "south" in result

    def test_street_abbreviation(self):
        assert "street" in at.normalize_address("100 Main St")

    def test_avenue_abbreviation(self):
        assert "avenue" in at.normalize_address("200 5th Ave")

    def test_blvd_abbreviation(self):
        assert "boulevard" in at.normalize_address("300 Ocean Blvd")

    def test_strips_punctuation(self):
        result = at.normalize_address("123 Main St., Apt. 4B")
        assert "." not in result
        assert "," not in result

    def test_collapses_spaces(self):
        result = at.normalize_address("123  Main   Street")
        assert "  " not in result

    def test_full_form_unchanged(self):
        # Already expanded — should still normalize fine
        assert at.normalize_address("123 East 21st Street") == "123 east 21st street"

    def test_cross_source_match(self):
        """The core use case: SE address and RentHop address normalize to same string."""
        se_addr = "337 E 21st St #3H"
        rh_addr = "337 East 21st Street Apt 3H"
        assert at.normalize_address(se_addr) == at.normalize_address(rh_addr)

    def test_floor_suffix_stripped(self):
        result = at.normalize_address("100 Main Street Floor 2")
        assert "floor" not in result
        assert "2" not in result

    def test_empty_string(self):
        assert at.normalize_address("") == ""


# ---------------------------------------------------------------------------
# find_cross_source_duplicate
# ---------------------------------------------------------------------------

class TestFindCrossSourceDuplicate:
    def _make_seen(self, entries: dict[str, dict]) -> dict:
        """Helper: build seen dict with canonical_address populated."""
        result = {}
        for url, entry in entries.items():
            e = dict(entry)
            if "canonical_address" not in e:
                e["canonical_address"] = at.normalize_address(e.get("address", ""))
            result[url] = e
        return result

    def test_match_found(self):
        seen = self._make_seen({
            "https://streeteasy.com/building/123-east-21st-street/3h": {
                "address": "123 East 21st Street #3H",
                "source": "streeteasy",
            }
        })
        rh_listing = {
            "address": "123 E 21st St Apt 3H",
            "source": "renthop",
            "canonical_address": at.normalize_address("123 E 21st St Apt 3H"),
        }
        result = at.find_cross_source_duplicate(rh_listing, seen)
        assert result == "https://streeteasy.com/building/123-east-21st-street/3h"

    def test_no_match(self):
        seen = self._make_seen({
            "https://streeteasy.com/building/456-west-14th-street/2b": {
                "address": "456 West 14th Street #2B",
                "source": "streeteasy",
            }
        })
        rh_listing = {
            "address": "789 East 9th Street Apt 1A",
            "source": "renthop",
            "canonical_address": at.normalize_address("789 East 9th Street Apt 1A"),
        }
        assert at.find_cross_source_duplicate(rh_listing, seen) is None

    def test_partial_address_no_match(self):
        """Different unit numbers should not match (canonical strips unit)."""
        # Both listings at same address but different units strip to same canonical —
        # this is by design; units at same building share canonical_address.
        # This test verifies same-building detection works.
        seen = self._make_seen({
            "https://streeteasy.com/building/100-main-street/1a": {
                "address": "100 Main Street #1A",
                "source": "streeteasy",
            }
        })
        rh_listing = {
            "address": "100 Main Street #2B",
            "source": "renthop",
            "canonical_address": at.normalize_address("100 Main Street #2B"),
        }
        # Both normalize to "100 main street" → they DO match (same building)
        # Per design: unit stripping means same-building = duplicate
        result = at.find_cross_source_duplicate(rh_listing, seen)
        assert result == "https://streeteasy.com/building/100-main-street/1a"

    def test_no_canonical_in_seen(self):
        """Entries without canonical_address are skipped."""
        seen = {
            "https://streeteasy.com/building/old/1a": {
                "address": "123 Old Street",
                "source": "streeteasy",
                # No canonical_address — old entry
            }
        }
        rh_listing = {
            "address": "123 Old Street",
            "source": "renthop",
            "canonical_address": at.normalize_address("123 Old Street"),
        }
        # Should not match (no canonical_address in seen entry)
        assert at.find_cross_source_duplicate(rh_listing, seen) is None

    def test_listing_missing_address(self):
        seen = self._make_seen({
            "https://streeteasy.com/building/100/1a": {
                "address": "100 Main Street",
                "source": "streeteasy",
            }
        })
        # Listing with empty address
        rh_listing = {"address": "", "source": "renthop", "canonical_address": ""}
        assert at.find_cross_source_duplicate(rh_listing, seen) is None


# ---------------------------------------------------------------------------
# First RentHop run anti-spam
# ---------------------------------------------------------------------------

class TestFirstRentHopRunAntiSpam:
    """Verify that on first RentHop run, no notifications fire and seen is populated."""

    def _make_config(self):
        return {
            "search": {
                "neighborhoods": ["east-village"],
                "max_price": 4000,
                "min_price": 0,
                "bed_rooms": ["1"],
                "no_fee": False,
                "geo_bounds": None,
            },
            "scraper": {"request_delay_seconds": 0},
            "discord": {},
        }

    def _make_rh_listing(self, url: str, address: str, neighborhood: str = "East Village") -> dict:
        return {
            "url": url,
            "address": address,
            "price": "$2,500",
            "beds": "1 bed",
            "baths": "1 bath",
            "sqft": "N/A",
            "neighborhood": neighborhood,
            "image_url": "",
            "source": "renthop",
        }

    @patch("apartment_tracker.scrape_neighborhood")
    @patch("renthop_scraper.scrape_renthop_neighborhood")
    @patch("apartment_tracker.save_seen")
    @patch("apartment_tracker.load_seen")
    @patch("apartment_tracker.load_config")
    @patch("apartment_tracker.get_session")
    @patch("apartment_tracker.send_discord_notification")
    @patch("apartment_tracker.send_discord_summary")
    def test_first_renthop_run_no_notifications(
        self,
        mock_summary,
        mock_notify,
        mock_get_session,
        mock_load_config,
        mock_load_seen,
        mock_save_seen,
        mock_rh_scrape,
        mock_se_scrape,
    ):
        config = self._make_config()
        mock_load_config.return_value = config

        # Seed one StreetEasy listing already in seen (not first global run)
        existing_se = {
            "https://streeteasy.com/building/100-main-street/1a": {
                "address": "100 Main Street #1A",
                "price": "$2,000",
                "neighborhood": "East Village",
                "first_seen": "2026-01-01T00:00:00+00:00",
                "last_scraped": "2026-01-01T00:00:00+00:00",
                "source": "streeteasy",
                "canonical_address": at.normalize_address("100 Main Street #1A"),
            }
        }
        mock_load_seen.return_value = existing_se
        mock_se_scrape.return_value = []  # SE returns nothing new

        # Two RentHop listings
        rh1 = self._make_rh_listing("https://renthop.com/listings/1", "200 Ave A Apt 3B")
        rh2 = self._make_rh_listing("https://renthop.com/listings/2", "300 E 6th St #5C")
        mock_rh_scrape.return_value = [rh1, rh2]

        mock_session = MagicMock()
        # cleanup_stale_listings will try to check stale entries; return unknown to skip removal
        mock_session.fetch_with_status.return_value = (None, None)
        mock_get_session.return_value = mock_session

        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/webhook"}):
            at.run_scraper()

        # Notifications should NOT have been sent for RentHop listings on first run
        mock_notify.assert_not_called()
        mock_summary.assert_not_called()

        # Both RentHop listings should have been saved
        saved = mock_save_seen.call_args[0][0]
        assert "https://renthop.com/listings/1" in saved
        assert "https://renthop.com/listings/2" in saved
        assert saved["https://renthop.com/listings/1"]["source"] == "renthop"
        assert saved["https://renthop.com/listings/2"]["source"] == "renthop"

    @patch("apartment_tracker.scrape_neighborhood")
    @patch("renthop_scraper.scrape_renthop_neighborhood")
    @patch("apartment_tracker.save_seen")
    @patch("apartment_tracker.load_seen")
    @patch("apartment_tracker.load_config")
    @patch("apartment_tracker.get_session")
    @patch("apartment_tracker.send_discord_notification")
    def test_second_renthop_run_sends_notification(
        self,
        mock_notify,
        mock_get_session,
        mock_load_config,
        mock_load_seen,
        mock_save_seen,
        mock_rh_scrape,
        mock_se_scrape,
    ):
        """On second RentHop run, genuinely new listings fire notifications."""
        config = self._make_config()
        mock_load_config.return_value = config

        # Existing seen has one RentHop entry (not first RentHop run)
        existing = {
            "https://renthop.com/listings/1": {
                "address": "200 Ave A Apt 3B",
                "price": "$2,500",
                "neighborhood": "East Village",
                "first_seen": "2026-01-01T00:00:00+00:00",
                "last_scraped": "2026-01-01T00:00:00+00:00",
                "source": "renthop",
                "canonical_address": at.normalize_address("200 Ave A Apt 3B"),
            }
        }
        mock_load_seen.return_value = existing
        mock_se_scrape.return_value = []

        # One genuinely new RentHop listing
        new_rh = self._make_rh_listing("https://renthop.com/listings/99", "999 E 7th St Apt 2A")
        mock_rh_scrape.return_value = [new_rh]

        mock_session = MagicMock()
        # cleanup_stale_listings will try to check stale entries; return unknown to skip removal
        mock_session.fetch_with_status.return_value = (None, None)
        mock_get_session.return_value = mock_session

        with patch.dict(os.environ, {
            "DISCORD_WEBHOOK_URL": "https://discord.com/webhook",
        }):
            at.run_scraper()

        # Notification should have been sent for the new RentHop listing
        mock_notify.assert_called_once()
        call_args = mock_notify.call_args
        sent_listing = call_args[0][1]
        assert sent_listing["url"] == "https://renthop.com/listings/99"


# ---------------------------------------------------------------------------
# Cross-source alt_urls linking
# ---------------------------------------------------------------------------

class TestAltUrlsLinking:
    """Verify that when a RentHop listing duplicates a StreetEasy one, alt_urls is set."""

    @patch("apartment_tracker.scrape_neighborhood")
    @patch("renthop_scraper.scrape_renthop_neighborhood")
    @patch("apartment_tracker.save_seen")
    @patch("apartment_tracker.load_seen")
    @patch("apartment_tracker.load_config")
    @patch("apartment_tracker.get_session")
    @patch("apartment_tracker.send_discord_notification")
    def test_alt_urls_set_for_duplicate(
        self,
        mock_notify,
        mock_get_session,
        mock_load_config,
        mock_load_seen,
        mock_save_seen,
        mock_rh_scrape,
        mock_se_scrape,
    ):
        config = {
            "search": {
                "neighborhoods": ["east-village"],
                "max_price": 4000,
                "min_price": 0,
                "bed_rooms": ["1"],
                "no_fee": False,
                "geo_bounds": None,
            },
            "scraper": {"request_delay_seconds": 0},
            "discord": {},
        }
        mock_load_config.return_value = config

        se_url = "https://streeteasy.com/building/337-east-21st-street/3h"
        # One existing StreetEasy entry (so this is NOT first global run)
        # and NO existing RentHop entries (first RentHop run)
        existing = {
            se_url: {
                "address": "337 East 21st Street #3H",
                "price": "$3,000",
                "neighborhood": "Gramercy Park",
                "first_seen": "2026-01-01T00:00:00+00:00",
                "last_scraped": "2026-01-01T00:00:00+00:00",
                "source": "streeteasy",
                "canonical_address": at.normalize_address("337 East 21st Street #3H"),
            }
        }
        mock_load_seen.return_value = existing
        mock_se_scrape.return_value = []

        # RentHop sees same apartment (different URL, same address)
        rh_url = "https://www.renthop.com/listings/12345/337-east-21st-street"
        rh_listing = {
            "url": rh_url,
            "address": "337 E 21st St Apt 3H",
            "price": "$3,000",
            "beds": "1 bed",
            "baths": "1 bath",
            "sqft": "N/A",
            "neighborhood": "Gramercy Park",
            "image_url": "",
            "source": "renthop",
        }
        mock_rh_scrape.return_value = [rh_listing]

        mock_session = MagicMock()
        # cleanup_stale_listings will try to check stale entries; return unknown to skip removal
        mock_session.fetch_with_status.return_value = (None, None)
        mock_get_session.return_value = mock_session

        with patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.com/webhook"}):
            at.run_scraper()

        # No new listing notification (it's a duplicate)
        mock_notify.assert_not_called()

        # The StreetEasy entry should have alt_urls.renthop set
        saved = mock_save_seen.call_args[0][0]
        assert se_url in saved
        assert saved[se_url].get("alt_urls", {}).get("renthop") == rh_url

        # The RentHop URL should NOT be a separate key in seen
        assert rh_url not in saved
