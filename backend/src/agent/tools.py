"""Poka-Yoke Parameterized SQL and RAG Tools for Google ADK Agentic Harness.

File: backend/src/agent/tools.py
Purpose: Exposes 6 mistake-proof (Poka-Yoke) tools for high-precision structured
         and unstructured retrieval over the DarGlobal & Wasalt repository:
         1. search_darglobal_projects
         2. search_wasalt_listings
         3. search_wasalt_projects_and_auctions
         4. get_residency_and_market_rules
         5. search_knowledge_base
         6. read_document
Core Logic: Strict parameterized typing, automated bounds-checking, zero $0.00 price
            hallucinations, and guaranteed source URL attachment.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.src.rag.retriever import HybridRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = PROJECT_ROOT / "data" / "structured" / "db" / "real_estate.db"

_retriever_instance: Optional[HybridRetriever] = None


def get_retriever() -> HybridRetriever:
    """Singleton getter for HybridRetriever to avoid multi-instance memory overhead."""
    global _retriever_instance
    if _retriever_instance is None:
        _retriever_instance = HybridRetriever()
    return _retriever_instance


def search_darglobal_projects(
    country: Optional[str] = None,
    city: Optional[str] = None,
    brand_partner: Optional[str] = None,
    max_price_usd: Optional[float] = None,
    residency_eligible_only: bool = False,
    limit: int = 5,
) -> Dict[str, Any]:
    """Searches luxury branded developments by DarGlobal (e.g. Trump, Pagani, Aston Martin, Elie Saab).

    Args:
        country: Filter by country (e.g. 'UAE', 'Oman', 'Qatar', 'Spain', 'UK', 'Saudi Arabia').
        city: Filter by city (e.g. 'Dubai', 'Muscat', 'Doha', 'London').
        brand_partner: Filter by design partner (e.g. 'Trump', 'Pagani', 'Aston Martin', 'Missoni').
        max_price_usd: Maximum starting price in USD (null if unspecified).
        residency_eligible_only: If True, only returns projects qualifying for golden visas.
        limit: Maximum results to return (default 5, max 10).

    Returns:
        Structured dictionary containing matched projects and verified canonical URLs.
    """
    limit = min(max(1, limit), 10)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = """
    SELECT 
        project_id, project_name, brand_partner, country, city, district_or_location,
        price_display, starting_price_amount, starting_price_currency, starting_price_usd_est,
        completion_date, unit_configurations_json, area_sqm_min, area_sqm_max,
        freehold_for_foreigners, residency_eligible, tokenization_available,
        short_summary, project_url, brochure_url
    FROM v_darglobal_projects
    WHERE 1=1
    """
    params: List[Any] = []

    if country:
        sql += " AND country_lower = LOWER(?)"
        params.append(country.strip())
    if city:
        sql += " AND city_lower LIKE LOWER(?)"
        params.append(f"%{city.strip()}%")
    if brand_partner:
        sql += " AND brand_partner_lower LIKE LOWER(?)"
        params.append(f"%{brand_partner.strip()}%")
    if max_price_usd and max_price_usd > 0:
        sql += " AND (starting_price_usd_est IS NOT NULL AND starting_price_usd_est <= ?)"
        params.append(max_price_usd)
    if residency_eligible_only:
        sql += " AND residency_eligible = 1"

    sql += " ORDER BY project_name ASC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "brand_partner": r["brand_partner"],
            "location": f"{r['city']}, {r['country']}",
            "pricing": r["price_display"],
            "completion": r["completion_date"] or "Under Development",
            "unit_configurations": r["unit_configurations_json"],
            "area_sqm": f"{r['area_sqm_min']} - {r['area_sqm_max']} SQM" if r["area_sqm_min"] else "Inquire",
            "freehold_ownership": "100% Freehold" if r["freehold_for_foreigners"] else "Leasehold",
            "residency_eligible": bool(r["residency_eligible"]),
            "tokenization": bool(r["tokenization_available"]),
            "summary": r["short_summary"],
            "project_url": r["project_url"],
            "brochure_url": r["brochure_url"],
        })

    return {
        "count": len(results),
        "results": results,
    }


def search_wasalt_listings(
    city: Optional[str] = None,
    district: Optional[str] = None,
    property_type: Optional[str] = None,
    listing_purpose: str = "buy",
    min_bedrooms: Optional[int] = None,
    max_price_sar: Optional[float] = None,
    active_rega_only: bool = True,
    limit: int = 5,
) -> Dict[str, Any]:
    """Searches live verified property listings on Wasalt across Saudi Arabia.

    Args:
        city: Saudi municipality (e.g. 'Riyadh', 'Jeddah', 'Dammam').
        district: Specific municipal district (e.g. 'As-Saadah', 'An-Narjis', 'Al-Malqa').
        property_type: Property typology (e.g. 'apartment', 'villa', 'building', 'warehouse').
        listing_purpose: 'buy' for sales or 'rent' for rentals (default 'buy').
        min_bedrooms: Minimum bedroom count required (e.g. 2, 3, 4).
        max_price_sar: Maximum sales price in SAR (e.g. 600000, 1500000).
        active_rega_only: Enforce official REGA advertisement license authorization.
        limit: Maximum results to return (default 5, max 10).

    Returns:
        Structured listing objects including REGA license clearance and listing URLs.
    """
    limit = min(max(1, limit), 10)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = """
    SELECT 
        listing_id, ref_no, title, property_type, price_amount, price_currency,
        built_up_area_sqm, bedrooms, bathrooms, city, district,
        fal_license_no, rega_ad_license_no, rega_status, broker_agency_name,
        listing_url
    FROM v_wasalt_listings
    WHERE listing_purpose_lower = LOWER(?)
    """
    params: List[Any] = [listing_purpose.strip()]

    if city:
        sql += " AND city_lower = LOWER(?)"
        params.append(city.strip())
    if district:
        sql += " AND district_lower LIKE LOWER(?)"
        params.append(f"%{district.strip()}%")
    if property_type:
        sql += " AND property_type_lower = LOWER(?)"
        params.append(property_type.strip())
    if min_bedrooms is not None:
        sql += " AND bedrooms >= ?"
        params.append(min_bedrooms)
    if max_price_sar is not None:
        sql += " AND price_amount <= ?"
        params.append(max_price_sar)
    if active_rega_only:
        sql += " AND rega_status_lower = 'active'"

    sql += " ORDER BY price_amount ASC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "listing_id": r["listing_id"],
            "title": r["title"],
            "property_type": r["property_type"],
            "price_sar": f"{r['price_amount']:,.0f} SAR",
            "bedrooms": r["bedrooms"],
            "bathrooms": r["bathrooms"],
            "area_sqm": f"{r['built_up_area_sqm']} SQM",
            "location": f"{r['district'] or 'General'}, {r['city']}",
            "fal_license": r["fal_license_no"],
            "rega_ad_license": r["rega_ad_license_no"],
            "rega_status": r["rega_status"],
            "broker": r["broker_agency_name"],
            "listing_url": r["listing_url"],
        })

    return {
        "count": len(results),
        "results": results,
    }


def search_wasalt_projects_and_auctions(
    target: str = "auctions",
    city: Optional[str] = None,
    status: Optional[str] = None,
    max_deposit_sar: Optional[float] = None,
    auction_group_id: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Searches Wasalt master developer projects or live judicial/private auctions (Infath).

    Args:
        target: 'auctions' to search Infath/private auctions, or 'projects' for masterplans.
        city: City filter (e.g. 'Riyadh').
        status: Lifecycle status ('live', 'upcoming', 'ended', 'under_development').
        max_deposit_sar: Maximum required bank e-wallet deposit for auctions in SAR.
        auction_group_id: Specific auction group numeric ID (e.g. '1162', '1149').
        limit: Maximum results to return (default 5, max 10).

    Returns:
        Structured auction event clusters or developer project summaries.
    """
    limit = min(max(1, limit), 10)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if target.lower() == "projects":
        sql = """
        SELECT 
            project_id, project_name, developer_name, country, city, district,
            project_type, starting_price_sar, available_units_count, wafi_registered,
            completion_status, project_url, description
        FROM v_wasalt_projects
        WHERE 1=1
        """
        params: List[Any] = []
        if city:
            sql += " AND city_lower = LOWER(?)"
            params.append(city.strip())
        sql += " ORDER BY project_name ASC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        results = [
            {
                "project_id": r["project_id"],
                "project_name": r["project_name"],
                "developer": r["developer_name"],
                "location": f"{r['district'] or ''}, {r['city']}",
                "starting_price_sar": f"{r['starting_price_sar']:,.0f} SAR" if r["starting_price_sar"] else "Inquire",
                "available_units": r["available_units_count"],
                "wafi_escrow_registered": bool(r["wafi_registered"]),
                "status": r["completion_status"],
                "project_url": r["project_url"],
            }
            for r in rows
        ]
    else:
        # Search auctions
        sql = """
        SELECT 
            auction_group_id, auction_title, auction_category, auction_type, city, status,
            total_assets, min_deposit_sar, starts_at, ends_at, sales_agent_name,
            auction_url, active_assets_count, lowest_lot_deposit_sar
        FROM v_wasalt_auctions
        WHERE 1=1
        """
        params = []
        if auction_group_id:
            sql += " AND auction_group_id = ?"
            params.append(str(auction_group_id).strip())
        if city:
            sql += " AND city_lower = LOWER(?)"
            params.append(city.strip())
        if status:
            sql += " AND status_lower LIKE LOWER(?)"
            params.append(f"%{status.strip()}%")
        if max_deposit_sar is not None:
            sql += " AND (min_deposit_sar <= ? OR lowest_lot_deposit_sar <= ?)"
            params.extend([max_deposit_sar, max_deposit_sar])

        sql += " ORDER BY starts_at ASC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()

        # If a specific auction group was requested, also fetch its constituent asset lots
        results = []
        for r in rows:
            gid = r["auction_group_id"]
            asset_lots = []
            if auction_group_id:
                cursor.execute(
                    """
                    SELECT asset_id, asset_number, asset_title, asset_category, deposit_amount_sar, starting_bid_sar, highest_bid_sar, total_bidders, bids_count, asset_url
                    FROM v_wasalt_auction_assets
                    WHERE auction_group_id = ?
                    ORDER BY asset_number ASC LIMIT 20;
                    """,
                    (gid,),
                )
                asset_lots = [dict(a) for a in cursor.fetchall()]

            results.append({
                "auction_group_id": gid,
                "title": r["auction_title"],
                "category": r["auction_category"],
                "auction_type": r["auction_type"],
                "status": r["status"],
                "city": r["city"],
                "total_assets_in_group": r["total_assets"],
                "indexed_assets_count": r["active_assets_count"],
                "min_deposit_sar": f"{r['min_deposit_sar']:,.0f} SAR" if r["min_deposit_sar"] else "None",
                "starts_at": r["starts_at"],
                "ends_at": r["ends_at"],
                "sales_agent": r["sales_agent_name"],
                "auction_url": r["auction_url"],
                "sample_asset_lots": asset_lots if asset_lots else None,
            })

    conn.close()
    return {
        "target": target,
        "count": len(results),
        "results": results,
    }


def get_residency_and_market_rules(country: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves official real estate residency, Golden Visa, and foreign ownership regulations.

    Args:
        country: Target country (e.g. 'Saudi Arabia', 'UAE', 'Oman', 'Qatar', 'Spain', 'UK', 'Greece').
                 If omitted, returns all 9 indexed country investment frameworks.

    Returns:
        Structured statutory requirements, investment thresholds, tax benefits, and official links.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = """
    SELECT 
        country, residency_program_name, min_investment_local, min_investment_usd_approx,
        off_plan_eligible, foreign_freehold_allowed, key_tax_benefits, special_notes, source_url
    FROM v_residency_rules
    WHERE 1=1
    """
    params: List[Any] = []

    if country:
        sql += " AND country_lower LIKE LOWER(?)"
        params.append(f"%{country.strip()}%")

    sql += " ORDER BY country ASC"
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "country": r["country"],
            "program_name": r["residency_program_name"],
            "min_investment_local": r["min_investment_local"],
            "min_investment_usd_approx": f"${r['min_investment_usd_approx']:,.0f} USD" if r["min_investment_usd_approx"] else "N/A",
            "off_plan_eligible": bool(r["off_plan_eligible"]),
            "foreign_freehold_allowed": bool(r["foreign_freehold_allowed"]),
            "tax_benefits": r["key_tax_benefits"],
            "special_notes": r["special_notes"],
            "source_url": r["source_url"],
        })

    return {
        "count": len(results),
        "results": results,
    }


def search_knowledge_base(
    query: str,
    platform: Optional[str] = None,
    country: Optional[str] = None,
    top_k: int = 5,
) -> Dict[str, Any]:
    """Performs semantic hybrid search across the 88 Markdown knowledge guides and project narratives.

    Combines Sparse BM25 + Dense FastEmbed (BAAI/bge-small-en-v1.5) + RRF + FlashRank cross-encoder reranking.

    Args:
        query: User question or natural language query (e.g. 'How does DarGlobal tokenization work?').
        platform: Optional origin platform filter ('darglobal' or 'wasalt').
        country: Optional geographic filter ('UAE', 'Saudi Arabia', 'Oman', etc.).
        top_k: Number of reranked passages to return (default 5, max 10).

    Returns:
        Top reranked passages with contextual headers, relevance scores, and source URLs.
    """
    retriever = get_retriever()
    hits = retriever.search(query=query, top_k=top_k, platform=platform, country=country)
    return {
        "query": query,
        "count": len(hits),
        "results": [h.to_dict() for h in hits],
    }


def read_document(doc_id: str) -> Dict[str, Any]:
    """Retrieves full text of a knowledge guide, FAQ document, or project narrative by doc_id.

    Args:
        doc_id: Canonical document identifier (e.g. 'dg_03_why_invest_and_residency_guides',
                'ws_04_auctions', 'dg_proj_trump_international_hotel_and_tower_dubai').

    Returns:
        Complete document text and source metadata for deep-dive reasoning.
    """
    retriever = get_retriever()
    doc = retriever.read_document(doc_id=doc_id)
    if not doc:
        return {
            "status": "not_found",
            "message": f"Document '{doc_id}' not found in registry.",
        }
    return {
        "status": "found",
        "document": doc,
    }
