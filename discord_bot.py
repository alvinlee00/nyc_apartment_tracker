#!/usr/bin/env python3
"""Discord bot for managing per-user apartment tracker preferences.

Slash commands:
  /subscribe   — Subscribe with default filters
  /unsubscribe — Pause notifications (preserves preferences)
  /settings    — Interactive panel to configure filters
  /status      — Show current filter summary
  /setup       — Post the welcome panel in a channel
"""

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import db as db_module
from models import VALID_NEIGHBORHOODS, DEFAULT_FILTERS, DEFAULT_NOTIFICATION_SETTINGS, MANHATTAN_AVENUES, avenue_for_longitude
from apartment_tracker import get_stations_for_neighborhood

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("discord_bot")

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    # Register persistent view so welcome panel buttons survive restarts
    bot.add_view(WelcomeView())

    log.info("Bot ready as %s (ID: %s)", bot.user, bot.user.id)
    try:
        synced = await bot.tree.sync()
        log.info("Synced %d command(s)", len(synced))
    except Exception as e:
        log.error("Failed to sync commands: %s", e)

    # Ensure MongoDB indexes
    try:
        db_module.ensure_indexes()
        log.info("MongoDB indexes ensured")
    except Exception as e:
        log.error("Failed to ensure MongoDB indexes: %s", e)


# ---------------------------------------------------------------------------
# Helper: build settings embed showing current filter values
# ---------------------------------------------------------------------------

def _build_settings_embed(user: dict, message: str | None = None) -> discord.Embed:
    """Build a settings embed with the user's current filter summary."""
    filters = user.get("filters", {})

    hoods = filters.get("neighborhoods", [])
    hood_display = ", ".join(VALID_NEIGHBORHOODS.get(h, h) for h in hoods) if hoods else "All neighborhoods"

    max_p = filters.get("max_price", 0)
    min_p = filters.get("min_price", 0)
    price_display = f"${min_p:,} – ${max_p:,}" if min_p else f"Up to ${max_p:,}" if max_p else "No limit"

    beds = filters.get("bed_rooms", [])
    bed_display = ", ".join(b.title() for b in beds) if beds else "Any"

    no_fee = "Yes" if filters.get("no_fee") else "No"

    geo = filters.get("geo_bounds")
    if geo and geo.get("west_longitude") is not None:
        west_ave = geo.get("west_avenue") or avenue_for_longitude(geo["west_longitude"]) or str(geo["west_longitude"])
        east_ave = geo.get("east_avenue") or avenue_for_longitude(geo["east_longitude"]) or str(geo["east_longitude"])
        apply_to = geo.get("apply_to", [])
        if apply_to:
            hood_names = ", ".join(VALID_NEIGHBORHOODS.get(h, h) for h in apply_to)
            geo_display = f"{west_ave} → {east_ave} ({hood_names} only)"
        else:
            geo_display = f"{west_ave} → {east_ave} (all neighborhoods)"
    else:
        geo_display = "Off"

    description = message + "\n\n" if message else ""
    description += "Use the buttons below to configure your filters."

    embed = discord.Embed(title="Settings", description=description, color=0x3498DB)
    embed.add_field(name="Neighborhoods", value=hood_display, inline=False)
    embed.add_field(name="Price Range", value=price_display, inline=True)
    embed.add_field(name="Bed Types", value=bed_display, inline=True)
    embed.add_field(name="No-Fee Only", value=no_fee, inline=True)
    embed.add_field(name="Geo Filter", value=geo_display, inline=True)

    subway_prefs = filters.get("subway_preferences")
    if subway_prefs:
        lines = []
        for slug, prefs in subway_prefs.items():
            hood_name = VALID_NEIGHBORHOODS.get(slug, slug)
            count = len(prefs.get("preferred_stations", []))
            lines.append(f"{hood_name}: {count} station(s)")
        subway_display = "\n".join(lines) if lines else "Off (using defaults)"
    else:
        subway_display = "Off (using defaults)"
    embed.add_field(name="Subway Prefs", value=subway_display, inline=True)

    return embed


# ---------------------------------------------------------------------------
# Welcome panel (persistent buttons — survives bot restarts)
# ---------------------------------------------------------------------------

class WelcomeView(discord.ui.View):
    """Persistent welcome panel with Subscribe/Settings/Status buttons."""

    def __init__(self):
        super().__init__(timeout=None)  # Never expires

    @discord.ui.button(label="Subscribe", style=discord.ButtonStyle.success,
                       emoji="🔔", custom_id="welcome:subscribe")
    async def subscribe_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        username = str(interaction.user)

        existing = db_module.get_user(user_id)
        if existing and existing.get("subscribed"):
            await interaction.response.send_message(
                "You're already subscribed! Click **Settings** to adjust your filters.",
                ephemeral=True,
            )
            return

        if existing:
            db_module.set_user_subscribed(user_id, True)
            await interaction.response.send_message(
                "Welcome back! Your previous preferences have been restored.",
                ephemeral=True,
            )
            return

        db_module.create_user(user_id, username)
        embed = discord.Embed(
            title="You're subscribed!",
            description=(
                "You'll now receive personalized apartment DMs from StreetEasy and RentHop.\n\n"
                "Click **Settings** below to configure your neighborhoods, "
                "price range, and bed types.\n\n"
                "By default, you'll get notifications for all neighborhoods up to $5,000/mo."
            ),
            color=0x2ECC71,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Settings", style=discord.ButtonStyle.primary,
                       emoji="⚙️", custom_id="welcome:settings")
    async def settings_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        user = db_module.get_user(user_id)

        if not user:
            await interaction.response.send_message(
                "Click **Subscribe** first to get started!", ephemeral=True,
            )
            return

        view = SettingsView(user_id)
        embed = _build_settings_embed(user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="My Status", style=discord.ButtonStyle.secondary,
                       emoji="📊", custom_id="welcome:status")
    async def status_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        user = db_module.get_user(user_id)

        if not user:
            await interaction.response.send_message(
                "Click **Subscribe** first to get started!", ephemeral=True,
            )
            return

        filters = user.get("filters", {})

        hoods = filters.get("neighborhoods", [])
        hood_display = ", ".join(VALID_NEIGHBORHOODS.get(h, h) for h in hoods) if hoods else "All neighborhoods"

        max_p = filters.get("max_price", 0)
        min_p = filters.get("min_price", 0)
        price_display = f"${min_p:,} – ${max_p:,}" if min_p else f"Up to ${max_p:,}" if max_p else "No limit"

        beds = filters.get("bed_rooms", [])
        bed_display = ", ".join(b.title() for b in beds) if beds else "Any"

        subscribed = user.get("subscribed", False)

        embed = discord.Embed(
            title=f"Your Status — {'Active' if subscribed else 'Paused'}",
            color=0x2ECC71 if subscribed else 0x95A5A6,
        )
        embed.add_field(name="Neighborhoods", value=hood_display, inline=False)
        embed.add_field(name="Price Range", value=price_display, inline=True)
        embed.add_field(name="Bed Types", value=bed_display, inline=True)
        embed.add_field(name="No-Fee Only", value="Yes" if filters.get("no_fee") else "No", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Unsubscribe", style=discord.ButtonStyle.danger,
                       emoji="🔕", custom_id="welcome:unsubscribe")
    async def unsubscribe_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        existing = db_module.get_user(user_id)

        if not existing or not existing.get("subscribed"):
            await interaction.response.send_message(
                "You're not currently subscribed.", ephemeral=True,
            )
            return

        db_module.set_user_subscribed(user_id, False)
        await interaction.response.send_message(
            "Notifications paused. Click **Subscribe** to resume anytime.",
            ephemeral=True,
        )


def _build_welcome_embed() -> discord.Embed:
    return discord.Embed(
        title="🏠 NYC Apartment Tracker",
        description=(
            "Get personalized apartment alerts from **StreetEasy** and **RentHop** delivered straight to your DMs!\n\n"
            "**How it works:**\n"
            "1. Click **Subscribe** to sign up\n"
            "2. Click **Settings** to pick your neighborhoods, price range, and bed types\n"
            "3. Sit back — you'll get DMs when matching listings appear on either platform\n\n"
            "**Features:**\n"
            "• New listing alerts matching your filters (StreetEasy + RentHop)\n"
            "• Price drop notifications\n"
            "• Daily market digest\n"
            "• Value scores and nearby subway info"
        ),
        color=0x00B4D8,
    )


@bot.tree.command(name="setup", description="Post the welcome panel in this channel")
async def setup_command(interaction: discord.Interaction):
    # Delete any existing welcome panels from the bot in this channel
    async for msg in interaction.channel.history(limit=50):
        if msg.author == bot.user and msg.embeds:
            for embed in msg.embeds:
                if embed.title and "NYC Apartment Tracker" in embed.title:
                    await msg.delete()
                    break

    embed = _build_welcome_embed()
    view = WelcomeView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("Welcome panel posted!", ephemeral=True)


# ---------------------------------------------------------------------------
# /subscribe
# ---------------------------------------------------------------------------

@bot.tree.command(name="subscribe", description="Subscribe to apartment notifications")
async def subscribe(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    username = str(interaction.user)

    existing = db_module.get_user(user_id)
    if existing and existing.get("subscribed"):
        await interaction.response.send_message(
            "You're already subscribed! Use `/settings` to adjust your filters.",
            ephemeral=True,
        )
        return

    if existing:
        # Re-subscribe (was previously unsubscribed)
        db_module.set_user_subscribed(user_id, True)
        await interaction.response.send_message(
            "Welcome back! Your previous preferences have been restored.\n"
            "Use `/settings` to adjust your filters or `/status` to view them.",
            ephemeral=True,
        )
        return

    # New user
    db_module.create_user(user_id, username)
    embed = discord.Embed(
        title="Welcome to NYC Apartment Tracker!",
        description=(
            "You're now subscribed to personalized apartment notifications from StreetEasy and RentHop.\n\n"
            "**Next steps:**\n"
            "• Use `/settings` to configure your neighborhoods, price range, and bed types\n"
            "• Use `/status` to view your current filters\n"
            "• Use `/unsubscribe` to pause notifications\n\n"
            "By default, you'll receive notifications for all neighborhoods up to $5,000/mo."
        ),
        color=0x2ECC71,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /unsubscribe
# ---------------------------------------------------------------------------

@bot.tree.command(name="unsubscribe", description="Pause apartment notifications")
async def unsubscribe(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    existing = db_module.get_user(user_id)

    if not existing:
        await interaction.response.send_message(
            "You're not subscribed. Use `/subscribe` to get started!",
            ephemeral=True,
        )
        return

    if not existing.get("subscribed"):
        await interaction.response.send_message(
            "You're already unsubscribed. Use `/subscribe` to re-enable notifications.",
            ephemeral=True,
        )
        return

    db_module.set_user_subscribed(user_id, False)
    await interaction.response.send_message(
        "Notifications paused. Your preferences are saved — use `/subscribe` to resume anytime.",
        ephemeral=True,
    )


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

@bot.tree.command(name="status", description="View your current filter settings")
async def status(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    user = db_module.get_user(user_id)

    if not user:
        await interaction.response.send_message(
            "You're not subscribed. Use `/subscribe` to get started!",
            ephemeral=True,
        )
        return

    filters = user.get("filters", {})
    notif = user.get("notification_settings", {})

    # Neighborhoods
    hoods = filters.get("neighborhoods", [])
    if hoods:
        hood_display = ", ".join(VALID_NEIGHBORHOODS.get(h, h) for h in hoods)
    else:
        hood_display = "All neighborhoods"

    # Price
    min_p = filters.get("min_price", 0)
    max_p = filters.get("max_price", 0)
    if max_p > 0:
        price_display = f"${min_p:,} – ${max_p:,}" if min_p else f"Up to ${max_p:,}"
    else:
        price_display = "No limit"

    # Beds
    beds = filters.get("bed_rooms", [])
    bed_display = ", ".join(b.title() for b in beds) if beds else "Any"

    # No-fee
    no_fee = "Yes" if filters.get("no_fee") else "No"

    # Notifications
    notif_types = []
    if notif.get("new_listings", True):
        notif_types.append("New listings")
    if notif.get("price_drops", True):
        notif_types.append("Price drops")
    if notif.get("daily_digest", True):
        notif_types.append("Daily digest")
    notif_display = ", ".join(notif_types) if notif_types else "None"

    subscribed = user.get("subscribed", False)
    status_icon = "Active" if subscribed else "Paused"

    embed = discord.Embed(
        title=f"Your Settings — {status_icon}",
        color=0x2ECC71 if subscribed else 0x95A5A6,
    )
    embed.add_field(name="Neighborhoods", value=hood_display, inline=False)
    embed.add_field(name="Price Range", value=price_display, inline=True)
    embed.add_field(name="Bed Types", value=bed_display, inline=True)
    embed.add_field(name="No-Fee Only", value=no_fee, inline=True)
    embed.add_field(name="Notifications", value=notif_display, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# /settings — Interactive settings panel
# ---------------------------------------------------------------------------

@bot.tree.command(name="settings", description="Configure your apartment search filters")
async def settings(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    user = db_module.get_user(user_id)

    if not user:
        await interaction.response.send_message(
            "You need to `/subscribe` first!",
            ephemeral=True,
        )
        return

    view = SettingsView(user_id)
    embed = _build_settings_embed(user)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class SettingsView(discord.ui.View):
    """Main settings panel — all sub-views edit this same message."""

    def __init__(self, user_id: str):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="Neighborhoods", style=discord.ButtonStyle.primary, emoji="📍")
    async def neighborhoods_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        view = NeighborhoodSelectView(self.user_id, user)
        hoods = user.get("filters", {}).get("neighborhoods", [])
        hood_display = ", ".join(VALID_NEIGHBORHOODS.get(h, h) for h in hoods) if hoods else "None selected"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Select Neighborhoods",
                description=f"**Current:** {hood_display}\n\nSelect from the dropdowns below, then click **Back to Settings**.",
                color=0x3498DB,
            ),
            view=view,
        )

    @discord.ui.button(label="Price Range", style=discord.ButtonStyle.primary, emoji="💰")
    async def price_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        modal = PriceRangeModal(self.user_id, user)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Bed Types", style=discord.ButtonStyle.primary, emoji="🛏️")
    async def beds_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        view = BedTypeSelectView(self.user_id, user)
        beds = user.get("filters", {}).get("bed_rooms", [])
        bed_display = ", ".join(b.title() for b in beds) if beds else "Any"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Select Bed Types",
                description=f"**Current:** {bed_display}\n\nChoose which apartment sizes to include, then click **Back to Settings**.",
                color=0x3498DB,
            ),
            view=view,
        )

    @discord.ui.button(label="No-Fee Toggle", style=discord.ButtonStyle.secondary, emoji="💵")
    async def no_fee_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        filters = user.get("filters", {})
        new_val = not filters.get("no_fee", False)
        db_module.update_user(self.user_id, {"filters.no_fee": new_val})
        # Re-fetch and redisplay settings in the same message
        user = db_module.get_user(self.user_id)
        status = "ON — no-fee only" if new_val else "OFF — all listings"
        embed = _build_settings_embed(user, message=f"No-fee filter: **{status}**")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Notifications", style=discord.ButtonStyle.secondary, emoji="🔔")
    async def notif_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        view = NotificationToggleView(self.user_id, user)
        notif = user.get("notification_settings", {})
        items = []
        if notif.get("new_listings", True):
            items.append("New listings")
        if notif.get("price_drops", True):
            items.append("Price drops")
        if notif.get("daily_digest", True):
            items.append("Daily digest")
        current = ", ".join(items) if items else "None"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Notification Settings",
                description=f"**Current:** {current}\n\nToggle which types of notifications you receive.",
                color=0x3498DB,
            ),
            view=view,
        )

    @discord.ui.button(label="Subway Prefs", style=discord.ButtonStyle.secondary, emoji="🚇", row=1)
    async def subway_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        view = SubwayPrefsView(self.user_id, user)
        subway_prefs = user.get("filters", {}).get("subway_preferences") or {}
        hoods = user.get("filters", {}).get("neighborhoods", [])
        if subway_prefs:
            lines = []
            for slug, prefs in subway_prefs.items():
                hood_name = VALID_NEIGHBORHOODS.get(slug, slug)
                count = len(prefs.get("preferred_stations", []))
                lines.append(f"{hood_name}: {count} station(s)")
            current = "**Current:** " + ", ".join(lines)
        else:
            current = "**Current:** Off (using global defaults)"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Subway Station Preferences",
                description=f"{current}\n\n"
                    "Select a neighborhood to configure preferred subway stations.\n"
                    "These preferences personalize the subway match scores in your DMs.",
                color=0x3498DB,
            ),
            view=view,
        )

    @discord.ui.button(label="Geo Filter", style=discord.ButtonStyle.secondary, emoji="🗺️", row=1)
    async def geo_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        view = GeoFilterView(self.user_id, user)
        geo = user.get("filters", {}).get("geo_bounds")
        if geo and geo.get("west_longitude") is not None:
            west_ave = geo.get("west_avenue") or avenue_for_longitude(geo["west_longitude"]) or "?"
            east_ave = geo.get("east_avenue") or avenue_for_longitude(geo["east_longitude"]) or "?"
            apply_to = geo.get("apply_to", [])
            hood_names = ", ".join(VALID_NEIGHBORHOODS.get(h, h) for h in apply_to) if apply_to else "all"
            current = f"**Current:** {west_ave} → {east_ave} (applies to: {hood_names})"
        else:
            current = "**Current:** Off (no geo filter set)"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Geo Filter — Longitude Bounds",
                description=f"{current}\n\n"
                    "Filter listings by Manhattan avenue boundaries.\n"
                    "Select your **west** and **east** avenue limits, "
                    "then pick which neighborhoods this filter applies to.",
                color=0x3498DB,
            ),
            view=view,
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, emoji="✅", row=1)
    async def done_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        filters = user.get("filters", {})

        hoods = filters.get("neighborhoods", [])
        hood_display = ", ".join(VALID_NEIGHBORHOODS.get(h, h) for h in hoods) if hoods else "All neighborhoods"
        max_p = filters.get("max_price", 0)
        min_p = filters.get("min_price", 0)
        price_display = f"${min_p:,} – ${max_p:,}" if min_p else f"Up to ${max_p:,}" if max_p else "No limit"
        beds = filters.get("bed_rooms", [])
        bed_display = ", ".join(b.title() for b in beds) if beds else "Any"

        embed = discord.Embed(
            title="Settings Saved!",
            description="Your filters have been updated. You'll receive notifications matching these criteria.",
            color=0x2ECC71,
        )
        embed.add_field(name="Neighborhoods", value=hood_display, inline=False)
        embed.add_field(name="Price Range", value=price_display, inline=True)
        embed.add_field(name="Bed Types", value=bed_display, inline=True)
        embed.add_field(name="No-Fee Only", value="Yes" if filters.get("no_fee") else "No", inline=True)

        # Remove all buttons — settings panel is closed
        await interaction.response.edit_message(embed=embed, view=None)


# ---------------------------------------------------------------------------
# Neighborhood selection (grouped by borough, Discord 25-option limit)
# ---------------------------------------------------------------------------

# Group neighborhoods by area for select menus
_MANHATTAN_HOODS = {k: v for k, v in VALID_NEIGHBORHOODS.items() if k in {
    "battery-park-city", "carnegie-hill", "chelsea", "chinatown", "civic-center",
    "east-village", "financial-district", "flatiron", "fulton-seaport", "gramercy-park",
    "greenwich-village", "hells-kitchen", "hudson-yards", "kips-bay", "lenox-hill",
    "les", "little-italy", "manhattan-valley", "midtown", "midtown-east",
    "midtown-south", "midtown-west", "murray-hill", "noho", "nolita",
}}
_MANHATTAN_HOODS_2 = {k: v for k, v in VALID_NEIGHBORHOODS.items() if k in {
    "nomad", "soho", "stuyvesant-town", "tribeca", "two-bridges",
    "upper-east-side", "upper-west-side", "west-village", "yorkville",
}}
_BROOKLYN_HOODS = {k: v for k, v in VALID_NEIGHBORHOODS.items() if k in {
    "bay-ridge", "bed-stuy", "boerum-hill", "brooklyn-heights", "bushwick",
    "carroll-gardens", "clinton-hill", "cobble-hill", "crown-heights",
    "downtown-brooklyn", "dumbo", "flatbush", "fort-greene", "gowanus",
    "greenpoint", "kensington", "park-slope", "prospect-heights",
    "red-hook", "sunset-park", "williamsburg", "windsor-terrace",
}}
_QUEENS_UPTOWN = {k: v for k, v in VALID_NEIGHBORHOODS.items() if k in {
    "astoria", "flushing", "forest-hills", "jackson-heights",
    "long-island-city", "ridgewood", "sunnyside", "woodside",
    "east-harlem", "hamilton-heights", "harlem", "inwood",
    "morningside-heights", "washington-heights",
}}


def _make_hood_select(hoods: dict[str, str], current: list[str], placeholder: str) -> discord.ui.Select:
    options = []
    for slug, display in sorted(hoods.items(), key=lambda x: x[1]):
        options.append(discord.SelectOption(
            label=display,
            value=slug,
            default=slug in current,
        ))
    select = discord.ui.Select(
        placeholder=placeholder,
        min_values=0,
        max_values=len(options),
        options=options,
    )
    return select


class NeighborhoodSelectView(discord.ui.View):
    def __init__(self, user_id: str, user: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        current = user.get("filters", {}).get("neighborhoods", [])

        # Manhattan A-N
        self.manhattan_select = _make_hood_select(_MANHATTAN_HOODS, current, "Manhattan (A-N)")
        self.manhattan_select.callback = self._make_callback("manhattan")
        self.add_item(self.manhattan_select)

        # Manhattan N-Y
        self.manhattan2_select = _make_hood_select(_MANHATTAN_HOODS_2, current, "Manhattan (N-Y)")
        self.manhattan2_select.callback = self._make_callback("manhattan2")
        self.add_item(self.manhattan2_select)

        # Brooklyn
        self.brooklyn_select = _make_hood_select(_BROOKLYN_HOODS, current, "Brooklyn")
        self.brooklyn_select.callback = self._make_callback("brooklyn")
        self.add_item(self.brooklyn_select)

        # Queens & Upper Manhattan
        self.queens_select = _make_hood_select(_QUEENS_UPTOWN, current, "Queens & Upper Manhattan")
        self.queens_select.callback = self._make_callback("queens")
        self.add_item(self.queens_select)

        self._selections: dict[str, list[str]] = {
            "manhattan": [s for s in current if s in _MANHATTAN_HOODS],
            "manhattan2": [s for s in current if s in _MANHATTAN_HOODS_2],
            "brooklyn": [s for s in current if s in _BROOKLYN_HOODS],
            "queens": [s for s in current if s in _QUEENS_UPTOWN],
        }

    def _make_callback(self, group: str):
        async def callback(interaction: discord.Interaction):
            select_map = {
                "manhattan": self.manhattan_select,
                "manhattan2": self.manhattan2_select,
                "brooklyn": self.brooklyn_select,
                "queens": self.queens_select,
            }
            self._selections[group] = select_map[group].values

            # Combine all selections
            all_hoods = []
            for vals in self._selections.values():
                all_hoods.extend(vals)

            user_id = str(interaction.user.id)
            db_module.update_user(user_id, {"filters.neighborhoods": all_hoods})

            # Rebuild view so dropdown defaults reflect the new selections
            user = db_module.get_user(self.user_id)
            new_view = NeighborhoodSelectView(self.user_id, user)

            display = ", ".join(VALID_NEIGHBORHOODS.get(h, h) for h in all_hoods) or "None selected"
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Select Neighborhoods",
                    description=f"**Current:** {display}\n\nSelect from the dropdowns below, then click **Back to Settings**.",
                    color=0x3498DB,
                ),
                view=new_view,
            )
        return callback

    @discord.ui.button(label="← Back to Settings", style=discord.ButtonStyle.secondary, row=4)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user)
        view = SettingsView(self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


# ---------------------------------------------------------------------------
# Price range modal
# ---------------------------------------------------------------------------

class PriceRangeModal(discord.ui.Modal, title="Set Price Range"):
    def __init__(self, user_id: str, user: dict):
        super().__init__()
        self.user_id = user_id
        filters = user.get("filters", {})
        self.min_price_input = discord.ui.TextInput(
            label="Minimum Price ($)",
            placeholder="0",
            default=str(filters.get("min_price", 0)),
            required=False,
            max_length=10,
        )
        self.max_price_input = discord.ui.TextInput(
            label="Maximum Price ($)",
            placeholder="5000",
            default=str(filters.get("max_price", 5000)),
            required=False,
            max_length=10,
        )
        self.add_item(self.min_price_input)
        self.add_item(self.max_price_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            min_p = int(self.min_price_input.value or "0")
            max_p = int(self.max_price_input.value or "0")
        except ValueError:
            await interaction.response.send_message(
                "Please enter valid numbers for price range.", ephemeral=True,
            )
            return

        if max_p > 0 and min_p > max_p:
            await interaction.response.send_message(
                "Minimum price cannot be greater than maximum price.", ephemeral=True,
            )
            return

        db_module.update_user(self.user_id, {
            "filters.min_price": min_p,
            "filters.max_price": max_p,
        })
        price_str = f"${min_p:,} – ${max_p:,}" if min_p else f"Up to ${max_p:,}"
        await interaction.response.send_message(
            f"Price range updated: **{price_str}**\n"
            "The settings panel above reflects your changes.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Geo filter modal
# ---------------------------------------------------------------------------

class GeoFilterView(discord.ui.View):
    """Avenue-based geo filter with dropdowns for west/east boundary and neighborhood scope."""

    def __init__(self, user_id: str, user: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        geo = user.get("filters", {}).get("geo_bounds") or {}
        current_west = geo.get("west_longitude")
        current_east = geo.get("east_longitude")
        current_apply_to = set(geo.get("apply_to", []))

        # Build avenue options (east to west, i.e. ascending longitude value)
        ave_items = sorted(MANHATTAN_AVENUES.items(), key=lambda x: x[1], reverse=True)

        west_options = []
        for ave_name, lon in ave_items:
            is_default = (current_west is not None and lon == current_west)
            west_options.append(discord.SelectOption(
                label=ave_name, value=ave_name, default=is_default,
            ))
        east_options = []
        for ave_name, lon in ave_items:
            is_default = (current_east is not None and lon == current_east)
            east_options.append(discord.SelectOption(
                label=ave_name, value=ave_name, default=is_default,
            ))

        self.west_select = discord.ui.Select(
            placeholder="West boundary (e.g. 7th Avenue)",
            options=west_options,
            min_values=1, max_values=1, row=0,
        )
        self.east_select = discord.ui.Select(
            placeholder="East boundary (e.g. 1st Avenue)",
            options=east_options,
            min_values=1, max_values=1, row=1,
        )
        self.west_select.callback = self._noop
        self.east_select.callback = self._noop
        self.add_item(self.west_select)
        self.add_item(self.east_select)

        # Neighborhood multi-select — only Manhattan neighborhoods make sense for geo filter
        user_hoods = user.get("filters", {}).get("neighborhoods", [])
        manhattan_slugs = {
            "battery-park-city", "carnegie-hill", "chelsea", "chinatown", "civic-center",
            "east-village", "financial-district", "flatiron", "fulton-seaport", "gramercy-park",
            "greenwich-village", "hells-kitchen", "hudson-yards", "kips-bay", "lenox-hill",
            "les", "little-italy", "manhattan-valley", "midtown", "midtown-east",
            "midtown-south", "midtown-west", "murray-hill", "noho", "nolita",
            "nomad", "soho", "stuyvesant-town", "tribeca", "two-bridges",
            "upper-east-side", "upper-west-side", "west-village", "yorkville",
            "east-harlem", "hamilton-heights", "harlem", "inwood",
            "morningside-heights", "washington-heights",
        }
        # Show Manhattan neighborhoods the user is subscribed to
        hood_options = []
        for slug in sorted(user_hoods):
            if slug in manhattan_slugs:
                hood_options.append(discord.SelectOption(
                    label=VALID_NEIGHBORHOODS.get(slug, slug),
                    value=slug,
                    default=slug in current_apply_to,
                ))
        if hood_options:
            self.hood_select = discord.ui.Select(
                placeholder="Apply to which neighborhoods? (blank = all)",
                options=hood_options,
                min_values=0, max_values=len(hood_options), row=2,
            )
            self.hood_select.callback = self._noop
            self.add_item(self.hood_select)
        else:
            self.hood_select = None

    async def _noop(self, interaction: discord.Interaction):
        """Acknowledge select interactions without doing anything — save happens on button press."""
        await interaction.response.defer()

    @discord.ui.button(label="Save Geo Filter", style=discord.ButtonStyle.success, emoji="💾", row=3)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        west_values = self.west_select.values
        east_values = self.east_select.values
        if not west_values or not east_values:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Error",
                    description="Please select both a west and east avenue boundary.",
                    color=0xE74C3C,
                ),
                view=self,
            )
            return

        west_ave = west_values[0]
        east_ave = east_values[0]
        west_lon = MANHATTAN_AVENUES[west_ave]
        east_lon = MANHATTAN_AVENUES[east_ave]

        # West must be more negative (further west) than east
        if west_lon > east_lon:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Error",
                    description=f"**{west_ave}** is east of **{east_ave}** — swap them!\n"
                        "West boundary should be further west than east boundary.",
                    color=0xE74C3C,
                ),
                view=self,
            )
            return

        apply_to = self.hood_select.values if self.hood_select else []

        geo_data = {
            "west_longitude": west_lon,
            "east_longitude": east_lon,
            "west_avenue": west_ave,
            "east_avenue": east_ave,
            "apply_to": apply_to,
        }
        db_module.update_user(self.user_id, {"filters.geo_bounds": geo_data})

        if apply_to:
            hood_names = ", ".join(VALID_NEIGHBORHOODS.get(h, h) for h in apply_to)
            scope = f"Applies to: **{hood_names}** only"
        else:
            scope = "Applies to: **all neighborhoods**"

        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user, f"Geo filter saved: **{west_ave} → {east_ave}**\n{scope}")
        await interaction.response.edit_message(embed=embed, view=SettingsView(self.user_id))

    @discord.ui.button(label="Clear Geo Filter", style=discord.ButtonStyle.danger, emoji="🗑️", row=3)
    async def clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        db_module.update_user(self.user_id, {"filters.geo_bounds": None})
        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user, "Geo filter **removed** — all longitudes allowed.")
        await interaction.response.edit_message(embed=embed, view=SettingsView(self.user_id))

    @discord.ui.button(label="Back to Settings", style=discord.ButtonStyle.secondary, row=4)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user)
        await interaction.response.edit_message(embed=embed, view=SettingsView(self.user_id))


# ---------------------------------------------------------------------------
# Bed type selection
# ---------------------------------------------------------------------------

BED_OPTIONS = [
    discord.SelectOption(label="Studio", value="studio"),
    discord.SelectOption(label="1 Bedroom", value="1"),
    discord.SelectOption(label="2 Bedrooms", value="2"),
    discord.SelectOption(label="3+ Bedrooms", value="3"),
]


class BedTypeSelectView(discord.ui.View):
    def __init__(self, user_id: str, user: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        current = user.get("filters", {}).get("bed_rooms", [])
        options = []
        for opt in BED_OPTIONS:
            options.append(discord.SelectOption(
                label=opt.label,
                value=opt.value,
                default=opt.value in current,
            ))
        self.select = discord.ui.Select(
            placeholder="Select bed types",
            min_values=0,
            max_values=len(options),
            options=options,
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    async def on_select(self, interaction: discord.Interaction):
        selected = self.select.values
        user_id = str(interaction.user.id)
        db_module.update_user(user_id, {"filters.bed_rooms": selected})

        # Rebuild view so dropdown defaults reflect the new selections
        user = db_module.get_user(self.user_id)
        new_view = BedTypeSelectView(self.user_id, user)

        display = ", ".join(s.title() for s in selected) if selected else "Any"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Select Bed Types",
                description=f"**Current:** {display}\n\nChoose which apartment sizes to include, then click **Back to Settings**.",
                color=0x3498DB,
            ),
            view=new_view,
        )

    @discord.ui.button(label="← Back to Settings", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user)
        view = SettingsView(self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


# ---------------------------------------------------------------------------
# Notification toggles
# ---------------------------------------------------------------------------

class NotificationToggleView(discord.ui.View):
    def __init__(self, user_id: str, user: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        notif = user.get("notification_settings", {})

        self.new_listings_btn = discord.ui.Button(
            label=f"New Listings: {'ON' if notif.get('new_listings', True) else 'OFF'}",
            style=discord.ButtonStyle.success if notif.get("new_listings", True) else discord.ButtonStyle.secondary,
            emoji="🏠",
        )
        self.new_listings_btn.callback = self._make_toggle("new_listings")
        self.add_item(self.new_listings_btn)

        self.price_drops_btn = discord.ui.Button(
            label=f"Price Drops: {'ON' if notif.get('price_drops', True) else 'OFF'}",
            style=discord.ButtonStyle.success if notif.get("price_drops", True) else discord.ButtonStyle.secondary,
            emoji="📉",
        )
        self.price_drops_btn.callback = self._make_toggle("price_drops")
        self.add_item(self.price_drops_btn)

        self.digest_btn = discord.ui.Button(
            label=f"Daily Digest: {'ON' if notif.get('daily_digest', True) else 'OFF'}",
            style=discord.ButtonStyle.success if notif.get("daily_digest", True) else discord.ButtonStyle.secondary,
            emoji="📊",
        )
        self.digest_btn.callback = self._make_toggle("daily_digest")
        self.add_item(self.digest_btn)

    def _make_toggle(self, setting: str):
        async def callback(interaction: discord.Interaction):
            user = db_module.get_user(self.user_id)
            notif = user.get("notification_settings", {})
            new_val = not notif.get(setting, True)
            db_module.update_user(self.user_id, {f"notification_settings.{setting}": new_val})

            # Rebuild the view with updated button labels/colors
            user = db_module.get_user(self.user_id)
            new_view = NotificationToggleView(self.user_id, user)
            notif = user.get("notification_settings", {})
            items = []
            if notif.get("new_listings", True):
                items.append("New listings")
            if notif.get("price_drops", True):
                items.append("Price drops")
            if notif.get("daily_digest", True):
                items.append("Daily digest")
            current = ", ".join(items) if items else "None"
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Notification Settings",
                    description=f"**Current:** {current}\n\nToggle which types of notifications you receive.",
                    color=0x3498DB,
                ),
                view=new_view,
            )
        return callback

    @discord.ui.button(label="← Back to Settings", style=discord.ButtonStyle.secondary, row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user)
        view = SettingsView(self.user_id)
        await interaction.response.edit_message(embed=embed, view=view)


# ---------------------------------------------------------------------------
# Subway station preferences
# ---------------------------------------------------------------------------

class SubwayPrefsView(discord.ui.View):
    """Select a neighborhood to configure subway station preferences."""

    def __init__(self, user_id: str, user: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        hoods = user.get("filters", {}).get("neighborhoods", [])
        subway_prefs = user.get("filters", {}).get("subway_preferences") or {}

        if hoods:
            options = []
            for slug in sorted(hoods):
                display = VALID_NEIGHBORHOODS.get(slug, slug)
                has_prefs = slug in subway_prefs
                label = f"{'✅ ' if has_prefs else ''}{display}"
                count = len(subway_prefs.get(slug, {}).get("preferred_stations", []))
                desc = f"{count} station(s) configured" if has_prefs else "No preferences set"
                options.append(discord.SelectOption(
                    label=label[:100], value=slug, description=desc,
                ))
            self.hood_select = discord.ui.Select(
                placeholder="Select a neighborhood",
                options=options[:25],
                min_values=1, max_values=1, row=0,
            )
            self.hood_select.callback = self._noop
            self.add_item(self.hood_select)
        else:
            self.hood_select = None

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(label="Configure Stations", style=discord.ButtonStyle.primary, emoji="🚇", row=1)
    async def configure_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.hood_select or not self.hood_select.values:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Error",
                    description="Please select a neighborhood first.",
                    color=0xE74C3C,
                ),
                view=self,
            )
            return
        slug = self.hood_select.values[0]
        user = db_module.get_user(self.user_id)
        view = SubwayStationSelectView(self.user_id, user, slug)
        hood_name = VALID_NEIGHBORHOODS.get(slug, slug)
        subway_prefs = user.get("filters", {}).get("subway_preferences") or {}
        existing = subway_prefs.get(slug, {}).get("preferred_stations", [])
        if existing:
            current = ", ".join(p["name"] for p in existing)
        else:
            current = "None"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"Subway Stations — {hood_name}",
                description=f"**Current:** {current}\n\n"
                    "Select your preferred stations from the dropdown, then save.",
                color=0x3498DB,
            ),
            view=view,
        )

    @discord.ui.button(label="Clear All", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        db_module.update_user(self.user_id, {"filters.subway_preferences": None})
        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user, "Subway preferences **cleared** — using global defaults.")
        await interaction.response.edit_message(embed=embed, view=SettingsView(self.user_id))

    @discord.ui.button(label="Back to Settings", style=discord.ButtonStyle.secondary, row=2)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user)
        await interaction.response.edit_message(embed=embed, view=SettingsView(self.user_id))


class SubwayStationSelectView(discord.ui.View):
    """Select subway stations and weights for a specific neighborhood."""

    def __init__(self, user_id: str, user: dict, neighborhood_slug: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.neighborhood_slug = neighborhood_slug

        nearby = get_stations_for_neighborhood(neighborhood_slug)
        subway_prefs = user.get("filters", {}).get("subway_preferences") or {}
        existing_names = {
            p["name"]
            for p in subway_prefs.get(neighborhood_slug, {}).get("preferred_stations", [])
        }

        if nearby:
            options = []
            for s in nearby[:25]:
                routes = ", ".join(s["routes"])
                options.append(discord.SelectOption(
                    label=s["name"][:100],
                    value=s["name"],
                    description=f"{routes} ({s['distance_mi']} mi)"[:100],
                    default=s["name"] in existing_names,
                ))
            self.station_select = discord.ui.Select(
                placeholder="Select preferred stations",
                options=options,
                min_values=0, max_values=len(options), row=0,
            )
            self.station_select.callback = self._noop
            self.add_item(self.station_select)
        else:
            self.station_select = None

        self._nearby = nearby

    async def _noop(self, interaction: discord.Interaction):
        await interaction.response.defer()

    @discord.ui.button(label="Save (Equal Weight)", style=discord.ButtonStyle.success, emoji="💾", row=1)
    async def save_equal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.station_select or not self.station_select.values:
            # Clear prefs for this neighborhood
            user = db_module.get_user(self.user_id)
            full_prefs = user.get("filters", {}).get("subway_preferences") or {}
            full_prefs.pop(self.neighborhood_slug, None)
            save_val = full_prefs if full_prefs else None
            db_module.update_user(self.user_id, {"filters.subway_preferences": save_val})
            user = db_module.get_user(self.user_id)
            embed = _build_settings_embed(user, f"Subway prefs cleared for **{VALID_NEIGHBORHOODS.get(self.neighborhood_slug, self.neighborhood_slug)}**.")
            await interaction.response.edit_message(embed=embed, view=SettingsView(self.user_id))
            return

        selected = self.station_select.values
        prefs = {"preferred_stations": [{"name": n, "weight": 1.0} for n in selected]}
        user = db_module.get_user(self.user_id)
        full_prefs = user.get("filters", {}).get("subway_preferences") or {}
        full_prefs[self.neighborhood_slug] = prefs
        db_module.update_user(self.user_id, {"filters.subway_preferences": full_prefs})

        hood_name = VALID_NEIGHBORHOODS.get(self.neighborhood_slug, self.neighborhood_slug)
        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user, f"Saved **{len(selected)} station(s)** for {hood_name} (equal weight).")
        await interaction.response.edit_message(embed=embed, view=SettingsView(self.user_id))

    @discord.ui.button(label="Set Weights", style=discord.ButtonStyle.primary, emoji="⚖️", row=1)
    async def weights_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.station_select or not self.station_select.values:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Error",
                    description="Please select at least one station first.",
                    color=0xE74C3C,
                ),
                view=self,
            )
            return

        selected = self.station_select.values
        if len(selected) > 5:
            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="Too Many Stations",
                    description=f"Custom weights support up to **5 stations** (you selected {len(selected)}).\n"
                        "Please reduce your selection or use **Save (Equal Weight)**.",
                    color=0xE74C3C,
                ),
                view=self,
            )
            return

        # Build route lookup from nearby stations
        route_lookup = {s["name"]: ", ".join(s["routes"]) for s in (self._nearby or [])}
        station_routes = [route_lookup.get(n, "") for n in selected]
        modal = SubwayWeightModal(self.user_id, self.neighborhood_slug, selected, station_routes)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Remove Prefs", style=discord.ButtonStyle.danger, emoji="🗑️", row=2)
    async def remove_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        full_prefs = user.get("filters", {}).get("subway_preferences") or {}
        full_prefs.pop(self.neighborhood_slug, None)
        save_val = full_prefs if full_prefs else None
        db_module.update_user(self.user_id, {"filters.subway_preferences": save_val})
        hood_name = VALID_NEIGHBORHOODS.get(self.neighborhood_slug, self.neighborhood_slug)
        user = db_module.get_user(self.user_id)
        embed = _build_settings_embed(user, f"Subway prefs **removed** for {hood_name}.")
        await interaction.response.edit_message(embed=embed, view=SettingsView(self.user_id))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = db_module.get_user(self.user_id)
        view = SubwayPrefsView(self.user_id, user)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Subway Station Preferences",
                description="Select a neighborhood to configure preferred subway stations.",
                color=0x3498DB,
            ),
            view=view,
        )


class SubwayWeightModal(discord.ui.Modal, title="Set Station Weights"):
    """Modal with up to 5 TextInput fields for station weights."""

    def __init__(self, user_id: str, neighborhood_slug: str,
                 station_names: list[str], station_routes: list[str]):
        super().__init__()
        self.user_id = user_id
        self.neighborhood_slug = neighborhood_slug
        self.station_names = station_names
        self.weight_inputs: list[discord.ui.TextInput] = []

        for i, name in enumerate(station_names[:5]):
            routes = station_routes[i] if i < len(station_routes) else ""
            label = f"{name} ({routes})" if routes else name
            inp = discord.ui.TextInput(
                label=label[:45],
                placeholder="1.0",
                default="1.0",
                required=True,
                max_length=10,
            )
            self.weight_inputs.append(inp)
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        stations = []
        for i, inp in enumerate(self.weight_inputs):
            try:
                weight = float(inp.value)
                if weight <= 0:
                    raise ValueError("Weight must be positive")
            except ValueError:
                await interaction.response.send_message(
                    f"Invalid weight for **{self.station_names[i]}**: '{inp.value}'. "
                    "Please enter a positive number (e.g. 1.0, 2.5).",
                    ephemeral=True,
                )
                return
            stations.append({"name": self.station_names[i], "weight": weight})

        user = db_module.get_user(self.user_id)
        full_prefs = user.get("filters", {}).get("subway_preferences") or {}
        full_prefs[self.neighborhood_slug] = {"preferred_stations": stations}
        db_module.update_user(self.user_id, {"filters.subway_preferences": full_prefs})

        hood_name = VALID_NEIGHBORHOODS.get(self.neighborhood_slug, self.neighborhood_slug)
        details = ", ".join(f"{s['name']} (×{s['weight']})" for s in stations)
        await interaction.response.send_message(
            f"Subway weights saved for **{hood_name}**:\n{details}\n\n"
            "The settings panel above reflects your changes.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        log.error("DISCORD_BOT_TOKEN not set")
        return

    # Ensure MongoDB connection
    mongodb_uri = os.environ.get("MONGODB_URI", "")
    if not mongodb_uri:
        log.error("MONGODB_URI not set")
        return

    bot.run(token)


if __name__ == "__main__":
    main()
