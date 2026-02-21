#!/usr/bin/env python3
"""One-time migration: copy seen_listings.json into MongoDB Atlas.

Usage:
    MONGODB_URI="mongodb+srv://..." python migrate_to_mongodb.py

This is idempotent — re-running will upsert without duplicates.
"""

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("migrate")

SEEN_PATH = Path(__file__).resolve().parent / "seen_listings.json"


def main():
    mongodb_uri = os.environ.get("MONGODB_URI", "")
    if not mongodb_uri:
        log.error("MONGODB_URI environment variable is not set")
        sys.exit(1)

    if not SEEN_PATH.exists():
        log.error("seen_listings.json not found at %s", SEEN_PATH)
        sys.exit(1)

    with open(SEEN_PATH) as f:
        data = json.load(f)

    if isinstance(data, list):
        log.info("Converting old list format to dict...")
        from datetime import datetime, timezone
        data = {url: {"first_seen": datetime.now(timezone.utc).isoformat()} for url in data}

    log.info("Loaded %d listings from seen_listings.json", len(data))

    import db as db_module
    db_module.ensure_indexes()

    count = 0
    for url, entry in data.items():
        db_module.upsert_seen_listing(url, entry)
        count += 1
        if count % 50 == 0:
            log.info("  Migrated %d / %d listings...", count, len(data))

    log.info("Migration complete: %d listings upserted to MongoDB", count)

    # Verify
    loaded = db_module.load_seen_from_mongo()
    log.info("Verification: %d listings in MongoDB", len(loaded))

    if len(loaded) != len(data):
        log.warning("Mismatch! JSON has %d, MongoDB has %d", len(data), len(loaded))
    else:
        log.info("Counts match.")

    db_module.close()


if __name__ == "__main__":
    main()
