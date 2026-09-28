#!/usr/bin/env python3
"""Interactive HTML report over Israeli real-estate deals (Tax Authority register, via over.org.il MCPs).

  over_report.py areas --city "תל אביב" [--q "הצפון"]
      List neighborhoods known for a city (Survey-of-Israel polygons + address-list labels).

  over_report.py build --city "תל אביב"
      [--neighborhood "כוכב הצפון" | --streets "דיזנגוף,בן יהודה" | --parcels "6963:14,7091"]
      [--min-rooms 4] [--max-rooms N] [--min-amount 2000000] [--max-amount N]
      [--date-from 2023-01-01] [--date-to YYYY-MM-DD] [--nature "דירה בבית קומות" ...]
      [--title T] [--out report.html] [--json data.json] [--max-deals 5000] [--no-benchmark] [--open]

No area flag = the whole settlement. Exit code 2 = unknown/ambiguous place; candidates on stderr.
"""
import argparse, collections, concurrent.futures as cf, datetime as dt, json, pathlib, re, sys
import urllib.request, webbrowser

HERE = pathlib.Path(__file__).resolve().parent
sys.dont_write_bytecode = True  # keep the skill directory free of __pycache__
sys.path.insert(0, str(HERE))
from over_mcp import McpError, call_tool  # noqa: E402

VENDOR = pathlib.Path.home() / ".config" / "over-mcp" / "vendor"
LIBS = {
    "echarts.min.js": "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js",
    "leaflet.js": "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
    "leaflet.css": "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
}
# Versioned table names drift when over.org.il re-snapshots; these are only fallbacks for discover_tables().
TABLES = {
    "parcels": "public.append_shape_ff3176b1",
    "gazetteer": "odata.gaztir_41720377",
    "address_list": "odata.ac1ae1fa_6d43_4685_8434_9953e950ca9b_19c5be7f",
    "neighborhoods": "idx.govmap_22_bd519a1c_f6a7046f",
    "deals": "public.append_taxes_nadlan_full_f41fb496_fd06f5ae",
}
NBR_TITLE = "שכונות — המרכז למיפוי ישראל"
NEAREST_MAX_M = 150
NBR_PREFIX = re.compile(r"^\s*(שכונת|שכונה|שכ[׳'`]?)\s+")
NUM_RE = "'^[0-9]+(\\.[0-9]+)?$'"


class Ambiguous(Exception):
    pass


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------- text normalization (identical in Python and SQL) ----------

def norm(s):
    s = re.sub(r"[^\u05d0-\u05ea0-9]", "", s or "")
    return s.replace("יי", "י").replace("וו", "ו")


def norm_nbr(s):
    return norm(NBR_PREFIX.sub("", s or ""))


def lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def nsql(x):
    return f"replace(replace(regexp_replace(coalesce({x},''),'[^א-ת0-9]','','g'),'יי','י'),'וו','ו')"


def nbr_sql(x):
    return nsql(f"regexp_replace({x}, '^\\s*(שכונת|שכונה|שכ[׳''`]?)\\s+', '')")


# ---------- SQL over the data MCP ----------

def sql(q, max_rows=1000):
    r = call_tool("data", "run_sql", {"sql": q, "max_rows": max_rows})
    if isinstance(r, str):
        raise McpError(f"run_sql: {r[:800]}")
    return r["rows"]


def sql_all(q):
    out, off = [], 0
    while True:
        rows = sql(f"SELECT * FROM ({q}) _q ORDER BY 1, 2 LIMIT 1000 OFFSET {off}")
        out += rows
        if len(rows) < 1000:
            return out
        off += 1000


def discover_tables():
    t = dict(TABLES)
    try:
        lp = call_tool("nadlan", "lookup_property", {"gush": 7091, "helka": 7, "include_deals": False,
                                                     "include_stat_area": False, "include_addresses": False})
        src = lp["properties"][0]["sources"]
        for k in ("parcels", "gazetteer", "address_list", "deals"):
            if (src.get(k) or {}).get("table"):
                t[k] = src[k]["table"]
    except (McpError, KeyError, IndexError) as e:
        log("table discovery via nadlan failed, using defaults:", e)
    try:
        lt = call_tool("data", "list_tables", {"q": NBR_TITLE, "schema": "idx", "limit": 10})
        for row in lt.get("tables") or lt.get("data") or []:
            if row.get("title") == NBR_TITLE:
                t["neighborhoods"] = f"{row['schema']}.{row['table']}"
                break
    except (McpError, KeyError) as e:
        log("neighborhood table discovery failed, using default:", e)
    return t


# ---------- place resolution ----------

def resolve_settlement(city):
    rows = []
    for q in dict.fromkeys((city.strip(), city.strip()[:3])):
        rows = call_tool("deals", "list_settlements", {"query": q, "limit": 400}).get("settlements", [])
        exact = [r for r in rows if norm(r["settlement"]) == norm(city)]
        if exact:
            return max(exact, key=lambda r: r["deals"])
        near = [r for r in rows if norm(city) in norm(r["settlement"])]
        if near:
            best = max(near, key=lambda r: r["deals"])
            log(f"settlement '{city}' → '{best['settlement']}' (closest of: {', '.join(r['settlement'] for r in near[:8])})")
            return best
    raise Ambiguous(f"settlement '{city}' not found in the register. Candidates: "
                    + ", ".join(r["settlement"] for r in rows[:40]))


def list_areas(T, city_norms, q=None):
    cn = ",".join(lit(c) for c in city_norms)
    like = lit(f"%{norm_nbr(q)}%") if q else None
    poly = sql(f"""WITH v AS (SELECT setl_name, fname, max(_first_seen) fs FROM {T['neighborhoods']} GROUP BY 1, 2)
        SELECT n.setl_name city, n.fname name, round(sum(ST_Area(n.geom::geography)))::bigint size
        FROM {T['neighborhoods']} n JOIN v ON v.setl_name = n.setl_name AND v.fname = n.fname AND v.fs = n._first_seen
        WHERE {nsql('n.setl_name')} IN ({cn}) {f"AND {nbr_sql('n.fname')} LIKE {like}" if like else ""}
        GROUP BY 1, 2 ORDER BY 2""")
    addr = sql(f"""SELECT city, neighbourhood name, count(*) size FROM {T['address_list']}
        WHERE {nsql('city')} IN ({cn}) AND neighbourhood <> '' {f"AND {nbr_sql('neighbourhood')} LIKE {like}" if like else ""}
        GROUP BY 1, 2 ORDER BY 2""")
    return [dict(r, src="polygon") for r in poly] + [dict(r, src="addresses") for r in addr]


def pick_area(cands, q):
    exact = [c for c in cands if norm_nbr(c["name"]) == norm_nbr(q)]
    pool = exact or cands
    polys = [c for c in pool if c["src"] == "polygon"]
    best = polys or pool
    # names that differ only in punctuation (e.g. "'רמת אביב ג" vs "רמת אביב ג'") are the same neighborhood
    if best and len({norm_nbr(c["name"]) for c in best}) == 1:
        return max(best, key=lambda c: c["size"])
    listing = "\n".join(f"  {c['name']}  [{c['src']}, {'m²' if c['src'] == 'polygon' else 'addresses'}={c['size']}]"
                        for c in cands) or "  (none)"
    raise Ambiguous(f"neighborhood '{q}' is {'ambiguous' if cands else 'unknown'}. Candidates:\n{listing}")


def _parcels_select(T, from_join, where=""):
    return f"""SELECT DISTINCT ON (1, 2) p."GUSH_NUM"::int gush, p."PARCEL"::int helka,
        round(ST_Y(ST_PointOnSurface(p.geom))::numeric, 6)::float8 lat,
        round(ST_X(ST_PointOnSurface(p.geom))::numeric, 6)::float8 lon
        FROM {T['parcels']} p {from_join}
        WHERE p."GUSH_NUM" ~ '^[0-9]+$' AND p."PARCEL" ~ '^[0-9]+$' {where}
        ORDER BY 1, 2, p.first_seen DESC"""


def area_polygon(T, city, name):
    n = f"""n AS (SELECT ST_Union(geom) g FROM {T['neighborhoods']} WHERE setl_name = {lit(city)} AND fname = {lit(name)}
        AND _first_seen = (SELECT max(_first_seen) FROM {T['neighborhoods']} WHERE setl_name = {lit(city)} AND fname = {lit(name)}))"""
    parcels = sql_all(f"WITH {n} " + _parcels_select(
        T, ", n", "AND p.geom && n.g AND ST_Within(ST_PointOnSurface(p.geom), n.g)"))
    geo = sql(f"WITH {n} SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(g, 0.00002), 6) gj FROM n")[0]["gj"]
    streets = sql(f"""WITH {n}, a AS ({_addr_points(T, None)})
        SELECT a.street, count(*) addresses FROM a, n WHERE ST_Within(a.pt, n.g) AND a.street <> '' GROUP BY 1""")
    return parcels, json.loads(geo), streets


def _addr_points(T, city_norms, where="TRUE"):
    city = f"{nsql('city')} IN ({','.join(lit(c) for c in city_norms)}) AND" if city_norms else ""
    return f"""SELECT street, number, ST_Transform(ST_SetSRID(ST_MakePoint("X"::float8, "Y"::float8), 2039), 4326) pt
        FROM {T['address_list']} WHERE {city} {where}
        AND "X" ~ {NUM_RE} AND "Y" ~ {NUM_RE} AND "X"::float8 > 0 AND "Y"::float8 > 0"""


def area_addresses(T, city_norms, where):
    a = f"a AS MATERIALIZED ({_addr_points(T, city_norms, where)})"
    parcels = sql_all(f"WITH {a} " + _parcels_select(T, "JOIN a ON p.geom && a.pt AND ST_Contains(p.geom, a.pt)"))
    rows = sql(f"""WITH {a} SELECT ST_AsGeoJSON(ST_Buffer(ST_ConvexHull(ST_Collect(pt))::geography, 25)::geometry, 6) gj,
        count(*) n FROM a""")
    streets = sql(f"WITH {a} SELECT street, count(*) addresses FROM a WHERE street <> '' GROUP BY 1")
    geo = json.loads(rows[0]["gj"]) if rows and rows[0]["gj"] else None
    return parcels, geo, streets


def parse_parcels(spec):
    out = []
    for tok in re.split(r"[,\s]+", spec.strip()):
        if tok:
            g, _, h = tok.partition(":")
            out.append((int(g), int(h) if h else None))
    return out


# ---------- deals ----------

def fetch_deals(plan, filters, natures, max_deals, workers=6):
    jobs = [(dict(q, **filters, **({"nature": n} if n else {})), keep)
            for q, keep in plan for n in (natures or [None])]

    def run(job):
        args, keep = job
        pages, off, total, capped, url = [], 0, None, False, None
        while True:
            r = call_tool("deals", "search_deals", dict(args, limit=200, offset=off, sort="date_desc"))
            page = r.get("data", [])
            total, capped = r.get("total"), r.get("total_capped")
            url = url or r.get("row_url")
            pages.append(page)
            off += len(page)
            if len(page) < 200 or off >= max_deals:
                break
        # Offsets are disjoint and the register has no transaction id: identical rows are separate deals.
        rows = [d for page in pages for d in page]
        fetched = len(rows)
        if keep is not None:
            rows = [d for d in rows if int(d["helka"]) in keep]
        return args, rows, total, capped, fetched, url

    deals, notes, urls = [], [], []
    with cf.ThreadPoolExecutor(workers) as ex:
        for args, rows, total, capped, fetched, url in ex.map(run, jobs):
            deals += rows
            if url:
                urls.append(url)
            q = json.dumps({k: v for k, v in args.items() if k not in filters}, ensure_ascii=False)
            if capped or (total is not None and total > fetched >= max_deals):
                notes.append(f"query {q} had {'>' if capped else ''}{total} deals; fetched {fetched} (cap --max-deals {max_deals})")
            elif total is not None and fetched != total:
                notes.append(f"query {q}: source reported {total} deals but paging returned {fetched} (register changed mid-fetch?)")
    return deals, notes, urls


def restore_rooms(T, raw):
    """search_deals filters on the raw room count but returns rooms=null for fractional values ("4.5");
    refill them from the register table, matched on gush/helka/sub-parcel/date/amount. Returns how many were fixed."""
    miss = [d for d in raw if not d.get("rooms") and d.get("date_src") and d.get("amount")]
    key = lambda d: (str(d["gush"]), str(d["helka"]), d.get("sub_parcel") or "", d["date_src"], str(int(d["amount"])))  # noqa: E731
    keys = sorted({key(d) for d in miss})
    found = {}
    for i in range(0, len(keys), 300):
        vals = ",".join("(" + ",".join(lit(x) for x in k) + ")" for k in keys[i:i + 300])
        for r in sql(f"""WITH k(g, h, s, dd, a) AS (VALUES {vals})
                SELECT k.g, k.h, k.s, k.dd, k.a, max(d.room_num) room_num FROM k JOIN {T['deals']} d
                ON d.gush = k.g AND d.chelka = k.h AND d.sub_chelka = k.s AND d.deal_date = k.dd AND d.deal_amount = k.a
                WHERE d.room_num ~ {NUM_RE} GROUP BY 1, 2, 3, 4, 5"""):
            found[(r["g"], r["h"], r["s"], r["dd"], r["a"])] = float(r["room_num"])
    fixed = 0
    for d in miss:
        v = found.get(key(d))
        if v and v > 0:
            d["rooms"] = v
            fixed += 1
    return fixed


def parcel_info(T, pairs, city_norms, streets=None, workers=4):
    """(gush, helka) → point, gazetteer street (parcel mode + per sub-parcel), dwellings, nearest address street.
    `streets` limits nearest-address candidates to the area's own streets (avoids picking a street across the border)."""
    pairs = sorted(set(pairs))
    near_where = f"{nsql('street')} IN ({','.join(lit(norm(s)) for s in streets)})" if streets else "TRUE"
    chunks = [pairs[i:i + 300] for i in range(0, len(pairs), 300)]

    def run(ps):
        vals = ",".join(f"({g},{h})" for g, h in ps)
        gl = ",".join(lit(x) for g in sorted({g for g, _ in ps}) for x in (str(g), f"{g}.0"))
        q = f"""WITH k(gush, helka) AS (VALUES {vals}),
        pp AS (SELECT DISTINCT ON (k.gush, k.helka) k.gush, k.helka, ST_PointOnSurface(p.geom) pt,
                      ST_AsGeoJSON(ST_SimplifyPreserveTopology(p.geom, 0.000003), 6) gj, round(ST_Area(p.geom::geography)) area_m2
               FROM k JOIN {T['parcels']} p ON p."GUSH_NUM" = k.gush::text AND p."PARCEL" = k.helka::text
               ORDER BY k.gush, k.helka, p.first_seen DESC),
        gz AS (SELECT split_part("GushNum", '.', 1)::int gush, split_part("ParcelNum", '.', 1)::int helka,
                      split_part("SubParcelNum", '.', 1) sub, "StreetNameHeb" street, "Type" typ
               FROM {T['gazetteer']} WHERE "GushNum" IN ({gl}) AND "ParcelNum" ~ '^[0-9]+(\\.0*)?$'),
        gs AS (SELECT gush, helka, mode() WITHIN GROUP (ORDER BY street) FILTER (WHERE street <> '') street,
                      count(*) FILTER (WHERE typ LIKE 'דירת מגורים%') dwellings,
                      jsonb_object_agg(sub, street) FILTER (WHERE street <> '' AND sub ~ '^[0-9]+$') subs
               FROM gz JOIN k USING (gush, helka) GROUP BY 1, 2),
        ad AS MATERIALIZED ({_addr_points(T, city_norms, near_where)})
        SELECT pp.gush, pp.helka, ST_Y(pp.pt) lat, ST_X(pp.pt) lon, pp.gj, pp.area_m2, gs.street gz_street, gs.dwellings, gs.subs,
               na.street near_street, na.dist near_dist
        FROM pp LEFT JOIN gs USING (gush, helka)
        LEFT JOIN LATERAL (SELECT street, round(ST_Distance(pp.pt::geography, ad.pt::geography)) dist
                           FROM ad ORDER BY pp.pt <-> ad.pt LIMIT 1) na ON true"""
        return sql(q)

    out = {}
    with cf.ThreadPoolExecutor(workers) as ex:
        for rows in ex.map(run, chunks):
            for r in rows:
                subs = r.get("subs") or {}
                if isinstance(subs, str):
                    subs = json.loads(subs)
                out[f"{r['gush']}-{r['helka']}"] = {
                    "lat": round(float(r["lat"]), 6), "lon": round(float(r["lon"]), 6),
                    "geom": json.loads(r["gj"]) if r.get("gj") else None,
                    "area_m2": float(r["area_m2"]) if r.get("area_m2") is not None else None,
                    "gz_street": r.get("gz_street") or "", "dwellings": int(r.get("dwellings") or 0),
                    "subs": {str(int(k)): v for k, v in subs.items()},
                    "near_street": r.get("near_street") or "",
                    "near_dist": float(r["near_dist"]) if r.get("near_dist") is not None else None,
                }
    return out


def gush_outlines(T, gushim, max_gushim=40):
    """Outline + label point of each whole gush (all its parcels), for the map. Skipped for very wide areas."""
    if not gushim or len(gushim) > max_gushim:
        return []
    gl = ",".join(lit(g) for g in gushim)
    rows = sql(f"""WITH u AS (SELECT "GUSH_NUM"::int gush, ST_Union(geom) g FROM {T['parcels']}
                              WHERE "GUSH_NUM" IN ({gl}) AND geom IS NOT NULL GROUP BY 1)
        SELECT gush, ST_AsGeoJSON(ST_SimplifyPreserveTopology(g, 0.00001), 6) gj,
               ST_Y(ST_PointOnSurface(g)) lat, ST_X(ST_PointOnSurface(g)) lon FROM u""")
    return [{"gush": r["gush"], "geom": json.loads(r["gj"]), "lat": round(float(r["lat"]), 6),
             "lon": round(float(r["lon"]), 6)} for r in rows if r.get("gj")]


def street_of(address):
    return re.sub(r"\s+\d+\s*[א-ת]?\s*$", "", address).strip()


def shape_deal(d, info):
    sub = (d.get("sub_parcel") or "").strip()
    sub_key = str(int(sub)) if sub.isdigit() else ""
    addresses = d.get("addresses") or []
    street, src = "", ""
    if info and sub_key and info["subs"].get(sub_key):
        street, src = info["subs"][sub_key], "gazetteer-unit"
    elif addresses:
        street, src = street_of(addresses[0]), "address"
    elif info and info["gz_street"]:
        street, src = info["gz_street"], "gazetteer"
    elif info and info["near_street"] and (info["near_dist"] or 1e9) <= NEAREST_MAX_M:
        street, src = info["near_street"], "nearest"
    return {
        "date": d["date"], "amount": d.get("amount"), "nature": d.get("nature") or "",
        "area": d.get("area_sqm") or None, "rooms": d.get("rooms") or None, "yb": d.get("year_built") or None,
        "portion": d.get("portion_fraction"), "ppsqm": d.get("price_per_sqm_normalized"),
        "gush": int(d["gush"]), "helka": int(d["helka"]), "sub": sub,
        "addresses": addresses, "street": street, "street_src": src, "settlement": d.get("settlement") or "",
    }


# ---------- report assembly ----------

def fmt_ils(v):
    return f"₪{int(v):,}"


def fmt_date(s):
    return f"{s[8:10]}/{s[5:7]}/{s[:4]}"


def chips(a, area_label):
    c = [f"📍 {area_label}"]
    r = lambda x: f"{x:g}"  # noqa: E731
    if a.min_rooms is not None and a.max_rooms is not None:
        c.append(f"{r(a.min_rooms)} חדרים" if a.min_rooms == a.max_rooms else f"{r(a.min_rooms)}–{r(a.max_rooms)} חדרים")
    elif a.min_rooms is not None:
        c.append(f"{r(a.min_rooms)}+ חדרים")
    elif a.max_rooms is not None:
        c.append(f"עד {r(a.max_rooms)} חדרים")
    if a.min_amount is not None and a.max_amount is not None:
        c.append(f"{fmt_ils(a.min_amount)} – {fmt_ils(a.max_amount)}")
    elif a.min_amount is not None:
        c.append(f"מ־{fmt_ils(a.min_amount)} ומעלה")
    elif a.max_amount is not None:
        c.append(f"עד {fmt_ils(a.max_amount)}")
    if a.date_from or a.date_to:
        c.append(f"{fmt_date(a.date_from) if a.date_from else 'מתחילת המאגר'} – {fmt_date(a.date_to) if a.date_to else 'היום'}")
    c.append("סוג: " + ", ".join(a.nature) if a.nature else "כל סוגי הנכסים")
    return c


def vendor_libs():
    VENDOR.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, url in LIBS.items():
        p = VENDOR / name
        if not p.exists():
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    p.write_bytes(r.read())
            except OSError as e:
                log(f"could not download {name} ({e}); report will load it from the CDN")
        out[name] = p.read_text(encoding="utf-8") if p.exists() else None
    return out


def render(payload, out):
    tpl = (HERE / "report_template.html").read_text(encoding="utf-8")
    libs = vendor_libs()
    tags = []
    if libs["leaflet.css"]:
        tags.append(f"<style>{libs['leaflet.css']}</style>")
    else:
        tags.append(f'<link rel="stylesheet" href="{LIBS["leaflet.css"]}">')
    for name in ("echarts.min.js", "leaflet.js"):
        if libs[name]:
            tags.append("<script>" + libs[name].replace("</script", "<\\/script") + "</script>")
        else:
            tags.append(f'<script src="{LIBS[name]}"></script>')
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    title = payload["meta"]["title"].replace("&", "&amp;").replace("<", "&lt;")
    # libs last: their minified source must never be scanned for the other placeholders
    html = (tpl.replace("__TITLE__", title)
               .replace("__DATA__", data)
               .replace("<!--__LIBS__-->", "\n".join(tags)))
    out.write_text(html, encoding="utf-8")


def build(a):
    today = dt.date.today().isoformat()
    T = discover_tables()
    S = resolve_settlement(a.city)
    settlement = S["settlement"]
    city_norms = sorted({norm(settlement), norm(a.city)})
    filters = {k: v for k, v in {
        "min_rooms": a.min_rooms, "max_rooms": a.max_rooms, "min_amount": a.min_amount,
        "max_amount": a.max_amount, "date_from": a.date_from, "date_to": a.date_to}.items() if v is not None}

    area = {"mode": "settlement", "name": settlement, "polygon": None, "parcels": [], "streets": []}
    plan, area_pairs = [({"settlement": settlement}, None)], []
    if a.neighborhood:
        c = pick_area(list_areas(T, city_norms, a.neighborhood), a.neighborhood)
        if c["src"] == "polygon":
            parcels, geo, streets = area_polygon(T, c["city"], c["name"])
            src = f"פוליגון השכונה בשכבת '{NBR_TITLE}' (govmap); חלקות שהנקודה הפנימית שלהן בתוכו"
        else:
            parcels, geo, streets = area_addresses(T, city_norms, f"neighbourhood = {lit(c['name'])}")
            src = f"כתובות שמסומנות '{c['name']}' ברשימת הכתובות הארצית, והחלקות שהן יושבות בהן"
        area.update(mode="neighborhood", name=NBR_PREFIX.sub("", c["name"]), src=src, polygon=geo, streets=streets)
        log(f"area: {c['name']} ({c['src']}) → {len(parcels)} parcels")
    elif a.streets:
        names = [s.strip() for s in a.streets.split(",") if s.strip()]
        where = f"{nsql('street')} IN ({','.join(lit(norm(s)) for s in names)})"
        parcels, geo, streets = area_addresses(T, city_norms, where)
        area.update(mode="streets", name="רחובות: " + ", ".join(names), polygon=geo, streets=streets,
                    src="כתובות הרחובות ברשימת הכתובות הארצית, והחלקות שהן יושבות בהן")
        log(f"area: streets {names} → {len(parcels)} parcels")
    elif a.parcels:
        spec = parse_parcels(a.parcels)
        parcels = []
        area.update(mode="parcels", name="גוש/חלקה: " + ", ".join(f"{g}" + (f"/{h}" if h is not None else "") for g, h in spec),
                    src="רשימת גושים/חלקות שנמסרה")
        plan = [({"gush": g, **({"helka": h} if h is not None else {})}, None) for g, h in spec]
    else:
        parcels = []
        area["src"] = "כל היישוב (סינון לפי שם היישוב במאגר)"

    if area["mode"] in ("neighborhood", "streets"):
        if not parcels:
            raise Ambiguous(f"area '{area['name']}' resolved to 0 parcels")
        by_gush = collections.defaultdict(set)
        for p in parcels:
            by_gush[p["gush"]].add(p["helka"])
        plan = []
        for g, hs in sorted(by_gush.items()):
            plan += [({"gush": g, "helka": h}, None) for h in sorted(hs)] if len(hs) <= 3 else [({"gush": g}, hs)]
        area_pairs = [(p["gush"], p["helka"]) for p in parcels]

    log(f"fetching deals: {len(plan)} queries × {len(a.nature) or 1} natures, filters {filters}")
    raw, notes, urls = fetch_deals(plan, filters, a.nature, a.max_deals)
    log(f"deals: {len(raw)}")
    if raw:
        try:
            fixed = restore_rooms(T, raw)
            if fixed:
                log(f"rooms restored from the register for {fixed} deals (fractional values the API returns as null)")
        except McpError as e:
            notes.append(f"room-count restore failed: {e}")

    info = parcel_info(T, [(int(d["gush"]), int(d["helka"])) for d in raw] + area_pairs, city_norms,
                       [r["street"] for r in area["streets"]] or None) if (raw or area_pairs) else {}

    # streets of the area: address-list streets inside the area ∪ gazetteer streets of its parcels,
    # merged across spelling variants (same norm()) under the most frequent spelling
    st = collections.defaultdict(lambda: {"addresses": 0, "units": 0})
    spell = collections.defaultdict(collections.Counter)
    for r in area["streets"]:
        st[norm(r["street"])]["addresses"] += int(r["addresses"])
        spell[norm(r["street"])][r["street"]] += int(r["addresses"])
    dwellings = 0
    for g, h in area_pairs:
        i = info.get(f"{g}-{h}")
        if i:
            dwellings += i["dwellings"]
            for s in i["subs"].values():
                st[norm(s)]["units"] += 1
                spell[norm(s)][s] += 1
    canon = {k: c.most_common(1)[0][0] for k, c in spell.items()}
    deals = sorted((shape_deal(d, info.get(f"{int(d['gush'])}-{int(d['helka'])}")) for d in raw),
                   key=lambda d: d["date"], reverse=True)
    for d in deals:
        d["street"] = canon.get(norm(d["street"]), d["street"])
    area["streets"] = sorted(({"street": canon[k], **v} for k, v in st.items()), key=lambda r: -(r["units"] + r["addresses"]))
    area["dwellings"] = dwellings if area_pairs else None
    area["parcels"] = [[p["gush"], p["helka"], p["lat"], p["lon"]] for p in parcels]
    area["n_parcels"] = len(parcels) or len({(d["gush"], d["helka"]) for d in deals})
    area["gushim"] = sorted({p["gush"] for p in parcels} or {d["gush"] for d in deals})

    try:
        area["gush_outlines"] = gush_outlines(T, area["gushim"])
    except McpError as e:
        area["gush_outlines"] = []
        notes.append(f"gush outlines failed: {e}")

    bench = None
    if not a.no_benchmark:
        args = dict(filters, settlement=settlement)
        if len(a.nature) == 1:
            args["nature"] = a.nature[0]
        try:
            bench = {"settlement": settlement, "nature": args.get("nature"),
                     "years": call_tool("deals", "price_series", args).get("years", [])}
        except McpError as e:
            notes.append(f"benchmark failed: {e}")
    try:
        reg = call_tool("deals", "register_stats", {}).get("stats", {})
    except McpError:
        reg = {}

    label = f"{area['name']}, {settlement}" if area["mode"] == "neighborhood" else (
        settlement if area["mode"] == "settlement" else f"{area['name']} · {settlement}")
    payload = {
        "meta": {
            "title": a.title or f"עסקאות נדל״ן — {label}",
            "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "today": today,
            "settlement": settlement, "settlement_last_deal": S.get("last_deal"),
            "area": area, "filters": filters, "natures": a.nature, "chips": chips(a, label),
            "register": {"last_deal": reg.get("last_deal"), "deals": reg.get("deals"),
                         "versions_url": f"https://www.over.org.il/versions/{reg['dataset_id']}" if reg.get("dataset_id") else None},
            "row_urls": urls[:12], "notes": notes, "tables": T,
            "benchmark": bench,
        },
        "deals": deals,
        "parcels": {k: {"lat": v["lat"], "lon": v["lon"], "dwellings": v["dwellings"], "area_m2": v["area_m2"],
                        "geom": v["geom"]} for k, v in info.items()},
    }
    slug = re.sub(r"[^\w]+", "-", label).strip("-")
    out = pathlib.Path(a.out or f"nadlan-report-{slug}-{today.replace('-', '')}.html")
    render(payload, out)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for n in notes:
        log("NOTE:", n)
    print(out.resolve())
    if a.open:
        webbrowser.open(out.resolve().as_uri())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    ar = sp.add_parser("areas")
    ar.add_argument("--city", required=True)
    ar.add_argument("--q")
    b = sp.add_parser("build")
    b.add_argument("--city", required=True)
    g = b.add_mutually_exclusive_group()
    g.add_argument("--neighborhood")
    g.add_argument("--streets")
    g.add_argument("--parcels")
    b.add_argument("--min-rooms", type=float)
    b.add_argument("--max-rooms", type=float)
    b.add_argument("--min-amount", type=int)
    b.add_argument("--max-amount", type=int)
    b.add_argument("--date-from")
    b.add_argument("--date-to")
    b.add_argument("--nature", action="append", default=[])
    b.add_argument("--title")
    b.add_argument("--out")
    b.add_argument("--json")
    b.add_argument("--max-deals", type=int, default=5000)
    b.add_argument("--no-benchmark", action="store_true")
    b.add_argument("--open", action="store_true")
    a = ap.parse_args()
    for k in ("date_from", "date_to"):
        v = getattr(a, k, None)
        if v and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            ap.error(f"--{k.replace('_', '-')} must be YYYY-MM-DD")
    try:
        if a.cmd == "areas":
            S = resolve_settlement(a.city)
            T = discover_tables()
            for c in list_areas(T, sorted({norm(S["settlement"]), norm(a.city)}), a.q):
                print(f"{c['name']}\t{c['src']}\t{'m²' if c['src'] == 'polygon' else 'addresses'}={c['size']}\t[{c['city']}]")
        else:
            build(a)
    except Ambiguous as e:
        log(str(e))
        sys.exit(2)
    except McpError as e:
        log("MCP error:", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
