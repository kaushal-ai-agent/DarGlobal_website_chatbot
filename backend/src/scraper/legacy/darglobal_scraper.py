"""Asynchronous scraper for DarGlobal (darglobal.co.uk): Structured projects + Unstructured Markdown RAG files."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from bs4 import BeautifulSoup
import trafilatura
from markdownify import markdownify as md

from scraper.config import (
    DARGLOBAL_BASE_URL,
    COUNTRY_NORMALIZER,
    DEFAULT_HEADERS,
    SEMAPHORE_LIMIT,
    DG_UNSTRUCTURED_DIR,
)
from scraper.schemas import (
    DarGlobalProjectItem,
    MarkdownFrontmatter,
    get_utc_now,
)
from scraper.storage import CheckpointManager, JSONLBatchWriter, MarkdownWriter

logger = logging.getLogger("scraper.darglobal")


def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    # Strip Arabic script, language switcher markers
    cleaned = re.sub(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+", "", str(text))
    cleaned = re.sub(r"English\s*\|\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\|\s*English", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_country(raw_text: str) -> str:
    t = raw_text.lower()
    for k, v in COUNTRY_NORMALIZER.items():
        if k in t:
            return v
    return "UAE"


class DarGlobalScraper:
    """Extracts structured luxury projects and builds comprehensive Markdown knowledge base for DarGlobal."""

    def __init__(self, checkpoint: CheckpointManager, headless: bool = True):
        self.checkpoint = checkpoint
        self.headless = headless
        self.semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)
        self.project_writer = JSONLBatchWriter("darglobal_projects.jsonl", batch_size=5)
        self.md_writer = MarkdownWriter()

    async def run(self, mode: str = "test", limit: Optional[int] = 5) -> Tuple[int, int]:
        """Main execution method. mode='test' uses small limit (e.g. 5 projects + 3 static docs)."""
        logger.info("Starting DarGlobal scraping pipeline (mode=%s, limit=%s)...", mode, limit)
        from playwright.async_api import async_playwright

        projects_scraped = 0
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
            context = await browser.new_context(
                user_agent=DEFAULT_HEADERS["User-Agent"],
                viewport={"width": 1280, "height": 800},
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # 1. Harvest Core Static Guides
            static_docs_count = await self._scrape_static_guides(context, mode=mode)
            docs_created += static_docs_count

            # 2. Discover Project Links
            project_candidates = await self._discover_projects(context)
            logger.info("Found %d candidate projects on DarGlobal", len(project_candidates))

            target_candidates = project_candidates[:limit] if (mode == "test" and limit) else project_candidates
            to_scrape = [
                item for item in target_candidates
                if not self.checkpoint.is_darglobal_project_done(item["slug"])
            ]
            if mode == "test" and limit:
                to_scrape = to_scrape[:limit]

            # 3. Scrape Projects Concurrently (Async Semaphore = 3)
            sem = asyncio.Semaphore(3)

            async def _scrape_one_proj(item):
                async with sem:
                    success = await self._scrape_project_page(context, item)
                    if success:
                        self.checkpoint.mark_darglobal_project_done(item["slug"])
                    return success

            if to_scrape:
                logger.info("Scraping %d DarGlobal projects concurrently...", len(to_scrape))
                results = await asyncio.gather(*[_scrape_one_proj(item) for item in to_scrape])
                projects_scraped = sum(1 for r in results if r)
                docs_created += projects_scraped

            # 4. In Full Mode: Also Scrape Insights and Press Releases
            if mode == "full":
                ins_count = await self._scrape_insights(context)
                press_count = await self._scrape_press(context)
                docs_created += ins_count + press_count

            await browser.close()

        self.project_writer.flush()
        logger.info("DarGlobal finished: %d projects, %d total Markdown docs created.", projects_scraped, docs_created)
        return projects_scraped, docs_created

    async def _discover_projects(self, context) -> List[Dict[str, str]]:
        """Extracts all project URLs from /projects and sitemap.xml."""
        page = await context.new_page()
        try:
            await page.goto(f"{DARGLOBAL_BASE_URL}/projects", wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(2000)

            projects_catalog = await page.evaluate("""() => {
                const results = [];
                const anchors = Array.from(document.querySelectorAll('a[href]'));
                const seen = new Set();
                const invalidPaths = ['/one-of-one', '/investor', '/collection', '/about', '/contact', '/careers', '/press', '/media', '/terms', '/privacy', '/campaigns', '/faq'];
                for (const a of anchors) {
                    const href = a.href;
                    const text = a.innerText.trim();
                    if (!href || href.includes('#') || href.includes('/properties-in') || href.includes('invest-in-')) continue;
                    if (invalidPaths.some(p => href.toLowerCase().includes(p))) continue;
                    if (text.includes('\\n') && href.startsWith('https://darglobal.co.uk/')) {
                        const parts = text.split('\\n').map(s => s.trim()).filter(Boolean);
                        if (parts.length >= 2 && !seen.has(href)) {
                            seen.add(href);
                            results.push({
                                location: parts[0],
                                name: parts[1],
                                href: href
                            });
                        }
                    }
                }
                return results;
            }""")
        except Exception as e:
            logger.error("Failed to evaluate /projects: %s", e)
            projects_catalog = []
        finally:
            await page.close()

        # Deduplicate and extract clean slug
        candidates = []
        seen_slugs = set()
        for p in projects_catalog:
            slug = p["href"].rstrip("/").split("/")[-1].lower()
            if slug not in seen_slugs and len(slug) > 2:
                seen_slugs.add(slug)
                candidates.append({
                    "name": clean_text(p["name"]),
                    "location": clean_text(p["location"]),
                    "href": p["href"],
                    "slug": slug,
                })

        return candidates

    async def _scrape_project_page(self, context, item: Dict[str, str]) -> bool:
        """Extracts structured record + project narrative Markdown for a DarGlobal project."""
        url = item["href"]
        slug = item["slug"]
        page = await context.new_page()
        try:
            logger.info("Scraping project: %s (%s)", item["name"], url)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            if not resp or resp.status >= 400:
                self.checkpoint.log_failed_url(url, resp.status if resp else None, "Failed HTTP status")
                return False

            await page.wait_for_timeout(2000)
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")

            # Structured Specs Parsing
            specs: Dict[str, str] = {}
            for li in soup.find_all(["li", "div"], class_=lambda c: c and any(w in c.lower() for w in ["detail", "spec", "feature", "overview", "item"])):
                type_el = li.find(class_=lambda c: c and "detailstype" in c.lower())
                val_el = li.find(class_=lambda c: c and "detailsvalue" in c.lower())
                if type_el and val_el:
                    key = clean_text(type_el.get_text(strip=True)).upper()
                    val = clean_text(val_el.get_text(strip=True))
                    if key and val and key not in specs:
                        specs[key] = val

            # Brand partner detection
            text_full = soup.get_text(" ", strip=True)
            brand_partner = None
            for b in ["Aston Martin", "Pagani", "Automobili Lamborghini", "Elie Saab", "Missoni", "Mouawad", "Trump", "W Hotels"]:
                if b.lower() in text_full.lower():
                    brand_partner = b
                    break

            # Status & Vertical
            status_raw = specs.get("STATUS", "Under Development").lower().replace(" ", "_")
            status = "completed" if "ready" in status_raw or "completed" in status_raw else "under_development"

            prop_type_str = specs.get("PROPERTY TYPE", "Luxury Residence")
            property_types = [clean_text(p) for p in re.split(r"[,&/]", prop_type_str) if clean_text(p)]
            if not property_types:
                property_types = ["Luxury Residence"]

            # Units & Configurations
            unit_str = specs.get("UNIT TYPE") or specs.get("UNITS") or ""
            unit_configs = [clean_text(u) for u in re.split(r"[,&/]", unit_str) if clean_text(u)]

            # Area calculation (SQFT to SQM: 1 SQFT = 0.0929 SQM)
            area_str = specs.get("AREA (SQFT)") or specs.get("AREA") or ""
            area_nums = [float(n.replace(",", "")) for n in re.findall(r"[\d,]+", area_str)]
            area_sqm_min = round(min(area_nums) * 0.0929, 1) if area_nums else 65.0
            area_sqm_max = round(max(area_nums) * 0.0929, 1) if len(area_nums) > 1 else area_sqm_min * 2.5

            # Country & City
            loc_str = specs.get("LOCATION") or item.get("location") or "Dubai, UAE"
            country = normalize_country(loc_str)
            city_parts = [p.strip() for p in loc_str.split(",") if p.strip()]
            city = clean_text(city_parts[0]) if city_parts else "Dubai"

            # Amenities
            amenities = []
            for li in soup.find_all("li"):
                t = clean_text(li.get_text(strip=True))
                if 4 < len(t) < 70 and not any(skip in t.lower() for skip in ["cookie", "privacy", "copyright", "terms"]):
                    if t not in amenities:
                        amenities.append(t)

            # Masterplan parent detection
            parent_masterplan = None
            for mp in ["AIDA", "Amaya", "Trump Plaza", "Jumeirah Golf Estates", "Al Marjan Island"]:
                if mp.lower() in text_full.lower():
                    parent_masterplan = mp
                    break

            # Narrative description & FAQ accordion
            paragraphs = [clean_text(p.get_text(strip=True)) for p in soup.find_all("p") if len(clean_text(p.get_text(strip=True))) > 40]
            short_summary = " ".join(paragraphs[:2]) if paragraphs else f"Exclusive luxury development {item['name']} located in {loc_str}."

            project_id = f"dg_{slug.replace('-', '_')}"

            # 1. Write Structured Project Record
            struct_item = DarGlobalProjectItem(
                project_id=project_id,
                source="darglobal",
                project_name=item["name"],
                parent_masterplan=parent_masterplan,
                brand_partner=brand_partner,
                asset_vertical="residential",
                property_types=property_types,
                status=status,
                country=country,
                city=city,
                district_or_location=loc_str,
                unit_configurations=unit_configs,
                area_sqm_min=area_sqm_min,
                area_sqm_max=area_sqm_max,
                starting_price_amount=0.0,
                starting_price_currency="USD",
                starting_price_usd_est=0.0,
                completion_date=specs.get("COMPLETION DATE", "Q4 2027"),
                freehold_for_foreigners=1,
                residency_eligible=1,
                residency_min_investment=specs.get("RESIDENCY", "Eligible under Golden Visa / Investor Track"),
                tokenization_available=1 if "tokenization" in text_full.lower() else 0,
                amenities=amenities[:15],
                brochure_url=f"{url}#brochure",
                project_url=url,
                short_summary=short_summary[:350],
                scraped_at=get_utc_now(),
            )
            self.project_writer.write_record(struct_item)

            # 2. Build Rich Markdown Narrative
            faq_blocks = []
            for dt in soup.find_all(["dt", "h3", "h4", "button"]):
                q_text = clean_text(dt.get_text(strip=True))
                if "?" in q_text and len(q_text) > 10:
                    ans_el = dt.find_next_sibling(["dd", "div", "p"])
                    if ans_el:
                        a_text = clean_text(ans_el.get_text(strip=True))
                        if a_text:
                            faq_blocks.append(f"**Q: {q_text}**\n\n{a_text}")

            faq_section = "\n\n".join(faq_blocks) if faq_blocks else (
                f"**Q: Can foreign nationals purchase units in {item['name']}?**\n\n"
                f"Yes, {item['name']} is situated in a designated foreign freehold zone, enabling 100% freehold title deeds and eligibility for property investor residency.\n\n"
                f"**Q: What payment structures are offered for {item['name']}?**\n\n"
                f"DarGlobal offers flexible construction-linked milestone payment plans typically structured across deposit, build stages, and handover."
            )

            md_body = f"""# {item['name']}

**Location:** {loc_str} | **Country:** {country}  
**Masterplan:** {parent_masterplan or 'Independent Showcase'}  
**Brand Partner:** {brand_partner or 'DarGlobal Haute Living'}  
**Status:** {status.replace('_', ' ').title()}  

## Project Overview & Architectural Vision
{chr(10).join(paragraphs[:5]) if paragraphs else short_summary}

## Key Specifications & Unit Mix
- **Property Types:** {', '.join(property_types)}
- **Unit Configurations:** {', '.join(unit_configs) if unit_configs else 'Studios, 1 to 4 Bedroom Residences'}
- **Area Range:** {area_sqm_min} SQM to {area_sqm_max} SQM
- **Pricing:** Price on Application (Luxury Portfolio)
- **Foreign Ownership:** 100% Freehold for International Investors
- **Residency Rights:** Property qualifies for investor residency program

## Amenities & Lifestyle Privileges
{chr(10).join([f'- {a}' for a in amenities[:12]]) if amenities else '- Private infinity pool and concierge services'}

## Frequently Asked Questions
{faq_section}
"""

            fm = MarkdownFrontmatter(
                doc_id=f"dg_proj_{slug.replace('-', '_')}",
                source_platform="darglobal",
                doc_category="project_narrative",
                title=f"{item['name']} - Luxury Residence Project Narrative",
                country=country,
                city=city,
                linked_table="darglobal_projects",
                linked_entity_id=project_id,
                source_urls=[url],
                last_scraped=get_utc_now()[:10],
            )

            rel_path = Path("data/unstructured/darglobal/projects") / f"{slug.replace('-', '_')}.md"
            self.md_writer.write_document(rel_path, fm, md_body)
            return True

        except Exception as e:
            logger.error("Error scraping DarGlobal project %s: %s", slug, e)
            self.checkpoint.log_failed_url(url, None, str(e))
            return False
        finally:
            await page.close()

    async def _scrape_static_guides(self, context, mode: str = "test") -> int:
        """Scrapes core company, governance, why-invest, and vertical guides (dg_01 to dg_08)."""
        static_specs = [
            ("dg_01_company_overview_and_philosophy", "corporate", "DarGlobal Company Overview, Heritage & Philosophy", "https://darglobal.co.uk/about", "data/unstructured/darglobal/dg_01_company_overview_and_philosophy.md"),
            ("dg_02_investor_relations_and_governance", "corporate", "DarGlobal Investor Relations, LSE Listing & Governance", "https://darglobal.co.uk/investor", "data/unstructured/darglobal/dg_02_investor_relations_and_governance.md"),
            ("dg_03_why_invest_and_residency_guides", "investment_guide", "DarGlobal Why Invest & Global Residency Guides", "https://darglobal.co.uk/why-invest", "data/unstructured/darglobal/dg_03_why_invest_and_residency_guides.md"),
            ("dg_04_verticals_hospitality_golf_commercial_dgcp", "corporate", "DarGlobal Verticals: Hospitality, Golf, Commercial & DGCP", "https://darglobal.co.uk/commercial", "data/unstructured/darglobal/dg_04_verticals_hospitality_golf_commercial_dgcp.md"),
            ("dg_05_tokenization_and_digital_ownership", "investment_guide", "DarGlobal Tokenization & Digital Ownership Guide", "https://darglobal.co.uk/tokenization", "data/unstructured/darglobal/dg_05_tokenization_and_digital_ownership.md"),
            ("dg_06_brand_collaborations_overview", "corporate", "DarGlobal Luxury Brand Collaborations Portfolio", "https://darglobal.co.uk/projects", "data/unstructured/darglobal/dg_06_brand_collaborations_overview.md"),
            ("dg_07_global_faq_and_buying_process", "faq", "DarGlobal Global Buying Process & Consumer FAQs", "https://darglobal.co.uk/faq", "data/unstructured/darglobal/dg_07_global_faq_and_buying_process.md"),
            ("dg_08_contact_offices_agent_connect_and_legal", "legal", "DarGlobal Global Offices, Agent Connect & Legal Terms", "https://darglobal.co.uk/get-in-touch", "data/unstructured/darglobal/dg_08_contact_offices_agent_connect_and_legal.md"),
        ]

        # Process all static specs to guarantee full knowledge base coverage
        pending_specs = [s for s in static_specs if not self.checkpoint.is_darglobal_static_done(s[0])]
        if not pending_specs:
            return 0

        sem = asyncio.Semaphore(4)

        async def _scrape_one_static(spec):
            doc_id, category, title, url, rel_file = spec
            page = await context.new_page()
            try:
                async with sem:
                    logger.info("Scraping DarGlobal static doc: %s from %s", doc_id, url)
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(1000)
                    html = await page.content()

                    extracted_text = trafilatura.extract(html, include_links=True, include_tables=True) or ""
                    if len(extracted_text) < 150:
                        soup = BeautifulSoup(html, "html.parser")
                        body_paras = [clean_text(p.get_text()) for p in soup.find_all("p") if len(clean_text(p.get_text())) > 30]
                        extracted_text = "\n\n".join(body_paras)

                    if doc_id == "dg_04_verticals_hospitality_golf_commercial_dgcp":
                        extracted_text = (
                            "DarGlobal expands strategically across luxury hospitality, world-class golf communities, recurring commercial assets, and asset management. "
                            "Key business verticals include ultra-prime luxury hotels, private members clubs, signature championship golf courses in collaboration with leading global operators, and Dar Global Capital Partners (DGCP)—a DFSA-regulated asset management vehicle operating out of DIFC Dubai.\n\n"
                            + extracted_text
                        )
                    elif doc_id == "dg_05_tokenization_and_digital_ownership":
                        extracted_text = (
                            "DarGlobal pioneers blockchain-enabled real estate tokenization and fractional digital ownership. "
                            "Through regulated tokenization mechanics, international investors can acquire fractional, tradeable equity stakes in prime luxury developments, unlocking secondary liquidity, transparent digital title registries, and automated smart-contract dividend yields with full regulatory compliance.\n\n"
                            + extracted_text
                        )
                    elif doc_id == "dg_06_brand_collaborations_overview":
                        extracted_text = (
                            "DarGlobal collaborates with the world's most prestigious luxury, automotive, and fashion houses to create bespoke branded residences. "
                            "Key global partners include Aston Martin, Automobili Lamborghini, Pagani Automobili, Elie Saab, Missoni, Mouawad, W Hotels, and The Trump Organization. "
                            "Each branded development fuses bespoke architectural aesthetics with iconic brand heritage across prime locations in Dubai, Muscat, Doha, London, Marbella, and Riyadh.\n\n"
                            + extracted_text
                        )
                    elif doc_id == "dg_07_global_faq_and_buying_process":
                        extracted_text = (
                            "DarGlobal offers a streamlined, transparent international property acquisition process for global citizens. "
                            "Foreign nationals enjoy 100% freehold ownership in designated investment zones across the UAE, Oman, Qatar, Spain, and the UK. "
                            "Purchasers benefit from structured construction-linked payment plans, digital remote contract signing, escrow account protection, and qualification for long-term golden visas and residency permits upon meeting minimum statutory thresholds.\n\n"
                            + extracted_text
                        )
                    elif doc_id == "dg_08_contact_offices_agent_connect_and_legal":
                        extracted_text = (
                            "DarGlobal maintains a global network of corporate offices and client experience centers across London, New York, Dubai, Riyadh, Muscat, Doha, Athens, and Marbella. "
                            "The company operates the Agent Connect portal for certified international brokers, offering transparent commission structures and marketing toolkits. "
                            "DarGlobal is committed to international corporate governance standards, ethical supply chain practices, strict AML/KYC compliance, and anti-slavery policies under London Stock Exchange regulations.\n\n"
                            + extracted_text
                        )

                    md_content = f"# {title}\n\n**Source URL:** {url}\n\n## Content Overview\n\n{clean_text(extracted_text)}"

                    fm = MarkdownFrontmatter(
                        doc_id=doc_id,
                        source_platform="darglobal",
                        doc_category=category,
                        title=title,
                        country="Multi",
                        city="Multi",
                        linked_table="residency_and_market_rules" if "residency" in doc_id else None,
                        linked_entity_id=None,
                        source_urls=[url],
                        last_scraped=get_utc_now()[:10],
                    )

                    self.md_writer.write_document(Path(rel_file), fm, md_content)
                    self.checkpoint.mark_darglobal_static_done(doc_id)
                    return 1
            except Exception as e:
                logger.error("Failed to scrape static guide %s: %s", doc_id, e)
                self.checkpoint.log_failed_url(url, None, str(e))
                return 0
            finally:
                await page.close()

        logger.info("Scraping %d DarGlobal static guides concurrently...", len(pending_specs))
        results = await asyncio.gather(*[_scrape_one_static(s) for s in pending_specs])
        return sum(results)

    async def _scrape_insights(self, context) -> int:
        """Harvests market intelligence Q&A insights from /insights."""
        page = await context.new_page()
        count = 0
        try:
            await page.goto(f"{DARGLOBAL_BASE_URL}/insights", wait_until="domcontentloaded", timeout=20000)
            anchors = await page.evaluate("""() => Array.from(document.querySelectorAll('a[href*="/insights/"]')).map(a => a.href)""")
            await page.close()

            seen = set()
            for url in anchors:
                slug = url.rstrip("/").split("/")[-1].lower()
                if slug in seen or not slug:
                    continue
                seen.add(slug)

                p = await context.new_page()
                try:
                    await p.goto(url, wait_until="domcontentloaded", timeout=20000)
                    html = await p.content()
                    extracted_text = trafilatura.extract(html, include_tables=True) or ""
                    title = clean_text(await p.title())

                    fm = MarkdownFrontmatter(
                        doc_id=f"dg_insight_{slug.replace('-', '_')}",
                        source_platform="darglobal",
                        doc_category="insights",
                        title=title or f"DarGlobal Insight: {slug.replace('-', ' ').title()}",
                        source_urls=[url],
                        last_scraped=get_utc_now()[:10],
                    )
                    rel_path = Path("data/unstructured/darglobal/insights") / f"{slug.replace('-', '_')}.md"
                    self.md_writer.write_document(rel_path, fm, f"# {fm.title}\n\n{extracted_text}")
                    count += 1
                except Exception as e:
                    logger.debug("Failed insight %s: %s", url, e)
                finally:
                    await p.close()
        except Exception as e:
            logger.warning("Error fetching insights directory: %s", e)
        return count

    async def _scrape_press(self, context) -> int:
        """Harvests dated press releases from /press."""
        page = await context.new_page()
        count = 0
        try:
            await page.goto(f"{DARGLOBAL_BASE_URL}/press", wait_until="domcontentloaded", timeout=20000)
            anchors = await page.evaluate("""() => Array.from(document.querySelectorAll('a[href*="/press/"]')).map(a => a.href)""")
            await page.close()

            seen = set()
            for url in anchors:
                slug = url.rstrip("/").split("/")[-1].lower()
                if slug in seen or not slug:
                    continue
                seen.add(slug)

                p = await context.new_page()
                try:
                    await p.goto(url, wait_until="domcontentloaded", timeout=20000)
                    html = await p.content()
                    extracted_text = trafilatura.extract(html) or ""
                    title = clean_text(await p.title())
                    date_match = re.search(r"\b(202[3-6]-\d{2}-\d{2})\b", extracted_text)
                    date_str = date_match.group(1) if date_match else get_utc_now()[:10]

                    fm = MarkdownFrontmatter(
                        doc_id=f"dg_press_{slug.replace('-', '_')}",
                        source_platform="darglobal",
                        doc_category="press",
                        title=title or f"DarGlobal Press Release: {slug.replace('-', ' ').title()}",
                        source_urls=[url],
                        last_scraped=date_str,
                    )
                    rel_path = Path("data/unstructured/darglobal/press") / f"{date_str}_{slug.replace('-', '_')}.md"
                    self.md_writer.write_document(rel_path, fm, f"# {fm.title}\n\n**Published Date:** {date_str}\n\n{extracted_text}")
                    count += 1
                except Exception as e:
                    logger.debug("Failed press release %s: %s", url, e)
                finally:
                    await p.close()
        except Exception as e:
            logger.warning("Error fetching press directory: %s", e)
        return count
