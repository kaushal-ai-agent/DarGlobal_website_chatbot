"""Master CLI orchestrator for DarGlobal & Wasalt unified data pipeline.

Modes:
  --mode test   : Strict caps (5-10 samples per table, 2-3 md files per type), executes verification loop.
  --mode full   : Comprehensive multi-partition crawl across all 5 hostnames.

Sources:
  --source all        : Crawls DarGlobal, Wasalt, and Wasalt Auctions.
  --source darglobal  : Crawls DarGlobal projects, guides, insights, press.
  --source wasalt     : Crawls Wasalt listings, projects, blogs, guides.
  --source auctions   : Crawls Wasalt auction groups, asset lots, guides.

Options:
  --reset-state       : Clears all checkpoints, databases, JSONL, and Markdown files to start 100% fresh.
  --limit <N>         : Custom item limit per category.
  --headed            : Run browser in visible mode (default is headless).
"""

import argparse
import asyncio
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = Path(__file__).resolve().parents[1]
for p in [str(PROJECT_ROOT), str(BACKEND_SRC)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from backend.src.scraper.config import (
    DATA_DIR,
    DB_PATH,
    DB_DIR,
    CHECKPOINT_DIR,
    STATE_FILE,
    FAILED_URLS_FILE,
    COMPLETENESS_REPORT_FILE,
    RAW_JSONL_DIR,
    MANIFESTS_DIR,
    UNSTRUCTURED_DIR,
    DG_UNSTRUCTURED_DIR,
    WS_UNSTRUCTURED_DIR,
)
from backend.src.scraper.darglobal_scraper import DarGlobalScraper
from backend.src.scraper.wasalt_scraper import WasaltScraper
from backend.src.scraper.wasalt_auction_scraper import WasaltAuctionScraper
from backend.src.scraper.storage import CheckpointManager, SQLiteDatabaseBuilder
from backend.src.scraper.validate_data import DatasetValidator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("scraper.pipeline")


async def run_pipeline(
    mode: str = "test",
    source: str = "all",
    limit: Optional[int] = None,
    reset_state: bool = False,
    headless: bool = True,
) -> bool:
    """Executes the complete scraping, database loading, and validation pipeline with performance timing."""
    t_start = time.perf_counter()
    stage_durations: Dict[str, float] = {}

    logger.info("==================================================================")
    logger.info("  STARTING UNIFIED PIPELINE (mode=%s, source=%s, limit=%s)", mode, source, limit)
    logger.info("==================================================================")

    # 1. Reset state if requested (Thorough clean slate)
    if reset_state:
        logger.warning("--reset-state supplied. Thoroughly wiping all checkpoints, databases, raw JSONL, and Markdown files...")
        if STATE_FILE.exists():
            STATE_FILE.unlink(missing_ok=True)
        if FAILED_URLS_FILE.exists():
            FAILED_URLS_FILE.unlink(missing_ok=True)
        if COMPLETENESS_REPORT_FILE.exists():
            COMPLETENESS_REPORT_FILE.unlink(missing_ok=True)
        if DB_PATH.exists():
            DB_PATH.unlink(missing_ok=True)
        for wal in DB_DIR.glob("real_estate.db*"):
            wal.unlink(missing_ok=True)
        for p in RAW_JSONL_DIR.glob("*.jsonl"):
            p.unlink(missing_ok=True)
        for p in MANIFESTS_DIR.glob("*.jsonl"):
            p.unlink(missing_ok=True)
        if UNSTRUCTURED_DIR.exists():
            for p in UNSTRUCTURED_DIR.rglob("*.md"):
                p.unlink(missing_ok=True)

    # Ensure all target directories exist
    for d in [
        RAW_JSONL_DIR,
        DB_DIR,
        MANIFESTS_DIR,
        CHECKPOINT_DIR,
        DG_UNSTRUCTURED_DIR / "projects",
        DG_UNSTRUCTURED_DIR / "insights",
        DG_UNSTRUCTURED_DIR / "press",
        WS_UNSTRUCTURED_DIR / "blog_guides",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    # 2. Initialize Checkpoint Manager & Database Builder
    t_step = time.perf_counter()
    checkpoint = CheckpointManager()
    db_builder = SQLiteDatabaseBuilder()

    # 3. Always Seed Residency Rules First (satisfies foreign keys)
    logger.info("Initializing schema and seeding residency rules...")
    db_builder.initialize_schema()
    db_builder.seed_residency_rules()
    stage_durations["Setup & Schema Initialization"] = time.perf_counter() - t_step

    # Default limits for test mode
    eff_limit = limit if limit is not None else (5 if mode == "test" else None)

    # 4. Execute Scrapers
    sources_to_run = ["darglobal", "wasalt", "auctions"] if source == "all" else [source]

    # A. DarGlobal
    if "darglobal" in sources_to_run:
        t_step = time.perf_counter()
        logger.info(">>> STEP: Scraping DarGlobal ecosystem...")
        dg_scraper = DarGlobalScraper(checkpoint=checkpoint, headless=headless)
        dg_projs, dg_docs = await dg_scraper.run(mode=mode, limit=eff_limit)
        dur = time.perf_counter() - t_step
        stage_durations[f"DarGlobal ({dg_projs} projs, {dg_docs} docs)"] = dur
        logger.info(">>> DarGlobal done in %.2fs: %d projects, %d docs.", dur, dg_projs, dg_docs)

    # B. Wasalt
    if "wasalt" in sources_to_run:
        t_step = time.perf_counter()
        logger.info(">>> STEP: Scraping Wasalt portal (Listings & Projects)...")
        ws_scraper = WasaltScraper(checkpoint=checkpoint, headless=headless)
        ws_listings, ws_projs, ws_docs = await ws_scraper.run(mode=mode, limit=eff_limit)
        dur = time.perf_counter() - t_step
        stage_durations[f"Wasalt ({ws_listings} listings, {ws_projs} projs, {ws_docs} docs)"] = dur
        logger.info(">>> Wasalt done in %.2fs: %d listings, %d projects, %d docs.", dur, ws_listings, ws_projs, ws_docs)

    # C. Wasalt Auctions
    if "auctions" in sources_to_run:
        t_step = time.perf_counter()
        logger.info(">>> STEP: Scraping Wasalt Auctions (Infath & Private)...")
        auc_scraper = WasaltAuctionScraper(checkpoint=checkpoint, headless=headless)
        auc_groups, auc_assets, auc_docs = await auc_scraper.run(mode=mode, limit=eff_limit)
        dur = time.perf_counter() - t_step
        stage_durations[f"Wasalt Auctions ({auc_groups} groups, {auc_assets} assets)"] = dur
        logger.info(">>> Wasalt Auctions done in %.2fs: %d groups, %d assets, %d docs.", dur, auc_groups, auc_assets, auc_docs)

    # 5. Load all JSONL sinks into SQLite
    t_step = time.perf_counter()
    logger.info(">>> STEP: Loading JSONL sinks into SQLite database: %s ...", DB_PATH)
    counts = db_builder.load_jsonl_to_sqlite()
    dur = time.perf_counter() - t_step
    stage_durations["SQLite Batch Ingestion & Indexing"] = dur
    logger.info("Loaded records per table in %.2fs: %s", dur, counts)

    # 6. Execute Validation Loop
    t_step = time.perf_counter()
    logger.info(">>> STEP: Running relational & content validation audit...")
    validator = DatasetValidator(db_path=DB_PATH)
    is_valid = validator.validate_all()
    validator.print_samples()
    stage_durations["Integrity Validation & QA Audit"] = time.perf_counter() - t_step

    t_total = time.perf_counter() - t_start

    # 7. Print Performance & Profiling Summary Table
    print("\n" + "=" * 80)
    print("                 PIPELINE PERFORMANCE & EXECUTION PROFILE")
    print("=" * 80)
    print(f"{'Stage':<52} | {'Duration (s)':<12} | {'Status'}")
    print("-" * 80)
    for stage_name, duration in stage_durations.items():
        print(f"{stage_name:<52} | {duration:>10.2f}s | PASS")
    print("-" * 80)
    print(f"{'TOTAL PIPELINE EXECUTION TIME':<52} | {t_total:>10.2f}s | {'SUCCESS' if is_valid else 'FAIL'}")
    print("=" * 80 + "\n")

    if is_valid:
        if mode == "test":
            print("*" * 60)
            print("  Test Mode Validated. Ready for Full Run.")
            print("*" * 60 + "\n")
        else:
            print("*" * 60)
            print("  FULL PRODUCTION PIPELINE COMPLETED SUCCESSFULLY.")
            print("*" * 60 + "\n")
        return True
    else:
        logger.error("Dataset validation failed! Inspect completeness_report.json for details.")
        return False


def main():
    parser = argparse.ArgumentParser(description="DarGlobal & Wasalt Real Estate Scraper Pipeline")
    parser.add_argument("--mode", choices=["test", "full"], default="test", help="Execution mode (test or full)")
    parser.add_argument("--source", choices=["all", "darglobal", "wasalt", "auctions"], default="all", help="Target source")
    parser.add_argument("--limit", type=int, default=None, help="Per-category sample cap (default: 5 in test mode)")
    parser.add_argument("--reset-state", action="store_true", help="Clear all state, database, and docs before running")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible mode (default is headless)")

    args = parser.parse_args()
    success = asyncio.run(
        run_pipeline(
            mode=args.mode,
            source=args.source,
            limit=args.limit,
            reset_state=args.reset_state,
            headless=not args.headed,
        )
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
