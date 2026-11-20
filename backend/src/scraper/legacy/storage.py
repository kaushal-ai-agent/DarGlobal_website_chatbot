"""Storage manager: JSONL batch writers, atomic checkpoint tracker, Markdown document writer, and SQLite database builder."""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import yaml

from scraper.config import (
    PROJECT_ROOT,
    RAW_JSONL_DIR,
    DB_DIR,
    MANIFESTS_DIR,
    DB_PATH,
    STATE_FILE,
    FAILED_URLS_FILE,
    UNSTRUCTURED_DIR,
    DG_UNSTRUCTURED_DIR,
    WS_UNSTRUCTURED_DIR,
    BATCH_SIZE,
    RESIDENCY_SEEDS,
)
from scraper.schemas import (
    ResidencyRuleItem,
    DarGlobalProjectItem,
    WasaltProjectItem,
    WasaltListingItem,
    WasaltAuctionGroupItem,
    WasaltAuctionAssetItem,
    DocumentRegistryItem,
    MarkdownFrontmatter,
    get_utc_now,
)


class CheckpointManager:
    """Manages resumable cursors, completed partition states, and failed URLs atomically."""

    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = state_file
        self.state: Dict[str, Any] = self._load_or_initialize()

    def _load_or_initialize(self) -> Dict[str, Any]:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        initial_state = {
            "darglobal": {
                "completed_project_slugs": [],
                "completed_insight_slugs": [],
                "completed_press_slugs": [],
                "completed_static_docs": [],
            },
            "wasalt": {
                "listings_partitions": {},
                "seen_listing_ids": [],
                "completed_project_ids": [],
                "completed_auction_group_ids": [],
                "completed_blog_slugs": [],
                "completed_static_docs": [],
            },
            "updated_at": get_utc_now(),
        }
        self._atomic_save(initial_state)
        return initial_state

    def _atomic_save(self, state: Dict[str, Any]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = get_utc_now()
        temp_file = self.state_file.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        temp_file.replace(self.state_file)

    def save(self) -> None:
        self._atomic_save(self.state)

    def is_darglobal_project_done(self, slug: str) -> bool:
        return slug in self.state["darglobal"]["completed_project_slugs"]

    def mark_darglobal_project_done(self, slug: str) -> None:
        if slug not in self.state["darglobal"]["completed_project_slugs"]:
            self.state["darglobal"]["completed_project_slugs"].append(slug)
            self.save()

    def is_darglobal_static_done(self, doc_id: str) -> bool:
        return doc_id in self.state["darglobal"]["completed_static_docs"]

    def mark_darglobal_static_done(self, doc_id: str) -> None:
        if doc_id not in self.state["darglobal"]["completed_static_docs"]:
            self.state["darglobal"]["completed_static_docs"].append(doc_id)
            self.save()

    def is_listing_seen(self, listing_id: str) -> bool:
        return listing_id in self.state["wasalt"]["seen_listing_ids"]

    def mark_listing_seen(self, listing_id: str) -> None:
        if listing_id not in self.state["wasalt"]["seen_listing_ids"]:
            self.state["wasalt"]["seen_listing_ids"].append(listing_id)

    def get_partition_cursor(self, partition_key: str) -> Dict[str, Any]:
        return self.state["wasalt"]["listings_partitions"].get(partition_key, {"last_page": 0, "done": False, "count": 0})

    def update_partition_cursor(self, partition_key: str, last_page: int, done: bool, added_count: int = 0) -> None:
        cur = self.get_partition_cursor(partition_key)
        cur["last_page"] = last_page
        cur["done"] = done
        cur["count"] = cur.get("count", 0) + added_count
        self.state["wasalt"]["listings_partitions"][partition_key] = cur
        self.save()

    def is_auction_group_done(self, group_id: str) -> bool:
        return str(group_id) in self.state["wasalt"]["completed_auction_group_ids"]

    def is_wasalt_auction_group_done(self, group_id: str) -> bool:
        return self.is_auction_group_done(group_id)

    def mark_auction_group_done(self, group_id: str) -> None:
        if str(group_id) not in self.state["wasalt"]["completed_auction_group_ids"]:
            self.state["wasalt"]["completed_auction_group_ids"].append(str(group_id))
            self.save()

    def mark_wasalt_auction_group_done(self, group_id: str) -> None:
        self.mark_auction_group_done(group_id)

    def is_blog_slug_done(self, slug: str) -> bool:
        return slug in self.state["wasalt"]["completed_blog_slugs"]

    def mark_blog_slug_done(self, slug: str) -> None:
        if slug not in self.state["wasalt"]["completed_blog_slugs"]:
            self.state["wasalt"]["completed_blog_slugs"].append(slug)
            self.save()

    def is_wasalt_static_done(self, doc_id: str) -> bool:
        return doc_id in self.state["wasalt"]["completed_static_docs"]

    def mark_wasalt_static_done(self, doc_id: str) -> None:
        if doc_id not in self.state["wasalt"]["completed_static_docs"]:
            self.state["wasalt"]["completed_static_docs"].append(doc_id)
            self.save()

    def reset(self) -> None:
        """Completely clears checkpoint state and truncates failed URLs file."""
        if self.state_file.exists():
            self.state_file.unlink()
        if FAILED_URLS_FILE.exists():
            FAILED_URLS_FILE.unlink()
        self.state = self._load_or_initialize()

    def log_failed_url(self, url: str, status_code: Optional[int], error_msg: str) -> None:
        FAILED_URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "url": url,
            "status_code": status_code,
            "error": error_msg,
            "timestamp": get_utc_now(),
        }
        with open(FAILED_URLS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class JSONLBatchWriter:
    """O(B) memory streaming JSONL sink with automatic batch flushing."""

    def __init__(self, filename: str, batch_size: int = BATCH_SIZE):
        RAW_JSONL_DIR.mkdir(parents=True, exist_ok=True)
        MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
        if filename == "document_registry.jsonl":
            self.file_path = MANIFESTS_DIR / filename
        else:
            self.file_path = RAW_JSONL_DIR / filename
        self.batch_size = batch_size
        self.buffer: List[Dict[str, Any]] = []

    def write_record(self, record: Any) -> None:
        if hasattr(record, "model_dump"):
            self.buffer.append(record.model_dump())
        elif isinstance(record, dict):
            self.buffer.append(record)
        if len(self.buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        if not self.buffer:
            return
        with open(self.file_path, "a", encoding="utf-8") as f:
            for item in self.buffer:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        self.buffer.clear()


class MarkdownWriter:
    """Writes Markdown files with YAML frontmatter and registers them in document_registry.jsonl."""

    def __init__(self):
        self.registry_writer = JSONLBatchWriter("document_registry.jsonl", batch_size=5)

    def write_document(
        self,
        relative_path: Path,
        frontmatter: MarkdownFrontmatter,
        body_content: str,
    ) -> Path:
        full_path = PROJECT_ROOT / relative_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        clean_body = body_content.strip()
        # Word count calculation
        word_count = len(re.findall(r"\b\w+\b", clean_body))

        fm_dict = frontmatter.model_dump()
        yaml_header = yaml.dump(fm_dict, sort_keys=False, allow_unicode=True).strip()

        full_doc = f"---\n{yaml_header}\n---\n\n{clean_body}\n"
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(full_doc)

        # Register in manifest
        reg_item = DocumentRegistryItem(
            doc_id=frontmatter.doc_id,
            source_platform=frontmatter.source_platform,
            doc_category=frontmatter.doc_category,
            title=frontmatter.title,
            file_path=str(relative_path).replace("\\", "/"),
            linked_table=frontmatter.linked_table,
            linked_entity_id=frontmatter.linked_entity_id,
            country=frontmatter.country,
            city=frontmatter.city,
            word_count=word_count,
            source_urls=frontmatter.source_urls,
            last_scraped=frontmatter.last_scraped,
        )
        self.registry_writer.write_record(reg_item)
        self.registry_writer.flush()
        return full_path


class SQLiteDatabaseBuilder:
    """Initializes SQLite database with foreign keys, indexes, and loads JSONL datasets idempotently."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def initialize_schema(self) -> None:
        conn = self.get_connection()
        cur = conn.cursor()

        # 1. residency_and_market_rules
        cur.execute("""
        CREATE TABLE IF NOT EXISTS residency_and_market_rules (
            country TEXT PRIMARY KEY,
            residency_program_name TEXT,
            min_investment_local TEXT,
            min_investment_usd_approx REAL,
            off_plan_eligible INTEGER,
            foreign_freehold_allowed INTEGER DEFAULT 1,
            key_tax_benefits TEXT,
            special_notes TEXT,
            source_url TEXT NOT NULL,
            scraped_at TEXT NOT NULL
        );
        """)

        # 2. darglobal_projects
        cur.execute("""
        CREATE TABLE IF NOT EXISTS darglobal_projects (
            project_id TEXT PRIMARY KEY,
            source TEXT DEFAULT 'darglobal',
            project_name TEXT NOT NULL,
            parent_masterplan TEXT,
            brand_partner TEXT,
            asset_vertical TEXT DEFAULT 'residential',
            property_types_json TEXT,
            status TEXT DEFAULT 'under_development',
            country TEXT NOT NULL,
            city TEXT NOT NULL,
            district_or_location TEXT,
            unit_configurations_json TEXT,
            area_sqm_min REAL,
            area_sqm_max REAL,
            starting_price_amount REAL,
            starting_price_currency TEXT,
            starting_price_usd_est REAL,
            completion_date TEXT,
            freehold_for_foreigners INTEGER DEFAULT 1,
            residency_eligible INTEGER DEFAULT 1,
            residency_min_investment TEXT,
            tokenization_available INTEGER DEFAULT 0,
            amenities_json TEXT,
            brochure_url TEXT,
            project_url TEXT UNIQUE NOT NULL,
            short_summary TEXT,
            scraped_at TEXT NOT NULL,
            FOREIGN KEY (country) REFERENCES residency_and_market_rules(country)
        );
        """)

        # 3. wasalt_projects
        cur.execute("""
        CREATE TABLE IF NOT EXISTS wasalt_projects (
            project_id TEXT PRIMARY KEY,
            source TEXT DEFAULT 'wasalt_projects',
            project_name TEXT NOT NULL,
            developer_name TEXT,
            country TEXT NOT NULL DEFAULT 'Saudi Arabia',
            city TEXT NOT NULL,
            district TEXT,
            project_type TEXT NOT NULL,
            starting_price_sar REAL,
            area_range_sqm TEXT,
            available_units_count INTEGER,
            wafi_registered INTEGER,
            completion_status TEXT,
            amenities_json TEXT,
            description TEXT,
            project_url TEXT UNIQUE NOT NULL,
            scraped_at TEXT NOT NULL,
            FOREIGN KEY (country) REFERENCES residency_and_market_rules(country)
        );
        """)

        # 4. wasalt_listings
        cur.execute("""
        CREATE TABLE IF NOT EXISTS wasalt_listings (
            listing_id TEXT PRIMARY KEY,
            ref_no TEXT,
            source TEXT DEFAULT 'wasalt',
            wasalt_project_id TEXT,
            listing_purpose TEXT NOT NULL,
            property_usage TEXT DEFAULT 'residential',
            property_type TEXT NOT NULL,
            title TEXT NOT NULL,
            country TEXT NOT NULL DEFAULT 'Saudi Arabia',
            city TEXT NOT NULL,
            region_zone TEXT,
            district TEXT,
            street_name TEXT,
            plan_number TEXT,
            price_amount REAL,
            price_currency TEXT DEFAULT 'SAR',
            price_period TEXT DEFAULT 'total',
            monthly_rent_display REAL,
            built_up_area_sqm REAL,
            land_area_sqm REAL,
            bedrooms INTEGER,
            bathrooms INTEGER,
            living_rooms INTEGER,
            guest_rooms INTEGER,
            furnishing_status TEXT DEFAULT 'unknown',
            property_age_years TEXT,
            building_year INTEGER,
            facade_direction TEXT,
            street_width_m REAL,
            utilities_json TEXT,
            other_obligations TEXT,
            broker_agency_name TEXT,
            broker_agent_name TEXT,
            fal_license_no TEXT,
            rega_ad_license_no TEXT,
            rega_status TEXT DEFAULT 'unknown',
            rega_issue_date TEXT,
            rega_expiry_date TEXT,
            ad_source TEXT,
            views_last_7_days INTEGER,
            description TEXT,
            listing_url TEXT UNIQUE NOT NULL,
            last_updated_text TEXT,
            scraped_at TEXT NOT NULL,
            FOREIGN KEY (country) REFERENCES residency_and_market_rules(country),
            FOREIGN KEY (wasalt_project_id) REFERENCES wasalt_projects(project_id)
        );
        """)

        # 5. wasalt_auction_groups
        cur.execute("""
        CREATE TABLE IF NOT EXISTS wasalt_auction_groups (
            auction_group_id TEXT PRIMARY KEY,
            auction_title TEXT NOT NULL,
            auction_category TEXT NOT NULL,
            auction_type TEXT NOT NULL,
            city TEXT,
            status TEXT NOT NULL,
            total_assets INTEGER DEFAULT 0,
            min_deposit_sar REAL,
            starts_at TEXT,
            ends_at TEXT,
            sales_agent_name TEXT,
            sales_agent_fal_license TEXT,
            is_test_record INTEGER DEFAULT 0,
            group_description TEXT,
            auction_url TEXT UNIQUE NOT NULL,
            scraped_at TEXT NOT NULL
        );
        """)

        # 6. wasalt_auction_assets
        cur.execute("""
        CREATE TABLE IF NOT EXISTS wasalt_auction_assets (
            asset_id TEXT PRIMARY KEY,
            auction_group_id TEXT NOT NULL,
            asset_number INTEGER NOT NULL,
            asset_title TEXT NOT NULL,
            asset_category TEXT NOT NULL,
            city_or_location TEXT,
            asset_status TEXT NOT NULL,
            deposit_amount_sar REAL,
            starting_bid_sar REAL,
            highest_bid_sar REAL,
            total_bidders INTEGER,
            bids_count INTEGER,
            followers_count INTEGER,
            area_sqm REAL,
            asset_specs_json TEXT,
            asset_url TEXT NOT NULL,
            scraped_at TEXT NOT NULL,
            FOREIGN KEY (auction_group_id) REFERENCES wasalt_auction_groups(auction_group_id) ON DELETE CASCADE
        );
        """)

        # 7. document_registry
        cur.execute("""
        CREATE TABLE IF NOT EXISTS document_registry (
            doc_id TEXT PRIMARY KEY,
            source_platform TEXT NOT NULL,
            doc_category TEXT NOT NULL,
            title TEXT NOT NULL,
            file_path TEXT NOT NULL,
            linked_table TEXT,
            linked_entity_id TEXT,
            country TEXT DEFAULT 'Multi',
            city TEXT DEFAULT 'Multi',
            word_count INTEGER DEFAULT 0,
            source_urls_json TEXT,
            last_scraped TEXT NOT NULL
        );
        """)

        # Create Indexes for high-performance agent SQL queries
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wasalt_listings_city_purpose_type_price ON wasalt_listings(city, listing_purpose, property_type, price_amount);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wasalt_listings_rega ON wasalt_listings(rega_status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_darglobal_projects_country_city_brand ON darglobal_projects(country, city, brand_partner);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_auction_groups_status_cat_test ON wasalt_auction_groups(status, auction_category, is_test_record);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_auction_assets_group_status ON wasalt_auction_assets(auction_group_id, asset_status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_doc_registry_platform_cat_entity ON document_registry(source_platform, doc_category, linked_entity_id);")

        conn.commit()
        conn.close()

    def seed_residency_rules(self) -> None:
        """Seeds the residency rules table first so foreign keys are always satisfied."""
        writer = JSONLBatchWriter("residency_and_market_rules.jsonl")
        for s in RESIDENCY_SEEDS:
            item = ResidencyRuleItem(**s)
            writer.write_record(item)
        writer.flush()

    def load_jsonl_to_sqlite(self) -> Dict[str, int]:
        """Loads all JSONL files into SQLite tables using high-throughput executemany batches."""
        self.initialize_schema()
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute("PRAGMA synchronous = OFF;")
        cur.execute("PRAGMA journal_mode = MEMORY;")
        counts = {}

        def _stream_jsonl(path: Path):
            if not path.exists():
                return
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except Exception:
                            continue

        # 1. residency_and_market_rules
        p1 = RAW_JSONL_DIR / "residency_and_market_rules.jsonl"
        if p1.exists():
            batch = [
                (
                    r.get("country"), r.get("residency_program_name"), r.get("min_investment_local"),
                    r.get("min_investment_usd_approx"), r.get("off_plan_eligible"), r.get("foreign_freehold_allowed"),
                    r.get("key_tax_benefits"), r.get("special_notes"), r.get("source_url"), r.get("scraped_at")
                )
                for r in _stream_jsonl(p1)
            ]
            cur.executemany("""
            INSERT OR REPLACE INTO residency_and_market_rules (
                country, residency_program_name, min_investment_local, min_investment_usd_approx,
                off_plan_eligible, foreign_freehold_allowed, key_tax_benefits, special_notes,
                source_url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, batch)
            counts["residency_and_market_rules"] = len(batch)

        # 2. darglobal_projects
        p2 = RAW_JSONL_DIR / "darglobal_projects.jsonl"
        if p2.exists():
            batch = [
                (
                    r.get("project_id"), r.get("source", "darglobal"), r.get("project_name"), r.get("parent_masterplan"),
                    r.get("brand_partner"), r.get("asset_vertical", "residential"), json.dumps(r.get("property_types", [])),
                    r.get("status", "under_development"), r.get("country"), r.get("city"), r.get("district_or_location"),
                    json.dumps(r.get("unit_configurations", [])), r.get("area_sqm_min"), r.get("area_sqm_max"),
                    r.get("starting_price_amount"), r.get("starting_price_currency"), r.get("starting_price_usd_est"),
                    r.get("completion_date"), r.get("freehold_for_foreigners"), r.get("residency_eligible"),
                    r.get("residency_min_investment"), r.get("tokenization_available"), json.dumps(r.get("amenities", [])),
                    r.get("brochure_url"), r.get("project_url"), r.get("short_summary"), r.get("scraped_at")
                )
                for r in _stream_jsonl(p2)
            ]
            cur.executemany("""
            INSERT OR REPLACE INTO darglobal_projects (
                project_id, source, project_name, parent_masterplan, brand_partner, asset_vertical,
                property_types_json, status, country, city, district_or_location, unit_configurations_json,
                area_sqm_min, area_sqm_max, starting_price_amount, starting_price_currency,
                starting_price_usd_est, completion_date, freehold_for_foreigners, residency_eligible,
                residency_min_investment, tokenization_available, amenities_json, brochure_url,
                project_url, short_summary, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, batch)
            counts["darglobal_projects"] = len(batch)

        # 3. wasalt_projects
        p3 = RAW_JSONL_DIR / "wasalt_projects.jsonl"
        if p3.exists():
            batch = [
                (
                    r.get("project_id"), r.get("source", "wasalt_projects"), r.get("project_name"), r.get("developer_name"),
                    r.get("country", "Saudi Arabia"), r.get("city"), r.get("district"), r.get("project_type"),
                    r.get("starting_price_sar"), r.get("area_range_sqm"), r.get("available_units_count"),
                    r.get("wafi_registered"), r.get("completion_status"), json.dumps(r.get("amenities", [])),
                    r.get("description"), r.get("project_url"), r.get("scraped_at")
                )
                for r in _stream_jsonl(p3)
            ]
            cur.executemany("""
            INSERT OR REPLACE INTO wasalt_projects (
                project_id, source, project_name, developer_name, country, city, district,
                project_type, starting_price_sar, area_range_sqm, available_units_count,
                wafi_registered, completion_status, amenities_json, description, project_url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, batch)
            counts["wasalt_projects"] = len(batch)

        # 4. wasalt_listings
        p4 = RAW_JSONL_DIR / "wasalt_listings.jsonl"
        if p4.exists():
            batch = [
                (
                    r.get("listing_id"), r.get("ref_no"), r.get("source", "wasalt"), r.get("wasalt_project_id"),
                    r.get("listing_purpose"), r.get("property_usage", "residential"), r.get("property_type"),
                    r.get("title"), r.get("country", "Saudi Arabia"), r.get("city"), r.get("region_zone"),
                    r.get("district"), r.get("street_name"), r.get("plan_number"), r.get("price_amount"),
                    r.get("price_currency", "SAR"), r.get("price_period", "total"), r.get("monthly_rent_display"),
                    r.get("built_up_area_sqm"), r.get("land_area_sqm"), r.get("bedrooms"), r.get("bathrooms"),
                    r.get("living_rooms"), r.get("guest_rooms"), r.get("furnishing_status", "unknown"),
                    r.get("property_age_years"), r.get("building_year"), r.get("facade_direction"),
                    r.get("street_width_m"), json.dumps(r.get("utilities", [])), r.get("other_obligations"),
                    r.get("broker_agency_name"), r.get("broker_agent_name"), r.get("fal_license_no"),
                    r.get("rega_ad_license_no"), r.get("rega_status", "unknown"), r.get("rega_issue_date"),
                    r.get("rega_expiry_date"), r.get("ad_source"), r.get("views_last_7_days"),
                    r.get("description"), r.get("listing_url"), r.get("last_updated_text"), r.get("scraped_at")
                )
                for r in _stream_jsonl(p4)
            ]
            cur.executemany("""
            INSERT OR REPLACE INTO wasalt_listings (
                listing_id, ref_no, source, wasalt_project_id, listing_purpose, property_usage,
                property_type, title, country, city, region_zone, district, street_name,
                plan_number, price_amount, price_currency, price_period, monthly_rent_display,
                built_up_area_sqm, land_area_sqm, bedrooms, bathrooms, living_rooms, guest_rooms,
                furnishing_status, property_age_years, building_year, facade_direction,
                street_width_m, utilities_json, other_obligations, broker_agency_name,
                broker_agent_name, fal_license_no, rega_ad_license_no, rega_status,
                rega_issue_date, rega_expiry_date, ad_source, views_last_7_days, description,
                listing_url, last_updated_text, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, batch)
            counts["wasalt_listings"] = len(batch)

        # 5. wasalt_auction_groups
        p5 = RAW_JSONL_DIR / "wasalt_auction_groups.jsonl"
        if p5.exists():
            batch = [
                (
                    r.get("auction_group_id"), r.get("auction_title"), r.get("auction_category"), r.get("auction_type"),
                    r.get("city"), r.get("status"), r.get("total_assets", 0), r.get("min_deposit_sar"),
                    r.get("starts_at"), r.get("ends_at"), r.get("sales_agent_name"), r.get("sales_agent_fal_license"),
                    r.get("is_test_record", 0), r.get("group_description"), r.get("auction_url"), r.get("scraped_at")
                )
                for r in _stream_jsonl(p5)
            ]
            cur.executemany("""
            INSERT OR REPLACE INTO wasalt_auction_groups (
                auction_group_id, auction_title, auction_category, auction_type, city, status,
                total_assets, min_deposit_sar, starts_at, ends_at, sales_agent_name,
                sales_agent_fal_license, is_test_record, group_description, auction_url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, batch)
            counts["wasalt_auction_groups"] = len(batch)

        # 6. wasalt_auction_assets
        p6 = RAW_JSONL_DIR / "wasalt_auction_assets.jsonl"
        if p6.exists():
            batch = [
                (
                    r.get("asset_id"), r.get("auction_group_id"), r.get("asset_number"), r.get("asset_title"),
                    r.get("asset_category"), r.get("city_or_location"), r.get("asset_status"),
                    r.get("deposit_amount_sar"), r.get("starting_bid_sar"), r.get("highest_bid_sar"),
                    r.get("total_bidders"), r.get("bids_count"), r.get("followers_count"), r.get("area_sqm"),
                    json.dumps(r.get("asset_specs", {})), r.get("asset_url"), r.get("scraped_at")
                )
                for r in _stream_jsonl(p6)
            ]
            cur.executemany("""
            INSERT OR REPLACE INTO wasalt_auction_assets (
                asset_id, auction_group_id, asset_number, asset_title, asset_category, city_or_location,
                asset_status, deposit_amount_sar, starting_bid_sar, highest_bid_sar, total_bidders,
                bids_count, followers_count, area_sqm, asset_specs_json, asset_url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, batch)
            counts["wasalt_auction_assets"] = len(batch)

        # 7. document_registry
        p7 = MANIFESTS_DIR / "document_registry.jsonl"
        if p7.exists():
            batch = [
                (
                    r.get("doc_id"), r.get("source_platform"), r.get("doc_category"), r.get("title"),
                    r.get("file_path"), r.get("linked_table"), r.get("linked_entity_id"), r.get("country", "Multi"),
                    r.get("city", "Multi"), r.get("word_count", 0), json.dumps(r.get("source_urls", [])),
                    r.get("last_scraped")
                )
                for r in _stream_jsonl(p7)
            ]
            cur.executemany("""
            INSERT OR REPLACE INTO document_registry (
                doc_id, source_platform, doc_category, title, file_path, linked_table,
                linked_entity_id, country, city, word_count, source_urls_json, last_scraped
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, batch)
            counts["document_registry"] = len(batch)

        conn.commit()
        conn.close()
        return counts

        conn.commit()
        conn.close()
        return counts
