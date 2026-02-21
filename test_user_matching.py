"""Tests for models.py — listing-to-user matching logic."""

import pytest

from models import listing_matches_user, VALID_NEIGHBORHOODS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(neighborhoods=None, min_price=0, max_price=0, bed_rooms=None,
          no_fee=False, geo_bounds=None):
    """Build a minimal user_prefs dict for testing."""
    return {
        "filters": {
            "neighborhoods": neighborhoods or [],
            "min_price": min_price,
            "max_price": max_price,
            "bed_rooms": bed_rooms or [],
            "no_fee": no_fee,
            "geo_bounds": geo_bounds,
        }
    }


def _listing(neighborhood="East Village", price="$3,000", beds="1 bed",
             latitude=None, longitude=None, url="https://streeteasy.com/test"):
    return {
        "address": "123 Test St",
        "price": price,
        "neighborhood": neighborhood,
        "beds": beds,
        "url": url,
        "latitude": latitude,
        "longitude": longitude,
    }


# ---------------------------------------------------------------------------
# Neighborhood matching
# ---------------------------------------------------------------------------

class TestNeighborhoodMatching:
    def test_exact_neighborhood_match(self):
        user = _user(neighborhoods=["east-village"])
        listing = _listing(neighborhood="East Village")
        assert listing_matches_user(listing, user) is True

    def test_sub_neighborhood_match(self):
        """Manhattan Valley is a sub-neighborhood of UWS."""
        user = _user(neighborhoods=["upper-west-side"])
        listing = _listing(neighborhood="Manhattan Valley")
        assert listing_matches_user(listing, user) is True

    def test_lincoln_square_matches_uws(self):
        user = _user(neighborhoods=["upper-west-side"])
        listing = _listing(neighborhood="Lincoln Square")
        assert listing_matches_user(listing, user) is True

    def test_wrong_neighborhood_no_match(self):
        user = _user(neighborhoods=["east-village"])
        listing = _listing(neighborhood="Upper East Side")
        assert listing_matches_user(listing, user) is False

    def test_empty_listing_neighborhood_no_match(self):
        user = _user(neighborhoods=["east-village"])
        listing = _listing(neighborhood="")
        assert listing_matches_user(listing, user) is False

    def test_empty_user_neighborhoods_matches_all(self):
        """No neighborhood filter = match all neighborhoods."""
        user = _user(neighborhoods=[])
        listing = _listing(neighborhood="Bushwick")
        assert listing_matches_user(listing, user) is True

    def test_multiple_user_neighborhoods(self):
        user = _user(neighborhoods=["east-village", "chelsea"])
        assert listing_matches_user(_listing(neighborhood="East Village"), user) is True
        assert listing_matches_user(_listing(neighborhood="Chelsea"), user) is True
        assert listing_matches_user(_listing(neighborhood="West Chelsea"), user) is True
        assert listing_matches_user(_listing(neighborhood="SoHo"), user) is False

    def test_les_aliases(self):
        user = _user(neighborhoods=["les"])
        assert listing_matches_user(_listing(neighborhood="Lower East Side"), user) is True
        assert listing_matches_user(_listing(neighborhood="Two Bridges"), user) is True
        assert listing_matches_user(_listing(neighborhood="Chinatown"), user) is True
        assert listing_matches_user(_listing(neighborhood="East Village"), user) is False

    def test_ues_aliases(self):
        user = _user(neighborhoods=["upper-east-side"])
        assert listing_matches_user(_listing(neighborhood="Upper East Side"), user) is True
        assert listing_matches_user(_listing(neighborhood="Yorkville"), user) is True
        assert listing_matches_user(_listing(neighborhood="Carnegie Hill"), user) is True
        assert listing_matches_user(_listing(neighborhood="Lenox Hill"), user) is True

    def test_chelsea_aliases(self):
        user = _user(neighborhoods=["chelsea"])
        assert listing_matches_user(_listing(neighborhood="Chelsea"), user) is True
        assert listing_matches_user(_listing(neighborhood="West Chelsea"), user) is True

    def test_gramercy_aliases(self):
        user = _user(neighborhoods=["gramercy-park"])
        assert listing_matches_user(_listing(neighborhood="Gramercy Park"), user) is True
        assert listing_matches_user(_listing(neighborhood="Gramercy"), user) is True
        assert listing_matches_user(_listing(neighborhood="Kips Bay"), user) is True


# ---------------------------------------------------------------------------
# Price filtering
# ---------------------------------------------------------------------------

class TestPriceFiltering:
    def test_within_price_range(self):
        user = _user(max_price=3600)
        listing = _listing(price="$3,000")
        assert listing_matches_user(listing, user) is True

    def test_above_max_price(self):
        user = _user(max_price=3600)
        listing = _listing(price="$4,000")
        assert listing_matches_user(listing, user) is False

    def test_below_min_price(self):
        user = _user(min_price=2000, max_price=3600)
        listing = _listing(price="$1,500")
        assert listing_matches_user(listing, user) is False

    def test_at_max_price(self):
        user = _user(max_price=3600)
        listing = _listing(price="$3,600")
        assert listing_matches_user(listing, user) is True

    def test_no_max_price_matches_all(self):
        user = _user(max_price=0)
        listing = _listing(price="$10,000")
        assert listing_matches_user(listing, user) is True

    def test_unparseable_price_passes(self):
        user = _user(max_price=3600)
        listing = _listing(price="N/A")
        assert listing_matches_user(listing, user) is True


# ---------------------------------------------------------------------------
# Bed type filtering
# ---------------------------------------------------------------------------

class TestBedTypeFiltering:
    def test_studio_match(self):
        user = _user(bed_rooms=["studio"])
        listing = _listing(beds="Studio")
        assert listing_matches_user(listing, user) is True

    def test_one_bed_match(self):
        user = _user(bed_rooms=["1"])
        listing = _listing(beds="1 bed")
        assert listing_matches_user(listing, user) is True

    def test_two_bed_no_match(self):
        user = _user(bed_rooms=["studio", "1"])
        listing = _listing(beds="2 beds")
        assert listing_matches_user(listing, user) is False

    def test_multi_bed_types(self):
        user = _user(bed_rooms=["studio", "1", "2"])
        assert listing_matches_user(_listing(beds="Studio"), user) is True
        assert listing_matches_user(_listing(beds="1 bed"), user) is True
        assert listing_matches_user(_listing(beds="2 beds"), user) is True
        assert listing_matches_user(_listing(beds="3 beds"), user) is False

    def test_no_bed_filter_matches_all(self):
        user = _user(bed_rooms=[])
        listing = _listing(beds="4 beds")
        assert listing_matches_user(listing, user) is True

    def test_na_beds_passes(self):
        """Listings with unknown bed count should pass the filter."""
        user = _user(bed_rooms=["1"])
        listing = _listing(beds="N/A")
        assert listing_matches_user(listing, user) is True


# ---------------------------------------------------------------------------
# Geo bounds filtering
# ---------------------------------------------------------------------------

class TestGeoFiltering:
    BOUNDS = {"west_longitude": -74.001, "east_longitude": -73.983}

    def test_within_bounds(self):
        user = _user(geo_bounds=self.BOUNDS)
        listing = _listing(longitude=-73.990)
        assert listing_matches_user(listing, user) is True

    def test_outside_east(self):
        user = _user(geo_bounds=self.BOUNDS)
        listing = _listing(longitude=-73.980)
        assert listing_matches_user(listing, user) is False

    def test_outside_west(self):
        user = _user(geo_bounds=self.BOUNDS)
        listing = _listing(longitude=-74.005)
        assert listing_matches_user(listing, user) is False

    def test_no_geo_bounds_matches_all(self):
        user = _user(geo_bounds=None)
        listing = _listing(longitude=-73.980)
        assert listing_matches_user(listing, user) is True

    def test_no_listing_coords_passes(self):
        user = _user(geo_bounds=self.BOUNDS)
        listing = _listing(longitude=None)
        assert listing_matches_user(listing, user) is True


# ---------------------------------------------------------------------------
# Multi-filter AND logic
# ---------------------------------------------------------------------------

class TestMultiFilter:
    def test_all_filters_match(self):
        user = _user(
            neighborhoods=["east-village"],
            max_price=3600,
            bed_rooms=["1"],
            geo_bounds={"west_longitude": -74.001, "east_longitude": -73.983},
        )
        listing = _listing(
            neighborhood="East Village",
            price="$3,000",
            beds="1 bed",
            longitude=-73.990,
        )
        assert listing_matches_user(listing, user) is True

    def test_neighborhood_fails_others_pass(self):
        user = _user(neighborhoods=["chelsea"], max_price=3600, bed_rooms=["1"])
        listing = _listing(neighborhood="East Village", price="$3,000", beds="1 bed")
        assert listing_matches_user(listing, user) is False

    def test_price_fails_others_pass(self):
        user = _user(neighborhoods=["east-village"], max_price=2000, bed_rooms=["1"])
        listing = _listing(neighborhood="East Village", price="$3,000", beds="1 bed")
        assert listing_matches_user(listing, user) is False

    def test_beds_fail_others_pass(self):
        user = _user(neighborhoods=["east-village"], max_price=3600, bed_rooms=["studio"])
        listing = _listing(neighborhood="East Village", price="$3,000", beds="2 beds")
        assert listing_matches_user(listing, user) is False

    def test_no_filters_matches_everything(self):
        user = _user()
        listing = _listing(neighborhood="Anywhere", price="$99,000", beds="10 beds")
        assert listing_matches_user(listing, user) is True


# ---------------------------------------------------------------------------
# VALID_NEIGHBORHOODS completeness
# ---------------------------------------------------------------------------

class TestValidNeighborhoods:
    def test_has_manhattan(self):
        assert "east-village" in VALID_NEIGHBORHOODS
        assert "chelsea" in VALID_NEIGHBORHOODS
        assert "upper-west-side" in VALID_NEIGHBORHOODS

    def test_has_brooklyn(self):
        assert "williamsburg" in VALID_NEIGHBORHOODS
        assert "park-slope" in VALID_NEIGHBORHOODS

    def test_has_queens(self):
        assert "astoria" in VALID_NEIGHBORHOODS
        assert "long-island-city" in VALID_NEIGHBORHOODS

    def test_has_upper_manhattan(self):
        assert "harlem" in VALID_NEIGHBORHOODS
        assert "washington-heights" in VALID_NEIGHBORHOODS

    def test_values_are_display_names(self):
        assert VALID_NEIGHBORHOODS["east-village"] == "East Village"
        assert VALID_NEIGHBORHOODS["les"] == "Lower East Side"
        assert VALID_NEIGHBORHOODS["bed-stuy"] == "Bedford-Stuyvesant"
