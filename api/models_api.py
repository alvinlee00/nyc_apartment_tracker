"""Pydantic schemas for the REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Device registration
# ---------------------------------------------------------------------------

class DeviceRegisterRequest(BaseModel):
    apns_token: str = Field(..., min_length=1)
    device_name: str | None = None


class DeviceRegisterResponse(BaseModel):
    device_id: str
    created: bool


# ---------------------------------------------------------------------------
# Device preferences / filters
# ---------------------------------------------------------------------------

class GeoBounds(BaseModel):
    west_longitude: float
    east_longitude: float
    apply_to: list[str] = []


class FiltersUpdate(BaseModel):
    neighborhoods: list[str] | None = None
    min_price: int | None = None
    max_price: int | None = None
    bed_rooms: list[str] | None = None
    no_fee: bool | None = None
    geo_bounds: GeoBounds | None = None


class NotificationSettingsUpdate(BaseModel):
    new_listings: bool | None = None
    price_drops: bool | None = None
    daily_digest: bool | None = None


class DevicePreferencesResponse(BaseModel):
    device_id: str
    subscribed: bool
    filters: dict
    notification_settings: dict


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

class PriceHistoryEntry(BaseModel):
    price: int
    date: str


class SubwayStation(BaseModel):
    name: str
    routes: list[str]
    distance_mi: float


class ListingSummary(BaseModel):
    id: str
    url: str
    address: str
    price: str
    beds: str
    baths: str
    sqft: str
    neighborhood: str
    image_url: str
    source: str = "streeteasy"
    first_seen: str | None = None
    days_on_market: int | None = None
    value_score: float | None = None
    value_grade: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class ListingDetail(ListingSummary):
    cross_streets: str | None = None
    price_history: list[PriceHistoryEntry] = []
    nearby_stations: list[SubwayStation] = []
    google_maps_url: str | None = None


class ListingsPage(BaseModel):
    listings: list[ListingSummary]
    total: int
    page: int
    per_page: int
    has_more: bool


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class NeighborhoodInfo(BaseModel):
    slug: str
    name: str
    borough: str


class AvenueInfo(BaseModel):
    name: str
    longitude: float


# ---------------------------------------------------------------------------
# Push dispatch (internal)
# ---------------------------------------------------------------------------

class PushDispatchRequest(BaseModel):
    listings: list[dict] = []
    price_drops: list[dict] = []


class PushDispatchResponse(BaseModel):
    devices_notified: int
    pushes_sent: int
