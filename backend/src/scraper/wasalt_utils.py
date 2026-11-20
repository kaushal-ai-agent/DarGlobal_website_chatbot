"""
Wasalt Scraping Utilities & Diagnostic Telemetry (`wasalt_utils.py`)

What it does:
Provides foundational storage, database schemas, quality telemetry, text normalization, and retry utilities for the Wasalt scraping pipeline. Manages SQLite initialization, upserts, CSV export routines, checkpoint states, Cloudflare block detection, and per-field null tracking via DiagnosticTracker.

Important Modules & Functions:
- `DiagnosticTracker`: Class tracking record counts, field null rates, and detailed missing-field logs for data quality assurance.
- `init_sqlite(db_path)`: Creates normalized SQLite tables (wasalt_properties, wasalt_projects, wasalt_project_units, wasalt_auctions).
- `upsert_sqlite_rows(db_path, table_name, rows)`: High-throughput batch upserting using INSERT OR REPLACE.
- `export_sqlite_to_csvs(db_path, csv_dir)`: Exports all relational SQLite tables to cleanly formatted CSV files.
- `detect_block_or_error(html)`: Evaluates page HTML for Cloudflare Turnstile blocks, captcha challenges, or HTTP 5xx errors.
- `clean_text(text)` / `parse_number(val)` / `parse_int(val)`: Safe parsing utilities that prevent conversion exceptions and strip whitespace.
- `classify_url(url)`: Categorizes URLs into 'property', 'project', or 'auction' entity types.
- `load_state(state_file)` / `save_state(state_file, state)`: Resilient JSON state tracking for resuming interrupted crawls.

Core Logic (One-Liner):
Supplies SQLite schema management, batch persistence, CSV export, Cloudflare error gatekeeping, and field completeness telemetry across the Wasalt scraping pipeline.
"""

import csv
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup

# Resolve project root and ensure it is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_DIR = Path(__file__).resolve().parent
for p in [str(PROJECT_ROOT), str(SCRAPER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Paths & Constants
# ---------------------------------------------------------------------------

OUTPUT_DIR = PROJECT_ROOT / "data" / "wasalt" / "sale_listings"
# Hyperparameter: max characters stored in SQLite/CSV raw_source_text (set to None for full unlimited text)
RAW_TEXT_MAX_CHARS: int | None = 50000

TABLES_DIR = OUTPUT_DIR / "tables"
RAW_DUMP_DIR = OUTPUT_DIR / "raw_dump"
CSV_DIR = OUTPUT_DIR / "csv"
DB_PATH = OUTPUT_DIR / "wasalt_sale.db"
CHECKPOINT_DIR = PROJECT_ROOT / "data" / "wasalt" / "checkpoints"
DIAG_DIR = PROJECT_ROOT / "data" / "wasalt" / "diagnostics"

FAILED_JSONL = CHECKPOINT_DIR / "failed_sale_urls.jsonl"
STATE_FILE = CHECKPOINT_DIR / "sale_search_state.json"
MISSES_JSONL = DIAG_DIR / "field_misses_log.jsonl"
DIAG_REPORT_JSON = DIAG_DIR / "diagnostic_report.json"
DIAG_SUMMARY_MD = DIAG_DIR / "diagnostic_summary.md"

TABLE_FILES = {
    "wasalt_properties": TABLES_DIR / "wasalt_properties.jsonl",
    "wasalt_projects": TABLES_DIR / "wasalt_projects.jsonl",
    "wasalt_project_units": TABLES_DIR / "wasalt_project_units.jsonl",
    "wasalt_auctions": TABLES_DIR / "wasalt_auctions.jsonl",
}
RAW_DUMP_FILES = {
    "wasalt_properties": RAW_DUMP_DIR / "wasalt_properties_raw.jsonl",
    "wasalt_projects": RAW_DUMP_DIR / "wasalt_projects_raw.jsonl",
    "wasalt_project_units": RAW_DUMP_DIR / "wasalt_project_units_raw.jsonl",
    "wasalt_auctions": RAW_DUMP_DIR / "wasalt_auctions_raw.jsonl",
}


def truncate_raw(text: str | None) -> str | None:
    if text is None or RAW_TEXT_MAX_CHARS is None:
        return text
    return text[:RAW_TEXT_MAX_CHARS]


BLOCK_SIGNATURES = (
    "just a moment...",
    "checking if the site connection is secure",
    "verify you are human",
    "cf-browser-verification",
    "enable javascript and cookies to continue",
    "504 gateway time-out",
    "502 bad gateway",
    "503 service temporarily unavailable",
    "access denied",
    "attention required! | cloudflare",
)


# ---------------------------------------------------------------------------
# Text, Number, URL & Block Detection Helpers
# ---------------------------------------------------------------------------

def clean_text(text: Any) -> str | None:
    if text is None:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip()
    return s if s else None


def strip_html_tags(text: Any) -> str | None:
    if not text:
        return None
    s = str(text)
    if "<" in s and ">" in s:
        s = BeautifulSoup(s, "lxml").get_text(" ", strip=True)
    return clean_text(s)


def parse_number(val: Any) -> float | None:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace(",", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*([kKmM])?", s)
    if not m:
        return None
    num = float(m.group(1))
    suffix = (m.group(2) or "").lower()
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    return num


def parse_int(val: Any) -> int | None:
    num = parse_number(val)
    return int(num) if num is not None else None


def classify_url(url: str) -> str:
    lower = url.lower()
    if "auction.wasalt.sa" in lower or "/auction/" in lower:
        return "auction"
    if "/project/" in lower:
        return "project"
    return "property"


def extract_numeric_id(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    m = re.search(r"(\d+)$", path)
    if m:
        return m.group(1)
    return re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")


def build_page_url(base_url: str, page_num: int) -> str:
    parsed = urlparse(base_url)
    qs = parse_qs(parsed.query)
    qs["page"] = [str(page_num)]
    flat_qs = {k: v[0] for k, v in qs.items()}
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(flat_qs)}"


def detect_block_or_error(html: str) -> str | None:
    """Detect Cloudflare challenges, gateway errors, or empty stub pages like <h1>wasalt.sa</h1>."""
    if not html or len(html) < 500:
        return "empty_or_short_html"
    lower = html.lower()
    for sig in BLOCK_SIGNATURES:
        if sig in lower:
            return f"block_signature:{sig}"

    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    h1_text = clean_text(h1.get_text(" ", strip=True)) if h1 else ""
    if h1_text and h1_text.lower() in ("wasalt.sa", "auction.wasalt.sa", "just a moment..."):
        return f"challenge_h1:{h1_text}"

    # Check if body text is suspiciously short
    for t in soup.select("script, style, noscript"):
        t.decompose()
    body_text = clean_text(soup.get_text(" ", strip=True)) or ""
    if len(body_text) < 120:
        return f"suspiciously_short_body:{len(body_text)}_chars"
    return None


def parse_location_parts(text: str | None) -> tuple[str | None, str | None, str | None]:
    if not text:
        return None, None, None
    parts = [clean_text(p) for p in text.split(",") if clean_text(p)]
    if len(parts) >= 3:
        return parts[0], parts[1], parts[-1]
    if len(parts) == 2:
        return parts[0], None, parts[1]
    if len(parts) == 1:
        return None, None, parts[0]
    return None, None, None


def deep_find_first(obj: Any, candidate_keys: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        lower_map = {k.lower(): v for k, v in obj.items()}
        for key in candidate_keys:
            val = lower_map.get(key.lower())
            if val not in (None, "", [], {}):
                return val
        for v in obj.values():
            found = deep_find_first(v, candidate_keys)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = deep_find_first(item, candidate_keys)
            if found not in (None, "", [], {}):
                return found
    return None


def deep_find_all_dicts_with_keys(obj: Any, required_any: set[str]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj[:3]):
            sample_keys = {k.lower() for k in obj[0].keys()}
            if len(sample_keys & required_any) >= 2:
                matches.extend([x for x in obj if isinstance(x, dict)])
        for item in obj:
            matches.extend(deep_find_all_dicts_with_keys(item, required_any))
    elif isinstance(obj, dict):
        for v in obj.values():
            matches.extend(deep_find_all_dicts_with_keys(v, required_any))
    return matches


def extract_next_data(soup: BeautifulSoup) -> dict[str, Any]:
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            data = json.loads(script.string)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def extract_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    blocks = []
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
            if isinstance(data, list):
                blocks.extend([x for x in data if isinstance(x, dict)])
            elif isinstance(data, dict):
                blocks.append(data)
        except Exception:
            continue
    return blocks


def extract_strict_label_values(lines: list[str], known_labels: list[str]) -> dict[str, str]:
    label_set = {lbl.lower(): lbl for lbl in known_labels}
    out: dict[str, str] = {}
    for idx, line in enumerate(lines):
        key = line.strip().rstrip(":").lower()
        if key in label_set and idx + 1 < len(lines):
            val = lines[idx + 1].strip()
            if val and val.rstrip(":").lower() not in label_set:
                out.setdefault(label_set[key], val)
    return out


def collect_json_keys(obj: Any, max_keys: int = 80) -> list[str]:
    """Collect unique dictionary keys from __NEXT_DATA__ so diagnostics reveal exact API field names."""
    keys: list[str] = []
    seen: set[str] = set()

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 4 or len(keys) >= max_keys:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if k not in seen and not k.startswith("_"):
                    seen.add(k)
                    keys.append(k)
                walk(v, depth + 1)
        elif isinstance(node, list) and node:
            walk(node[0], depth + 1)

    walk(obj)
    return keys


# ---------------------------------------------------------------------------
# Automated Field-Level Diagnostic & Root-Cause Tracker
# ---------------------------------------------------------------------------

class DiagnosticTracker:
    """
    Tracks every field across 20k+ records with O(1) memory per field.
    Automatically distinguishes:
      - not_applicable (e.g., bedrooms on Land)
      - possible_parser_miss (keyword EXISTS in raw_source_text -> captures snippet!)
      - genuinely_missing_on_page (keyword not in raw_source_text or JSON)
    """

    def __init__(self) -> None:
        self.table_totals: dict[str, int] = defaultdict(int)
        self.blocked_events: list[dict[str, Any]] = []
        # stats[table][field] -> counters + up to 5 sample misses
        self.stats: dict[str, dict[str, dict[str, Any]]] = defaultdict(
            lambda: defaultdict(
                lambda: {
                    "present": 0,
                    "not_applicable": 0,
                    "genuinely_missing": 0,
                    "possible_parser_miss": 0,
                    "checked_sources": [],
                    "sample_misses": [],
                }
            )
        )
        self.discovered_next_keys: dict[str, set[str]] = defaultdict(set)

    def log_block(self, url: str, reason: str, stage: str) -> None:
        if len(self.blocked_events) < 200:
            self.blocked_events.append(
                {"url": url, "reason": reason, "stage": stage, "ts": datetime.now(timezone.utc).isoformat()}
            )

    def audit_record(
        self,
        table: str,
        record_id: str,
        source_url: str,
        row: dict[str, Any],
        raw_text: str,
        field_specs: dict[str, dict[str, Any]],
        next_data_keys: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Audit all fields in `field_specs` for a single scraped row.
        Returns a list of missing-field diagnostic dicts (also written to field_misses_log.jsonl).
        """
        self.table_totals[table] += 1
        if next_data_keys:
            self.discovered_next_keys[table].update(next_data_keys[:60])

        raw_lower = (raw_text or "").lower()
        row_misses: list[dict[str, Any]] = []

        for field, spec in field_specs.items():
            st = self.stats[table][field]
            if not st["checked_sources"]:
                st["checked_sources"] = spec.get("checked", [])

            val = row.get(field)
            is_empty = val is None or val == "" or val == "[]" or val == "{}"
            if not is_empty:
                st["present"] += 1
                continue

            if spec.get("not_applicable", False):
                st["not_applicable"] += 1
                continue

            # Probe raw_source_text for clue keywords to detect parser misses vs truly absent fields
            probe_keywords = spec.get("probe_keywords", [])
            matched_snippet = None
            matched_kw = None
            for kw in probe_keywords:
                idx = raw_lower.find(kw.lower())
                if idx != -1:
                    start = max(0, idx - 50)
                    end = min(len(raw_text), idx + 110)
                    matched_snippet = raw_text[start:end].replace("\n", " | ")
                    matched_kw = kw
                    break

            if matched_snippet:
                verdict = "possible_parser_miss"
                st["possible_parser_miss"] += 1
            else:
                verdict = "genuinely_missing_on_page"
                st["genuinely_missing"] += 1

            miss_entry = {
                "table": table,
                "record_id": record_id,
                "source_url": source_url,
                "field": field,
                "verdict": verdict,
                "checked_keys_or_rules": spec.get("checked", []),
                "matched_keyword": matched_kw,
                "raw_text_snippet": matched_snippet,
            }
            row_misses.append(miss_entry)

            # Keep up to 5 concrete examples per field (prioritize possible_parser_miss)
            if len(st["sample_misses"]) < 5:
                st["sample_misses"].append(miss_entry)
            elif verdict == "possible_parser_miss":
                # Replace a genuinely_missing sample if we have a parser miss
                for i, existing in enumerate(st["sample_misses"]):
                    if existing["verdict"] != "possible_parser_miss":
                        st["sample_misses"][i] = miss_entry
                        break

        if row_misses:
            append_jsonl(MISSES_JSONL, row_misses)
        return row_misses

    def save_reports(self) -> None:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "table_totals": dict(self.table_totals),
            "blocked_or_challenge_count": len(self.blocked_events),
            "blocked_samples": self.blocked_events[:15],
            "tables": {},
            "discovered_next_data_keys": {
                tbl: sorted(list(keys))[:80] for tbl, keys in self.discovered_next_keys.items()
            },
        }

        md_lines = [
            "# Wasalt Scraper Diagnostic & Root-Cause Report",
            f"- **Generated:** {report['generated_at']}",
            f"- **Blocked / Challenge Pages Intercepted:** {len(self.blocked_events)}",
            "",
        ]

        for tbl, total in self.table_totals.items():
            report["tables"][tbl] = {}
            md_lines.append(f"## Table: `{tbl}` ({total} rows)")
            md_lines.append("")
            md_lines.append("| Field | Present | Not Applicable | Genuinely Missing | **Possible Parser Miss** | Action Needed |")
            md_lines.append("| :--- | :---: | :---: | :---: | :---: | :--- |")

            parser_miss_samples = []
            for field, st in self.stats[tbl].items():
                applicable = max(total - st["not_applicable"], 1)
                miss_pct = round((st["possible_parser_miss"] / applicable) * 100, 1)
                action = "FIX PARSER" if st["possible_parser_miss"] > 0 else ("OK" if st["present"] > 0 else "Check URL")
                report["tables"][tbl][field] = {
                    **st,
                    "total_rows": total,
                    "parser_miss_pct": miss_pct,
                }
                md_lines.append(
                    f"| `{field}` | {st['present']} | {st['not_applicable']} | "
                    f"{st['genuinely_missing']} | **{st['possible_parser_miss']} ({miss_pct}%)** | {action} |"
                )
                for sample in st["sample_misses"]:
                    if sample["verdict"] == "possible_parser_miss":
                        parser_miss_samples.append(sample)

            if parser_miss_samples:
                md_lines.append("\n### Suspected Parser Misses (Send These Snippets to Fix)")
                for s in parser_miss_samples[:15]:
                    md_lines.append(
                        f"- **`{s['field']}`** on `{s['record_id']}` ({s['source_url']})\n"
                        f"  - Checked: `{s['checked_keys_or_rules']}`\n"
                        f"  - Keyword `{s['matched_keyword']}` found in raw text: `...{s['raw_text_snippet']}...`"
                    )
            md_lines.append("")

        DIAG_REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        DIAG_SUMMARY_MD.write_text("\n".join(md_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Storage: Checkpoints, JSONL, SQLite, CSV
# ---------------------------------------------------------------------------

def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_completed_page": 0, "total_pages_detected": None, "scraped_urls": []}


def save_state(state: dict[str, Any]) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def init_sqlite(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wasalt_properties (
            record_id TEXT PRIMARY KEY,
            ref_no TEXT,
            source_url TEXT UNIQUE NOT NULL,
            search_page_url TEXT,
            listing_purpose TEXT,
            property_type TEXT,
            property_usage TEXT,
            title TEXT,
            country TEXT,
            city TEXT,
            region_zone TEXT,
            district TEXT,
            price_sar REAL,
            area_sqm REAL,
            land_area_sqm REAL,
            bedrooms INTEGER,
            bathrooms INTEGER,
            living_rooms INTEGER,
            guest_rooms INTEGER,
            furnishing_status TEXT,
            property_age TEXT,
            facade TEXT,
            street_width_m REAL,
            street_name TEXT,
            plan_number TEXT,
            land_number TEXT,
            utilities_json TEXT,
            broker_agency TEXT,
            fal_license_no TEXT,
            rega_ad_license_no TEXT,
            rega_status TEXT,
            rega_issue_date TEXT,
            rega_expiry_date TEXT,
            ad_source TEXT,
            views_last_7_days INTEGER,
            last_updated TEXT,
            description TEXT,
            extra_attributes_json TEXT,
            missing_fields_json TEXT,
            raw_source_text TEXT,
            scraped_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wasalt_projects (
            project_id TEXT PRIMARY KEY,
            ref_no TEXT,
            source_url TEXT UNIQUE NOT NULL,
            search_page_url TEXT,
            project_name TEXT,
            developer_name TEXT,
            country TEXT,
            city TEXT,
            region_zone TEXT,
            district TEXT,
            completion_status TEXT,
            price_min_sar REAL,
            price_max_sar REAL,
            area_min_sqm REAL,
            area_max_sqm REAL,
            total_units INTEGER,
            available_units INTEGER,
            residency_type TEXT,
            electricity TEXT,
            water TEXT,
            views_last_7_days INTEGER,
            last_updated TEXT,
            about_project TEXT,
            extra_attributes_json TEXT,
            missing_fields_json TEXT,
            raw_source_text TEXT,
            scraped_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wasalt_project_units (
            unit_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            unit_ref_no TEXT,
            unit_no TEXT,
            status TEXT,
            purpose TEXT,
            title TEXT,
            property_type TEXT,
            price_sar REAL,
            area_sqm REAL,
            bedrooms INTEGER,
            bathrooms INTEGER,
            highlights TEXT,
            source_url TEXT NOT NULL,
            missing_fields_json TEXT,
            raw_source_text TEXT,
            scraped_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES wasalt_projects(project_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS wasalt_auctions (
            asset_id TEXT PRIMARY KEY,
            auction_group_id TEXT,
            auction_group_name TEXT,
            auction_group_url TEXT,
            asset_number INTEGER,
            source_url TEXT UNIQUE NOT NULL,
            search_page_url TEXT,
            title TEXT,
            asset_category TEXT,
            city TEXT,
            district TEXT,
            status TEXT,
            area_sqm REAL,
            deposit_amount_sar REAL,
            starting_bid_sar REAL,
            highest_bid_sar REAL,
            bid_increment_sar REAL,
            total_bidders INTEGER,
            bids_count INTEGER,
            followers_count INTEGER,
            starts_at TEXT,
            ends_at TEXT,
            sales_agent_name TEXT,
            sales_agent_fal_license TEXT,
            extra_attributes_json TEXT,
            missing_fields_json TEXT,
            raw_source_text TEXT,
            scraped_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_props_city_price ON wasalt_properties(city, property_type, price_sar);
        CREATE INDEX IF NOT EXISTS idx_units_project ON wasalt_project_units(project_id, price_sar);
        CREATE INDEX IF NOT EXISTS idx_auctions_status ON wasalt_auctions(status, city, deposit_amount_sar);
        """
    )

    # Auto-migrate older SQLite DBs if new columns were added
    for tbl in TABLE_FILES.keys():
        existing_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
        for col in ("missing_fields_json", "raw_source_text"):
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT")

    conn.commit()
    return conn


def upsert_sqlite_rows(conn: sqlite3.Connection, table_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table_name} ({col_list}) VALUES ({placeholders})"
    values = [tuple(r.get(c) for c in cols) for r in rows]
    conn.executemany(sql, values)
    conn.commit()


def export_sqlite_to_csvs(conn: sqlite3.Connection) -> None:
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    for table in TABLE_FILES.keys():
        cur = conn.execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        csv_path = CSV_DIR / f"{table}.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            writer.writerows(rows)
