"""MongoDB connection layer shared by the scraper and Discord bot.

When MONGODB_URI is not set, this module is not used — the scraper falls back
to its original JSON-file persistence.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient, ASCENDING
from pymongo.collection import Collection
from pymongo.database import Database

log = logging.getLogger("apartment_tracker.db")

_client: MongoClient | None = None
_db: Database | None = None


def get_client() -> MongoClient:
    """Return a singleton MongoClient, creating it on first call."""
    global _client
    if _client is None:
        uri = os.environ.get("MONGODB_URI", "")
        if not uri:
            raise RuntimeError("MONGODB_URI environment variable is not set")
        _client = MongoClient(uri)
    return _client


def get_db() -> Database:
    """Return the apartment_tracker database."""
    global _db
    if _db is None:
        client = get_client()
        _db = client["apartment_tracker"]
    return _db


def close():
    """Close the MongoDB connection."""
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None


def ensure_indexes():
    """Create indexes and TTL expiry on first run."""
    db = get_db()

    # seen_listings: unique on url
    db.seen_listings.create_index("url", unique=True)

    # user_preferences: unique on discord_user_id
    db.user_preferences.create_index("discord_user_id", unique=True)

    # notification_log: compound index for dedup lookups + 30-day TTL
    db.notification_log.create_index([
        ("discord_user_id", ASCENDING),
        ("listing_url", ASCENDING),
        ("notification_type", ASCENDING),
    ])
    db.notification_log.create_index("sent_at", expireAfterSeconds=30 * 24 * 3600)


# ---------------------------------------------------------------------------
# seen_listings CRUD
# ---------------------------------------------------------------------------

def _seen_col() -> Collection:
    return get_db().seen_listings


def load_seen_from_mongo() -> dict[str, dict]:
    """Load all seen listings from MongoDB into the same dict format the scraper uses."""
    result: dict[str, dict] = {}
    for doc in _seen_col().find():
        url = doc.pop("url")
        doc.pop("_id", None)
        result[url] = doc
    return result


def save_seen_to_mongo(seen: dict[str, dict]) -> None:
    """Upsert all seen listings to MongoDB."""
    col = _seen_col()
    for url, entry in seen.items():
        doc = {**entry, "url": url}
        col.update_one({"url": url}, {"$set": doc}, upsert=True)


def upsert_seen_listing(url: str, entry: dict) -> None:
    """Upsert a single seen listing."""
    doc = {**entry, "url": url}
    _seen_col().update_one({"url": url}, {"$set": doc}, upsert=True)


def delete_seen_listing(url: str) -> None:
    """Remove a seen listing by URL."""
    _seen_col().delete_one({"url": url})


def get_seen_listing(url: str) -> dict | None:
    """Get a single seen listing by URL."""
    doc = _seen_col().find_one({"url": url})
    if doc:
        doc.pop("_id", None)
        doc.pop("url", None)
    return doc


# ---------------------------------------------------------------------------
# user_preferences CRUD
# ---------------------------------------------------------------------------

def _user_col() -> Collection:
    return get_db().user_preferences


def get_user(discord_user_id: str) -> dict | None:
    """Get a user's preferences by Discord user ID."""
    doc = _user_col().find_one({"discord_user_id": discord_user_id})
    if doc:
        doc.pop("_id", None)
    return doc


def create_user(discord_user_id: str, discord_username: str, filters: dict | None = None,
                notification_settings: dict | None = None) -> dict:
    """Create a new user with default or specified preferences."""
    from models import DEFAULT_FILTERS, DEFAULT_NOTIFICATION_SETTINGS

    now = datetime.now(timezone.utc)
    doc = {
        "discord_user_id": discord_user_id,
        "discord_username": discord_username,
        "subscribed": True,
        "created_at": now,
        "updated_at": now,
        "filters": filters or {**DEFAULT_FILTERS},
        "notification_settings": notification_settings or {**DEFAULT_NOTIFICATION_SETTINGS},
    }
    _user_col().insert_one(doc)
    doc.pop("_id", None)
    return doc


def update_user(discord_user_id: str, updates: dict) -> bool:
    """Update a user's preferences. Returns True if document was found."""
    updates["updated_at"] = datetime.now(timezone.utc)
    result = _user_col().update_one(
        {"discord_user_id": discord_user_id},
        {"$set": updates},
    )
    return result.matched_count > 0


def set_user_subscribed(discord_user_id: str, subscribed: bool) -> bool:
    """Set a user's subscription status."""
    return update_user(discord_user_id, {"subscribed": subscribed})


def get_all_subscribed_users() -> list[dict]:
    """Get all users with subscribed=True."""
    users = []
    for doc in _user_col().find({"subscribed": True}):
        doc.pop("_id", None)
        users.append(doc)
    return users


def get_all_users() -> list[dict]:
    """Get all users regardless of subscription status."""
    users = []
    for doc in _user_col().find():
        doc.pop("_id", None)
        users.append(doc)
    return users


def delete_user(discord_user_id: str) -> bool:
    """Permanently delete a user. Returns True if user existed."""
    result = _user_col().delete_one({"discord_user_id": discord_user_id})
    return result.deleted_count > 0


# ---------------------------------------------------------------------------
# notification_log CRUD
# ---------------------------------------------------------------------------

def _notif_col() -> Collection:
    return get_db().notification_log


def was_notification_sent(discord_user_id: str, listing_url: str,
                          notification_type: str) -> bool:
    """Check if a notification was already sent (dedup across restarts)."""
    return _notif_col().find_one({
        "discord_user_id": discord_user_id,
        "listing_url": listing_url,
        "notification_type": notification_type,
    }) is not None


def log_notification(discord_user_id: str, listing_url: str,
                     notification_type: str, success: bool) -> None:
    """Log a sent notification for deduplication."""
    _notif_col().insert_one({
        "discord_user_id": discord_user_id,
        "listing_url": listing_url,
        "notification_type": notification_type,
        "sent_at": datetime.now(timezone.utc),
        "success": success,
    })
