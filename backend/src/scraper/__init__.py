"""
DarGlobal and Wasalt Real Estate Scraping & Ingestion Engine (`backend.src.scraper`).

Modules:
- `darglobal_home_scraper`: Deep crawler for DarGlobal homepage, navigation drawer, and footer links.
- `darglobal_collection_scraper`: Granular crawler for DarGlobal property collections and regional tabs.
- `wasalt_home_scraper`: Deep crawler for Wasalt portal homepage, quick filters, and interactive UI graph.
- `wasalt_sale_scraper`: Live multi-table crawler for Wasalt residential sale listings.
- `wasalt_parsers`: Next.js state tree, Schema.org JSON-LD, and HTML extraction parsers.
- `wasalt_utils`: Database initialization, upserts, CSV export, and DiagnosticTracker telemetry.
- `wasalt_raw_store` (alias `raw_store`): Zlib-compressed SQLite raw cache manager.
- `wasalt_raw_fetcher`: Stealth anti-detection raw crawler (Patchright + curl_cffi).
- `wasalt_offline_parser`: High-speed multi-core CPU offline parser for the raw cache.
"""

from backend.src.scraper.wasalt_raw_store import (
    DATA_ROOT,
    DEFAULT_RAW_DB,
    RawCacheDB,
    detect_block_or_error,
    log_error_context,
    quick_inspect_search_html,
    setup_logger,
)

__all__ = [
    "DATA_ROOT",
    "DEFAULT_RAW_DB",
    "RawCacheDB",
    "detect_block_or_error",
    "log_error_context",
    "quick_inspect_search_html",
    "setup_logger",
]
