"""
Wasalt Next.js & DOM Extraction Parsers (`wasalt_parsers.py`)

What it does:
Houses pure parsing functions that extract structured real estate entities from Wasalt search and detail pages. Combines Next.js SSR payloads (__NEXT_DATA__), Schema.org JSON-LD scripts, and BeautifulSoup DOM fallback parsing to extract properties, projects, project units, and judicial/private auctions.

Important Modules & Functions:
- `bs4.BeautifulSoup`: DOM query engine used for structural HTML fallback extraction.
- `parse_search_page_cards(html, page_url)`: Scans listing search pages, detects pagination bounds, and extracts card envelopes.
- `parse_property_detail(html, url, card, diag)`: Deep extraction of property specs (price, sqm, beds, baths, furnished, facade, street width, FAL/REGA licenses, amenities, coordinates).
- `parse_project_and_units(html, url, card, diag)`: Extracts developer masterplans, starting prices, handover dates, and child unit configurations.
- `parse_auction_detail(html, url, card, diag)`: Extracts Infath judicial and private auction parameters (terms, deposit, starting bid, event dates, zoning).
- `deep_find_first(obj, target_key)` & `deep_find_all_dicts_with_keys(obj, required_keys)`: Recursive dictionary traversers for arbitrarily nested Next.js props.
- `extract_next_data(soup)` / `extract_json_ld(soup)`: Safe JSON script tag parsers.

Core Logic (One-Liner):
Recursively navigates Next.js state trees and JSON-LD schemas with robust DOM selector fallbacks to extract normalized property, project, and auction records.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# Resolve project root and ensure it is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_DIR = Path(__file__).resolve().parent
for p in [str(PROJECT_ROOT), str(SCRAPER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from wasalt_utils import (
    DiagnosticTracker,
    classify_url,
    clean_text,
    RAW_DUMP_FILES,
    append_jsonl,
    truncate_raw,
    collect_json_keys,
    deep_find_all_dicts_with_keys,
    deep_find_first,
    extract_json_ld,
    extract_next_data,
    extract_numeric_id,
    extract_strict_label_values,
    parse_int,
    parse_location_parts,
    parse_number,
    strip_html_tags,
)


def parse_search_page_cards(html: str, page_url: str) -> tuple[list[dict[str, Any]], int | None]:
    soup = BeautifulSoup(html, "lxml")
    cards: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    total_pages = None
    pag_ul = soup.select_one("ul[class*='pagination' i]")
    if pag_ul:
        nums = [int(t) for t in re.findall(r"\b\d+\b", pag_ul.get_text(" "))]
        if nums:
            total_pages = max(nums)

    card_containers = soup.select(
        "div[class*='cardWrapper' i], div[class*='featuredWrapper' i], article[class*='listingCard' i]"
    )
    for container in card_containers:
        anchor = container.find(
            "a", href=re.compile(r"(/property/|/project/|auction\.wasalt\.sa/en/auction/)", re.I)
        )
        if not anchor or not anchor.get("href"):
            continue

        abs_url = urljoin("https://wasalt.sa", anchor["href"].strip()).split("#")[0]
        if abs_url in seen_urls:
            continue
        seen_urls.add(abs_url)

        rec_type = classify_url(abs_url)
        num_id = extract_numeric_id(abs_url)
        prefix = {"property": "wasalt", "project": "ws_proj", "auction": "auction"}[rec_type]

        card_ld = None
        ld_tag = container.find("script", type="application/ld+json")
        if ld_tag and ld_tag.string:
            try:
                card_ld = json.loads(ld_tag.string)
            except Exception:
                card_ld = None

        cards.append(
            {
                "record_id": f"{prefix}_{num_id}",
                "numeric_id": num_id,
                "record_type": rec_type,
                "source_url": abs_url,
                "search_page_url": page_url,
                "card_title": clean_text(anchor.get("title") or anchor.get_text(" ", strip=True)),
                "card_raw_text": clean_text(container.get_text(" | ", strip=True)),
                "card_json_ld": card_ld,
            }
        )

    return cards, total_pages


# ---------------------------------------------------------------------------
# Parser 1: Standard Property -> wasalt_properties
# ---------------------------------------------------------------------------

def parse_property_detail(
    html: str, url: str, card: dict[str, Any], diag: DiagnosticTracker
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    next_data = extract_next_data(soup)
    json_ld = extract_json_ld(soup)

    clean_soup = BeautifulSoup(html, "lxml")
    for tag in clean_soup.select("script, style, noscript, iframe, header, footer, [class*='map' i]"):
        tag.decompose()

    lines = [clean_text(x) for x in clean_soup.get_text("\n", strip=True).splitlines() if clean_text(x)]
    raw_source_text = "\n".join(lines)

    # Note: "Land Area" is intentionally excluded from next-line kv because Wasalt puts the value ABOVE "Land Area" and Price BELOW it
    known_labels = [
        "Property usage", "Property Type", "Type", "Age", "Property Age", "Facade", "Facing",
        "Street", "Street Name", "Street Width", "Plan number", "Land number", "Plot number",
        "Built-up Area", "Bedrooms", "Bathrooms", "Living rooms", "Guest rooms", "Furnishing",
        "Ad source", "Status", "Issue Date", "Expiring on", "FAL License No",
        "REGA Ad License No", "Ref no.", "Ref No", "Electricity", "Water", "Sewage",
    ]
    kv = extract_strict_label_values(lines, known_labels)

    ld_desc = None
    ld_price = None
    for block in json_ld:
        if block.get("description") and not ld_desc:
            ld_desc = strip_html_tags(block.get("description"))
        offers = block.get("offers")
        if isinstance(offers, dict) and offers.get("price") and not ld_price:
            ld_price = parse_number(offers.get("price"))

    prop_props = next_data.get("props", {}).get("pageProps", {}) if isinstance(next_data, dict) else {}
    next_keys = collect_json_keys(prop_props)

    h1 = clean_soup.find("h1")
    title = clean_text(h1.get_text(" ", strip=True)) if h1 else card.get("card_title")

    # Location
    loc_str = None
    if card.get("card_raw_text"):
        parts = [p.strip() for p in card["card_raw_text"].split("|")]
        if len(parts) >= 3:
            loc_str = parts[2]
    district, region_zone, city = parse_location_parts(loc_str)
    city = clean_text(deep_find_first(prop_props, ("cityName", "city_name", "city"))) or city
    district = clean_text(deep_find_first(prop_props, ("districtName", "district_name", "district"))) or district

    # Property type
    prop_type = clean_text(deep_find_first(prop_props, ("propertyTypeName", "propertyType", "subCategory")))
    if not prop_type:
        m_type = re.search(r"/sale/([a-z-]+?)-\d", url.lower())
        prop_type = m_type.group(1).replace("-", " ").title() if m_type else kv.get("Property Type")
    is_land = bool(prop_type and "land" in prop_type.lower())

    # Description (strip HTML <p> tags cleanly!)
    description = strip_html_tags(
        deep_find_first(prop_props, ("descriptionEn", "description_en", "description", "propertyDescription"))
    )
    if not description:
        for i, line in enumerate(lines):
            if line.lower() in ("description", "about property", "property description") and i + 1 < len(lines):
                chunk = []
                for nxt in lines[i + 1 : i + 10]:
                    if nxt.lower() in ("additional information", "rega information", "location", "similar properties", "see more", "see less"):
                        break
                    chunk.append(nxt)
                if chunk:
                    description = strip_html_tags(" ".join(chunk))
                break
    if not description:
        description = ld_desc

    # Price
    price_sar = (
        parse_number(deep_find_first(prop_props, ("price", "salePrice", "priceAmount")))
        or ld_price
        or parse_number(card.get("card_raw_text"))
    )

    # Strip bottom related/recommended listing cards so regex & diagnostics only inspect THIS listing
    hero_and_body_text = re.split(
        r"\n(?:Similar|Related|Recommended|More)\s+(?:Properties|Listings|Apartments|Villas|Lands)\b"
        r"|\nWhatsApp\nCall\n(?:Apartment|Villa|Land|Duplex|Floor|Building)\b",
        raw_source_text,
        maxsplit=1,
        flags=re.I,
    )[0]

    # Area & Land Area (handles both "191\nSQM\nbuilt-up area" and "960 SQM\nLand Area")
    m_title_sqm = re.search(r"([\d,]+(?:\.\d+)?)\s*SQM", title or "", re.I)
    m_builtup_above = re.search(r"([\d,]+(?:\.\d+)?)\s*\n?\s*(?:SQM|m2|m²)\s*\n\s*built[- ]?up\s*area", hero_and_body_text, re.I)
    m_land_above = re.search(r"([\d,]+(?:\.\d+)?)\s*\n?\s*(?:SQM|m2|m²)\s*\n\s*land\s*area", hero_and_body_text, re.I)
    area_sqm = parse_number(
        deep_find_first(prop_props, ("builtUpArea", "area", "totalArea", "propertyArea"))
        or (m_builtup_above.group(1) if m_builtup_above else None)
        or (m_land_above.group(1) if m_land_above else None)
        or (m_title_sqm.group(1) if m_title_sqm else None)
    )
    land_area_sqm = parse_number(
        deep_find_first(prop_props, ("landArea", "plotArea"))
        or (m_land_above.group(1) if m_land_above else None)
    )
    # Sanity guard: if land_area_sqm accidentally equals price_sar (> 100,000 while area_sqm is smaller), discard
    if land_area_sqm and price_sar and land_area_sqm == price_sar and area_sqm and area_sqm != price_sar:
        land_area_sqm = area_sqm if is_land else None
    if is_land and land_area_sqm is None:
        land_area_sqm = area_sqm

    # Broker agency (JSON -> DOM line right before FAL License No)
    broker_agency = clean_text(
        deep_find_first(
            prop_props,
            ("agencyName", "companyName", "advertiserName", "brokerName", "officeName", "agentName", "ownerName"),
        )
    )
    if not broker_agency:
        for i, line in enumerate(lines):
            if line.lower().startswith("fal license") and i > 0:
                cand = lines[i - 1]
                if len(cand) < 100 and not re.match(r"^[\d\s\W]+$", cand) and cand.lower() not in ("whatsapp", "call", "contact"):
                    broker_agency = cand
                    break

    # Street name & Property age (with strict guards against header collisions)
    street_name = clean_text(deep_find_first(prop_props, ("streetName", "street_name"))) or kv.get("Street Name") or kv.get("Street")
    if street_name and street_name.lower() in ("for sale", "for rent", "sale", "rent"):
        street_name = None

    prop_age = clean_text(deep_find_first(prop_props, ("propertyAge", "age", "buildingAge"))) or kv.get("Property Age") or kv.get("Age")
    if prop_age and prop_age.lower() in ("residential", "commercial", "mixed"):
        prop_age = None

    utilities = [u for u in ("Electricity", "Water", "Sewage", "Fiber") if kv.get(u, "").lower() == "yes"]

    def strict_regex(pattern: str) -> str | None:
        m = re.search(pattern, raw_source_text, re.IGNORECASE)
        return clean_text(m.group(1)) if m else None

    append_jsonl(
        RAW_DUMP_FILES["wasalt_properties"],
        [{
            "record_id": card["record_id"],
            "source_url": url,
            "raw_source_text": raw_source_text,
            "next_data": prop_props,
            "json_ld": json_ld,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }],
    )
    row = {
        "record_id": card["record_id"],
        "ref_no": clean_text(deep_find_first(prop_props, ("refNo", "referenceNumber", "ref_no"))) or kv.get("Ref no.") or strict_regex(r"Ref\s*no\.?\s*[:#]?\s*([A-Za-z0-9-]+)"),
        "source_url": url,
        "search_page_url": card.get("search_page_url"),
        "listing_purpose": "buy",
        "property_type": prop_type,
        "property_usage": clean_text(deep_find_first(prop_props, ("propertyUsage", "usage"))) or kv.get("Property usage"),
        "title": title,
        "country": "Saudi Arabia",
        "city": city,
        "region_zone": region_zone,
        "district": district,
        "price_sar": price_sar,
        "area_sqm": area_sqm,
        "land_area_sqm": land_area_sqm,
        "bedrooms": parse_int(
            deep_find_first(prop_props, ("bedrooms", "bedroomCount"))
            or kv.get("Bedrooms")
            or (re.search(r"\b(\d+)\s*\n\s*bedrooms?\b", hero_and_body_text, re.I).group(1) if re.search(r"\b(\d+)\s*\n\s*bedrooms?\b", hero_and_body_text, re.I) else None)
            or (re.search(r"with\s+(\d+)\s+bedrooms?\b", title or "", re.I).group(1) if re.search(r"with\s+(\d+)\s+bedrooms?\b", title or "", re.I) else None)
        ),
        "bathrooms": parse_int(
            deep_find_first(prop_props, ("bathrooms", "bathroomCount"))
            or kv.get("Bathrooms")
            or (re.search(r"\b(\d+)\s*\n\s*bathrooms?\b", hero_and_body_text, re.I).group(1) if re.search(r"\b(\d+)\s*\n\s*bathrooms?\b", hero_and_body_text, re.I) else None)
        ),
        "living_rooms": parse_int(
            deep_find_first(prop_props, ("livingRooms", "halls"))
            or kv.get("Living rooms")
            or (re.search(r"\b(\d+)\s*\n\s*living\s*rooms?\b", hero_and_body_text, re.I).group(1) if re.search(r"\b(\d+)\s*\n\s*living\s*rooms?\b", hero_and_body_text, re.I) else None)
        ),
        "guest_rooms": parse_int(
            deep_find_first(prop_props, ("guestRooms", "majlis"))
            or kv.get("Guest rooms")
            or (re.search(r"\b(\d+)\s*\n\s*guest\s*rooms?\b", hero_and_body_text, re.I).group(1) if re.search(r"\b(\d+)\s*\n\s*guest\s*rooms?\b", hero_and_body_text, re.I) else None)
        ),
        "furnishing_status": clean_text(deep_find_first(prop_props, ("furnishing", "furnishedStatus"))) or kv.get("Furnishing"),
        "property_age": prop_age,
        "facade": clean_text(deep_find_first(prop_props, ("facade", "facing", "direction"))) or kv.get("Facade") or strict_regex(r"Facing\s+(North East|North West|South East|South West|North|South|East|West)"),
        "street_width_m": parse_number(deep_find_first(prop_props, ("streetWidth", "street_width")) or kv.get("Street Width") or strict_regex(r"([\d\.]+)\s*m\s*Width\s*Street")),
        "street_name": street_name,
        "plan_number": clean_text(deep_find_first(prop_props, ("planNumber", "plan_number"))) or kv.get("Plan number"),
        "land_number": clean_text(deep_find_first(prop_props, ("landNumber", "plotNumber", "land_number"))) or kv.get("Land number") or kv.get("Plot number"),
        "utilities_json": json.dumps(utilities, ensure_ascii=False),
        "broker_agency": broker_agency,
        "fal_license_no": clean_text(deep_find_first(prop_props, ("falLicenseNo", "falLicenseNumber"))) or kv.get("FAL License No") or strict_regex(r"FAL\s*License\s*No\.?[:\s]*(\d+)"),
        "rega_ad_license_no": clean_text(deep_find_first(prop_props, ("adLicenseNumber", "regaLicenseNo", "regaAdLicenseNo"))) or kv.get("REGA Ad License No") or strict_regex(r"REGA\s*Ad\s*License\s*No\.?[:\s]*([A-Za-z0-9-]+)"),
        "rega_status": clean_text(deep_find_first(prop_props, ("adLicenseStatus", "regaStatus"))) or kv.get("Status"),
        "rega_issue_date": clean_text(deep_find_first(prop_props, ("adLicenseIssueDate", "issueDate"))) or kv.get("Issue Date"),
        "rega_expiry_date": clean_text(deep_find_first(prop_props, ("adLicenseExpiryDate", "expiryDate"))) or kv.get("Expiring on"),
        "ad_source": clean_text(deep_find_first(prop_props, ("adSource", "sourceName"))) or kv.get("Ad source"),
        "views_last_7_days": parse_int(strict_regex(r"(\d+)\s*people\s*viewed")),
        "last_updated": strict_regex(r"Last\s*updated[:\s]*([^\n|]+)"),
        "description": description,
        "extra_attributes_json": json.dumps(kv, ensure_ascii=False),
        "missing_fields_json": "[]",
        "raw_source_text": truncate_raw(raw_source_text),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    # Automated Field-Level Diagnostics
    field_specs = {
        "ref_no": {"checked": ["__NEXT_DATA__.refNo", "kv['Ref no.']", "regex:Ref no"], "probe_keywords": ["ref no", "reference"]},
        "price_sar": {"checked": ["__NEXT_DATA__.price", "json_ld.offers.price", "card_raw_text"], "probe_keywords": ["sar", "price"]},
        "area_sqm": {"checked": ["__NEXT_DATA__.area", "regex:SQM"], "probe_keywords": ["sqm", "land area"]},
        "land_area_sqm": {"checked": ["__NEXT_DATA__.landArea", "regex:SQM\\nLand Area"], "probe_keywords": ["land area"]},
        "bedrooms": {"checked": ["__NEXT_DATA__.bedrooms", "kv['Bedrooms']"], "probe_keywords": ["bedroom"], "not_applicable": is_land},
        "bathrooms": {"checked": ["__NEXT_DATA__.bathrooms", "kv['Bathrooms']"], "probe_keywords": ["bathroom"], "not_applicable": is_land},
        "property_age": {"checked": ["__NEXT_DATA__.propertyAge", "kv['Property Age']"], "probe_keywords": ["property age", "years old"], "not_applicable": is_land},
        "facade": {"checked": ["__NEXT_DATA__.facade", "kv['Facade']", "regex:Facing"], "probe_keywords": ["facade", "facing"]},
        "street_width_m": {"checked": ["__NEXT_DATA__.streetWidth", "kv['Street Width']"], "probe_keywords": ["street width", "width street"]},
        "street_name": {"checked": ["__NEXT_DATA__.streetName", "kv['Street Name']"], "probe_keywords": ["street name"]},
        "plan_number": {"checked": ["__NEXT_DATA__.planNumber", "kv['Plan number']"], "probe_keywords": ["plan number"]},
        "land_number": {"checked": ["__NEXT_DATA__.landNumber", "kv['Land number']"], "probe_keywords": ["land number", "plot number"]},
        "broker_agency": {"checked": ["__NEXT_DATA__.agencyName/companyName", "line_above:FAL License"], "probe_keywords": ["fal license", "advertiser", "company"]},
        "fal_license_no": {"checked": ["__NEXT_DATA__.falLicenseNo", "kv['FAL License No']"], "probe_keywords": ["fal license"]},
        "rega_ad_license_no": {"checked": ["__NEXT_DATA__.adLicenseNumber", "kv['REGA Ad License No']"], "probe_keywords": ["rega ad license"]},
        "description": {"checked": ["__NEXT_DATA__.description", "DOM:Description", "json_ld.description"], "probe_keywords": ["description", "about property"]},
    }
    misses = diag.audit_record("wasalt_properties", row["record_id"], url, row, hero_and_body_text, field_specs, next_keys)
    row["missing_fields_json"] = json.dumps([m["field"] for m in misses], ensure_ascii=False)
    return row


# ---------------------------------------------------------------------------
# Parser 2: Project + Child Units -> wasalt_projects & wasalt_project_units
# ---------------------------------------------------------------------------

def parse_project_and_units(
    html: str,
    url: str,
    card: dict[str, Any],
    intercepted_json_payloads: list[Any],
    diag: DiagnosticTracker,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    soup = BeautifulSoup(html, "lxml")
    next_data = extract_next_data(soup)

    clean_soup = BeautifulSoup(html, "lxml")
    for tag in clean_soup.select(
        "script, style, noscript, iframe, header, footer, "
        "[class*='map' i], [class*='similar' i], [class*='related' i], "
        "[class*='recommend' i], [class*='carousel' i], [class*='slider' i]"
    ):
        tag.decompose()

    lines = [clean_text(x) for x in clean_soup.get_text("\n", strip=True).splitlines() if clean_text(x)]
    raw_source_text = "\n".join(lines)

    known_labels = [
        "Electricity", "Water", "Residency Type", "Ref no.", "Ref No", "Last updated",
        "Units Available", "Completion Status", "Developer",
    ]
    kv = extract_strict_label_values(lines, known_labels)

    proj_props = next_data.get("props", {}).get("pageProps", {}) if isinstance(next_data, dict) else {}
    next_keys = collect_json_keys(proj_props)
    project_id = card["record_id"]

    h1 = clean_soup.find("h1")
    project_name = clean_text(deep_find_first(proj_props, ("projectName", "name", "title"))) or (
        clean_text(h1.get_text(" ", strip=True)) if h1 else card.get("card_title")
    )

    price_min, price_max = None, None
    m_pr = re.search(r"([\d,\.]+\s*[kKmM]?)\s*-\s*([\d,\.]+\s*[kKmM]?)", card.get("card_raw_text") or "")
    if m_pr:
        price_min, price_max = parse_number(m_pr.group(1)), parse_number(m_pr.group(2))
    if price_min is None:
        m_start = re.search(r"starts?\s*from\s*([\d,\.]+\s*[kKmM]?)", raw_source_text, re.I)
        if m_start:
            price_min = parse_number(m_start.group(1))

    area_min, area_max = None, None
    m_ar = re.search(r"([\d,\.]+)\s*-\s*([\d,\.]+)\s*SQM", raw_source_text, re.I)
    if m_ar:
        area_min, area_max = parse_number(m_ar.group(1)), parse_number(m_ar.group(2))

    avail_units, total_units = None, None
    m_units = re.search(r"Units\s*Available\s*\(\s*(\d+)\s*of\s*(\d+)\s*\)", raw_source_text, re.I)
    if m_units:
        avail_units, total_units = int(m_units.group(1)), int(m_units.group(2))
    else:
        m_u2 = re.search(r"Units\s*\(\s*(\d+)\s*\)", raw_source_text, re.I)
        if m_u2:
            avail_units = total_units = int(m_u2.group(1))

    loc_str = None
    if card.get("card_raw_text"):
        for p in card["card_raw_text"].split("|"):
            if "," in p and not re.search(r"\d", p):
                loc_str = p.strip()
                break
    district, region_zone, city = parse_location_parts(loc_str)
    city = clean_text(deep_find_first(proj_props, ("cityName", "city"))) or city

    about_project = strip_html_tags(deep_find_first(proj_props, ("aboutProject", "descriptionEn", "description")))
    if not about_project:
        for i, line in enumerate(lines):
            if line.lower() == "about project" and i + 1 < len(lines):
                chunk = []
                for nxt in lines[i + 1 : i + 12]:
                    if nxt.lower().startswith(("ref no", "units available", "whatsapp", "call")):
                        break
                    chunk.append(nxt)
                about_project = strip_html_tags(" ".join(chunk))
                break

    developer_name = clean_text(deep_find_first(proj_props, ("developerName", "companyName", "agencyName")))
    if not developer_name:
        for i, line in enumerate(lines):
            if line.lower().startswith("ref no") and i > 0:
                cand = lines[i - 1]
                if len(cand) < 80 and not cand.isdigit():
                    developer_name = cand
                    break

    m_status = re.search(r"\b(Under Construction|Ready|Completed|Off Plan|Sold Out)\b", raw_source_text, re.I)
    m_ref = re.search(r"Ref\s*no\.?\s*[:#]?\s*([A-Za-z0-9-]+)", raw_source_text, re.I)
    m_views = re.search(r"(\d+)\s*people\s*viewed", raw_source_text, re.I)
    m_upd = re.search(r"Last\s*updated\s*([^\n]+)", raw_source_text, re.I)

    append_jsonl(
        RAW_DUMP_FILES["wasalt_projects"],
        [{
            "project_id": project_id,
            "source_url": url,
            "raw_source_text": raw_source_text,
            "next_data": proj_props,
            "intercepted_json": intercepted_json_payloads,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }],
    )
    project_row = {
        "project_id": project_id,
        "ref_no": clean_text(m_ref.group(1)) if m_ref else card["numeric_id"],
        "source_url": url,
        "search_page_url": card.get("search_page_url"),
        "project_name": project_name,
        "developer_name": developer_name,
        "country": "Saudi Arabia",
        "city": city,
        "region_zone": region_zone,
        "district": district,
        "completion_status": clean_text(m_status.group(1)) if m_status else None,
        "price_min_sar": price_min,
        "price_max_sar": price_max,
        "area_min_sqm": area_min,
        "area_max_sqm": area_max,
        "total_units": total_units,
        "available_units": avail_units,
        "residency_type": kv.get("Residency Type"),
        "electricity": kv.get("Electricity"),
        "water": kv.get("Water"),
        "views_last_7_days": int(m_views.group(1)) if m_views else None,
        "last_updated": clean_text(m_upd.group(1)) if m_upd else None,
        "about_project": about_project,
        "extra_attributes_json": json.dumps(kv, ensure_ascii=False),
        "missing_fields_json": "[]",
        "raw_source_text": truncate_raw(raw_source_text),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    proj_specs = {
        "developer_name": {"checked": ["__NEXT_DATA__.developerName", "line_above:Ref no"], "probe_keywords": ["ref no", "developer"]},
        "completion_status": {"checked": ["regex:Under Construction|Ready|Completed"], "probe_keywords": ["construction", "ready", "completed"]},
        "price_min_sar": {"checked": ["card_raw_text range", "regex:starts from"], "probe_keywords": ["starts from", "price"]},
        "total_units": {"checked": ["regex:Units Available"], "probe_keywords": ["units"]},
        "about_project": {"checked": ["__NEXT_DATA__.aboutProject", "DOM:About Project"], "probe_keywords": ["about project"]},
    }
    p_misses = diag.audit_record("wasalt_projects", project_id, url, project_row, raw_source_text, proj_specs, next_keys)
    project_row["missing_fields_json"] = json.dumps([m["field"] for m in p_misses], ensure_ascii=False)

    # --- Extract Child Units (Table: wasalt_project_units) ---
    units: list[dict[str, Any]] = []
    seen_unit_keys: set[str] = set()

    unit_key_hints = {"unitno", "unit_no", "unitnumber", "refno", "ref_no", "price", "area", "landarea", "propertytype"}
    raw_unit_dicts: list[dict[str, Any]] = []
    for payload in [proj_props, *intercepted_json_payloads]:
        raw_unit_dicts.extend(deep_find_all_dicts_with_keys(payload, unit_key_hints))

    for idx, u in enumerate(raw_unit_dicts, start=1):
        u_ref = clean_text(deep_find_first(u, ("refNo", "ref_no", "referenceNo", "propertyRefNo", "id")))
        u_no = clean_text(deep_find_first(u, ("unitNo", "unit_no", "unitNumber")))
        u_price = parse_number(
            deep_find_first(
                u,
                ("price", "salePrice", "sellingPrice", "totalPrice", "unitPrice", "priceAmount", "amount", "formattedPrice"),
            )
        )

        # Extract area from nested icon-spec lists (e.g. {"unit": "SQM", "iconClass": "plot-area", "value": 1920})
        u_area = None
        for v in u.values():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and (
                        str(item.get("unit", "")).upper() == "SQM"
                        or "area" in str(item.get("iconClass", "")).lower()
                    ):
                        u_area = parse_number(
                            item.get("value") or item.get("count") or item.get("label") or item.get("text")
                        )
                        if u_area is not None:
                            break
            if u_area is not None:
                break
        if u_area is None:
            u_area = parse_number(
                deep_find_first(u, ("area", "landArea", "plotArea", "builtUpArea", "carpetArea", "totalArea", "size"))
            )

        if not (u_ref or u_no or u_price):
            continue
        dedup_key = f"{u_ref or ''}|{u_no or ''}|{u_price or ''}|{u_area or ''}"
        if dedup_key in seen_unit_keys:
            continue
        seen_unit_keys.add(dedup_key)

        u_raw_full = json.dumps(u, ensure_ascii=False)
        u_id = f"{project_id}_u_{u_no or u_ref or idx}"
        append_jsonl(
            RAW_DUMP_FILES["wasalt_project_units"],
            [{"unit_id": u_id, "project_id": project_id, "source_url": url, "raw_unit_json": u}],
        )

        u_slug = deep_find_first(u, ("url", "slug", "propertyUrl"))
        unit_url = urljoin("https://wasalt.sa", str(u_slug)) if u_slug and "/" in str(u_slug) else url
        u_ptype = clean_text(deep_find_first(u, ("propertyType", "propertySubType", "propertyMainType", "type")))
        unit_row = {
            "unit_id": u_id,
            "project_id": project_id,
            "unit_ref_no": u_ref,
            "unit_no": u_no,
            "status": clean_text(deep_find_first(u, ("status", "availabilityStatus", "unitStatus")) or "Available"),
            "purpose": clean_text(deep_find_first(u, ("purpose", "propertyFor", "listingPurpose")) or "For Sale"),
            "title": clean_text(deep_find_first(u, ("title", "propertyTitle", "name")) or f"{u_ptype or 'Unit'} {u_area or ''} SQM"),
            "property_type": u_ptype,
            "price_sar": u_price,
            "area_sqm": u_area,
            "bedrooms": parse_int(deep_find_first(u, ("bedrooms", "bedroomCount"))),
            "bathrooms": parse_int(deep_find_first(u, ("bathrooms", "bathroomCount"))),
            "highlights": strip_html_tags(deep_find_first(u, ("highlights", "description", "note", "tagline"))),
            "source_url": unit_url,
            "missing_fields_json": "[]",
            "raw_source_text": truncate_raw(u_raw_full),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        units.append(unit_row)

    # DOM fallback for unit cards
    if not units:
        for idx, card_el in enumerate(clean_soup.find_all(["div", "article", "li"]), start=1):
            txt = clean_text(card_el.get_text(" | ", strip=True)) or ""
            if "Unit No:" in txt and "Ref No:" in txt and len(txt) < 700:
                m_u_ref = re.search(r"Ref\s*No:\s*([A-Za-z0-9-]+)", txt, re.I)
                m_u_no = re.search(r"Unit\s*No:\s*([A-Za-z0-9-]+)", txt, re.I)
                m_u_area = re.search(r"([\d,]+(?:\.\d+)?)\s*sqm", txt, re.I)
                m_u_price = re.search(r"\b(\d{1,3}(?:,\d{3})+)\b", txt)

                u_ref = m_u_ref.group(1) if m_u_ref else None
                u_no = m_u_no.group(1) if m_u_no else None
                dedup_key = f"{u_ref}|{u_no}"
                if dedup_key in seen_unit_keys:
                    continue
                seen_unit_keys.add(dedup_key)

                m_pt = re.search(r"\b(Land|Villa|Apartment|Townhouse|Floor|Duplex)\b", txt, re.I)
                units.append(
                    {
                        "unit_id": f"{project_id}_u_{u_no or u_ref or idx}",
                        "project_id": project_id,
                        "unit_ref_no": u_ref,
                        "unit_no": u_no,
                        "status": "Available" if "available" in txt.lower() else None,
                        "purpose": "For Sale" if "for sale" in txt.lower() else ("For Rent" if "for rent" in txt.lower() else None),
                        "title": clean_text(re.search(r"(Land|Villa|Apartment|Townhouse|Floor|Duplex)[^|]*SQM", txt, re.I).group(0)) if re.search(r"(Land|Villa|Apartment|Townhouse|Floor|Duplex)[^|]*SQM", txt, re.I) else None,
                        "property_type": m_pt.group(1).title() if m_pt else None,
                        "price_sar": parse_number(m_u_price.group(1)) if m_u_price else None,
                        "area_sqm": parse_number(m_u_area.group(1)) if m_u_area else None,
                        "bedrooms": parse_int(re.search(r"(\d+)\s*Bed", txt, re.I).group(1)) if re.search(r"(\d+)\s*Bed", txt, re.I) else None,
                        "bathrooms": parse_int(re.search(r"(\d+)\s*Bath", txt, re.I).group(1)) if re.search(r"(\d+)\s*Bath", txt, re.I) else None,
                        "highlights": txt[:350],
                        "source_url": url,
                        "missing_fields_json": "[]",
                        "raw_source_text": txt[:2000],
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    }
                )

    for u_row in units:
        u_specs = {
            "unit_ref_no": {"checked": ["unit_json.refNo", "regex:Ref No:"], "probe_keywords": ["ref no"]},
            "unit_no": {"checked": ["unit_json.unitNo", "regex:Unit No:"], "probe_keywords": ["unit no"]},
            "price_sar": {"checked": ["unit_json.price", "regex:price"], "probe_keywords": ["sar", ","]},
            "area_sqm": {"checked": ["unit_json.area", "regex:sqm"], "probe_keywords": ["sqm"]},
        }
        u_misses = diag.audit_record("wasalt_project_units", u_row["unit_id"], u_row["source_url"], u_row, u_row["raw_source_text"], u_specs)
        u_row["missing_fields_json"] = json.dumps([m["field"] for m in u_misses], ensure_ascii=False)

    return project_row, units


# ---------------------------------------------------------------------------
# Parser 3: Auction Asset -> wasalt_auctions
# ---------------------------------------------------------------------------

def parse_auction_detail(
    html: str, url: str, card: dict[str, Any], diag: DiagnosticTracker
) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    next_data = extract_next_data(soup)

    clean_soup = BeautifulSoup(html, "lxml")
    for tag in clean_soup.select("script, style, noscript, iframe, header, footer, [class*='map' i]"):
        tag.decompose()

    lines = [clean_text(x) for x in clean_soup.get_text("\n", strip=True).splitlines() if clean_text(x)]
    raw_source_text = "\n".join(lines)
    auc_props = next_data.get("props", {}).get("pageProps", {}) if isinstance(next_data, dict) else {}
    next_keys = collect_json_keys(auc_props)

    known_labels = [
        "Starting Bid", "Highest Bid", "Current Bid", "Deposit Amount", "Bid Increment",
        "Sales Agent", "FAL Licensed No.", "FAL License No.", "Total Bidders", "Bids", "Followers",
    ]
    kv = extract_strict_label_values(lines, known_labels)

    h1 = clean_soup.find("h1")
    title = clean_text(h1.get_text(" ", strip=True)) if h1 else card.get("card_title")

    group_anchor = soup.find("a", href=re.compile(r"/auction-group/\d+", re.I))
    auction_group_url = urljoin("https://auction.wasalt.sa", group_anchor["href"]) if group_anchor else None
    auction_group_id = extract_numeric_id(auction_group_url) if auction_group_url else None
    auction_group_name = clean_text(group_anchor.get_text(" ", strip=True).replace("Back to", "")) if group_anchor else None

    m_asset_no = re.search(r"\bAsset\s*(\d+)\b", raw_source_text, re.I)

    # Comma-aware area extraction (3,328 SQM -> 3328.0)
    area_sqm = parse_number(deep_find_first(auc_props, ("area", "landArea", "builtUpArea")))
    if area_sqm is None:
        m_area = re.search(r"([\d,]+(?:\.\d+)?)\s*(?:SQM|m2|m²|م2)", raw_source_text, re.I)
        if m_area:
            area_sqm = parse_number(m_area.group(1))

    status = clean_text(deep_find_first(auc_props, ("status", "auctionStatus", "assetStatus")))
    if not status and card.get("card_raw_text"):
        m_st = re.search(r"\b(Upcoming|Live|Ongoing|Ended|Closed|Sold|Cancelled)\b", card["card_raw_text"], re.I)
        if m_st:
            status = m_st.group(1).title()

    loc_str = None
    if card.get("card_raw_text"):
        for part in card["card_raw_text"].split("|"):
            if "," in part and "Deposit" not in part and "Bid" not in part:
                loc_str = part.strip()
                break
    district, _, city = parse_location_parts(loc_str)
    city = clean_text(deep_find_first(auc_props, ("cityName", "city"))) or city

    starts_at = clean_text(deep_find_first(auc_props, ("startDate", "startsAt", "auctionStartDate", "start_date")))
    ends_at = clean_text(deep_find_first(auc_props, ("endDate", "endsAt", "auctionEndDate", "end_date")))

    m_fal = re.search(r"FAL\s*Licensed?\s*No\.?[:\s]*(\d+)", raw_source_text, re.I)
    sales_agent = clean_text(deep_find_first(auc_props, ("salesAgentName", "agentName"))) or kv.get("Sales Agent")

    def clean_bid(val: str | None) -> float | None:
        if not val or "not added" in val.lower():
            return None
        return parse_number(val)

    append_jsonl(
        RAW_DUMP_FILES["wasalt_auctions"],
        [{
            "asset_id": card["record_id"],
            "source_url": url,
            "raw_source_text": raw_source_text,
            "next_data": auc_props,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }],
    )
    row = {
        "asset_id": card["record_id"],
        "auction_group_id": auction_group_id,
        "auction_group_name": auction_group_name,
        "auction_group_url": auction_group_url,
        "asset_number": int(m_asset_no.group(1)) if m_asset_no else None,
        "source_url": url,
        "search_page_url": card.get("search_page_url"),
        "title": title,
        "asset_category": clean_text(deep_find_first(auc_props, ("category", "assetCategory"))) or "real_estate",
        "city": city,
        "district": district,
        "status": status,
        "area_sqm": area_sqm,
        "deposit_amount_sar": parse_number(deep_find_first(auc_props, ("depositAmount", "deposit"))) or clean_bid(kv.get("Deposit Amount")),
        "starting_bid_sar": parse_number(deep_find_first(auc_props, ("startingBid", "startPrice"))) or clean_bid(kv.get("Starting Bid")),
        "highest_bid_sar": parse_number(deep_find_first(auc_props, ("highestBid", "currentBid"))) or clean_bid(kv.get("Highest Bid") or kv.get("Current Bid")),
        "bid_increment_sar": parse_number(deep_find_first(auc_props, ("bidIncrement", "incrementAmount"))) or clean_bid(kv.get("Bid Increment")),
        "total_bidders": parse_int(deep_find_first(auc_props, ("totalBidders", "biddersCount")) or kv.get("Total Bidders")),
        "bids_count": parse_int(deep_find_first(auc_props, ("bidsCount", "totalBids")) or kv.get("Bids")),
        "followers_count": parse_int(deep_find_first(auc_props, ("followersCount", "followers")) or kv.get("Followers")),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "sales_agent_name": sales_agent,
        "sales_agent_fal_license": clean_text(m_fal.group(1)) if m_fal else None,
        "extra_attributes_json": json.dumps(kv, ensure_ascii=False),
        "missing_fields_json": "[]",
        "raw_source_text": truncate_raw(raw_source_text),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    auc_specs = {
        "status": {"checked": ["__NEXT_DATA__.status", "card_raw_text"], "probe_keywords": ["upcoming", "live", "ended", "sold"]},
        "area_sqm": {"checked": ["__NEXT_DATA__.area", "regex:SQM|م2"], "probe_keywords": ["sqm", "م2"]},
        "deposit_amount_sar": {"checked": ["__NEXT_DATA__.depositAmount", "kv['Deposit Amount']"], "probe_keywords": ["deposit"]},
        "starting_bid_sar": {
            "checked": ["__NEXT_DATA__.startingBid", "kv['Starting Bid']"],
            "probe_keywords": ["starting bid"],
            "not_applicable": "not added" in (kv.get("Starting Bid") or "").lower(),
        },
        "starts_at": {"checked": ["__NEXT_DATA__.startDate/startsAt"], "probe_keywords": ["starts", "2026"]},
        "ends_at": {"checked": ["__NEXT_DATA__.endDate/endsAt"], "probe_keywords": ["ends", "2026"]},
        "sales_agent_name": {"checked": ["__NEXT_DATA__.salesAgentName", "kv['Sales Agent']"], "probe_keywords": ["sales agent"]},
        "sales_agent_fal_license": {"checked": ["regex:FAL Licensed No"], "probe_keywords": ["fal"]},
    }
    misses = diag.audit_record("wasalt_auctions", row["asset_id"], url, row, raw_source_text, auc_specs, next_keys)
    row["missing_fields_json"] = json.dumps([m["field"] for m in misses], ensure_ascii=False)
    return row
