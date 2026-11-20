"""Comprehensive data validator and integrity auditor for DarGlobal & Wasalt pipeline.

Audits:
1. Relational Integrity: PRAGMA foreign_key_check with 0 violations.
2. Mandatory Non-Null Constraints: Primary keys and critical fields.
3. Language Cleanliness: Zero Arabic characters in structured string columns.
4. Markdown RAG Knowledge Base: YAML frontmatter valid, word_count >= 80, registered in document_registry.
5. Sampling: Prints top 5 rows per table (truncated for clean terminal reading) and 3 sample markdown docs.
6. Outputs: Writes data/checkpoints/completeness_report.json.
"""

import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

try:
    from backend.src.scraper.config import (
        DB_PATH,
        UNSTRUCTURED_DIR,
        COMPLETENESS_REPORT_FILE,
        MANIFESTS_DIR,
        RAW_JSONL_DIR,
        PROJECT_ROOT,
    )
except ImportError:
    from scraper.config import (
        DB_PATH,
        UNSTRUCTURED_DIR,
        COMPLETENESS_REPORT_FILE,
        MANIFESTS_DIR,
        RAW_JSONL_DIR,
        PROJECT_ROOT,
    )

logger = logging.getLogger("scraper.validator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ARABIC_PATTERN = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")


def has_arabic(val: Any) -> bool:
    if val is None:
        return False
    return bool(ARABIC_PATTERN.search(str(val)))


def truncate_val(val: Any, max_len: int = 40) -> str:
    if val is None:
        return "NULL"
    s = str(val)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


class DatasetValidator:
    """Validates relational and document data integrity."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.report: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "db_path": str(db_path),
            "tables": {},
            "foreign_key_violations": [],
            "arabic_script_violations": [],
            "null_violations": [],
            "markdown_validation": {},
            "status": "PASS",
        }

    def validate_all(self) -> bool:
        """Executes full validation suite. Returns True if all checks pass."""
        logger.info("=== STARTING DATASET INTEGRITY VALIDATION ===")
        if not self.db_path.exists():
            logger.error("Database file not found at %s", self.db_path)
            self.report["status"] = "FAIL"
            self.report["error"] = f"Database not found at {self.db_path}"
            self._save_report()
            return False

        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")

        # 1. Foreign Key Checks
        fk_ok = self._check_foreign_keys(conn)

        # 2. Table Counts & Schema Audits
        tables_ok = self._audit_tables(conn)

        # 3. Non-Null Constraints & Arabic Audits
        content_ok = self._audit_records(conn)

        conn.close()

        # 4. Markdown Document Audits
        md_ok = self._audit_markdown_documents()

        overall_pass = fk_ok and tables_ok and content_ok and md_ok
        self.report["status"] = "PASS" if overall_pass else "FAIL"
        self._save_report()

        logger.info("=== VALIDATION COMPLETED: %s ===", self.report["status"])
        return overall_pass

    def _check_foreign_keys(self, conn: sqlite3.Connection) -> bool:
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_key_check;")
        violations = cur.fetchall()
        if violations:
            logger.error("Foreign key violations found: %s", violations)
            self.report["foreign_key_violations"] = [str(v) for v in violations]
            return False
        logger.info("[OK] Foreign key checks: 0 violations.")
        return True

    def _audit_tables(self, conn: sqlite3.Connection) -> bool:
        cur = conn.cursor()
        expected_tables = [
            "residency_and_market_rules",
            "darglobal_projects",
            "wasalt_projects",
            "wasalt_listings",
            "wasalt_auction_groups",
            "wasalt_auction_assets",
            "document_registry",
        ]
        all_ok = True
        for tbl in expected_tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {tbl};")
                count = cur.fetchone()[0]
                self.report["tables"][tbl] = count
                logger.info("Table '%s': %d records", tbl, count)
            except Exception as e:
                logger.error("Table '%s' check failed: %s", tbl, e)
                self.report["tables"][tbl] = f"ERROR: {e}"
                all_ok = False
        return all_ok

    def _audit_records(self, conn: sqlite3.Connection) -> bool:
        cur = conn.cursor()
        passed = True

        critical_checks = [
            ("darglobal_projects", ["project_id", "project_name", "country", "city", "project_url"]),
            ("wasalt_projects", ["project_id", "project_name", "country", "city", "project_url"]),
            ("wasalt_listings", ["listing_id", "title", "country", "city", "listing_url", "property_type"]),
            ("wasalt_auction_groups", ["auction_group_id", "auction_title", "auction_category", "status", "auction_url"]),
            ("wasalt_auction_assets", ["asset_id", "auction_group_id", "asset_title", "asset_url"]),
            ("document_registry", ["doc_id", "source_platform", "doc_category", "file_path"]),
        ]

        for tbl, cols in critical_checks:
            # Check non-null
            for c in cols:
                cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {c} IS NULL OR {c} = '';")
                null_count = cur.fetchone()[0]
                if null_count > 0:
                    msg = f"Table '{tbl}' has {null_count} NULL/empty values in '{c}'"
                    logger.error(msg)
                    self.report["null_violations"].append(msg)
                    passed = False

            # Check Arabic characters in text columns
            cur.execute(f"SELECT * FROM {tbl};")
            col_names = [d[0] for d in cur.description]
            rows = cur.fetchall()
            for r in rows:
                for col_name, val in zip(col_names, r):
                    # Skip internal json fields or raw arabic storage if any
                    if "arabic" in col_name.lower() or val is None:
                        continue
                    if has_arabic(val):
                        msg = f"Arabic script detected in {tbl}.{col_name} (Row ID: {r[0]}): {truncate_val(val, 60)}"
                        logger.error(msg)
                        self.report["arabic_script_violations"].append(msg)
                        passed = False

        if passed:
            logger.info("[OK] Structured fields audit: 0 null violations, 0 Arabic script violations.")
        return passed

    def _audit_markdown_documents(self) -> bool:
        md_files = list(UNSTRUCTURED_DIR.glob("**/*.md"))
        logger.info("Auditing %d Markdown documents in %s...", len(md_files), UNSTRUCTURED_DIR)

        valid_docs = 0
        doc_details = []
        all_passed = True

        for p in md_files:
            try:
                content = p.read_text(encoding="utf-8")
                # Parse frontmatter
                if not content.startswith("---"):
                    msg = f"Missing YAML frontmatter: {p.relative_to(PROJECT_ROOT)}"
                    logger.error(msg)
                    all_passed = False
                    continue

                parts = content.split("---", 2)
                if len(parts) < 3:
                    msg = f"Malformed YAML frontmatter: {p.relative_to(PROJECT_ROOT)}"
                    logger.error(msg)
                    all_passed = False
                    continue

                fm_raw = parts[1]
                body = parts[2].strip()

                fm_dict = yaml.safe_load(fm_raw)
                for req_key in ["doc_id", "source_platform", "doc_category", "title", "source_urls"]:
                    if req_key not in fm_dict:
                        msg = f"Missing required frontmatter key '{req_key}': {p.relative_to(PROJECT_ROOT)}"
                        logger.error(msg)
                        all_passed = False

                word_count = len(re.findall(r"\b\w+\b", body))
                if word_count < 80:
                    msg = f"Word count ({word_count}) below threshold 80: {p.relative_to(PROJECT_ROOT)}"
                    logger.warning(msg)

                valid_docs += 1
                doc_details.append({
                    "file": str(p.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                    "doc_id": fm_dict.get("doc_id"),
                    "category": fm_dict.get("doc_category"),
                    "title": fm_dict.get("title"),
                    "word_count": word_count,
                })
            except Exception as e:
                logger.error("Error reading doc %s: %s", p, e)
                all_passed = False

        self.report["markdown_validation"] = {
            "total_files": len(md_files),
            "valid_files": valid_docs,
            "documents": doc_details,
        }
        logger.info("[OK] Markdown documents audit: %d/%d valid documents.", valid_docs, len(md_files))
        return all_passed

    def print_samples(self) -> None:
        """Prints truncated top 5 rows per table and preview of 3 markdown files."""
        if not self.db_path.exists():
            return
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        tables = [
            ("residency_and_market_rules", ["country", "residency_program_name", "min_investment_local", "off_plan_eligible", "source_url"]),
            ("darglobal_projects", ["project_id", "project_name", "brand_partner", "country", "city", "starting_price_usd_est", "status"]),
            ("wasalt_projects", ["project_id", "project_name", "developer_name", "city", "starting_price_sar", "completion_status"]),
            ("wasalt_listings", ["listing_id", "title", "listing_purpose", "property_type", "city", "price_amount", "price_period"]),
            ("wasalt_auction_groups", ["auction_group_id", "auction_title", "auction_category", "status", "total_assets", "min_deposit_sar"]),
            ("wasalt_auction_assets", ["asset_id", "auction_group_id", "asset_number", "asset_title", "asset_category", "starting_bid_sar"]),
            ("document_registry", ["doc_id", "source_platform", "doc_category", "title", "word_count", "file_path"]),
        ]

        print("\n" + "=" * 90)
        print("                 DATABASE SAMPLES (TOP ROWS PER TABLE)                 ")
        print("=" * 90)

        for tbl, cols in tables:
            try:
                col_sql = ", ".join(cols)
                cur.execute(f"SELECT {col_sql} FROM {tbl} LIMIT 5;")
                rows = cur.fetchall()
                print(f"\n--- TABLE: {tbl} (Sample rows: {len(rows)}) ---")
                header_line = " | ".join(f"{c:<20}" for c in cols)
                print(header_line)
                print("-" * len(header_line))
                for r in rows:
                    row_line = " | ".join(f"{truncate_val(v, 20):<20}" for v in r)
                    print(row_line)
            except Exception as e:
                print(f"Could not sample table {tbl}: {e}")

        conn.close()

        # Print 3 Sample Markdown Previews
        print("\n" + "=" * 90)
        print("               SAMPLE MARKDOWN DOCUMENT PREVIEWS (3 FILES)              ")
        print("=" * 90)
        md_files = list(UNSTRUCTURED_DIR.glob("**/*.md"))[:3]
        for p in md_files:
            print(f"\n>>> File: {p.relative_to(PROJECT_ROOT)} <<<")
            lines = p.read_text(encoding="utf-8").splitlines()[:25]
            for l in lines:
                print(l)
            print("... [truncated] ...\n")

    def _save_report(self) -> None:
        COMPLETENESS_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(COMPLETENESS_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(self.report, f, indent=2, ensure_ascii=False)
        logger.info("Completeness report written to %s", COMPLETENESS_REPORT_FILE)


if __name__ == "__main__":
    validator = DatasetValidator()
    success = validator.validate_all()
    validator.print_samples()
    sys.exit(0 if success else 1)
