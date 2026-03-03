"""APNs push notification client using aioapns."""

from __future__ import annotations

import json
import logging
import os

from aioapns import APNs, NotificationRequest, PushType

log = logging.getLogger("api.push")

_apns_client: APNs | None = None


def _get_apns_client() -> APNs | None:
    """Lazily create the APNs client from environment variables.

    Required env vars:
      APNS_KEY_PATH — path to .p8 private key file
      APNS_KEY_ID — 10-character Key ID from Apple
      APNS_TEAM_ID — 10-character Team ID
      APNS_TOPIC — bundle identifier (e.g. com.yourname.ApartmentTracker)

    Optional:
      APNS_USE_SANDBOX — set to "1" for sandbox (default: production)
    """
    global _apns_client
    if _apns_client is not None:
        return _apns_client

    key_path = os.environ.get("APNS_KEY_PATH")
    key_id = os.environ.get("APNS_KEY_ID")
    team_id = os.environ.get("APNS_TEAM_ID")

    if not all([key_path, key_id, team_id]):
        log.warning("APNs not configured (missing APNS_KEY_PATH, APNS_KEY_ID, or APNS_TEAM_ID)")
        return None

    use_sandbox = os.environ.get("APNS_USE_SANDBOX", "0") == "1"

    _apns_client = APNs(
        key=key_path,
        key_id=key_id,
        team_id=team_id,
        topic=os.environ.get("APNS_TOPIC", "com.apartment.tracker"),
        use_sandbox=use_sandbox,
    )
    log.info("APNs client initialized (sandbox=%s)", use_sandbox)
    return _apns_client


async def send_push(
    device_token: str,
    title: str,
    body: str,
    data: dict | None = None,
) -> bool:
    """Send a single push notification to a device.

    Returns True if the push was accepted by APNs, False otherwise.
    """
    client = _get_apns_client()
    if client is None:
        log.debug("APNs not configured — skipping push to %s...", device_token[:8])
        return False

    payload = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
            "badge": 1,
        },
    }
    if data:
        payload["data"] = data

    request = NotificationRequest(
        device_token=device_token,
        message=payload,
        push_type=PushType.ALERT,
    )

    try:
        response = await client.send_notification(request)
        if not response.is_successful:
            log.warning(
                "APNs rejected push to %s...: %s %s",
                device_token[:8],
                response.status,
                response.description,
            )
            return False
        return True
    except Exception as e:
        log.error("APNs send error for %s...: %s", device_token[:8], e)
        return False


async def send_new_listing_push(
    device_token: str,
    listing: dict,
) -> bool:
    """Send a push notification for a new listing."""
    address = listing.get("address", "New listing")
    price = listing.get("price", "")
    neighborhood = listing.get("neighborhood", "")
    beds = listing.get("beds", "")

    title = f"New: {address}"
    parts = [p for p in [price, beds, neighborhood] if p and p != "N/A"]
    body = " - ".join(parts) if parts else "New apartment listing found"

    return await send_push(
        device_token=device_token,
        title=title,
        body=body,
        data={"type": "new_listing", "listing_url": listing.get("url", "")},
    )


async def send_price_drop_push(
    device_token: str,
    listing: dict,
    price_change: dict,
) -> bool:
    """Send a push notification for a price drop."""
    address = listing.get("address", "Price drop")
    old_price = price_change.get("old_price", 0)
    new_price = price_change.get("new_price", 0)
    savings = price_change.get("savings", 0)

    title = f"Price Drop: {address}"
    body = f"${old_price:,} -> ${new_price:,} (save ${savings:,}/mo)"

    return await send_push(
        device_token=device_token,
        title=title,
        body=body,
        data={"type": "price_drop", "listing_url": listing.get("url", "")},
    )
