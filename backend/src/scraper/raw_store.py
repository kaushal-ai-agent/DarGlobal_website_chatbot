"""
Raw Store Module Alias (`raw_store.py`)

Provides seamless backward compatibility and alias re-exports for `wasalt_raw_store`.
"""

import sys
from pathlib import Path

# Resolve project root and scraper dir
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_DIR = Path(__file__).resolve().parent
for p in [str(PROJECT_ROOT), str(SCRAPER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from wasalt_raw_store import (
    DATA_ROOT,
    RAW_CACHE_DIR,
    LOGS_DIR,
    PASS_A_OUT_DIR,
    DEFAULT_RAW_DB,
    BLOCK_SIGNATURES,
    setup_logger,
    log_error_context,
    detect_block_or_error,
    quick_inspect_search_html,
    RawCacheDB,
)

__all__ = [
    "DATA_ROOT",
    "RAW_CACHE_DIR",
    "LOGS_DIR",
    "PASS_A_OUT_DIR",
    "DEFAULT_RAW_DB",
    "BLOCK_SIGNATURES",
    "setup_logger",
    "log_error_context",
    "detect_block_or_error",
    "quick_inspect_search_html",
    "RawCacheDB",
]
