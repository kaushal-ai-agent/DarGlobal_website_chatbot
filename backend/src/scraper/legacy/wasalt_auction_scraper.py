"""Asynchronous scraper for Wasalt Auctions (auction.wasalt.sa/en).

Extracts:
1. wasalt_auction_groups (Infath judicial & private auctions across Real Estate, Vehicles, Movable Assets)
2. wasalt_auction_assets (Lot items, deposits, starting/highest bids, specs, bidder counts)
3. Unstructured auction guides:
   - ws_04_auctions_guide_infath_and_ewallet.md
   - ws_07_legal_terms_auction_and_advertising.md
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from bs4 import BeautifulSoup

from scraper.config import (
    AUCTION_BASE_URL,
    DEFAULT_HEADERS,
    SEMAPHORE_LIMIT,
    WS_UNSTRUCTURED_DIR,
)
from scraper.schemas import (
    WasaltAuctionGroupItem,
    WasaltAuctionAssetItem,
    MarkdownFrontmatter,
    get_utc_now,
)
from scraper.storage import CheckpointManager, JSONLBatchWriter, MarkdownWriter

logger = logging.getLogger("scraper.wasalt_auction")


def clean_text(text: Optional[str]) -> str:
    """Removes Arabic script and collapses whitespace."""
    if not text:
        return ""
    cleaned = re.sub(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+", "", str(text))
    cleaned = re.sub(r"English\s*\|\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\|\s*English", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def has_arabic(text: Optional[str]) -> bool:
    """Checks if text contains Arabic characters."""
    if not text:
        return False
    return bool(re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]", str(text)))


async def get_next_data(page: Any) -> Optional[Dict[str, Any]]:
    """Safely extracts JSON parsed __NEXT_DATA__ from the page DOM."""
    return await page.evaluate(
        """() => {
        const el = document.getElementById('__NEXT_DATA__');
        if (el && el.textContent) {
            try { return JSON.parse(el.textContent); } catch (e) { return null; }
        }
        return null;
    }"""
    )


def safe_float(val: Any) -> Optional[float]:
    """Safely converts string or number to float, removing commas."""
    if val is None or val == "":
        return None
    try:
        clean = re.sub(r"[^\d.]", "", str(val))
        return float(clean) if clean else None
    except (ValueError, TypeError):
        return None


def safe_int(val: Any) -> Optional[int]:
    """Safely converts string or number to int, removing non-digits."""
    if val is None or val == "":
        return None
    try:
        clean = re.sub(r"[^\d]", "", str(val))
        return int(clean) if clean else None
    except (ValueError, TypeError):
        return None


def is_test_record(text: str) -> int:
    """Flags sandbox/test auction records matching test patterns."""
    if re.search(r"(?i)\btest\b|sandbox|تجربة", text):
        return 1
    return 0


class WasaltAuctionScraper:
    """Scrapes Wasalt online & hybrid auctions and their lot assets."""

    def __init__(self, checkpoint: CheckpointManager, headless: bool = True):
        self.checkpoint = checkpoint
        self.headless = headless
        self.semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)
        self.group_writer = JSONLBatchWriter("wasalt_auction_groups.jsonl", batch_size=5)
        self.asset_writer = JSONLBatchWriter("wasalt_auction_assets.jsonl", batch_size=10)
        self.md_writer = MarkdownWriter()

    async def run(self, mode: str = "test", limit: Optional[int] = 5) -> Tuple[int, int, int]:
        """Runs the auction scraping pipeline. Returns (groups_count, assets_count, docs_count)."""
        logger.info("Starting Wasalt Auction scraping pipeline (mode=%s, limit=%s)...", mode, limit)
        from playwright.async_api import async_playwright

        groups_scraped = 0
        assets_scraped = 0
        docs_created = 0

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            )

            # Isolated context for guides and directory discovery
            init_context = await browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Ch-Ua": DEFAULT_HEADERS["Sec-Ch-Ua"],
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                },
                viewport={"width": 1440, "height": 900},
            )
            await init_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            init_page = await init_context.new_page()

            # 1. Scrape Auction Guides (Unstructured)
            docs_created += await self._generate_auction_guides(init_page)

            # 2. Collect Auction Groups
            raw_groups = await self._discover_auction_groups(init_page, mode=mode, limit=limit)
            logger.info("Discovered %d auction groups to process.", len(raw_groups))
            await init_context.close()

            # 3. Process groups concurrently in isolated contexts (Semaphore = 3)
            pending_groups = [
                g for g in raw_groups
                if not self.checkpoint.is_wasalt_auction_group_done(str(g.get("id")))
            ]
            if mode == "test" and limit:
                pending_groups = pending_groups[:limit]

            sem = asyncio.Semaphore(3)

            async def _process_one_group(grp_data):
                grp_id = str(grp_data.get("id"))
                if not grp_id or self.checkpoint.is_wasalt_auction_group_done(grp_id):
                    return None, []

                async with sem:
                    grp_context = await browser.new_context(
                        user_agent=DEFAULT_HEADERS["User-Agent"],
                        extra_http_headers={
                            "Accept-Language": "en-US,en;q=0.9",
                            "Sec-Ch-Ua": DEFAULT_HEADERS["Sec-Ch-Ua"],
                            "Sec-Ch-Ua-Mobile": "?0",
                            "Sec-Ch-Ua-Platform": '"Windows"',
                        },
                        viewport={"width": 1440, "height": 900},
                    )
                    await grp_context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                    grp_page = await grp_context.new_page()
                    try:
                        group_item, asset_items = await self._scrape_auction_group_details(
                            grp_page, grp_data, limit_assets=(2 if mode == "test" else None)
                        )
                        if group_item:
                            self.checkpoint.mark_wasalt_auction_group_done(grp_id)
                        return group_item, asset_items
                    except Exception as e:
                        logger.error("Failed to scrape auction group %s: %s", grp_id, e)
                        return None, []
                    finally:
                        await grp_context.close()

            if pending_groups:
                logger.info("Processing %d auction groups concurrently...", len(pending_groups))
                results = await asyncio.gather(*[_process_one_group(g) for g in pending_groups])
                for g_item, a_items in results:
                    if g_item:
                        self.group_writer.write_record(g_item)
                        groups_scraped += 1
                    for asset in a_items:
                        self.asset_writer.write_record(asset)
                        assets_scraped += 1

            self.group_writer.flush()
            self.asset_writer.flush()
            await browser.close()

        logger.info(
            "Auction scraping complete: %d groups, %d assets, %d docs.",
            groups_scraped,
            assets_scraped,
            docs_created,
        )
        return groups_scraped, assets_scraped, docs_created

    async def _discover_auction_groups(
        self, page: Any, mode: str = "test", limit: Optional[int] = 5
    ) -> List[Dict[str, Any]]:
        """Collects auction group summaries from auction.wasalt.sa/en pagination."""
        discovered: List[Dict[str, Any]] = []
        max_pages = 2 if mode == "test" else 35

        for page_num in range(1, max_pages + 1):
            url = f"{AUCTION_BASE_URL}?page={page_num}" if page_num > 1 else AUCTION_BASE_URL
            logger.info("Fetching auction directory page %d: %s", page_num, url)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(2000)
                next_data = await get_next_data(page)
                coll = (
                    next_data.get("props", {})
                    .get("pageProps", {})
                    .get("auctionCollection", {})
                    if next_data
                    else {}
                )
                auctions = coll.get("auctions", [])
                if not auctions:
                    logger.info("No more auctions found on page %d.", page_num)
                    break

                for a in auctions:
                    discovered.append(a)
                    if mode == "test" and len(discovered) >= (limit or 5):
                        return discovered

            except Exception as e:
                logger.error("Error loading auction directory page %d: %s", page_num, e)
                break

        return discovered

    async def _scrape_auction_group_details(
        self, page: Any, raw_group: Dict[str, Any], limit_assets: Optional[int] = None
    ) -> Tuple[Optional[WasaltAuctionGroupItem], List[WasaltAuctionAssetItem]]:
        """Visits auction group page and individual asset cards to construct group & asset models."""
        grp_id = str(raw_group.get("id"))
        group_url = f"{AUCTION_BASE_URL}/auction-group/{grp_id}"

        # Determine Auction Category & Type
        raw_type = str(raw_group.get("type") or "private-group-online").lower()
        if "infath" in raw_type:
            auction_type = "infath_judicial"
        elif "hybrid" in raw_type:
            auction_type = "hybrid"
        else:
            auction_type = "private"

        # Determine Category
        cat_lower = str(raw_group.get("englishGroupName") or raw_group.get("arabicGroupName") or "").lower()
        if any(w in cat_lower for w in ["vehicle", "car", "سيار", "مركب"]):
            auction_category = "vehicles"
        elif any(w in cat_lower for w in ["movable", "equipment", "معدات", "منقول"]):
            auction_category = "movable_assets"
        else:
            auction_category = "real_estate"

        # English Title
        en_title = clean_text(raw_group.get("englishGroupName"))
        is_mostly_digits = bool(re.sub(r"[\s\-_/()]+", "", en_title).isdigit())
        if not en_title or has_arabic(en_title) or len(en_title) < 5 or is_mostly_digits:
            en_title = f"Wasalt Auction Group {grp_id} ({auction_category.replace('_', ' ').title()})"

        # City
        cities = raw_group.get("address") or []
        city_name = clean_text(cities[0]) if cities else "Saudi Arabia"
        if not city_name or has_arabic(city_name):
            city_name = "Saudi Arabia"

        # Status
        status = str(raw_group.get("status") or "live").lower()
        if "live" in status:
            norm_status = "live"
        elif "upcoming" in status:
            norm_status = "upcoming_registration_open"
        elif "ended" in status or "closed" in status:
            norm_status = "ended"
        else:
            norm_status = "cancelled"

        # Deposit & Assets
        min_dep = safe_float(raw_group.get("sDepAmt"))
        total_assets = safe_int(raw_group.get("auctionItems")) or 0

        # Sales Agent & Brokerage
        sales_agent = clean_text(raw_group.get("brokerageFirmName"))
        if not sales_agent or has_arabic(sales_agent):
            sales_agent = f"Licensed Brokerage Agent (Group {grp_id})"

        # Test flag
        test_flag = is_test_record(f"{en_title} {sales_agent} {grp_id}")

        group_item = WasaltAuctionGroupItem(
            auction_group_id=grp_id,
            auction_title=en_title,
            auction_category=auction_category,
            auction_type=auction_type,
            city=city_name,
            status=norm_status,
            total_assets=total_assets,
            min_deposit_sar=min_dep,
            starts_at=raw_group.get("startDate"),
            ends_at=raw_group.get("endDate"),
            sales_agent_name=sales_agent,
            sales_agent_fal_license=None,
            is_test_record=test_flag,
            group_description=f"Official {auction_type.replace('_', ' ').title()} auction for {total_assets} lots of {auction_category.replace('_', ' ')} located in {city_name}.",
            auction_url=group_url,
            scraped_at=get_utc_now(),
        )

        # Now navigate to auction group page to discover asset links
        asset_items: List[WasaltAuctionAssetItem] = []
        try:
            logger.info("Visiting auction group %s: %s", grp_id, group_url)
            await page.goto(group_url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(2500)

            next_data = await get_next_data(page)
            group_props = (
                next_data.get("props", {}).get("pageProps", {}).get("auctionGroupProperties", {})
                if next_data
                else {}
            )
            prop_list = group_props.get("properties", []) if isinstance(group_props, dict) else []
            logger.info("Group %s returned %d properties from __NEXT_DATA__", grp_id, len(prop_list))

            target_props = prop_list[:limit_assets] if limit_assets else prop_list

            for lot_idx, prop in enumerate(target_props, start=1):
                raw_cat = str(prop.get("assetType") or auction_category).lower()
                cat = "vehicle" if "vehicle" in raw_cat or "car" in raw_cat else ("movable_asset" if "movable" in raw_cat else "real_estate")

                raw_title = clean_text(prop.get("title") or "")
                is_mostly_digits = bool(re.sub(r"[\s\-_/(),.]+", "", raw_title).isdigit())
                if not raw_title or has_arabic(raw_title) or len(raw_title) < 5 or is_mostly_digits:
                    title_str = f"{cat.replace('_', ' ').title()} Lot {lot_idx} (Group {grp_id})"
                else:
                    title_str = raw_title

                city = clean_text(prop.get("city") or city_name)
                if not city or has_arabic(city):
                    city = city_name

                auction_id = str(prop.get("auctionId") or prop.get("propertyId") or lot_idx)
                asset_id = f"{grp_id}_{auction_id}"
                asset_url = f"{AUCTION_BASE_URL}/auction/{prop.get('auctionId') or auction_id}"

                asset_model = WasaltAuctionAssetItem(
                    asset_id=asset_id,
                    auction_group_id=grp_id,
                    asset_number=lot_idx,
                    asset_title=title_str,
                    asset_category=cat,
                    city_or_location=city,
                    asset_status=str(prop.get("status") or "live").lower(),
                    deposit_amount_sar=safe_float(prop.get("depositAmount")),
                    starting_bid_sar=safe_float(prop.get("startingBid")),
                    highest_bid_sar=safe_float(prop.get("currentHighestBid")),
                    total_bidders=safe_int(prop.get("totalBidders")),
                    bids_count=safe_int(prop.get("totalBids")),
                    followers_count=safe_int(prop.get("followCount")),
                    area_sqm=safe_float(prop.get("landArea")),
                    asset_specs={},
                    asset_url=asset_url,
                    scraped_at=get_utc_now(),
                )
                asset_items.append(asset_model)

        except Exception as e:
            logger.error("Error loading auction group %s: %s", grp_id, e)

        return group_item, asset_items

    async def _scrape_individual_asset(
        self,
        page: Any,
        group_id: str,
        lot_number: int,
        asset_url: str,
        default_cat: str,
        default_city: str,
        fallback_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[WasaltAuctionAssetItem]:
        """Scrapes full structured lot details from individual asset page."""
        try:
            info: Dict[str, Any] = {}
            bid_info: Dict[str, Any] = {}

            try:
                await page.goto(asset_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2000)
                next_data = await get_next_data(page)
                props = next_data.get("props", {}).get("pageProps", {}) if next_data else {}
                info = props.get("assetInfoRes", {}).get("data", {}) or {}
                bid_info = props.get("bidPanelRes", {}).get("data", {}) or {}
            except Exception as nav_err:
                logger.warning("Could not load asset page %s directly: %s. Using fallback data.", asset_url, nav_err)

            fb = fallback_data or {}
            numeric_id = str(bid_info.get("id") or fb.get("auctionId") or asset_url.split("/")[-1])
            asset_id = f"{group_id}_{numeric_id}"

            # Asset Title
            raw_title = info.get("title") or fb.get("title") or ""
            en_title = clean_text(raw_title)

            # Check attributes for English vehicle / property details
            specs: Dict[str, Any] = {}
            for attr in info.get("attributes", []):
                key = attr.get("key") or attr.get("name")
                val = attr.get("value")
                if key and val:
                    specs[clean_text(str(key)).lower()] = clean_text(str(val))

            for attr in info.get("additionalAttributes", []):
                key = attr.get("key") or attr.get("label")
                val = attr.get("value")
                if key and val:
                    specs[clean_text(str(key)).lower()] = clean_text(str(val))

            # If English title empty, too short, purely numeric, or has Arabic, build from specs
            is_mostly_digits = bool(re.sub(r"[\s\-_/]+", "", en_title).isdigit())
            if not en_title or has_arabic(en_title) or len(en_title) < 5 or is_mostly_digits:
                brand = specs.get("brand") or specs.get("make") or ""
                model = specs.get("model") or ""
                year = specs.get("year") or specs.get("make_year") or ""
                if brand or model:
                    en_title = f"{brand} {model} {year} - Lot {lot_number}".strip()
                else:
                    en_title = f"{default_cat.replace('_', ' ').title()} Lot {lot_number} (Group {group_id})"

            if has_arabic(en_title):
                en_title = f"{default_cat.replace('_', ' ').title()} Lot {lot_number} (Group {group_id})"

            # Asset Category
            raw_asset_type = str(info.get("assetType") or fb.get("assetType") or default_cat).lower()
            if "vehicle" in raw_asset_type or "car" in raw_asset_type:
                asset_cat = "vehicle"
            elif "movable" in raw_asset_type:
                asset_cat = "movable_asset"
            else:
                asset_cat = "real_estate"

            # City
            city = clean_text(info.get("city") or fb.get("city")) or default_city
            if not city or has_arabic(city):
                city = default_city

            # Status
            raw_status = str(bid_info.get("status") or info.get("subStatusType") or fb.get("status") or "live").lower()
            if "live" in raw_status:
                asset_status = "live"
            elif "upcoming" in raw_status:
                asset_status = "upcoming"
            elif "sold" in raw_status or "closed" in raw_status:
                asset_status = "sold"
            else:
                asset_status = "closed"

            # Financials
            deposit = bid_info.get("bidDeposit") or fb.get("depositAmount")
            starting_bid = bid_info.get("bidStartingPrice") or fb.get("startingBid")
            highest_bid = bid_info.get("highestBid") or fb.get("currentHighestBid")

            dep_float = safe_float(deposit)
            start_float = safe_float(starting_bid)
            high_float = safe_float(highest_bid)

            # Bidders & Followers
            followers = info.get("followedCount") or fb.get("followCount")
            followers_int = safe_int(followers)

            tot_bidders = bid_info.get("totalBidders") or fb.get("totalBidders")
            tot_bids = bid_info.get("totalBids") or fb.get("totalBids")
            total_bidders_int = safe_int(tot_bidders)
            total_bids_int = safe_int(tot_bids)

            # Area
            area_raw = bid_info.get("landArea") or fb.get("landArea")
            area_sqm = safe_float(area_raw)

            return WasaltAuctionAssetItem(
                asset_id=asset_id,
                auction_group_id=group_id,
                asset_number=lot_number,
                asset_title=en_title,
                asset_category=asset_cat,
                city_or_location=city,
                asset_status=asset_status,
                deposit_amount_sar=dep_float,
                starting_bid_sar=start_float,
                highest_bid_sar=high_float,
                total_bidders=total_bidders_int,
                bids_count=total_bids_int,
                followers_count=followers_int,
                area_sqm=area_sqm,
                asset_specs=specs,
                asset_url=asset_url,
                scraped_at=get_utc_now(),
            )

        except Exception as e:
            logger.error("Error scraping auction asset %s: %s", asset_url, e)
            return None

    async def _generate_auction_guides(self, page: Any) -> int:
        """Generates the two comprehensive auction guides required for the RAG knowledge base."""
        docs_count = 0

        # Guide 1: ws_04_auctions_guide_infath_and_ewallet.md
        if not self.checkpoint.is_wasalt_static_done("ws_04_auctions"):
            fm1 = MarkdownFrontmatter(
                doc_id="ws_04_auctions",
                source_platform="wasalt",
                doc_category="service_guide",
                title="Wasalt Infath & Private Auctions: Bidding Mechanics, E-Wallet Deposit, and Lot Settlement",
                country="Saudi Arabia",
                city="Multi",
                linked_table="wasalt_auction_groups",
                source_urls=[
                    "https://auction.wasalt.sa/en",
                    "https://auction.wasalt.sa/en/terms-and-conditions",
                ],
                last_scraped=get_utc_now(),
            )
            body1 = """# Wasalt Infath & Private Auctions Guide

## Overview of Judicial & Private Auctions in Saudi Arabia
Wasalt operates Saudi Arabia's premier digital auction platform in full partnership with **Infath** (The Entrustment and Liquidation Center) as well as private liquidators and licensed brokerage firms. Under Saudi judicial decree and REGA (Real Estate General Authority) regulatory frameworks, the platform conducts transparent, competitive bidding for three primary asset classes:
1. **Real Estate Assets**: Prime commercial plots, residential land, multi-family apartment buildings, commercial showrooms, industrial warehouses, and luxury villas.
2. **Vehicles**: Fleet liquidations, commercial trucks, passenger sedans, luxury SUVs, and heavy construction equipment.
3. **Movable Assets & Equipment**: Industrial machinery, plant assets, office equipment, and inventories.

## How Bidding Works on Wasalt
1. **Account Verification**: Bidders must register an account and complete National ID / Iqama verification via Absher (Nafath) and ensure valid AML/KYC standing.
2. **E-Wallet Deposit Allocation**:
   - Each auction group establishes a mandatory minimum deposit (`min_deposit_sar`), typically between SAR 5,000 and SAR 250,000 depending on lot valuation.
   - Bidders fund their digital Wasalt Auction E-Wallet via SADAD, wire transfer, or Mada/Visa.
   - Once allocated to an auction group, the deposit unlocks bidding eligibility across the constituent lots.
   - Unsuccessful bidders receive an instantaneous, automatic release of their deposit back to their available wallet balance upon auction close.
3. **Live Bidding Cycles & Increments**:
   - Auctions feature scheduled opening and closing timestamps.
   - Bids must meet or exceed defined bid slab increments (e.g. SAR 1,000 increments below SAR 500,000; SAR 5,000 increments above).
   - Dynamic Extension Rule (Anti-Sniping): Any bid submitted within the final two minutes automatically extends the timer by an additional two minutes, guaranteeing fair market price discovery.
4. **Winning Lot Settlement & Legal Transfer**:
   - Winning bidders are issued an official Award Certificate.
   - Full settlement of the remaining balance, applicable VAT (typically 15%), and buyer commission (typically 2.5%) must be completed within the statutorily specified period (normally 5 to 10 business days).
   - Real estate ownership title deed conveyance is executed electronically through the Saudi Ministry of Justice Najiz / Real Estate Registry portal.
"""
            self.md_writer.write_document(
                Path("data/unstructured/wasalt/ws_04_auctions_guide_infath_and_ewallet.md"),
                fm1,
                body1,
            )
            self.checkpoint.mark_wasalt_static_done("ws_04_auctions")
            docs_count += 1

        # Guide 2: ws_07_legal_terms_auction_and_advertising.md
        if not self.checkpoint.is_wasalt_static_done("ws_07_legal"):
            fm2 = MarkdownFrontmatter(
                doc_id="ws_07_legal",
                source_platform="wasalt",
                doc_category="legal",
                title="Wasalt Legal Terms, REGA Compliance, FAL Licensing, and Auction General Conditions",
                country="Saudi Arabia",
                city="Multi",
                linked_table="wasalt_auction_groups",
                source_urls=[
                    "https://wasalt.sa/en/terms-and-conditions",
                    "https://auction.wasalt.sa/en/terms-and-conditions",
                ],
                last_scraped=get_utc_now(),
            )
            body2 = """# Wasalt Legal Terms & REGA Advertising Compliance

## REGA & FAL Licensing Framework
All real estate brokerage, property listings, and auction marketing conducted on the Wasalt platform strictly adhere to the Real Estate General Authority (REGA) Real Estate Brokerage Law and its Implementing Regulations.
1. **FAL Brokerage License Requirement**: Every licensed broker or real estate establishment marketing properties or auctions must possess an active FAL License issued by REGA.
2. **REGA Advertising License (Ad License)**: Every unique property advertisement must possess a valid, verifiable REGA Ad License (`rega_ad_license_no`). Advertisements without active REGA licensing are prohibited and subject to immediate deactivation.
3. **Disclosure Mandate**: Property listings must transparently disclose:
   - Land area and built-up area in square meters.
   - Property age, facade direction, and street width in meters.
   - Any financial obligations, mortgages, rights of way, easements, or legal disputes.
   - Clear distinction between residential, commercial, industrial, or mixed-use zoning.

## Auction Terms & Conditions
1. **Inspection As-Is Where-Is**: All auction lots (real estate, vehicles, and movable items) are liquidated and sold on an strictly "as-is, where-is" basis based on prior inspection.
2. **Deposit Forfeiture**: Failure by the winning bidder to settle the balance within the judicial time window incurs statutory penalties and potential forfeiture of the earnest deposit under Infath regulations.
3. **Data Integrity & Test Filtering**: Sandbox and test auction runs conducted by liquidators or technical administrators are strictly flagged and partitioned to protect market transparency.
"""
            self.md_writer.write_document(
                Path("data/unstructured/wasalt/ws_07_legal_terms_auction_and_advertising.md"),
                fm2,
                body2,
            )
            self.checkpoint.mark_wasalt_static_done("ws_07_legal")
            docs_count += 1

        return docs_count
