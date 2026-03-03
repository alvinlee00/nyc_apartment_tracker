"""REST API routes for the NYC Apartment Tracker iOS backend."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from api.models_api import (
    AvenueInfo,
    DevicePreferencesResponse,
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    FiltersUpdate,
    ListingDetail,
    ListingSummary,
    ListingsPage,
    NeighborhoodInfo,
    NotificationSettingsUpdate,
    PriceHistoryEntry,
    PushDispatchRequest,
    PushDispatchResponse,
    SubwayStation,
)
from api.push import send_new_listing_push, send_price_drop_push

log = logging.getLogger("api.routes")

router = APIRouter()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _get_db():
    """Return the MongoDB database instance."""
    import db as db_module
    return db_module.get_db()


def _require_device_id(x_device_id: str = Header(...)) -> str:
    """Extract and validate the X-Device-ID header."""
    if not x_device_id or len(x_device_id) < 8:
        raise HTTPException(status_code=400, detail="Invalid X-Device-ID header")
    return x_device_id


def _require_api_secret(x_api_secret: str = Header(...)) -> str:
    """Validate the X-API-Secret header for internal endpoints."""
    expected = os.environ.get("PUSH_API_SECRET", "")
    if not expected:
        raise HTTPException(status_code=503, detail="PUSH_API_SECRET not configured")
    if x_api_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid API secret")
    return x_api_secret


# ---------------------------------------------------------------------------
# Device collection helpers
# ---------------------------------------------------------------------------

def _device_col(db=None):
    if db is None:
        db = _get_db()
    return db.device_preferences


def _get_device(device_id: str, db=None):
    doc = _device_col(db).find_one({"device_id": device_id})
    if doc:
        doc.pop("_id", None)
    return doc


# ---------------------------------------------------------------------------
# Device endpoints
# ---------------------------------------------------------------------------

@router.post("/devices/register", response_model=DeviceRegisterResponse)
def register_device(body: DeviceRegisterRequest, x_device_id: str = Header(...)):
    """Register a device and its APNs token. Creates or updates."""
    from models import DEFAULT_FILTERS, DEFAULT_NOTIFICATION_SETTINGS

    device_id = x_device_id
    if not device_id or len(device_id) < 8:
        raise HTTPException(status_code=400, detail="Invalid X-Device-ID header")

    db = _get_db()
    existing = _get_device(device_id, db)

    now = datetime.now(timezone.utc)
    if existing:
        _device_col(db).update_one(
            {"device_id": device_id},
            {"$set": {
                "apns_token": body.apns_token,
                "device_name": body.device_name,
                "updated_at": now,
            }},
        )
        return DeviceRegisterResponse(device_id=device_id, created=False)

    doc = {
        "device_id": device_id,
        "apns_token": body.apns_token,
        "device_name": body.device_name,
        "subscribed": True,
        "created_at": now,
        "updated_at": now,
        "filters": {**DEFAULT_FILTERS},
        "notification_settings": {**DEFAULT_NOTIFICATION_SETTINGS},
    }
    _device_col(db).insert_one(doc)
    return DeviceRegisterResponse(device_id=device_id, created=True)


@router.get("/devices/me", response_model=DevicePreferencesResponse)
def get_device_preferences(device_id: str = Depends(_require_device_id)):
    """Get current device preferences."""
    device = _get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")
    return DevicePreferencesResponse(
        device_id=device["device_id"],
        subscribed=device.get("subscribed", True),
        filters=device.get("filters", {}),
        notification_settings=device.get("notification_settings", {}),
    )


@router.put("/devices/me/filters", response_model=DevicePreferencesResponse)
def update_filters(body: FiltersUpdate, device_id: str = Depends(_require_device_id)):
    """Update device filter preferences (partial update)."""
    device = _get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    filters = device.get("filters", {})
    update_data = body.model_dump(exclude_none=True)

    # Handle geo_bounds specially — convert Pydantic model to dict
    if "geo_bounds" in update_data and update_data["geo_bounds"] is not None:
        update_data["geo_bounds"] = body.geo_bounds.model_dump()
    elif body.geo_bounds is None and "geo_bounds" in body.model_fields_set:
        update_data["geo_bounds"] = None

    filters.update(update_data)

    db = _get_db()
    _device_col(db).update_one(
        {"device_id": device_id},
        {"$set": {"filters": filters, "updated_at": datetime.now(timezone.utc)}},
    )

    updated = _get_device(device_id, db)
    return DevicePreferencesResponse(
        device_id=device_id,
        subscribed=updated.get("subscribed", True),
        filters=updated.get("filters", {}),
        notification_settings=updated.get("notification_settings", {}),
    )


@router.put("/devices/me/notifications", response_model=DevicePreferencesResponse)
def update_notifications(body: NotificationSettingsUpdate, device_id: str = Depends(_require_device_id)):
    """Update device notification settings (partial update)."""
    device = _get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    settings = device.get("notification_settings", {})
    settings.update(body.model_dump(exclude_none=True))

    db = _get_db()
    _device_col(db).update_one(
        {"device_id": device_id},
        {"$set": {"notification_settings": settings, "updated_at": datetime.now(timezone.utc)}},
    )

    updated = _get_device(device_id, db)
    return DevicePreferencesResponse(
        device_id=device_id,
        subscribed=updated.get("subscribed", True),
        filters=updated.get("filters", {}),
        notification_settings=updated.get("notification_settings", {}),
    )


@router.delete("/devices/me", status_code=204)
def unsubscribe_device(device_id: str = Depends(_require_device_id)):
    """Unsubscribe and remove device."""
    db = _get_db()
    result = _device_col(db).delete_one({"device_id": device_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Device not registered")


# ---------------------------------------------------------------------------
# Listing endpoints
# ---------------------------------------------------------------------------

def _listing_from_doc(url: str, doc: dict) -> ListingSummary:
    """Convert a seen_listings MongoDB document to a ListingSummary."""
    from apartment_tracker import compute_days_on_market, compute_neighborhood_medians, compute_value_score, parse_price

    dom = compute_days_on_market(doc.get("first_seen"))

    return ListingSummary(
        id=url,
        url=url,
        address=doc.get("address", "Unknown"),
        price=doc.get("price", "N/A"),
        beds=doc.get("beds", "N/A"),
        baths=doc.get("baths", "N/A"),
        sqft=doc.get("sqft", "N/A"),
        neighborhood=doc.get("neighborhood", ""),
        image_url=doc.get("image_url", ""),
        source=doc.get("source", "streeteasy"),
        first_seen=doc.get("first_seen"),
        days_on_market=dom,
        value_score=doc.get("value_score"),
        value_grade=doc.get("value_grade"),
        latitude=doc.get("latitude"),
        longitude=doc.get("longitude"),
    )


@router.get("/listings", response_model=ListingsPage)
def get_listings(
    device_id: str = Depends(_require_device_id),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    sort: str = Query("newest", pattern="^(newest|price_asc|price_desc|value)$"),
):
    """Get matched listings for a device, paginated and server-side filtered."""
    from models import listing_matches_user
    from apartment_tracker import parse_price

    device = _get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered")

    db = _get_db()
    all_docs = list(db.seen_listings.find())

    # Filter to user's preferences
    matched = []
    for doc in all_docs:
        doc.pop("_id", None)
        url = doc.pop("url", "")
        if not url:
            continue
        # Build a listing-like dict for matching
        if listing_matches_user(doc, device):
            matched.append((url, doc))

    # Sort
    if sort == "newest":
        matched.sort(key=lambda x: x[1].get("first_seen", ""), reverse=True)
    elif sort == "price_asc":
        matched.sort(key=lambda x: parse_price(x[1].get("price", "")) or 99999)
    elif sort == "price_desc":
        matched.sort(key=lambda x: parse_price(x[1].get("price", "")) or 0, reverse=True)
    elif sort == "value":
        matched.sort(key=lambda x: x[1].get("value_score") or 0, reverse=True)

    total = len(matched)
    start = (page - 1) * per_page
    page_items = matched[start : start + per_page]

    listings = [_listing_from_doc(url, doc) for url, doc in page_items]

    return ListingsPage(
        listings=listings,
        total=total,
        page=page,
        per_page=per_page,
        has_more=start + per_page < total,
    )


@router.get("/listings/{listing_id:path}", response_model=ListingDetail)
def get_listing_detail(listing_id: str, device_id: str = Depends(_require_device_id)):
    """Get full detail for a single listing."""
    from apartment_tracker import (
        build_google_maps_url,
        compute_days_on_market,
        compute_neighborhood_medians,
        compute_value_score,
        find_nearby_stations,
        _load_subway_stations,
    )

    db = _get_db()
    doc = db.seen_listings.find_one({"url": listing_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Listing not found")
    doc.pop("_id", None)

    dom = compute_days_on_market(doc.get("first_seen"))

    # Subway stations
    nearby = []
    lat, lon = doc.get("latitude"), doc.get("longitude")
    if lat and lon:
        stations = _load_subway_stations()
        raw_nearby = find_nearby_stations(lat, lon, stations)
        nearby = [
            SubwayStation(name=s["name"], routes=s["routes"], distance_mi=s["distance_mi"])
            for s in raw_nearby
        ]

    # Price history
    price_history = [
        PriceHistoryEntry(price=ph["price"], date=ph["date"])
        for ph in doc.get("price_history", [])
    ]

    maps_url = build_google_maps_url(doc.get("address", "")) if doc.get("address") else None

    return ListingDetail(
        id=listing_id,
        url=listing_id,
        address=doc.get("address", "Unknown"),
        price=doc.get("price", "N/A"),
        beds=doc.get("beds", "N/A"),
        baths=doc.get("baths", "N/A"),
        sqft=doc.get("sqft", "N/A"),
        neighborhood=doc.get("neighborhood", ""),
        image_url=doc.get("image_url", ""),
        source=doc.get("source", "streeteasy"),
        first_seen=doc.get("first_seen"),
        days_on_market=dom,
        value_score=doc.get("value_score"),
        value_grade=doc.get("value_grade"),
        latitude=lat,
        longitude=lon,
        cross_streets=doc.get("cross_streets"),
        price_history=price_history,
        nearby_stations=nearby,
        google_maps_url=maps_url,
    )


# ---------------------------------------------------------------------------
# Meta endpoints
# ---------------------------------------------------------------------------

BOROUGH_MAP = {
    "battery-park-city": "Manhattan", "carnegie-hill": "Manhattan", "chelsea": "Manhattan",
    "chinatown": "Manhattan", "civic-center": "Manhattan", "east-village": "Manhattan",
    "financial-district": "Manhattan", "flatiron": "Manhattan", "fulton-seaport": "Manhattan",
    "gramercy-park": "Manhattan", "greenwich-village": "Manhattan", "hells-kitchen": "Manhattan",
    "hudson-yards": "Manhattan", "kips-bay": "Manhattan", "lenox-hill": "Manhattan",
    "les": "Manhattan", "little-italy": "Manhattan", "manhattan-valley": "Manhattan",
    "midtown": "Manhattan", "midtown-east": "Manhattan", "midtown-south": "Manhattan",
    "midtown-west": "Manhattan", "murray-hill": "Manhattan", "noho": "Manhattan",
    "nolita": "Manhattan", "nomad": "Manhattan", "soho": "Manhattan",
    "stuyvesant-town": "Manhattan", "tribeca": "Manhattan", "two-bridges": "Manhattan",
    "upper-east-side": "Manhattan", "upper-west-side": "Manhattan", "west-village": "Manhattan",
    "yorkville": "Manhattan",
    "east-harlem": "Upper Manhattan", "hamilton-heights": "Upper Manhattan",
    "harlem": "Upper Manhattan", "inwood": "Upper Manhattan",
    "morningside-heights": "Upper Manhattan", "washington-heights": "Upper Manhattan",
    "bay-ridge": "Brooklyn", "bed-stuy": "Brooklyn", "boerum-hill": "Brooklyn",
    "brooklyn-heights": "Brooklyn", "bushwick": "Brooklyn", "carroll-gardens": "Brooklyn",
    "clinton-hill": "Brooklyn", "cobble-hill": "Brooklyn", "crown-heights": "Brooklyn",
    "downtown-brooklyn": "Brooklyn", "dumbo": "Brooklyn", "flatbush": "Brooklyn",
    "fort-greene": "Brooklyn", "gowanus": "Brooklyn", "greenpoint": "Brooklyn",
    "kensington": "Brooklyn", "park-slope": "Brooklyn", "prospect-heights": "Brooklyn",
    "red-hook": "Brooklyn", "sunset-park": "Brooklyn", "williamsburg": "Brooklyn",
    "windsor-terrace": "Brooklyn",
    "astoria": "Queens", "flushing": "Queens", "forest-hills": "Queens",
    "jackson-heights": "Queens", "long-island-city": "Queens", "ridgewood": "Queens",
    "sunnyside": "Queens", "woodside": "Queens",
}


@router.get("/meta/neighborhoods", response_model=list[NeighborhoodInfo])
def get_neighborhoods():
    """Return all valid neighborhoods grouped by borough."""
    from models import VALID_NEIGHBORHOODS
    return [
        NeighborhoodInfo(slug=slug, name=name, borough=BOROUGH_MAP.get(slug, "Other"))
        for slug, name in VALID_NEIGHBORHOODS.items()
    ]


@router.get("/meta/avenues", response_model=list[AvenueInfo])
def get_avenues():
    """Return Manhattan avenues with longitudes for geo filter."""
    from models import MANHATTAN_AVENUES
    return [
        AvenueInfo(name=name, longitude=lon)
        for name, lon in MANHATTAN_AVENUES.items()
    ]


# ---------------------------------------------------------------------------
# Internal push dispatch
# ---------------------------------------------------------------------------

@router.post("/internal/push-dispatch", response_model=PushDispatchResponse)
async def push_dispatch(body: PushDispatchRequest, _secret: str = Depends(_require_api_secret)):
    """Receive new listings/price drops from the scraper and send push notifications."""
    from models import listing_matches_user

    db = _get_db()
    devices = list(_device_col(db).find({"subscribed": True}))

    devices_notified = 0
    pushes_sent = 0

    for device in devices:
        device.pop("_id", None)
        apns_token = device.get("apns_token")
        if not apns_token:
            continue

        notif_settings = device.get("notification_settings", {})
        device_sent = False

        # New listing pushes
        if notif_settings.get("new_listings", True):
            for listing in body.listings:
                if listing_matches_user(listing, device):
                    success = await send_new_listing_push(apns_token, listing)
                    if success:
                        pushes_sent += 1
                        device_sent = True

        # Price drop pushes
        if notif_settings.get("price_drops", True):
            for drop in body.price_drops:
                listing = drop.get("listing", {})
                price_change = drop.get("price_change", {})
                if listing_matches_user(listing, device):
                    success = await send_price_drop_push(apns_token, listing, price_change)
                    if success:
                        pushes_sent += 1
                        device_sent = True

        if device_sent:
            devices_notified += 1

    log.info("Push dispatch: %d pushes sent to %d devices", pushes_sent, devices_notified)
    return PushDispatchResponse(devices_notified=devices_notified, pushes_sent=pushes_sent)
