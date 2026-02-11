# StreetEasy Neighborhood Slugs

Use these slugs in `config.json` under `search.neighborhoods`.

## Manhattan (Verified)

| Slug | Neighborhood |
|------|-------------|
| `battery-park-city` | Battery Park City |
| `carnegie-hill` | Carnegie Hill |
| `chelsea` | Chelsea |
| `chinatown` | Chinatown |
| `civic-center` | Civic Center |
| `east-village` | East Village |
| `financial-district` | Financial District |
| `flatiron` | Flatiron / Union Square |
| `fulton-seaport` | Fulton / Seaport |
| `gramercy-park` | Gramercy Park |
| `greenwich-village` | Greenwich Village |
| `hells-kitchen` | Hell's Kitchen |
| `hudson-yards` | Hudson Yards |
| `kips-bay` | Kips Bay |
| `lenox-hill` | Lenox Hill |
| `les` | Lower East Side |
| `little-italy` | Little Italy |
| `manhattan-valley` | Manhattan Valley |
| `midtown` | Midtown |
| `midtown-east` | Midtown East |
| `midtown-south` | Midtown South |
| `midtown-west` | Midtown West |
| `murray-hill` | Murray Hill |
| `noho` | NoHo |
| `nolita` | Nolita |
| `nomad` | NoMad |
| `soho` | SoHo |
| `stuyvesant-town` | Stuyvesant Town |
| `tribeca` | Tribeca |
| `two-bridges` | Two Bridges |
| `upper-east-side` | Upper East Side |
| `upper-west-side` | Upper West Side |
| `west-village` | West Village |
| `yorkville` | Yorkville |

**Shorthand aliases:**
- `ues` — All Upper East Side (includes Yorkville, Carnegie Hill, Lenox Hill)
- `uws` — All Upper West Side (includes Manhattan Valley)

## Brooklyn

| Slug | Neighborhood |
|------|-------------|
| `bay-ridge` | Bay Ridge |
| `bed-stuy` | Bedford-Stuyvesant |
| `boerum-hill` | Boerum Hill |
| `brooklyn-heights` | Brooklyn Heights |
| `bushwick` | Bushwick |
| `carroll-gardens` | Carroll Gardens |
| `clinton-hill` | Clinton Hill |
| `cobble-hill` | Cobble Hill |
| `crown-heights` | Crown Heights |
| `downtown-brooklyn` | Downtown Brooklyn |
| `dumbo` | DUMBO |
| `flatbush` | Flatbush |
| `fort-greene` | Fort Greene |
| `gowanus` | Gowanus |
| `greenpoint` | Greenpoint |
| `kensington` | Kensington |
| `park-slope` | Park Slope |
| `prospect-heights` | Prospect Heights |
| `red-hook` | Red Hook |
| `sunset-park` | Sunset Park |
| `williamsburg` | Williamsburg |
| `windsor-terrace` | Windsor Terrace |

## Queens

| Slug | Neighborhood |
|------|-------------|
| `astoria` | Astoria |
| `flushing` | Flushing |
| `forest-hills` | Forest Hills |
| `jackson-heights` | Jackson Heights |
| `long-island-city` | Long Island City |
| `ridgewood` | Ridgewood |
| `sunnyside` | Sunnyside |
| `woodside` | Woodside |

## Upper Manhattan

| Slug | Neighborhood |
|------|-------------|
| `east-harlem` | East Harlem |
| `hamilton-heights` | Hamilton Heights |
| `harlem` | Harlem |
| `inwood` | Inwood |
| `morningside-heights` | Morningside Heights |
| `washington-heights` | Washington Heights |

## Tips

- **USQ / Gramercy area**: Use `gramercy-park` + `flatiron` + `kips-bay`. There is no `union-square` slug — those listings appear under `gramercy-park` and `flatiron`.
- **LES**: The slug is `les`, not `lower-east-side`.
- **UWS / UES shortcuts**: `uws` and `ues` return broader results than `upper-west-side` / `upper-east-side` (they include sub-neighborhoods).
- **Sponsored listings**: StreetEasy injects sponsored listings from other neighborhoods. The tracker automatically filters these out using the `NEIGHBORHOOD_ALIASES` map in `apartment_tracker.py`.
- **Adding new neighborhoods**: If you add a slug that isn't in `NEIGHBORHOOD_ALIASES`, the tracker will still work — it just won't filter out sponsored listings for that neighborhood.
