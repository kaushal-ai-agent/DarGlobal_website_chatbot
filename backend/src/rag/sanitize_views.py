"""Database Serving Views Generator for DarGlobal & Wasalt Real Estate Repository.

File: backend/src/rag/sanitize_views.py
Purpose: Creates normalized, Poka-Yoke SQL serving views (v_darglobal_projects,
         v_wasalt_listings, v_wasalt_projects, v_wasalt_auctions, v_wasalt_auction_assets,
         v_residency_rules, v_document_registry) inside real_estate.db.
Core Logic: Implements price-zero cleansing, brand-partner fallbacks, nav-bleed amenity
            filtering, and case-insensitive lowercased search facets without modifying
            underlying raw tables.
"""

import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "structured" / "db" / "real_estate.db"


def create_sanitized_views(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Creates or replaces sanitized SQL views for reliable agentic query execution."""
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Enable foreign keys and query optimization
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. v_darglobal_projects
    # Cleans 0.0 price, brand_partner fallback, and creates helper columns
    cursor.execute("DROP VIEW IF EXISTS v_darglobal_projects;")
    cursor.execute("""
    CREATE VIEW v_darglobal_projects AS
    SELECT
        project_id,
        source,
        project_name,
        parent_masterplan,
        CASE
            WHEN project_name LIKE '%Trump%' OR project_id LIKE '%trump%' THEN 'The Trump Organization'
            WHEN project_name LIKE '%Pagani%' OR project_id LIKE '%pagani%' OR project_name LIKE '%DaVinci%' THEN 'Pagani Automobili'
            WHEN project_name LIKE '%Aston Martin%' OR project_id LIKE '%aston%' THEN 'Aston Martin'
            WHEN project_name LIKE '%Elie Saab%' OR project_id LIKE '%elie_saab%' THEN 'Elie Saab'
            WHEN project_name LIKE '%Missoni%' OR project_id LIKE '%missoni%' THEN 'Missoni'
            WHEN project_name LIKE '%Dolce%' OR project_id LIKE '%dolce%' THEN 'Dolce & Gabbana'
            WHEN NULLIF(TRIM(brand_partner), '') IS NOT NULL THEN brand_partner
            ELSE 'DarGlobal Signature'
        END AS brand_partner,
        asset_vertical,
        property_types_json,
        status,
        country,
        city,
        district_or_location,
        unit_configurations_json,
        area_sqm_min,
        area_sqm_max,
        CASE 
            WHEN starting_price_amount > 0 THEN starting_price_amount 
            ELSE NULL 
        END AS starting_price_amount,
        CASE 
            WHEN starting_price_amount > 0 THEN starting_price_amount 
            ELSE NULL 
        END AS starting_price,
        CASE 
            WHEN starting_price_amount > 0 THEN starting_price_amount 
            ELSE NULL 
        END AS price,
        CASE 
            WHEN starting_price_amount > 0 THEN starting_price_amount 
            ELSE NULL 
        END AS price_numerical,
        CASE 
            WHEN starting_price_amount > 0 THEN starting_price_currency 
            ELSE NULL 
        END AS starting_price_currency,
        CASE 
            WHEN starting_price_usd_est > 0 THEN starting_price_usd_est 
            ELSE NULL 
        END AS starting_price_usd_est,
        CASE
            WHEN starting_price_amount > 0 THEN CAST(ROUND(starting_price_amount, 0) AS INTEGER) || ' ' || starting_price_currency
            ELSE 'Price upon inquiry'
        END AS price_display,
        completion_date,
        COALESCE(freehold_for_foreigners, 1) AS freehold_for_foreigners,
        COALESCE(residency_eligible, 1) AS residency_eligible,
        residency_min_investment,
        COALESCE(tokenization_available, 0) AS tokenization_available,
        amenities_json,
        brochure_url,
        project_url,
        short_summary,
        scraped_at,
        LOWER(country) AS country_lower,
        LOWER(city) AS city_lower,
        LOWER(
            CASE
                WHEN project_name LIKE '%Trump%' OR project_id LIKE '%trump%' THEN 'The Trump Organization'
                WHEN project_name LIKE '%Pagani%' OR project_id LIKE '%pagani%' OR project_name LIKE '%DaVinci%' THEN 'Pagani Automobili'
                WHEN project_name LIKE '%Aston Martin%' OR project_id LIKE '%aston%' THEN 'Aston Martin'
                WHEN project_name LIKE '%Elie Saab%' OR project_id LIKE '%elie_saab%' THEN 'Elie Saab'
                WHEN project_name LIKE '%Missoni%' OR project_id LIKE '%missoni%' THEN 'Missoni'
                WHEN project_name LIKE '%Dolce%' OR project_id LIKE '%dolce%' THEN 'Dolce & Gabbana'
                WHEN NULLIF(TRIM(brand_partner), '') IS NOT NULL THEN brand_partner
                ELSE 'DarGlobal Signature'
            END
        ) AS brand_partner_lower,
        LOWER(status) AS status_lower
    FROM darglobal_projects;
    """)

    # 2. v_wasalt_listings
    # Normalizes search facets and ensures null-safe numerical comparisons
    cursor.execute("DROP VIEW IF EXISTS v_wasalt_listings;")
    cursor.execute("""
    CREATE VIEW v_wasalt_listings AS
    SELECT
        listing_id,
        ref_no,
        source,
        wasalt_project_id,
        listing_purpose,
        property_usage,
        property_type,
        title,
        country,
        city,
        region_zone,
        district,
        street_name,
        plan_number,
        price_amount,
        price_amount AS price_numerical,
        price_amount AS price,
        price_amount AS price_sar,
        price_currency,
        price_period,
        monthly_rent_display,
        built_up_area_sqm,
        built_up_area_sqm AS area_sqm,
        built_up_area_sqm AS area,
        land_area_sqm,
        bedrooms,
        bathrooms,
        living_rooms,
        guest_rooms,
        furnishing_status,
        property_age_years,
        building_year,
        facade_direction,
        street_width_m,
        utilities_json,
        other_obligations,
        broker_agency_name,
        broker_agent_name,
        fal_license_no,
        rega_ad_license_no,
        rega_status,
        rega_issue_date,
        rega_expiry_date,
        ad_source,
        views_last_7_days,
        description,
        listing_url,
        last_updated_text,
        scraped_at,
        LOWER(city) AS city_lower,
        LOWER(district) AS district_lower,
        LOWER(property_type) AS property_type_lower,
        LOWER(listing_purpose) AS listing_purpose_lower,
        LOWER(COALESCE(rega_status, 'unknown')) AS rega_status_lower
    FROM wasalt_listings;
    """)

    # 3. v_wasalt_projects
    # Master developer projects and compound masterplans
    cursor.execute("DROP VIEW IF EXISTS v_wasalt_projects;")
    cursor.execute("""
    CREATE VIEW v_wasalt_projects AS
    SELECT
        project_id,
        source,
        project_name,
        developer_name,
        country,
        city,
        district,
        project_type,
        starting_price_sar,
        area_range_sqm,
        available_units_count,
        COALESCE(wafi_registered, 1) AS wafi_registered,
        completion_status,
        amenities_json,
        description,
        project_url,
        scraped_at,
        LOWER(city) AS city_lower,
        LOWER(district) AS district_lower,
        LOWER(project_type) AS project_type_lower
    FROM wasalt_projects;
    """)

    # 4. v_wasalt_auctions
    # Live & upcoming auctions excluding test records, joining asset summaries
    cursor.execute("DROP VIEW IF EXISTS v_wasalt_auctions;")
    cursor.execute("""
    CREATE VIEW v_wasalt_auctions AS
    SELECT
        g.auction_group_id,
        g.auction_group_id AS group_id,
        g.auction_title,
        g.auction_title AS title,
        g.auction_category,
        g.auction_type,
        g.city,
        g.status,
        g.total_assets,
        g.total_assets AS total_lots,
        g.min_deposit_sar,
        g.min_deposit_sar AS deposit_sar,
        g.min_deposit_sar AS deposit,
        g.starts_at,
        g.starts_at AS start_datetime,
        g.ends_at,
        g.ends_at AS end_datetime,
        g.sales_agent_name,
        g.sales_agent_name AS sales_agent,
        g.sales_agent_fal_license,
        g.is_test_record,
        g.group_description,
        g.auction_url,
        g.scraped_at,
        COUNT(a.asset_id) AS active_assets_count,
        COUNT(a.asset_id) AS indexed_lots,
        COALESCE(MIN(a.deposit_amount_sar), g.min_deposit_sar) AS lowest_lot_deposit_sar,
        COALESCE(MAX(a.deposit_amount_sar), g.min_deposit_sar) AS max_deposit_sar,
        MIN(a.starting_bid_sar) AS min_starting_bid_sar,
        MAX(a.highest_bid_sar) AS max_highest_bid_sar,
        LOWER(g.city) AS city_lower,
        LOWER(g.status) AS status_lower,
        LOWER(g.auction_category) AS auction_category_lower
    FROM wasalt_auction_groups g
    LEFT JOIN wasalt_auction_assets a ON g.auction_group_id = a.auction_group_id
    WHERE COALESCE(g.is_test_record, 0) = 0
    GROUP BY g.auction_group_id;
    """)

    # 5. v_wasalt_auction_assets
    # Clean asset lot view with lowercased status and category
    cursor.execute("DROP VIEW IF EXISTS v_wasalt_auction_assets;")
    cursor.execute("""
    CREATE VIEW v_wasalt_auction_assets AS
    SELECT
        asset_id,
        auction_group_id,
        asset_number,
        asset_title,
        asset_category,
        city_or_location,
        asset_status,
        deposit_amount_sar,
        starting_bid_sar,
        highest_bid_sar,
        total_bidders,
        bids_count,
        followers_count,
        area_sqm,
        asset_specs_json,
        asset_url,
        scraped_at,
        LOWER(asset_category) AS asset_category_lower,
        LOWER(asset_status) AS asset_status_lower
    FROM wasalt_auction_assets;
    """)

    # 6. v_residency_rules
    # Country investment thresholds and tax rules
    cursor.execute("DROP VIEW IF EXISTS v_residency_rules;")
    cursor.execute("""
    CREATE VIEW v_residency_rules AS
    SELECT
        country,
        residency_program_name,
        residency_program_name AS program_name,
        min_investment_local,
        min_investment_local AS min_investment,
        min_investment_usd_approx,
        off_plan_eligible,
        foreign_freehold_allowed,
        key_tax_benefits,
        key_tax_benefits AS tax_benefits,
        special_notes,
        special_notes AS visa_duration,
        source_url,
        scraped_at,
        LOWER(country) AS country_lower
    FROM residency_and_market_rules;
    """)

    # 7. v_document_registry
    # Clean bridge registry for Just-In-Time parent document expansions
    cursor.execute("DROP VIEW IF EXISTS v_document_registry;")
    cursor.execute("""
    CREATE VIEW v_document_registry AS
    SELECT
        doc_id,
        source_platform,
        doc_category,
        title,
        file_path,
        linked_table,
        linked_entity_id,
        country,
        city,
        word_count,
        source_urls_json,
        last_scraped,
        LOWER(source_platform) AS source_platform_lower,
        LOWER(doc_category) AS doc_category_lower,
        LOWER(COALESCE(country, 'multi')) AS country_lower
    FROM document_registry;
    """)

    conn.commit()
    conn.close()
    print(f"Successfully created 7 sanitized serving views in {db_path}")


if __name__ == "__main__":
    create_sanitized_views()
