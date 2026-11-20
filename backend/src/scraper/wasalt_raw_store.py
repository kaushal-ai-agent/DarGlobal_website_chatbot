"""
Wasalt Bronze Raw Cache Store (`wasalt_raw_store.py`)

What it does:
Provides a crash-safe, zlib-compressed SQLite WAL raw storage engine (`wasalt_sale_raw.db`) for caching fetched HTML search pages. Decouples network fetching from parsing, enforces Cloudflare challenge gatekeeping, maintains rotating debug logs, and supports O(1) page-level skip lookups and O(B) streaming keyset pagination.

Important Modules & Functions:
- `sqlite3` with WAL mode: High-concurrency local database engine with 64MB cache and memory temp store.
- `zlib`: Lossless compression engine reducing raw HTML storage footprint by ~85-90%.
- `setup_logger(name, verbose)`: Initializes rotating file loggers (10MB x 5 backups) and clean console streams.
- `detect_block_or_error(html)`: Quality gatekeeper rejecting Cloudflare challenge pages, 5xx stubs, or empty responses before caching.
- `quick_inspect_search_html(html)`: Rapid O(N) regex/DOM scan to count listing cards and extract total pagination bounds.
- `RawCacheDB`: SQLite manager class managing `raw_search_pages`, `failed_search_pages`, and `crawl_metadata`.
- `RawCacheDB.iter_raw_pages(batch_size, limit)`: Keyset-paginated streaming generator yielding decompressed HTML batches for offline workers.

Core Logic (One-Liner):
Manages a zlib-compressed SQLite raw cache with Cloudflare block detection and streaming keyset pagination to completely decouple HTML retrieval from downstream parsing.
"""

import json
import logging
import re
import sqlite3
import sys
import traceback
import zlib
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

from bs4 import BeautifulSoup

# Resolve project root and ensure it is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_DIR = Path(__file__).resolve().parent
for p in [str(PROJECT_ROOT), str(SCRAPER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

DATA_ROOT = PROJECT_ROOT / "data" / "wasalt"
RAW_CACHE_DIR = DATA_ROOT / "raw_cache"
LOGS_DIR = DATA_ROOT / "logs"
PASS_A_OUT_DIR = DATA_ROOT / "pass_a"
DEFAULT_RAW_DB = RAW_CACHE_DIR / "wasalt_sale_raw.db"

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
# Structured Rotating Logger
# ---------------------------------------------------------------------------

def setup_logger(name: str, verbose: bool = False) -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler (clean, concise)
    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # Rotating file handler (full DEBUG + tracebacks, 10MB x 5 backups)
    log_file = LOGS_DIR / f"{name}.log"
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.info("Log file initialized -> %s", log_file)
    return logger


def log_error_context(
    logger: logging.Logger,
    stage: str,
    url: str,
    exc: Exception | None = None,
    **extra: Any,
) -> None:
    """Emit a single-line JSON error record + full traceback in file log for instant debugging."""
    payload = {
        "stage": stage,
        "url": url,
        "exc_type": type(exc).__name__ if exc else None,
        "exc_msg": str(exc) if exc else None,
        **extra,
    }
    logger.error("ERROR_CONTEXT %s", json.dumps(payload, ensure_ascii=False))
    if exc is not None:
        logger.debug("TRACEBACK for %s:\n%s", url, traceback.format_exc())


# ---------------------------------------------------------------------------
# Fetch-Time Quality Gatekeeper
# ---------------------------------------------------------------------------

def detect_block_or_error(html: str) -> str | None:
    """Reject Cloudflare interstitials, 5xx pages, or stripped stubs before caching."""
    if not html or len(html) < 1000:
        return f"short_html:{len(html or '')}_bytes"

    lower = html.lower()
    for sig in BLOCK_SIGNATURES:
        if sig in lower:
            return f"block_signature:{sig}"

    if "__next_data__" not in lower and "cardwrapper" not in lower and "listingcard" not in lower:
        # Check if H1 is a Cloudflare domain stub
        m_h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
        h1_txt = re.sub(r"<[^>]+>", "", m_h1.group(1)).strip().lower() if m_h1 else ""
        if h1_txt in ("wasalt.sa", "auction.wasalt.sa", "just a moment..."):
            return f"challenge_h1:{h1_txt}"
        return "missing_next_data_and_cards"

    return None


def quick_inspect_search_html(html: str) -> tuple[int, int | None, bool]:
    """Fast O(N) scan of fetched search HTML for (cards_detected, total_pages, has_next_data)."""
    has_next = 'id="__NEXT_DATA__"' in html
    soup = BeautifulSoup(html, "lxml")

    total_pages = None
    pag_ul = soup.select_one("ul[class*='pagination' i]")
    if pag_ul:
        nums = [int(n) for n in re.findall(r"\b\d+\b", pag_ul.get_text(" "))]
        if nums:
            total_pages = max(nums)

    cards = soup.select(
        "div[class*='cardWrapper' i], div[class*='featuredWrapper' i], article[class*='listingCard' i]"
    )
    return len(cards), total_pages, has_next


# ---------------------------------------------------------------------------
# Compressed SQLite WAL Raw Cache (Bronze Layer)
# ---------------------------------------------------------------------------

class RawCacheDB:
    """
    Crash-safe, zlib-compressed local store for raw search HTML.
    - O(1) lookup of already-fetched pages via PRIMARY KEY + in-memory set[int].
    - O(B) RAM during both writes (atomic batch commits) and reads (streaming generator).
    """

    def __init__(self, db_path: Path = DEFAULT_RAW_DB) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, timeout=30.0)
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA synchronous = NORMAL;")
        self.conn.execute("PRAGMA cache_size = -64000;")  # 64 MB page cache
        self.conn.execute("PRAGMA temp_store = MEMORY;")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS raw_search_pages (
                page_num INTEGER PRIMARY KEY,
                url TEXT UNIQUE NOT NULL,
                http_status INTEGER NOT NULL,
                cards_detected INTEGER NOT NULL,
                total_pages_detected INTEGER,
                has_next_data INTEGER NOT NULL,
                raw_html_gz BLOB NOT NULL,
                raw_size_bytes INTEGER NOT NULL,
                compressed_size_bytes INTEGER NOT NULL,
                fetch_method TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS failed_search_pages (
                page_num INTEGER PRIMARY KEY,
                url TEXT NOT NULL,
                http_status INTEGER,
                error_type TEXT,
                error_message TEXT,
                attempts INTEGER DEFAULT 1,
                failed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS crawl_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO crawl_metadata (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM crawl_metadata WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def get_cached_page_nums(self) -> set[int]:
        rows = self.conn.execute("SELECT page_num FROM raw_search_pages").fetchall()
        return {r[0] for r in rows}

    def get_failed_page_nums(self) -> list[int]:
        rows = self.conn.execute("SELECT page_num FROM failed_search_pages ORDER BY page_num ASC").fetchall()
        return [r[0] for r in rows]

    def upsert_pages_batch(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        sql = """
            INSERT OR REPLACE INTO raw_search_pages (
                page_num, url, http_status, cards_detected, total_pages_detected,
                has_next_data, raw_html_gz, raw_size_bytes, compressed_size_bytes,
                fetch_method, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        values = [
            (
                r["page_num"],
                r["url"],
                r["http_status"],
                r["cards_detected"],
                r["total_pages_detected"],
                1 if r["has_next_data"] else 0,
                r["raw_html_gz"],
                r["raw_size_bytes"],
                r["compressed_size_bytes"],
                r["fetch_method"],
                r["fetched_at"],
            )
            for r in rows
        ]
        page_nums = [(r["page_num"],) for r in rows]
        with self.conn:
            self.conn.executemany(sql, values)
            # Clear from failed table if previously failed and now succeeded
            self.conn.executemany("DELETE FROM failed_search_pages WHERE page_num = ?", page_nums)

    def record_failure(
        self,
        page_num: int,
        url: str,
        http_status: int | None,
        error_type: str,
        error_message: str,
        attempts: int,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO failed_search_pages
                (page_num, url, http_status, error_type, error_message, attempts, failed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_num,
                    url,
                    http_status,
                    error_type,
                    error_message[:1000],
                    attempts,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def get_stats(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(cards_detected), 0),
                   COALESCE(SUM(raw_size_bytes), 0), COALESCE(SUM(compressed_size_bytes), 0),
                   MAX(total_pages_detected)
            FROM raw_search_pages
            """
        ).fetchone()
        failed_cnt = self.conn.execute("SELECT COUNT(*) FROM failed_search_pages").fetchone()[0]
        raw_mb = round(row[2] / (1024 * 1024), 2)
        comp_mb = round(row[3] / (1024 * 1024), 2)
        return {
            "cached_pages": row[0],
            "failed_pages": failed_cnt,
            "total_cards_detected": row[1],
            "max_total_pages_seen": row[4],
            "raw_html_mb": raw_mb,
            "compressed_mb": comp_mb,
            "compression_ratio_pct": round((1 - (comp_mb / raw_mb)) * 100, 1) if raw_mb > 0 else 0.0,
        }

    def iter_raw_pages(self, batch_size: int = 50, limit: int | None = None) -> Iterator[list[tuple[int, str, str]]]:
        """
        Stream decompressed (page_num, url, html) in O(B) memory batches for offline parsing.
        Uses keyset pagination (WHERE page_num > last_page) -> O(N) total scan with zero OFFSET overhead.
        """
        last_page = -1
        yielded = 0
        while True:
            fetch_n = batch_size if limit is None else min(batch_size, limit - yielded)
            if fetch_n <= 0:
                break
            rows = self.conn.execute(
                """
                SELECT page_num, url, raw_html_gz
                FROM raw_search_pages
                WHERE page_num > ?
                ORDER BY page_num ASC
                LIMIT ?
                """,
                (last_page, fetch_n),
            ).fetchall()
            if not rows:
                break
            batch = [(p, u, zlib.decompress(blob).decode("utf-8", errors="replace")) for p, u, blob in rows]
            last_page = rows[-1][0]
            yielded += len(batch)
            yield batch

    def get_single_page_html(self, page_num: int) -> tuple[str, str] | None:
        row = self.conn.execute(
            "SELECT url, raw_html_gz FROM raw_search_pages WHERE page_num = ?", (page_num,)
        ).fetchone()
        if not row:
            return None
        return row[0], zlib.decompress(row[1]).decode("utf-8", errors="replace")

    def close(self) -> None:
        self.conn.close()
