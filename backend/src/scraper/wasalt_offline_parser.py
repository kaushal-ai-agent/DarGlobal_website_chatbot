"""
Wasalt High-Speed Offline Multi-Process Parser (`wasalt_offline_parser.py`)

What it does:
Processes cached raw HTML pages from the SQLite bronze database (`wasalt_sale_raw.db`) completely offline with ZERO network calls. Distributes raw page decompression and parsing across multi-core CPU workers using `ProcessPoolExecutor`, extracting thousands of properties, projects, and auctions into structured SQLite tables (`wasalt_pass_a.db`), CSVs, JSONLs, and diagnostic completeness reports.

Important Modules & Functions:
- `concurrent.futures.ProcessPoolExecutor`: Multi-process parallelism optimized for CPU-bound HTML/JSON extraction on Windows and Linux.
- `_index_next_data_listings(next_data)`: Traverses Next.js state trees to index listing objects by numeric ID for instantaneous attribute lookup.
- `parse_single_search_page_worker(item)`: Picklable standalone worker function extracting properties, projects, and auctions from decompressed HTML.
- `init_output_db(db_path)`: Creates clean destination SQLite tables with WAL journaling and indexing.
- `upsert_batch(conn, table, rows)`: High-throughput batch insertion into `wasalt_search_properties`, `wasalt_search_projects`, and `wasalt_search_auctions`.
- `export_csvs(...)`: Exports parsed tables to CSV files.
- `write_diagnostics(...)`: Analyzes field null rates, total yields, and writes `diagnostic_report.json` and `diagnostic_summary.md`.
- `dump_next_data_sample(...)`: Saves Page 1's raw Next.js JSON for offline field inspection.
- `run_offline_parser(args)`: CLI orchestrator driving batch streaming from `RawCacheDB` through process pool workers to disk.

Core Logic (One-Liner):
Streams compressed raw pages from SQLite cache into a multi-process pool, parses Next.js and DOM cards in parallel, and exports relational SQLite, CSV, JSONL, and data quality diagnostics.
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

# Resolve project root and ensure it is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_DIR = Path(__file__).resolve().parent
for p in [str(PROJECT_ROOT), str(SCRAPER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from wasalt_raw_store import DEFAULT_RAW_DB, PASS_A_OUT_DIR, RawCacheDB, log_error_context, setup_logger

OUT_DB = PASS_A_OUT_DIR / "wasalt_pass_a.db"
OUT_CSV_DIR = PASS_A_OUT_DIR / "csv"
OUT_JSONL_DIR = PASS_A_OUT_DIR / "jsonl"
OUT_DIAG_DIR = PASS_A_OUT_DIR / "diagnostics"

TABLES = ("wasalt_search_properties", "wasalt_search_projects", "wasalt_search_auctions")


# ---------------------------------------------------------------------------
# Pure Picklable Parsing Helpers (Safe for ProcessPoolExecutor on Windows)
# ---------------------------------------------------------------------------

def _clean(val: Any) -> str | None:
    if val is None:
        return None
    s = re.sub(r"\s+", " ", str(val)).strip()
    return s if s else None


def _num(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace(",", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM])?", s)
    if not m:
        return None
    n = float(m.group(1))
    suf = (m.group(2) or "").lower()
    if suf == "k":
        n *= 1_000
    elif suf == "m":
        n *= 1_000_000
    return n


def _int(val: Any) -> int | None:
    n = _num(val)
    return int(n) if n is not None else None


def _classify_url(url: str) -> str:
    low = url.lower()
    if "auction.wasalt.sa" in low or "/auction/" in low:
        return "auction"
    if "/project/" in low:
        return "project"
    return "property"


def _extract_id(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    m = re.search(r"(\d+)$", path)
    return m.group(1) if m else re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")


def _parse_location(loc_text: str | None) -> tuple[str | None, str | None, str | None]:
    if not loc_text:
        return None, None, None
    parts = [_clean(p) for p in loc_text.split(",") if _clean(p)]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[-1]
    if len(parts) == 2:
        return parts[0], None, parts[1]
    if len(parts) == 1:
        return None, None, parts[0]
    return None, None, None


def _deep_get(obj: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        low = {k.lower(): v for k, v in obj.items()}
        for k in keys:
            v = low.get(k.lower())
            if v not in (None, "", [], {}):
                return v
        for v in obj.values():
            if isinstance(v, (dict, list)):
                found = _deep_get(v, keys)
                if found not in (None, "", [], {}):
                    return found
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                found = _deep_get(item, keys)
                if found not in (None, "", [], {}):
                    return found
    return None


def _index_next_data_listings(next_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Walk __NEXT_DATA__ to find listing/project/auction arrays and index each dict by its numeric ID.
    This unlocks fields hidden in SSR JSON that aren't rendered in the card HTML.
    """
    by_id: dict[str, dict[str, Any]] = {}
    hint_keys = {"id", "propertyid", "projectid", "auctionid", "price", "slug", "url", "title", "refno"}

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 7:
            return
        if isinstance(node, list) and len(node) >= 1 and isinstance(node[0], dict):
            sample_keys = {k.lower() for k in node[0].keys()}
            if len(sample_keys & hint_keys) >= 2:
                for item in node:
                    if not isinstance(item, dict):
                        continue
                    item_id = item.get("id") or item.get("propertyId") or item.get("projectId") or item.get("auctionId")
                    if item_id is not None:
                        by_id[str(item_id)] = item
        if isinstance(node, dict):
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node[:50]:
                if isinstance(v, (dict, list)):
                    walk(v, depth + 1)

    walk(next_data.get("props", {}).get("pageProps", {}))
    return by_id


def parse_single_search_page_worker(item: tuple[int, str, str]) -> dict[str, Any]:
    """
    CPU-bound worker executed in ProcessPoolExecutor.
    Fuses __NEXT_DATA__ + per-card JSON-LD + DOM card HTML into the 3 structured tables.
    """
    page_num, page_url, html = item
    out_props: list[dict[str, Any]] = []
    out_projs: list[dict[str, Any]] = []
    out_aucs: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        soup = BeautifulSoup(html, "lxml")

        next_data: dict[str, Any] = {}
        nd_tag = soup.find("script", id="__NEXT_DATA__")
        if nd_tag and nd_tag.string:
            try:
                next_data = json.loads(nd_tag.string)
            except Exception:
                next_data = {}

        next_by_id = _index_next_data_listings(next_data)
        now_iso = datetime.now(timezone.utc).isoformat()

        containers = soup.select(
            "div[class*='cardWrapper' i], div[class*='featuredWrapper' i], article[class*='listingCard' i]"
        )
        seen_urls: set[str] = set()

        for container in containers:
            anchor = container.find(
                "a", href=re.compile(r"(/property/|/project/|auction\.wasalt\.sa/en/auction/)", re.I)
            )
            if not anchor or not anchor.get("href"):
                continue

            abs_url = urljoin("https://wasalt.sa", anchor["href"].strip()).split("#")[0]
            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            rec_type = _classify_url(abs_url)
            num_id = _extract_id(abs_url)
            raw_card_text = _clean(container.get_text(" | ", strip=True)) or ""
            card_title = _clean(anchor.get("title") or anchor.get_text(" ", strip=True))

            # Per-card JSON-LD
            card_ld: dict[str, Any] = {}
            ld_tag = container.find("script", type="application/ld+json")
            if ld_tag and ld_tag.string:
                try:
                    parsed_ld = json.loads(ld_tag.string)
                    if isinstance(parsed_ld, dict):
                        card_ld = parsed_ld
                except Exception:
                    pass

            # Matching __NEXT_DATA__ JSON object (if present)
            nd_obj = next_by_id.get(num_id, {})
            raw_bundle = json.dumps(
                {"card_text": raw_card_text, "json_ld": card_ld, "next_data": nd_obj},
                ensure_ascii=False,
            )

            # Parse card pipe-separated parts for location & price fallback
            parts = [p.strip() for p in raw_card_text.split("|") if p.strip()]
            loc_candidate = next((p for p in parts if "," in p and "Deposit" not in p and "Bid" not in p), None)
            district, region_zone, city = _parse_location(loc_candidate)

            # Enrich location from __NEXT_DATA__ or JSON-LD
            city = _clean(_deep_get(nd_obj, ("cityName", "city", "cityEn"))) or city
            district = _clean(_deep_get(nd_obj, ("districtName", "district", "districtEn"))) or district
            region_zone = _clean(_deep_get(nd_obj, ("regionName", "zoneName", "region"))) or region_zone

            if rec_type == "property":
                m_type = re.search(r"/sale/([a-z-]+?)-\d", abs_url.lower())
                prop_type = _clean(_deep_get(nd_obj, ("propertyTypeName", "propertyType", "subCategory"))) or (
                    m_type.group(1).replace("-", " ").title() if m_type else None
                )
                m_sqm = re.search(r"([\d,]+(?:\.\d+)?)\s*SQM", (card_title or "") + " " + raw_card_text, re.I)
                m_face = re.search(r"Facing\s+(North East|North West|South East|South West|North|South|East|West)", raw_card_text, re.I)
                m_sw = re.search(r"([\d\.]+)\s*m\s*Width\s*Street", raw_card_text, re.I)
                m_bed = re.search(r"(\d+)\s*Bedrooms?", (card_title or "") + " " + raw_card_text, re.I)
                m_bath = re.search(r"(\d+)\s*Bathrooms?", raw_card_text, re.I)

                ld_price = _num((card_ld.get("offers") or {}).get("price")) if isinstance(card_ld.get("offers"), dict) else None
                price_sar = _num(_deep_get(nd_obj, ("price", "salePrice", "priceAmount"))) or ld_price or _num(parts[0] if parts else None)
                area_sqm = _num(_deep_get(nd_obj, ("area", "builtUpArea", "landArea", "propertyArea"))) or (
                    _num(m_sqm.group(1)) if m_sqm else None
                )

                out_props.append(
                    {
                        "record_id": f"wasalt_{num_id}",
                        "source_url": abs_url,
                        "search_page_num": page_num,
                        "search_page_url": page_url,
                        "ref_no": _clean(_deep_get(nd_obj, ("refNo", "referenceNumber", "ref_no"))),
                        "property_type": prop_type,
                        "property_usage": _clean(_deep_get(nd_obj, ("propertyUsage", "usage", "purposeType"))) or "Residential",
                        "title": _clean(_deep_get(nd_obj, ("title", "propertyTitle", "name"))) or card_title,
                        "city": city,
                        "region_zone": region_zone,
                        "district": district,
                        "price_sar": price_sar,
                        "area_sqm": area_sqm,
                        "bedrooms": _int(_deep_get(nd_obj, ("bedrooms", "bedroomCount"))) or (_int(m_bed.group(1)) if m_bed else None),
                        "bathrooms": _int(_deep_get(nd_obj, ("bathrooms", "bathroomCount"))) or (_int(m_bath.group(1)) if m_bath else None),
                        "facade": _clean(_deep_get(nd_obj, ("facade", "facing"))) or (m_face.group(1) if m_face else None),
                        "street_width_m": _num(_deep_get(nd_obj, ("streetWidth",))) or (_num(m_sw.group(1)) if m_sw else None),
                        "broker_agency": _clean(_deep_get(nd_obj, ("agencyName", "companyName", "advertiserName", "brokerName"))),
                        "fal_license_no": _clean(_deep_get(nd_obj, ("falLicenseNo", "falLicenseNumber"))),
                        "rega_ad_license_no": _clean(_deep_get(nd_obj, ("adLicenseNumber", "regaLicenseNo"))),
                        "is_featured": 1 if "featured" in raw_card_text.lower() else 0,
                        "raw_card_text": raw_card_text,
                        "raw_card_json": raw_bundle[:8000],
                        "parsed_at": now_iso,
                    }
                )

            elif rec_type == "project":
                m_pr = re.search(r"([\d,\.]+\s*[kKmM]?)\s*-\s*([\d,\.]+\s*[kKmM]?)", raw_card_text)
                m_u = re.search(r"\|\s*(\d+)\s*\|\s*Units\s*available", raw_card_text, re.I)
                out_projs.append(
                    {
                        "project_id": f"ws_proj_{num_id}",
                        "source_url": abs_url,
                        "search_page_num": page_num,
                        "search_page_url": page_url,
                        "project_name": _clean(_deep_get(nd_obj, ("projectName", "name", "title"))) or card_title,
                        "developer_name": _clean(_deep_get(nd_obj, ("developerName", "companyName"))),
                        "city": city,
                        "region_zone": region_zone,
                        "district": district,
                        "price_min_sar": _num(_deep_get(nd_obj, ("minPrice", "startingPrice"))) or (_num(m_pr.group(1)) if m_pr else None),
                        "price_max_sar": _num(_deep_get(nd_obj, ("maxPrice",))) or (_num(m_pr.group(2)) if m_pr else None),
                        "available_units": _int(_deep_get(nd_obj, ("availableUnits", "unitsCount"))) or (_int(m_u.group(1)) if m_u else None),
                        "raw_card_text": raw_card_text,
                        "raw_card_json": raw_bundle[:8000],
                        "parsed_at": now_iso,
                    }
                )

            elif rec_type == "auction":
                m_st = re.search(r"\b(Upcoming|Live|Ongoing|Ended|Closed|Sold|Cancelled)\b", raw_card_text, re.I)
                m_dep = re.search(r"Deposit\s*Amount:\s*\|\s*([\d,\.]+)", raw_card_text, re.I)
                m_bid = re.search(r"Highest\s*Bid:\s*\|\s*([\d,\.]+)", raw_card_text, re.I)
                m_area = re.search(r"([\d,]+(?:\.\d+)?)\s*(?:SQM|m2|m²|م2)", raw_card_text, re.I)
                out_aucs.append(
                    {
                        "asset_id": f"auction_{num_id}",
                        "source_url": abs_url,
                        "search_page_num": page_num,
                        "search_page_url": page_url,
                        "title": _clean(_deep_get(nd_obj, ("title", "name"))) or card_title,
                        "city": city,
                        "district": district,
                        "status": _clean(_deep_get(nd_obj, ("status", "auctionStatus"))) or (m_st.group(1).title() if m_st else None),
                        "area_sqm": _num(_deep_get(nd_obj, ("area", "landArea"))) or (_num(m_area.group(1)) if m_area else None),
                        "deposit_amount_sar": _num(_deep_get(nd_obj, ("depositAmount",))) or (_num(m_dep.group(1)) if m_dep else None),
                        "highest_bid_sar": _num(_deep_get(nd_obj, ("highestBid",))) or (_num(m_bid.group(1)) if m_bid else None),
                        "raw_card_text": raw_card_text,
                        "raw_card_json": raw_bundle[:8000],
                        "parsed_at": now_iso,
                    }
                )

    except Exception as exc:
        errors.append(f"page={page_num} url={page_url} err={type(exc).__name__}:{exc}")

    return {
        "page_num": page_num,
        "wasalt_search_properties": out_props,
        "wasalt_search_projects": out_projs,
        "wasalt_search_auctions": out_aucs,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Output SQLite + CSV + Diagnostics
# ---------------------------------------------------------------------------

def init_output_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()  # Clean deterministic rebuild from raw_cache.db
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.executescript(
        """
        CREATE TABLE wasalt_search_properties (
            record_id TEXT PRIMARY KEY,
            source_url TEXT UNIQUE NOT NULL,
            search_page_num INTEGER,
            search_page_url TEXT,
            ref_no TEXT,
            property_type TEXT,
            property_usage TEXT,
            title TEXT,
            city TEXT,
            region_zone TEXT,
            district TEXT,
            price_sar REAL,
            area_sqm REAL,
            bedrooms INTEGER,
            bathrooms INTEGER,
            facade TEXT,
            street_width_m REAL,
            broker_agency TEXT,
            fal_license_no TEXT,
            rega_ad_license_no TEXT,
            is_featured INTEGER,
            raw_card_text TEXT,
            raw_card_json TEXT,
            parsed_at TEXT NOT NULL
        );

        CREATE TABLE wasalt_search_projects (
            project_id TEXT PRIMARY KEY,
            source_url TEXT UNIQUE NOT NULL,
            search_page_num INTEGER,
            search_page_url TEXT,
            project_name TEXT,
            developer_name TEXT,
            city TEXT,
            region_zone TEXT,
            district TEXT,
            price_min_sar REAL,
            price_max_sar REAL,
            available_units INTEGER,
            raw_card_text TEXT,
            raw_card_json TEXT,
            parsed_at TEXT NOT NULL
        );

        CREATE TABLE wasalt_search_auctions (
            asset_id TEXT PRIMARY KEY,
            source_url TEXT UNIQUE NOT NULL,
            search_page_num INTEGER,
            search_page_url TEXT,
            title TEXT,
            city TEXT,
            district TEXT,
            status TEXT,
            area_sqm REAL,
            deposit_amount_sar REAL,
            highest_bid_sar REAL,
            raw_card_text TEXT,
            raw_card_json TEXT,
            parsed_at TEXT NOT NULL
        );

        CREATE INDEX idx_sp_city_type_price ON wasalt_search_properties(city, property_type, price_sar);
        CREATE INDEX idx_sp_district ON wasalt_search_properties(district);
        CREATE INDEX idx_sproj_city ON wasalt_search_projects(city, price_min_sar);
        CREATE INDEX idx_sauc_status ON wasalt_search_auctions(status, city, deposit_amount_sar);
        """
    )
    conn.commit()
    return conn


def upsert_batch(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})"
    with conn:
        conn.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])


def write_diagnostics(conn: sqlite3.Connection, worker_errors: list[str], elapsed_s: float) -> None:
    OUT_DIAG_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_s, 2),
        "worker_errors_count": len(worker_errors),
        "worker_errors_sample": worker_errors[:20],
        "tables": {},
    }

    md = [
        "# Wasalt Pass A Offline Parsing & Completeness Report",
        f"- **Generated:** {report['generated_at']}",
        f"- **Offline Parse Duration:** {report['elapsed_seconds']}s",
        f"- **Worker Errors:** {len(worker_errors)}",
        "",
    ]

    for tbl in TABLES:
        total = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
        report["tables"][tbl] = {"total_rows": total, "columns": {}}

        md.append(f"## Table: `{tbl}` ({total:,} unique rows)")
        md.append("")
        md.append("| Column | Non-Null | Missing | Completeness % |")
        md.append("| :--- | :---: | :---: | :---: |")

        for col in cols:
            if col in ("raw_card_json", "raw_card_text", "parsed_at"):
                continue
            non_null = conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE {col} IS NOT NULL AND TRIM(CAST({col} AS TEXT)) != ''"
            ).fetchone()[0]
            missing = total - non_null
            pct = round((non_null / total) * 100, 1) if total > 0 else 0.0
            report["tables"][tbl]["columns"][col] = {"non_null": non_null, "missing": missing, "pct": pct}
            md.append(f"| `{col}` | {non_null:,} | {missing:,} | **{pct}%** |")
        md.append("")

    (OUT_DIAG_DIR / "diagnostic_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIAG_DIR / "diagnostic_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def dump_next_data_sample(raw_db: RawCacheDB, logger) -> None:
    """Save Page 1's __NEXT_DATA__ structure so R&D can inspect every exact JSON key offline."""
    res = raw_db.get_single_page_html(1)
    if not res:
        logger.warning("Page 1 not found in raw_cache.db; skipping schema inspection.")
        return
    _, html = res
    soup = BeautifulSoup(html, "lxml")
    nd_tag = soup.find("script", id="__NEXT_DATA__")
    if not nd_tag or not nd_tag.string:
        logger.warning("No __NEXT_DATA__ script tag found on Page 1.")
        return
    OUT_DIAG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIAG_DIR / "page1_next_data_raw.json"
    out_path.write_text(json.dumps(json.loads(nd_tag.string), indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved Page 1 __NEXT_DATA__ JSON for R&D inspection -> %s", out_path)


def export_csvs(conn: sqlite3.Connection) -> None:
    OUT_CSV_DIR.mkdir(parents=True, exist_ok=True)
    for tbl in TABLES:
        cur = conn.execute(f"SELECT * FROM {tbl}")
        cols = [d[0] for d in cur.description]
        with (OUT_CSV_DIR / f"{tbl}.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            while True:
                chunk = cur.fetchmany(1000)
                if not chunk:
                    break
                writer.writerows(chunk)


def run_offline_parser(args: argparse.Namespace) -> None:
    logger = setup_logger("parse_wasalt_search_offline", verbose=args.verbose)
    t0 = time.monotonic()

    raw_db = RawCacheDB(Path(args.raw_db))
    cache_stats = raw_db.get_stats()
    logger.info("Reading local raw cache (%s): %s", args.raw_db, json.dumps(cache_stats))

    if args.inspect_schema:
        dump_next_data_sample(raw_db, logger)

    out_conn = init_output_db(OUT_DB)
    OUT_JSONL_DIR.mkdir(parents=True, exist_ok=True)

    # Open clean JSONL sinks (deduplicated via O(1) seen_ids sets)
    jsonl_handles = {tbl: (OUT_JSONL_DIR / f"{tbl}.jsonl").open("w", encoding="utf-8") for tbl in TABLES}
    seen_ids: dict[str, set[str]] = {tbl: set() for tbl in TABLES}
    pk_col = {
        "wasalt_search_properties": "record_id",
        "wasalt_search_projects": "project_id",
        "wasalt_search_auctions": "asset_id",
    }

    workers = args.workers or max(1, (os.cpu_count() or 2) - 1)
    logger.info("Launching ProcessPoolExecutor with %d CPU workers (batch_size=%d)...", workers, args.batch_size)

    pages_parsed = 0
    worker_errors: list[str] = []

    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for batch in raw_db.iter_raw_pages(batch_size=args.batch_size, limit=args.limit):
                batch_results = list(pool.map(parse_single_search_page_worker, batch))

                merged: dict[str, list[dict[str, Any]]] = {tbl: [] for tbl in TABLES}
                for res in batch_results:
                    pages_parsed += 1
                    worker_errors.extend(res["errors"])
                    for tbl in TABLES:
                        pk = pk_col[tbl]
                        for row in res[tbl]:
                            rid = row[pk]
                            if rid not in seen_ids[tbl]:
                                seen_ids[tbl].add(rid)
                                merged[tbl].append(row)

                for tbl in TABLES:
                    rows = merged[tbl]
                    if rows:
                        upsert_batch(out_conn, tbl, rows)
                        fh = jsonl_handles[tbl]
                        for r in rows:
                            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

                logger.info(
                    "Parsed %d pages -> unique totals: properties=%d, projects=%d, auctions=%d",
                    pages_parsed,
                    len(seen_ids["wasalt_search_properties"]),
                    len(seen_ids["wasalt_search_projects"]),
                    len(seen_ids["wasalt_search_auctions"]),
                )
    except Exception as exc:
        log_error_context(logger, "offline_parser_main", str(args.raw_db), exc)
        raise
    finally:
        for fh in jsonl_handles.values():
            fh.close()
        raw_db.close()

    elapsed = time.monotonic() - t0
    if not args.skip_csv:
        logger.info("Exporting CSV tables...")
        export_csvs(out_conn)

    write_diagnostics(out_conn, worker_errors, elapsed)
    out_conn.close()

    logger.info("OFFLINE_PARSE_COMPLETE in %.2fs", elapsed)
    logger.info("SQLite DB:          %s", OUT_DB)
    logger.info("CSV Tables:         %s", OUT_CSV_DIR)
    logger.info("Diagnostic Summary: %s", OUT_DIAG_DIR / "diagnostic_summary.md")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pass A Offline Multi-Core Parser (0 website hits).")
    parser.add_argument("--raw-db", default=str(DEFAULT_RAW_DB), help="Path to raw_cache SQLite DB")
    parser.add_argument("--batch-size", type=int, default=50, help="Pages streamed per memory batch")
    parser.add_argument("--workers", type=int, default=None, help="CPU worker processes (default: cpu_count - 1)")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on cached pages to parse for quick R&D")
    parser.add_argument("--inspect-schema", action="store_true", help="Dump Page 1 __NEXT_DATA__ JSON for R&D field mapping")
    parser.add_argument("--skip-csv", action="store_true", help="Skip CSV export during fast R&D iterations")
    parser.add_argument("--verbose", action="store_true")
    run_offline_parser(parser.parse_args())
