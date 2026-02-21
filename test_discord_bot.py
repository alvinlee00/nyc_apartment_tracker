"""Tests for discord_bot.py — slash command handlers with mocked Discord interactions."""

import os
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

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
    """Replace real MongoDB with mongomock."""
    if not HAS_MONGOMOCK:
        pytest.skip("mongomock not installed")

    db_module._client = None
    db_module._db = None

    with patch.dict(os.environ, {"MONGODB_URI": "mongodb://localhost:27017"}):
        with patch("db.MongoClient", mongomock.MongoClient):
            yield
            db_module.close()


def _make_interaction(user_id="123456789", username="testuser#1234"):
    """Create a mock Discord interaction."""
    interaction = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = int(user_id)
    interaction.user.__str__ = MagicMock(return_value=username)
    interaction.response = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# /subscribe
# ---------------------------------------------------------------------------

class TestSubscribeCommand:
    @pytest.mark.asyncio
    async def test_new_user_subscribes(self):
        from discord_bot import subscribe

        interaction = _make_interaction()
        await subscribe.callback(interaction)
        interaction.response.send_message.assert_called_once()

        # Verify user was created in DB
        user = db_module.get_user("123456789")
        assert user is not None
        assert user["subscribed"] is True
        assert user["discord_username"] == "testuser#1234"

    @pytest.mark.asyncio
    async def test_already_subscribed(self):
        from discord_bot import subscribe

        db_module.create_user("123456789", "testuser#1234")

        interaction = _make_interaction()
        await subscribe.callback(interaction)
        call_args = interaction.response.send_message.call_args
        assert "already subscribed" in call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_resubscribe(self):
        from discord_bot import subscribe

        db_module.create_user("123456789", "testuser#1234")
        db_module.set_user_subscribed("123456789", False)

        interaction = _make_interaction()
        await subscribe.callback(interaction)

        user = db_module.get_user("123456789")
        assert user["subscribed"] is True
        call_args = interaction.response.send_message.call_args
        assert "welcome back" in call_args[0][0].lower()


# ---------------------------------------------------------------------------
# /unsubscribe
# ---------------------------------------------------------------------------

class TestUnsubscribeCommand:
    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        from discord_bot import unsubscribe

        db_module.create_user("123456789", "testuser#1234")

        interaction = _make_interaction()
        await unsubscribe.callback(interaction)

        user = db_module.get_user("123456789")
        assert user["subscribed"] is False

    @pytest.mark.asyncio
    async def test_unsubscribe_not_subscribed(self):
        from discord_bot import unsubscribe

        interaction = _make_interaction()
        await unsubscribe.callback(interaction)
        call_args = interaction.response.send_message.call_args
        assert "not subscribed" in call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_unsubscribe_already_unsubscribed(self):
        from discord_bot import unsubscribe

        db_module.create_user("123456789", "testuser#1234")
        db_module.set_user_subscribed("123456789", False)

        interaction = _make_interaction()
        await unsubscribe.callback(interaction)
        call_args = interaction.response.send_message.call_args
        assert "already unsubscribed" in call_args[0][0].lower()


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

class TestStatusCommand:
    @pytest.mark.asyncio
    async def test_status_not_subscribed(self):
        from discord_bot import status

        interaction = _make_interaction()
        await status.callback(interaction)
        call_args = interaction.response.send_message.call_args
        assert "not subscribed" in call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_status_shows_filters(self):
        from discord_bot import status

        db_module.create_user("123456789", "testuser#1234", filters={
            "neighborhoods": ["east-village", "chelsea"],
            "min_price": 1000,
            "max_price": 3600,
            "bed_rooms": ["studio", "1"],
            "no_fee": False,
            "geo_bounds": None,
        })

        interaction = _make_interaction()
        await status.callback(interaction)
        call_args = interaction.response.send_message.call_args
        embed = call_args[1]["embed"]
        assert "Active" in embed.title

    @pytest.mark.asyncio
    async def test_status_shows_paused(self):
        from discord_bot import status

        db_module.create_user("123456789", "testuser#1234")
        db_module.set_user_subscribed("123456789", False)

        interaction = _make_interaction()
        await status.callback(interaction)
        call_args = interaction.response.send_message.call_args
        embed = call_args[1]["embed"]
        assert "Paused" in embed.title


# ---------------------------------------------------------------------------
# /settings
# ---------------------------------------------------------------------------

class TestSettingsCommand:
    @pytest.mark.asyncio
    async def test_settings_not_subscribed(self):
        from discord_bot import settings

        interaction = _make_interaction()
        await settings.callback(interaction)
        call_args = interaction.response.send_message.call_args
        assert "subscribe" in call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_settings_shows_panel(self):
        from discord_bot import settings

        db_module.create_user("123456789", "testuser#1234")

        interaction = _make_interaction()
        await settings.callback(interaction)
        call_args = interaction.response.send_message.call_args
        embed = call_args[1]["embed"]
        assert "Settings" in embed.title
        assert "view" in call_args[1]


# ---------------------------------------------------------------------------
# PriceRangeModal
# ---------------------------------------------------------------------------

class TestPriceRangeModal:
    @pytest.mark.asyncio
    async def test_valid_price_range(self):
        from discord_bot import PriceRangeModal

        db_module.create_user("123456789", "testuser#1234")
        user = db_module.get_user("123456789")
        modal = PriceRangeModal("123456789", user)

        modal.min_price_input = MagicMock()
        modal.min_price_input.value = "1000"
        modal.max_price_input = MagicMock()
        modal.max_price_input.value = "3600"

        interaction = _make_interaction()
        await modal.on_submit(interaction)

        updated = db_module.get_user("123456789")
        assert updated["filters"]["min_price"] == 1000
        assert updated["filters"]["max_price"] == 3600

    @pytest.mark.asyncio
    async def test_invalid_price(self):
        from discord_bot import PriceRangeModal

        user = {"filters": {"min_price": 0, "max_price": 5000}}
        modal = PriceRangeModal("123456789", user)
        modal.min_price_input = MagicMock()
        modal.min_price_input.value = "abc"
        modal.max_price_input = MagicMock()
        modal.max_price_input.value = "3600"

        interaction = _make_interaction()
        await modal.on_submit(interaction)
        call_args = interaction.response.send_message.call_args
        assert "valid numbers" in call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_min_greater_than_max(self):
        from discord_bot import PriceRangeModal

        user = {"filters": {"min_price": 0, "max_price": 5000}}
        modal = PriceRangeModal("123456789", user)
        modal.min_price_input = MagicMock()
        modal.min_price_input.value = "5000"
        modal.max_price_input = MagicMock()
        modal.max_price_input.value = "1000"

        interaction = _make_interaction()
        await modal.on_submit(interaction)
        call_args = interaction.response.send_message.call_args
        assert "cannot be greater" in call_args[0][0].lower()


# ---------------------------------------------------------------------------
# SettingsView no-fee toggle
# ---------------------------------------------------------------------------

class TestNoFeeToggle:
    @pytest.mark.asyncio
    async def test_toggle_no_fee_on(self):
        from discord_bot import SettingsView

        db_module.create_user("123456789", "testuser#1234")
        view = SettingsView("123456789")

        interaction = _make_interaction()
        # discord.py button callback only takes interaction (self is bound)
        await view.no_fee_btn.callback(interaction)

        updated = db_module.get_user("123456789")
        assert updated["filters"]["no_fee"] is True
        # Now uses edit_message instead of send_message
        interaction.response.edit_message.assert_called_once()
