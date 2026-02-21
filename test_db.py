"""Tests for db.py — MongoDB CRUD operations using mongomock."""

import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

# mongomock provides an in-memory MongoDB that doesn't need a real server
try:
    import mongomock
    HAS_MONGOMOCK = True
except ImportError:
    HAS_MONGOMOCK = False

import db as db_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_mongo():
    """Replace the real MongoClient with mongomock for all tests."""
    if not HAS_MONGOMOCK:
        pytest.skip("mongomock not installed")

    # Reset module state
    db_module._client = None
    db_module._db = None

    # Patch MongoClient to use mongomock
    with patch.dict(os.environ, {"MONGODB_URI": "mongodb://localhost:27017"}):
        with patch("db.MongoClient", mongomock.MongoClient):
            yield
            db_module.close()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

class TestConnection:
    def test_get_db_returns_database(self):
        db = db_module.get_db()
        assert db.name == "apartment_tracker"

    def test_get_client_reuses_singleton(self):
        c1 = db_module.get_client()
        c2 = db_module.get_client()
        assert c1 is c2

    def test_close_resets_state(self):
        db_module.get_client()
        db_module.close()
        assert db_module._client is None
        assert db_module._db is None

    def test_get_client_raises_without_uri(self):
        db_module._client = None
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="MONGODB_URI"):
                db_module.get_client()


# ---------------------------------------------------------------------------
# seen_listings CRUD
# ---------------------------------------------------------------------------

class TestSeenListings:
    def test_upsert_and_get(self):
        url = "https://streeteasy.com/building/test/1"
        entry = {
            "first_seen": "2026-02-11T00:00:00+00:00",
            "address": "123 Test St",
            "price": "$3,000",
            "neighborhood": "East Village",
        }
        db_module.upsert_seen_listing(url, entry)
        result = db_module.get_seen_listing(url)
        assert result is not None
        assert result["address"] == "123 Test St"
        assert result["price"] == "$3,000"
        assert "_id" not in result
        assert "url" not in result

    def test_upsert_updates_existing(self):
        url = "https://streeteasy.com/building/test/1"
        db_module.upsert_seen_listing(url, {"price": "$3,000", "address": "Test"})
        db_module.upsert_seen_listing(url, {"price": "$2,800", "address": "Test"})
        result = db_module.get_seen_listing(url)
        assert result["price"] == "$2,800"

    def test_load_seen_from_mongo(self):
        db_module.upsert_seen_listing("https://se.com/a", {"price": "$3,000", "address": "A"})
        db_module.upsert_seen_listing("https://se.com/b", {"price": "$2,500", "address": "B"})
        seen = db_module.load_seen_from_mongo()
        assert len(seen) == 2
        assert "https://se.com/a" in seen
        assert "https://se.com/b" in seen
        assert seen["https://se.com/a"]["price"] == "$3,000"
        # No _id or url keys in values
        for entry in seen.values():
            assert "_id" not in entry

    def test_save_seen_to_mongo(self):
        seen = {
            "https://se.com/a": {"price": "$3,000", "address": "A"},
            "https://se.com/b": {"price": "$2,500", "address": "B"},
        }
        db_module.save_seen_to_mongo(seen)
        loaded = db_module.load_seen_from_mongo()
        assert len(loaded) == 2
        assert loaded["https://se.com/a"]["price"] == "$3,000"

    def test_delete_seen_listing(self):
        url = "https://streeteasy.com/building/test/1"
        db_module.upsert_seen_listing(url, {"price": "$3,000", "address": "Test"})
        db_module.delete_seen_listing(url)
        assert db_module.get_seen_listing(url) is None

    def test_get_nonexistent_returns_none(self):
        assert db_module.get_seen_listing("https://nonexistent.com") is None


# ---------------------------------------------------------------------------
# user_preferences CRUD
# ---------------------------------------------------------------------------

class TestUserPreferences:
    def test_create_user(self):
        user = db_module.create_user("123", "test_user#1234")
        assert user["discord_user_id"] == "123"
        assert user["discord_username"] == "test_user#1234"
        assert user["subscribed"] is True
        assert "filters" in user
        assert "notification_settings" in user
        assert "_id" not in user

    def test_get_user(self):
        db_module.create_user("123", "test_user")
        user = db_module.get_user("123")
        assert user is not None
        assert user["discord_user_id"] == "123"

    def test_get_nonexistent_user(self):
        assert db_module.get_user("nonexistent") is None

    def test_update_user(self):
        db_module.create_user("123", "test_user")
        result = db_module.update_user("123", {"filters.max_price": 4000})
        assert result is True
        user = db_module.get_user("123")
        assert user["filters"]["max_price"] == 4000

    def test_update_nonexistent_user(self):
        result = db_module.update_user("nonexistent", {"subscribed": False})
        assert result is False

    def test_set_user_subscribed(self):
        db_module.create_user("123", "test_user")
        db_module.set_user_subscribed("123", False)
        user = db_module.get_user("123")
        assert user["subscribed"] is False

        db_module.set_user_subscribed("123", True)
        user = db_module.get_user("123")
        assert user["subscribed"] is True

    def test_get_all_subscribed_users(self):
        db_module.create_user("1", "user1")
        db_module.create_user("2", "user2")
        db_module.create_user("3", "user3")
        db_module.set_user_subscribed("2", False)

        users = db_module.get_all_subscribed_users()
        ids = {u["discord_user_id"] for u in users}
        assert ids == {"1", "3"}

    def test_get_all_users(self):
        db_module.create_user("1", "user1")
        db_module.create_user("2", "user2")
        db_module.set_user_subscribed("2", False)

        users = db_module.get_all_users()
        assert len(users) == 2

    def test_delete_user(self):
        db_module.create_user("123", "test_user")
        result = db_module.delete_user("123")
        assert result is True
        assert db_module.get_user("123") is None

    def test_delete_nonexistent_user(self):
        result = db_module.delete_user("nonexistent")
        assert result is False

    def test_create_user_with_custom_filters(self):
        filters = {
            "neighborhoods": ["east-village", "chelsea"],
            "min_price": 1000,
            "max_price": 3000,
            "bed_rooms": ["1"],
            "no_fee": True,
            "geo_bounds": None,
        }
        user = db_module.create_user("123", "test_user", filters=filters)
        assert user["filters"]["neighborhoods"] == ["east-village", "chelsea"]
        assert user["filters"]["max_price"] == 3000

    def test_created_at_and_updated_at(self):
        user = db_module.create_user("123", "test_user")
        assert "created_at" in user
        assert "updated_at" in user
        assert isinstance(user["created_at"], datetime)


# ---------------------------------------------------------------------------
# notification_log CRUD
# ---------------------------------------------------------------------------

class TestNotificationLog:
    def test_was_notification_sent_false(self):
        assert db_module.was_notification_sent("123", "https://se.com/a", "new_listing") is False

    def test_log_and_check_notification(self):
        db_module.log_notification("123", "https://se.com/a", "new_listing", True)
        assert db_module.was_notification_sent("123", "https://se.com/a", "new_listing") is True

    def test_different_type_not_matched(self):
        db_module.log_notification("123", "https://se.com/a", "new_listing", True)
        assert db_module.was_notification_sent("123", "https://se.com/a", "price_drop") is False

    def test_different_user_not_matched(self):
        db_module.log_notification("123", "https://se.com/a", "new_listing", True)
        assert db_module.was_notification_sent("456", "https://se.com/a", "new_listing") is False

    def test_different_listing_not_matched(self):
        db_module.log_notification("123", "https://se.com/a", "new_listing", True)
        assert db_module.was_notification_sent("123", "https://se.com/b", "new_listing") is False

    def test_log_notification_records_timestamp(self):
        db_module.log_notification("123", "https://se.com/a", "new_listing", True)
        doc = db_module._notif_col().find_one({"discord_user_id": "123"})
        assert doc is not None
        assert "sent_at" in doc
        assert doc["success"] is True


# ---------------------------------------------------------------------------
# ensure_indexes
# ---------------------------------------------------------------------------

class TestIndexes:
    def test_ensure_indexes_runs_without_error(self):
        db_module.ensure_indexes()
        # Just verify it doesn't crash
