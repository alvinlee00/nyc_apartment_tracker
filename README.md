# NYC Apartment Tracker

Automated StreetEasy scraper that sends Discord notifications for new rental listings matching your criteria. Runs on GitHub Actions every 15 minutes.

## Features

- Scrapes StreetEasy rental listings for configurable neighborhoods
- Tracks seen listings to avoid duplicate notifications
- Sends rich Discord embeds with price, beds/baths, sqft, images, and direct links
- Runs automatically via GitHub Actions cron schedule
- Manual trigger with optional dry-run mode

## Quick Setup

### 1. Fork this repository

Click the **Fork** button at the top right of this page.

### 2. Create a Discord webhook

1. In your Discord server, go to **Server Settings → Integrations → Webhooks**
2. Click **New Webhook**
3. Name it (e.g., "NYC Apartments"), choose a channel, and copy the webhook URL

### 3. Add the webhook as a GitHub secret

1. In your forked repo, go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret**
3. Name: `DISCORD_WEBHOOK_URL`
4. Value: paste your Discord webhook URL
5. Click **Add secret**

### 4. Customize search criteria

Edit `config.json` to match your preferences:

```json
{
  "search": {
    "neighborhoods": ["gramercy-park", "flatiron", "kips-bay", "east-village", "les", "west-village", "chelsea", "upper-west-side"],
    "max_price": 3600,
    "min_price": 0,
    "bed_rooms": ["studio", "1"],
    "area": "manhattan"
  }
}
```

**Neighborhood slugs** must match StreetEasy URL format. See **[NEIGHBORHOODS.md](NEIGHBORHOODS.md)** for the full list of valid slugs.

**Bedrooms**: Use `"studio"`, `"1"`, `"2"`, `"3"`, etc.

### 5. Enable GitHub Actions

1. Go to the **Actions** tab in your forked repo
2. Click **I understand my workflows, go ahead and enable them**

The tracker will now run every 15 minutes automatically.

## Manual Run

1. Go to **Actions → NYC Apartment Tracker**
2. Click **Run workflow**
3. Optionally check **dry_run** to scrape without sending Discord notifications

## Monitoring

- Check the **Actions** tab to see run history and logs
- Each run logs: neighborhoods scraped, listings found, new vs. previously seen
- `seen_listings.json` is auto-committed after each run to track state

## Discord Notification Example

Each new listing sends an embed with:
- Address (linked to StreetEasy listing)
- Price
- Beds / Baths / Square footage
- Neighborhood
- Listing photo

## Project Structure

```
├── apartment_tracker.py    # Main scraper script
├── config.json             # Search criteria (edit this)
├── seen_listings.json      # Tracked listings (auto-updated)
├── requirements.txt        # Python dependencies
├── .github/
│   └── workflows/
│       └── apartment-tracker.yml  # GitHub Actions workflow
└── README.md
```

## Notes

- GitHub Actions cron schedules may have delays of a few minutes — this is normal
- StreetEasy may rate-limit or block requests; the scraper includes delays between requests and retries
- The scraper respects a 2-second delay between page fetches (configurable in `config.json`)
- Free GitHub Actions accounts get 2,000 minutes/month — running every 15 min uses ~1,440 runs/month which fits within limits
