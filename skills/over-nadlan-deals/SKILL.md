---
name: over-nadlan-deals
description: "Query Israeli real-estate transactions (Tax Authority / מיסוי מקרקעין register, 3.8M deals since 1998) via the over.org.il MCP servers, and generate beautiful interactive HTML reports (filters, charts, gush/helka parcel map with each purchased apartment) for a neighborhood, streets, parcels or whole city from a natural-language request."
---

# Israeli real-estate deals — over.org.il MCP + interactive reports

Source: Israel Tax Authority real-estate register (מיסוי מקרקעין, nadlan.taxes.gov.il), mirrored by **גרסאות לעם / over.org.il**: ~3.84M reported deals since 1998, 1,336 settlements, 47 deal types. Rows are raw source rows (`processed: false`): values are *reported* amounts, not verified market prices.

MCP servers (Streamable HTTP, stateless: `tools/call` works without `initialize`):
- `https://www.over.org.il/deals/mcp` — the deals register (search, series, compare).
- `https://www.over.org.il/nadlan/mcp` — property cross-reference: address ↔ gush/helka, parcel data.
- `https://www.over.org.il/data/mcp` — read-only SQL (PostGIS) over the whole site database (all tables below).
- Cite/link: `https://www.over.org.il/projects/deals`; parcel page `https://www.over.org.il/projects/nadlan?tab=gush&g=<gush>&h=<helka>`.

## Files and state

`SKILL` = the directory containing this SKILL.md. Requires Python 3.9+ (standard library only).
- `$SKILL/scripts/over_mcp.py` — stdlib MCP client + OAuth login (importable: `call_tool`, `rpc`, `McpError`).
- `$SKILL/scripts/over_report.py` — builds the interactive HTML report.
- `$SKILL/scripts/report_template.html` — report UI (RTL Hebrew; ECharts + Leaflet).
- `$SKILL/scripts/test_over_report.py` — offline tests (`cd $SKILL/scripts && python3 -m unittest test_over_report`).

State lives **outside** the skill, so the skill folder can be shared/published safely:
- `~/.config/over-mcp/state.json` (mode 0600) — OAuth client id + tokens. Never copy into the skill or a repo.
- `~/.config/over-mcp/vendor/` — cached ECharts/Leaflet, inlined into reports (downloaded on first build; CDN tags if offline).
The scripts write nothing else; reports contain only public register data plus the parameters used.

## Access

OAuth 2.1 + PKCE; upstream login is **Google sign-in** (any Google account; first login auto-registers). Dynamic client registration is open. One token serves every over.org.il MCP (shared issuer `https://www.over.org.il/mcp`). Access token 1h, auto-refreshed.

```bash
C="python3 $SKILL/scripts/over_mcp.py"
$C login                                         # once: opens Google in the browser, listens on 127.0.0.1:53682
$C call deals list_settlements '{"query":"הרצ"}'
$C call nadlan lookup_property '{"city":"תל אביב","street":"דיזנגוף","number":"100","include_stat_area":false}'
$C call data run_sql '{"sql":"SELECT count(*) FROM idx.govmap_22_bd519a1c_f6a7046f"}'
$C tools deals                                   # live tool list + input schemas
```
- `login` needs the human: tell the user to finish the Google sign-in; don't loop. Errors saying "not logged in"/"refresh failed" → `login` again.
- Latency: usually 0.3–10 s per call; a cold query can take ~2 min. Give shell calls a timeout ≥ 250 s; run independent calls in parallel.
- No login at all? Public REST mirrors the deals tools: `GET https://www.over.org.il/api/deals/{search,series,compare,settlements,natures,stats,breakdown}`, `/api/deals/parcel/{gush}/{helka}` (spec `https://www.over.org.il/openapi.json`). Slow (25–75 s/call). `/search` and `/series` also accept `street`+`house` (with `settlement`). `/api/connector/sql` (Looker) needs a connector key — not usable. Reports require the MCP login.

## Interactive HTML report (primary workflow for "give me a report …")

### 1. Map the request to flags
| user says | flag |
|---|---|
| city / settlement (Hebrew or partial) | `--city "תל אביב"` (resolved to the register's exact name, e.g. `תל אביב -יפו`) |
| neighborhood "X in city Y" | `--city Y --neighborhood "X"` (Hebrew as written; `שכונת` prefix, קרית/קריית, spaces/punctuation are normalized) |
| specific streets | `--streets "דיזנגוף,בן יהודה"` |
| gush / helka | `--parcels "6963:14,7091"` (`gush:helka`, or a bare gush) |
| "N rooms and above" / "up to N rooms" | `--min-rooms N` / `--max-rooms N` (inclusive, on the raw room count; deals with no room count drop out) |
| "priced above / below ₪X" | `--min-amount X` / `--max-amount X` (inclusive) |
| "from 2024 until today" | `--date-from 2024-01-01` (omit `--date-to`) |
| "apartments only", "cottages" … | `--nature "<exact type>"`, repeatable; exact strings from `list_deal_types` (list below). Default: all types — the report breaks them down |
| title / file name | `--title`; `--slug <city>-<area>` in English (see below); `--out path.html` only if the user asks for a specific path |
No area flag = the whole settlement (fetch capped by `--max-deals`, default 5000; the cap is reported).

### 2. Resolve the area, then build
```bash
R="python3 $SKILL/scripts/over_report.py"
$R areas --city "תל אביב" --q "הצפון"      # list known neighborhoods: name, source (polygon|addresses), size
$R build --city "תל אביב" --neighborhood "כוכב הצפון" --min-rooms 4 --min-amount 2000000 --date-from 2023-01-01 \
    --slug tel-aviv-kochav-hatzafon --open
```
- **Where the report goes:** `reports/<slug>-<YYYY-MM-DD>.html` under the **current working directory**, created if missing (e.g. `my_working_dir/reports/tel-aviv-kochav-hatzafon-2026-09-28.html`). Run the command from the user's working directory, not from `$SKILL`. An existing file is never overwritten; a `-2`, `-3` … suffix is added.
- **Always pass `--slug`:** a readable English transliteration, city then area, lowercase and hyphenated: `tel-aviv-kochav-hatzafon`, `jerusalem-rehavia`, `tel-aviv-dizengoff-ben-yehuda`, `jerusalem-gush-30026-34`. Without it the script derives one from built-in English city names plus a rough letter-by-letter transliteration.
- Exit 2 = unknown/ambiguous place, with candidates printed on stderr → pick the exact name, or ask the user when several fit (e.g. "הצפון הישן" has two polygons: "החלק הצפוני"/"החלק הדרומי").
- stderr shows `area: … → N parcels` and `deals: N`; stdout prints the report's absolute path (tell the user). `--json data.json` also dumps the payload for further analysis.
- Build time: ~30 s for a neighborhood when the server is quiet; several minutes under load or for wide areas (three concurrent city builds took ~200 s each).

### 3. How the area is resolved
1. **Neighborhood polygon** — Survey of Israel layer `שכונות — המרכז למיפוי ישראל` (govmap; ~3.2k neighborhoods in 175 settlements; every polygon stored twice as two snapshots, the latest is used). Parcels whose inner point (`ST_PointOnSurface`) falls inside it.
2. Otherwise the **address-list label** (`neighbourhood` column, 88 settlements): the parcels containing the addresses carrying that label.
3. `--streets`: the parcels containing those streets' addresses.
Deals are fetched with `search_deals` by gush (or gush+helka when a gush has ≤3 area parcels) with the filters applied server-side, then kept only for area parcels.

### 4. What the report shows (all interactive, cross-filtering)
- **Parcel map** (Leaflet): outlines + labels of the whole gushim, parcel polygons (highlighted = has matching deals; helka numbers shown at zoom ≥17), neighborhood border, and **one dot per purchased apartment**, colored by normalized ₪/m². Clicking a dot shows the full purchase; clicking a parcel lists its deals (click → fly to that apartment). Base maps: light / streets / satellite (Esri, no key). Fullscreen + fit buttons. Dots are fanned out inside their parcel because the register has no in-building location — positions are schematic.
- Sticky filter bar: free-text search, date range, price range, multi-selects (rooms, type, street, new/second-hand, year), active-filter chips, reset.
- KPIs: count, median price, median ₪/m² (vs city), median area, total reported value, last deal, dwellings in the area (gazetteer).
- Charts (ECharts): deals + median over time (month/quarter/year; price / ₪/m² / area), donuts by type, rooms, new/second-hand, street bars, area-vs-price scatter, price histogram, area-vs-city median ₪/m² by year. Clicking any slice/bar filters everything.
- Breakdown table by street, rooms, type, year, quarter, price/area bucket, parcel; the deals table (sortable, paged, 📍 to map, CSV export); street chips of the area; methodology and source links.
- Street per deal: gazetteer street of the exact sub-parcel → linked address → gazetteer parcel street → nearest in-area address ≤150 m (marked ≈).

### 5. Before handing over
Open it (`--open` or `open <file>`), then tell the user the resolved area (name, source, parcel count), the deal count, and any `NOTE:` lines (caps, failures). Needs internet for tiles/fonts only (JS libs are inlined).

## Deals MCP tools

| tool | use for | args (optional unless marked) |
|---|---|---|
| `list_settlements` | exact settlement spelling — **first stop for any named place** | `query` (substring), `limit` ≤400 |
| `list_deal_types` | the 47 types with counts/medians; mix of a city/period | common filters, `limit` |
| `search_deals` | individual deals | common filters + `sort` (`date_desc`\|`date_asc`\|`amount_desc`\|`amount_asc`\|`area_desc`), `limit` ≤200, `offset` |
| `price_series` | per-year `deals`, `median_amount`, `median_area`, `median_ppsqm_normalized` | common filters |
| `compare_settlements` | "where rose/fell most" | **`year_from`, `year_to`**, `nature`, `min_deals` (default 30, each year), `order` (`change_desc`\|`change_asc`\|`median_desc`\|`deals_desc`), `limit` ≤200 |
| `parcel_deals` | all deals on one parcel | **`gush`, `helka`**, `sub_parcel`, `limit`, `offset` |
| `register_stats` | coverage, date span, `dataset_id`, caveats | — |

Common filters: `settlement` (exact register name), `gush`, `helka` (int), `sub_parcel` ("7"/"007"), `nature`, `date_from`/`date_to` (`YYYY-MM-DD`), `min_amount`/`max_amount` (₪ int), `min_rooms`/`max_rooms`. MCP `search_deals` has **no street filter** → go through nadlan or the report's `--streets`.

## Nadlan MCP tools
- `lookup_property`: one of `gush+helka` | `city+street(+number)` | `zip` | `lat+lon(+radius_m ≤2000)` | `q` free text ("גוש 6963 חלקה 14"). Flags `include_deals|include_stat_area|include_addresses` (default true; set false to shrink). Returns `properties[].identity.{gush,helka,settlement:{code,name},point,streets,addresses[].neighbourhood}`, a `deals` summary, `match.confidence`, and `sources.*.table` (current SQL table names). `identity.settlement.name` is the deals-register spelling.
- `suggest_streets` (`q` prefix, `settlement_code`) — when an address lookup returns 0, it's usually street spelling. Also `parcel_deals`, `parcel_geometry` (GeoJSON), `coverage_stats`.
- `match.confidence == "approximate"` → gush/helka shared by several parcels; say so.

## Data MCP (SQL) — for custom analysis
Tools: `list_tables {q, schema, include_columns}`, `get_table {table}` (columns, samples), `describe_schema`, `run_sql {sql, max_rows ≤1000}` (single SELECT/WITH, no `;`, read-only, PostGIS available). Table names carry version hashes and can change — take current ones from `lookup_property … sources.*.table` or `list_tables`. As of 2026-09:
- Deals: `public.append_taxes_nadlan_full_f41fb496_fd06f5ae` — all **text**: `settlement, settlement_code, gush, chelka, sub_chelka, deal_date` (DD/MM/YYYY → sort key `substr(d,7,4)||substr(d,4,2)||substr(d,1,2)`), `deal_amount, declared_amount, deal_nature, portion, year_built, asset_area, room_num`. Cast with `NULLIF(x,'')::numeric`.
- Parcels: `public.append_shape_ff3176b1` (`GUSH_NUM, GUSH_SUFFI, PARCEL, LOCALITY_N, geom` EPSG:4326; several snapshots per parcel → `DISTINCT ON … ORDER BY first_seen DESC`).
- Neighborhoods: `idx.govmap_22_bd519a1c_f6a7046f` (`setl_name, fname, geom` 4326; duplicate snapshots via `_first_seen`); points: `idx.govmap_310_396e3ded_e330b2ad`.
- Asset gazetteer: `odata.gaztir_41720377` (`GushNum, ParcelNum, SubParcelNum` as text like `"7091"`/`"7091.0"`, `StreetNameHeb, Type` e.g. `דירת מגורים`, `BuildingYear`).
- Address list: `odata.ac1ae1fa_6d43_4685_8434_9953e950ca9b_19c5be7f` (`city, street, number, neighbourhood`, `X,Y` in **EPSG:2039** → `ST_Transform(ST_SetSRID(ST_MakePoint("X"::float8,"Y"::float8),2039),4326)`).

## Answering rules (from the server's own instructions)
1. **Median, never mean** — one row can be a whole building.
2. **Filter by `nature`** when comparing places or years; residential flats = `דירה בבית קומות`.
3. **Settlements match by exact name** (17.5% of rows have no code). Misspelling ⇒ silent empty result.
4. **Show `deals` next to every median**; small counts are noise.
5. With price and area, show `portion_fraction` and normalized ₪/m² (= amount ÷ (area × portion)). Area is the whole property.
6. `total_capped: true` means "more than 10,000".
7. Say the values are **amounts reported to the Tax Authority** and link the source; responses carry `row_url` for verification.

## Data gotchas
- Spellings: `תל אביב -יפו` (space before hyphen), `הרצלייה`, `מודיעין-מכבים-רעות`, `קריית …` vs `קרית חיים`. Resolve with `list_settlements {query:<short substring>}`.
- Deal rows: `date` (ISO), `date_src` (DD/MM/YYYY), `amount`, `nature`, `area_sqm`, `rooms`, `year_built` (may be in the future: off-plan), `portion_fraction`, `price_per_sqm_normalized`, `sub_parcel` ("007"), `gush`/`helka` (strings in `search_deals`), `addresses[]` (cross-reference, often empty).
- **`rooms` is `null` for fractional counts** (4.5, 5.5) although `min_rooms`/`max_rooms` filter on the true value. Recover it from the register table (`room_num`, matched on gush/chelka/sub_chelka/deal_date/deal_amount); `over_report.py` does this automatically.
- Deals are linked by gush+helka only (no gush suffix). Freshness: the register trails today by 1–2 weeks overall; a single city can lag ~2 months (see `last_deal`).
- Gazetteer and address list spell some streets differently (e.g. a trailing ׳) — normalize before joining.

## Deal types (`nature`), exact strings by volume
`דירה בבית קומות` · `מגורים` · `ד. מגורים` · `ללא תיכנון` · `קוטג' דו משפחתי` · `לא מעובדת` · `קרקע` · `קוטג' חד משפחתי` · `בית בודד` · `משרד` · `חנות` · `דירת גן` · `בניין` · `מחסנים` · `קומבינציה` · `תעשיה` · `במשק חקלאי-נחלה` · `חניה` · `קוטג' טורי` · `דירת גג` · … (47 in total; `list_deal_types`).
