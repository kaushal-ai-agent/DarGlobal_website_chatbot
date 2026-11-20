"""Tools for Property Database Agent."""

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[5]
DB_PATH = PROJECT_ROOT / "data" / "structured" / "db" / "real_estate.db"
PASS_A_DB_PATH = PROJECT_ROOT / "data" / "wasalt" / "pass_a" / "wasalt_pass_a.db"


def search_properties(
    city: Optional[str] = None,
    district: Optional[str] = None,
    property_type: Optional[str] = None,
    min_bedrooms: Optional[int] = None,
    max_price_sar: Optional[float] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Searches live verified residential and commercial property listings on Wasalt across Saudi Arabia.

    Args:
        city: Municipality name (e.g. 'Riyadh', 'Jeddah', 'Dammam'). Note: current Wasalt sales snapshot covers Riyadh.
        district: District name in Saudi Arabia (e.g. 'An-Nadheem', 'As-Saadah', 'Dirab', 'Ar-Rimal', 'Taybah').
        property_type: Typology (e.g. 'apartment', 'villa', 'floor', 'land').
        min_bedrooms: Minimum bedroom count required (e.g. 1, 2, 3, 4).
        max_price_sar: Maximum sales price in SAR (e.g. 500000, 600000, 1000000).
        limit: Number of results to return (default 5, max 10).

    Returns:
        Structured listing cards with IDs, titles, prices, bedroom/bath counts, area sqm, REGA licenses, and URLs.
    """
    limit = min(max(1, limit), 10)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = """
    SELECT 
        listing_id, ref_no, title, property_type, price_amount, price_numerical, price_currency,
        built_up_area_sqm, bedrooms, bathrooms, city, district,
        fal_license_no, rega_ad_license_no, rega_status, broker_agency_name,
        listing_url
    FROM v_wasalt_listings
    WHERE listing_purpose_lower = 'buy'
    """
    params: List[Any] = []

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
            "price_numerical": r["price_numerical"],
            "bedrooms": r["bedrooms"],
            "bathrooms": r["bathrooms"],
            "area_sqm": f"{r['built_up_area_sqm']} SQM" if r["built_up_area_sqm"] else "N/A",
            "location": f"{r['district'] or 'General'}, {r['city']}",
            "broker_agency": r["broker_agency_name"],
            "fal_license": r["fal_license_no"],
            "rega_ad_license": r["rega_ad_license_no"],
            "rega_status": r["rega_status"],
            "listing_url": r["listing_url"],
        })

    return {
        "count": len(results),
        "results": results,
        "city_queried": city,
    }


def get_property_details(listing_id_or_title: str) -> Dict[str, Any]:
    """Retrieves exhaustive attributes and inspection data for a SPECIFIC property card or listing.

    ALWAYS use this tool when the user asks a follow-up question about a property card shown earlier,
    such as:
    - 'tell me more about this Apartment 124.69 SQM with 3 Bedrooms'
    - 'what are the details on wasalt_5975960'
    - 'give me more info on the Ar-Rimal apartment'

    Args:
        listing_id_or_title: The property ID (e.g. 'wasalt_5975960' or '5975960') or key title phrase
                             (e.g. '124.69 SQM', 'Ar-Rimal', 'An-Nadheem 82.75').

    Returns:
        Full details including facade, street width, broker, REGA/FAL licenses, price, area, and URL.
    """
    clean_query = listing_id_or_title.strip()
    clean_id = clean_query.replace("wasalt_", "")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Search in v_wasalt_listings by ID or title match
    cursor.execute(
        """
        SELECT *
        FROM v_wasalt_listings
        WHERE listing_id = ? 
           OR listing_id = ?
           OR ref_no = ?
           OR title LIKE ?
           OR district_lower LIKE LOWER(?)
        LIMIT 1
        """,
        (clean_query, f"wasalt_{clean_id}", clean_id, f"%{clean_query}%", f"%{clean_query}%"),
    )
    row = cursor.fetchone()
    conn.close()

    # If found in unified store
    if row:
        return {
            "found": True,
            "source": "wasalt_listings",
            "listing_id": row["listing_id"],
            "title": row["title"],
            "property_type": row["property_type"],
            "price_sar": f"{row['price_amount']:,.0f} SAR",
            "price_amount": row["price_amount"],
            "price_numerical": row["price_numerical"],
            "bedrooms": row["bedrooms"],
            "bathrooms": row["bathrooms"],
            "area_sqm": row["built_up_area_sqm"],
            "city": row["city"],
            "district": row["district"],
            "broker_agency": row["broker_agency_name"],
            "fal_license": row["fal_license_no"],
            "rega_ad_license": row["rega_ad_license_no"],
            "rega_status": row["rega_status"],
            "listing_url": row["listing_url"],
            "description": f"Verified {row['property_type']} in {row['district']}, {row['city']}. Features {row['bedrooms']} bedrooms and {row['bathrooms']} bathrooms covering {row['built_up_area_sqm']} SQM. Authorized under REGA advertising permit #{row['rega_ad_license_no']}.",
        }

    # Fallback to wasalt_pass_a.db for deep attributes (e.g. facade, street width)
    if PASS_A_DB_PATH.exists():
        conn_a = sqlite3.connect(str(PASS_A_DB_PATH))
        conn_a.row_factory = sqlite3.Row
        cur_a = conn_a.cursor()
        cur_a.execute(
            """
            SELECT * FROM wasalt_search_properties
            WHERE record_id = ? OR ref_no = ? OR title LIKE ?
            LIMIT 1
            """,
            (clean_query, clean_id, f"%{clean_query}%"),
        )
        row_a = cur_a.fetchone()
        conn_a.close()
        if row_a:
            return {
                "found": True,
                "source": "wasalt_search_properties",
                "listing_id": row_a["record_id"],
                "title": row_a["title"],
                "property_type": row_a["property_type"],
                "price_sar": f"{row_a['price_sar']:,.0f} SAR" if row_a["price_sar"] else "Price upon inquiry",
                "bedrooms": row_a["bedrooms"],
                "bathrooms": row_a["bathrooms"],
                "area_sqm": row_a["area_sqm"],
                "city": row_a["city"],
                "district": row_a["district"],
                "facade": row_a["facade"] or "Unspecified",
                "street_width_m": row_a["street_width_m"] or "Unspecified",
                "broker_agency": row_a["broker_agency"],
                "fal_license": row_a["fal_license_no"],
                "rega_ad_license": row_a["rega_ad_license_no"],
                "listing_url": row_a["source_url"],
                "raw_text": row_a["raw_card_text"],
            }

    # Also check DarGlobal luxury projects by name
    conn_dg = sqlite3.connect(str(DB_PATH))
    conn_dg.row_factory = sqlite3.Row
    cur_dg = conn_dg.cursor()
    cur_dg.execute(
        """
        SELECT * FROM v_darglobal_projects
        WHERE project_id = ? OR project_name LIKE ?
        LIMIT 1
        """,
        (clean_query, f"%{clean_query}%"),
    )
    row_dg = cur_dg.fetchone()
    conn_dg.close()
    if row_dg:
        brand = row_dg["brand_partner"]
        pname = str(row_dg["project_name"] or "")
        pid = str(row_dg["project_id"] or "")
        if "Trump" in pname or "trump" in pid:
            brand = "The Trump Organization"
        elif "Aston Martin" in pname:
            brand = "Aston Martin"
        elif "Pagani" in pname or "DaVinci" in pname:
            brand = "Pagani Automobili"
        elif "Lamborghini" in pname or "Tierra Viva" in pname:
            brand = "Automobili Lamborghini"
        elif "Elie Saab" in pname:
            brand = "Elie Saab"
        elif "Missoni" in pname:
            brand = "Missoni"
        elif "Dolce" in pname:
            brand = "Dolce & Gabbana"

        return {
            "found": True,
            "source": "darglobal_projects",
            "project_id": row_dg["project_id"],
            "title": row_dg["project_name"],
            "brand_partner": brand,
            "location": f"{row_dg['city']}, {row_dg['country']}",
            "pricing": row_dg["price_display"],
            "completion": row_dg["completion_date"],
            "unit_configurations": row_dg["unit_configurations_json"],
            "summary": row_dg["short_summary"],
            "project_url": row_dg["project_url"],
            "brochure_url": row_dg["brochure_url"],
        }

    return {
        "found": False,
        "query": listing_id_or_title,
        "error": f"No specific property record found matching '{listing_id_or_title}'.",
    }


def search_luxury_projects(
    brand: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    max_price_usd: Optional[float] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Searches DarGlobal international luxury masterplanned developments and branded residences.

    Includes Trump, Pagani, Aston Martin, Elie Saab, Missoni, and Dolce & Gabbana residences
    in UAE (Dubai), Oman (Muscat), Qatar (Simaisma), Spain (Marbella, Benahavís), UK (London), and Saudi Arabia (Jeddah).

    Args:
        brand: Luxury design partner (e.g. 'Trump', 'Pagani', 'Aston Martin', 'Elie Saab', 'Missoni').
        country: Host country (e.g. 'UAE', 'Oman', 'Qatar', 'Spain', 'UK', 'Saudi Arabia').
        city: City (e.g. 'Dubai', 'Muscat', 'Jeddah', 'London', 'Marbella').
        max_price_usd: Maximum starting price in USD (null if unconstrained).
        limit: Max results (default 5, max 10).

    Returns:
        Structured project objects with brand partner, location, completion date, pricing, and project URLs.
    """
    limit = min(max(1, limit), 10)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = """
    SELECT 
        project_id, project_name, brand_partner, country, city, district_or_location,
        price_display, starting_price_amount, starting_price_currency, starting_price_usd_est,
        completion_date, unit_configurations_json, freehold_for_foreigners, residency_eligible,
        tokenization_available, short_summary, project_url, brochure_url
    FROM v_darglobal_projects
    WHERE 1=1
    """
    params: List[Any] = []

    if brand:
        sql += " AND brand_partner_lower LIKE LOWER(?)"
        params.append(f"%{brand.strip()}%")
    if country:
        sql += " AND country_lower LIKE LOWER(?)"
        params.append(f"%{country.strip()}%")
    if city:
        sql += " AND city_lower LIKE LOWER(?)"
        params.append(f"%{city.strip()}%")
    if max_price_usd and max_price_usd > 0:
        sql += " AND (starting_price_usd_est IS NOT NULL AND starting_price_usd_est <= ?)"
        params.append(max_price_usd)

    sql += " ORDER BY project_name ASC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        brand_val = r["brand_partner"]
        pname = str(r["project_name"] or "")
        pid = str(r["project_id"] or "")
        if "Trump" in pname or "trump" in pid:
            brand_val = "The Trump Organization"
        elif "Aston Martin" in pname:
            brand_val = "Aston Martin"
        elif "Pagani" in pname or "DaVinci" in pname:
            brand_val = "Pagani Automobili"
        elif "Lamborghini" in pname or "Tierra Viva" in pname:
            brand_val = "Automobili Lamborghini"
        elif "Elie Saab" in pname:
            brand_val = "Elie Saab"
        elif "Missoni" in pname:
            brand_val = "Missoni"
        elif "Dolce" in pname:
            brand_val = "Dolce & Gabbana"

        results.append({
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "brand_partner": brand_val,
            "location": f"{r['city']}, {r['country']}",
            "pricing": r["price_display"],
            "completion": r["completion_date"] or "Under Development",
            "unit_configurations": r["unit_configurations_json"],
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


def search_auctions(
    status: Optional[str] = None,
    city: Optional[str] = None,
    max_deposit_sar: Optional[float] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Queries Saudi judicial (Infath) and private real estate auctions on Wasalt.

    Args:
        status: Lifecycle status ('LIVE', 'UPCOMING', 'CLOSED', 'CANCELLED').
        city: Municipality (e.g. 'Riyadh').
        max_deposit_sar: Maximum mandatory eWallet deposit requirement.
        limit: Number of auction groups to return (default 5).

    Returns:
        Auction event groups, deposit requirements, asset counts, and canonical auction URLs.
    """
    limit = min(max(1, limit), 10)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = """
    SELECT 
        group_id, title, city, status, start_datetime, end_datetime,
        total_lots, indexed_lots, min_deposit_sar, max_deposit_sar,
        sales_agent, auction_url
    FROM v_wasalt_auctions
    WHERE 1=1
    """
    params: List[Any] = []

    if status:
        sql += " AND status_lower = LOWER(?)"
        params.append(status.strip())
    if city:
        sql += " AND city_lower LIKE LOWER(?)"
        params.append(f"%{city.strip()}%")
    if max_deposit_sar and max_deposit_sar > 0:
        sql += " AND min_deposit_sar <= ?"
        params.append(max_deposit_sar)

    sql += " ORDER BY group_id DESC LIMIT ?"
    params.append(limit)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "group_id": r["group_id"],
            "title": r["title"],
            "city": r["city"],
            "status": r["status"],
            "start_time": r["start_datetime"],
            "end_time": r["end_datetime"],
            "total_lots": r["total_lots"],
            "min_deposit_sar": f"{r['min_deposit_sar']:,.0f} SAR" if r["min_deposit_sar"] else "Inquire",
            "sales_agent": r["sales_agent"] or "Authorized Broker",
            "auction_url": r["auction_url"],
        })

    return {
        "count": len(results),
        "results": results,
    }


def get_residency_rules(country: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves official property investment residency and Golden Visa rules.

    Covers Saudi Arabia (Premium Residency Real Estate Owner track), UAE (10-Year Golden Visa),
    Oman (Investor Residency Programme), Qatar (Permanent Residency), Spain, and UK.

    Args:
        country: Target country ('Saudi Arabia', 'UAE', 'Oman', 'Qatar', 'Spain', 'UK').

    Returns:
        Statutory minimum investment thresholds, foreign freehold rules, and official references.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    sql = "SELECT * FROM v_residency_rules WHERE 1=1"
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
            "program_name": r["program_name"],
            "min_investment_local": r["min_investment_local"],
            "min_investment_usd": r["min_investment_usd_approx"],
            "visa_duration": r["visa_duration"],
            "foreign_freehold": "Allowed" if r["foreign_freehold_allowed"] else "Restricted",
            "tax_benefits": r["tax_benefits"],
            "source_url": r["source_url"],
        })

    return {
        "count": len(results),
        "results": results,
    }


def execute_safe_sql(sql_query: str) -> Dict[str, Any]:
    """Executes a safe, read-only SELECT SQL query against the real_estate.db SQLite database.

    Use this when you need custom aggregation, minimums, maximums, averages, or counts.
    Always refer to the Database Schema Reference in your instructions for exact column names.

    Args:
        sql_query: Read-only SQL query starting with SELECT.

    Returns:
        Dictionary with success flag, row_count, and result rows.
    """
    query = sql_query.strip()
    if not query.upper().startswith("SELECT"):
        return {"success": False, "error": "Only read-only SELECT queries are permitted."}

    forbidden = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "REPLACE", "ATTACH", "PRAGMA"]
    for word in forbidden:
        if re.search(rf"\b{word}\b", query, re.IGNORECASE):
            return {"success": False, "error": f"Disallowed statement keyword '{word}' detected."}

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute(query)
        except sqlite3.OperationalError as op_err:
            # If the query failed because it targeted a base table instead of the enriched view
            rewritten = query
            if re.search(r"\bwasalt_listings\b", rewritten, re.IGNORECASE) and not re.search(r"\bv_wasalt_listings\b", rewritten, re.IGNORECASE):
                rewritten = re.sub(r"\bwasalt_listings\b", "v_wasalt_listings", rewritten, flags=re.IGNORECASE)
            if re.search(r"\bdarglobal_projects\b", rewritten, re.IGNORECASE) and not re.search(r"\bv_darglobal_projects\b", rewritten, re.IGNORECASE):
                rewritten = re.sub(r"\bdarglobal_projects\b", "v_darglobal_projects", rewritten, flags=re.IGNORECASE)
            if re.search(r"\bwasalt_auctions\b", rewritten, re.IGNORECASE) and not re.search(r"\bv_wasalt_auctions\b", rewritten, re.IGNORECASE):
                rewritten = re.sub(r"\bwasalt_auctions\b", "v_wasalt_auctions", rewritten, flags=re.IGNORECASE)
            if re.search(r"\bresidency_and_market_rules\b", rewritten, re.IGNORECASE) and not re.search(r"\bv_residency_rules\b", rewritten, re.IGNORECASE):
                rewritten = re.sub(r"\bresidency_and_market_rules\b", "v_residency_rules", rewritten, flags=re.IGNORECASE)

            if rewritten != query:
                cursor.execute(rewritten)
            else:
                raise op_err

        rows = cursor.fetchmany(10)
        col_names = [d[0] for d in cursor.description] if cursor.description else []
        conn.close()

        results = [dict(zip(col_names, row)) for row in rows]
        return {
            "success": True,
            "row_count": len(results),
            "rows": results,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
