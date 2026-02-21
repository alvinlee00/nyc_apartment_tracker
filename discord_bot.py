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
from models import VALID_NEIGHBORHOODS, DEFAULT_FILTERS, DEFAULT_NOTIFICATION_SETTINGS

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

    description = message + "\n\n" if message else ""
    description += "Use the buttons below to configure your filters."

    embed = discord.Embed(title="Settings", description=description, color=0x3498DB)
    embed.add_field(name="Neighborhoods", value=hood_display, inline=False)
    embed.add_field(name="Price Range", value=price_display, inline=True)
    embed.add_field(name="Bed Types", value=bed_display, inline=True)
    embed.add_field(name="No-Fee Only", value=no_fee, inline=True)

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
                "You'll now receive personalized apartment DMs.\n\n"
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
            "Get personalized StreetEasy apartment alerts delivered straight to your DMs!\n\n"
            "**How it works:**\n"
            "1. Click **Subscribe** to sign up\n"
            "2. Click **Settings** to pick your neighborhoods, price range, and bed types\n"
            "3. Sit back — you'll get DMs when matching listings appear\n\n"
            "**Features:**\n"
            "• New listing alerts matching your filters\n"
            "• Price drop notifications\n"
            "• Daily market digest\n"
            "• Value scores and nearby subway info"
        ),
        color=0x00B4D8,
    )


@bot.tree.command(name="setup", description="Post the welcome panel in this channel")
async def setup_command(interaction: discord.Interaction):
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
            "You're now subscribed to personalized apartment notifications.\n\n"
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
