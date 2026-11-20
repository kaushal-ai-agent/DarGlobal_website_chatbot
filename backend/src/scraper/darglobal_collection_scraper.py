"""
DarGlobal Collection & Property Category Scraper (`darglobal_collection_scraper.py`)

What it does:
Crawls granular DarGlobal property collection and category pages (e.g., properties-in-uae/villas), interactively expands FAQ accordions and 'Read More' sections, cycles through 'Similar Properties' tabs, and extracts all discovered clickable links (project detail pages, sub-collections, downloadable PDF brochures, and external links). Outputs rich Markdown and link manifest JSON into data/darglobal/collections/.

Important Modules & Functions:
- `playwright.async_api`: Asynchronous browser control with asset blocking (images, media, fonts) for high-speed page loads.
- `goto_with_retry(page, url)`: Robust navigation with exponential backoff on HTTP 502/503/504 and timeout errors.
- `dismiss_cookie_banner(page)`: Dismisses cookie consent overlays.
- `expand_all_accordions_and_read_more(page)`: Clicks all collapsible FAQs and view-more triggers to populate the DOM.
- `extract_similar_tabs(page, base_url)`: Cycles through multi-region tabs (Oman, Qatar, Spain, KSA) to extract regional cross-links.
- `extract_all_clickable_links(page, base_url)`: Categorizes all anchors into project detail links, collection filters, PDFs/assets, and header/footer chrome.
- `extract_main_markdown(html, base_url)`: Strips non-content tags and converts main HTML to clean ATX Markdown.
- `scrape_collection_page(target_url)`: Main orchestrator for single-collection harvesting and link manifest creation.

Core Logic (One-Liner):
Loads targeted DarGlobal collection URLs via Playwright with retry resilience, interactively reveals accordions and tab panels, and outputs structured Markdown alongside partitioned link discovery queues.
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from playwright.async_api import Page, async_playwright

# Resolve project root and ensure it is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_DIR = Path(__file__).resolve().parent
for p in [str(PROJECT_ROOT), str(SCRAPER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_URL = "https://darglobal.co.uk/projects/properties-in-uae/villas"
OUTPUT_DIR = PROJECT_ROOT / "data" / "darglobal" / "collections"


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slugify_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_").lower()
    return slug or "page"


def normalize_url(href: str, base_url: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "#")):
        return None
    if href.startswith(("tel:", "mailto:")):
        return href
    return urljoin(base_url, href).split("#")[0]


async def dismiss_cookie_banner(page: Page) -> None:
    for sel in ["button:has-text('ACCEPT')", "button:has-text('Accept')", "a:has-text('ACCEPT')"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await page.wait_for_timeout(400)
                return
        except Exception:
            continue


async def scroll_full_page(page: Page) -> None:
    total_height = await page.evaluate("document.body.scrollHeight")
    step = 600
    current = 0
    while current < total_height:
        await page.evaluate(f"window.scrollTo(0, {current})")
        await page.wait_for_timeout(300)
        current += step
        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height > total_height:
            total_height = new_height
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(400)


async def expand_all_accordions_and_read_more(page: Page) -> None:
    """Click all FAQ accordions and 'Read More' / 'View More' buttons so hidden text is in the DOM."""
    expand_selectors = [
        "button:has-text('Read more')",
        "button:has-text('Read More')",
        "button:has-text('Show more')",
        "button:has-text('View more')",
        "[aria-expanded='false']",
        "[class*='accordion' i] button",
        "[class*='faq' i] button",
        "[class*='faq' i] [role='button']",
    ]
    for sel in expand_selectors:
        try:
            elements = page.locator(sel)
            count = await elements.count()
            for i in range(count):
                el = elements.nth(i)
                try:
                    if await el.is_visible(timeout=500):
                        await el.scroll_into_view_if_needed(timeout=1000)
                        await el.click(timeout=1000)
                        await page.wait_for_timeout(200)
                except Exception:
                    continue
        except Exception:
            continue


async def extract_similar_tabs(page: Page, base_url: str) -> list[dict[str, Any]]:
    """
    Click through 'Similar Villas / Properties Around the World' tabs
    (e.g., Oman, Qatar, Spain, Saudi Arabia) to capture cards and links inside each tab.
    """
    tab_results: list[dict[str, Any]] = []
    tab_selectors = [
        "[role='tab']",
        "button:has-text('Similar ')",
        "li:has-text('Similar ')",
        "div[class*='tab' i] button",
    ]

    clicked_labels = set()
    for sel in tab_selectors:
        try:
            tabs = page.locator(sel)
            count = await tabs.count()
            if count == 0:
                continue

            for i in range(count):
                tab = tabs.nth(i)
                try:
                    if not await tab.is_visible(timeout=500):
                        continue
                    label = clean_text(await tab.inner_text())
                    if not label or label in clicked_labels or len(label) > 80:
                        continue
                    clicked_labels.add(label)

                    await tab.scroll_into_view_if_needed(timeout=1000)
                    await tab.click(timeout=1500)
                    await page.wait_for_timeout(500)

                    # Extract visible links inside the active tab panel or nearby container
                    links = await page.evaluate(
                        """() => {
                            const out = [];
                            const panels = document.querySelectorAll('[role="tabpanel"], [class*="similar" i], [class*="slider" i], [class*="carousel" i], main');
                            const scope = panels.length ? panels : [document.body];
                            const seen = new Set();
                            for (const container of scope) {
                                for (const a of container.querySelectorAll('a[href]')) {
                                    const rect = a.getBoundingClientRect();
                                    if (rect.width === 0 || rect.height === 0) continue;
                                    const href = a.getAttribute('href') || '';
                                    const text = (a.innerText || '').trim();
                                    if (href && text && !seen.has(href)) {
                                        seen.add(href);
                                        out.push({ text, href });
                                    }
                                }
                            }
                            return out;
                        }"""
                    )

                    normalized_links = []
                    for item in links:
                        u = normalize_url(item["href"], base_url)
                        if u and u.startswith("http") and "darglobal.co.uk" in u:
                            normalized_links.append({"label": clean_text(item["text"]), "url": u})

                    tab_results.append({"tab_label": label, "links": normalized_links})
                except Exception:
                    continue
        except Exception:
            continue

    return tab_results


async def extract_all_clickable_links(page: Page, base_url: str) -> dict[str, list[dict[str, str]]]:
    """
    Extract every clickable link on the page, split into:
    - main_content_links: links inside <main> (projects, breadcrumbs, collection filters, editorial)
    - header_footer_links: navigation chrome links (kept separate so nested crawlers don't loop)
    - pdf_or_asset_links: brochures, policies, PDFs
    - external_links: social, WhatsApp, external portals
    """
    raw_links = await page.evaluate(
        """() => {
            const results = [];
            for (const a of document.querySelectorAll('a[href]')) {
                const href = a.getAttribute('href') || '';
                const text = (a.innerText || a.getAttribute('aria-label') || a.getAttribute('title') || '').trim();
                const inHeader = !!a.closest('header, nav');
                const inFooter = !!a.closest('footer');
                const inMain = !!a.closest('main') || (!inHeader && !inFooter);
                const rect = a.getBoundingClientRect();
                results.push({
                    href,
                    text,
                    section: inHeader ? 'header' : (inFooter ? 'footer' : (inMain ? 'main' : 'other')),
                    visible: rect.width > 0 && rect.height > 0
                });
            }
            return results;
        }"""
    )

    categorized: dict[str, list[dict[str, str]]] = {
        "main_project_or_nested_links": [],
        "main_collection_or_filter_links": [],
        "pdf_or_asset_links": [],
        "external_links": [],
        "header_footer_links": [],
    }

    seen_per_bucket: dict[str, set[str]] = {k: set() for k in categorized}
    base_domain = urlparse(base_url).netloc.lower()
    base_clean = base_url.rstrip("/")

    for item in raw_links:
        url = normalize_url(item["href"], base_url)
        if not url:
            continue
        label = clean_text(item["text"]) or url
        parsed = urlparse(url)

        # Skip self-links
        if url.rstrip("/") == base_clean:
            continue

        # 1. PDFs / downloadable assets
        if parsed.path.lower().endswith((".pdf", ".doc", ".docx", ".xlsx")):
            bucket = "pdf_or_asset_links"
        # 2. External or tel/mailto links
        elif not url.startswith("http") or (parsed.netloc.lower() and base_domain not in parsed.netloc.lower()):
            bucket = "external_links"
        # 3. Header / Footer navigation chrome
        elif item["section"] in ("header", "footer"):
            bucket = "header_footer_links"
        # 4. Main content links -> distinguish collection/filter links vs individual project/nested detail pages
        elif "/projects/" in parsed.path.lower() or parsed.path.rstrip("/").lower() == "/projects":
            bucket = "main_collection_or_filter_links"
        else:
            bucket = "main_project_or_nested_links"

        if url not in seen_per_bucket[bucket]:
            seen_per_bucket[bucket].add(url)
            categorized[bucket].append(
                {
                    "label": label,
                    "url": url,
                    "section": item["section"],
                }
            )

    return categorized


def extract_metadata(soup: BeautifulSoup, final_url: str, slug: str) -> dict[str, Any]:
    title = clean_text(soup.title.string) if soup.title and soup.title.string else slug

    def meta_content(attr: str, val: str) -> str | None:
        tag = soup.find("meta", attrs={attr: val})
        return clean_text(tag["content"]) if tag and tag.get("content") else None

    canonical_tag = soup.find("link", rel="canonical")
    canonical_url = canonical_tag["href"] if canonical_tag and canonical_tag.get("href") else final_url

    # Extract breadcrumbs if present
    breadcrumbs = []
    bc_nav = soup.find(attrs={"aria-label": re.compile("breadcrumb", re.I)}) or soup.select_one("[class*='breadcrumb' i]")
    if bc_nav:
        breadcrumbs = [clean_text(x.get_text(" ", strip=True)) for x in bc_nav.find_all(["a", "li", "span"]) if clean_text(x.get_text(" ", strip=True))]
        breadcrumbs = list(dict.fromkeys(breadcrumbs))

    # Extract JSON-LD blocks (often contains FAQPage, BreadcrumbList, Product/RealEstate schemas)
    json_ld_blocks = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            json_ld_blocks.append(json.loads(script.string or ""))
        except Exception:
            continue

    # Infer country & property type from URL path (/projects/properties-in-<country>/<type>)
    path_parts = [p for p in urlparse(final_url).path.strip("/").split("/") if p]
    inferred_country = None
    inferred_property_type = None
    for part in path_parts:
        if part.startswith("properties-in-"):
            inferred_country = part.replace("properties-in-", "").replace("-", " ").title()
    if len(path_parts) >= 3 and path_parts[0] == "projects":
        inferred_property_type = path_parts[-1].replace("-", " ").title()

    return {
        "doc_id": f"dg_col_{slug}",
        "source_platform": "darglobal",
        "page_type": "property_collection",
        "doc_category": "collection_guide",
        "title": title,
        "country": inferred_country or "Multi",
        "property_type": inferred_property_type or "All",
        "breadcrumbs": breadcrumbs,
        "source_url": final_url,
        "canonical_url": canonical_url,
        "meta_description": meta_content("name", "description"),
        "og_title": meta_content("property", "og:title"),
        "og_description": meta_content("property", "og:description"),
        "json_ld_raw": json_ld_blocks,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def extract_main_markdown(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    for tag in soup.select("script, style, noscript, iframe, svg, header, footer"):
        tag.decompose()

    # Remove cookie banner text
    for div in soup.find_all(["div", "section"]):
        txt = div.get_text(" ", strip=True)
        if "We value our privacy" in txt and len(txt) < 400:
            div.decompose()

    main_container = soup.find("main") or soup.body or soup

    # Convert relative links to absolute URLs so every inline link in Markdown is clickable
    for a in main_container.find_all("a", href=True):
        abs_url = normalize_url(a["href"], base_url)
        if abs_url:
            a["href"] = abs_url

    raw_md = md(
        str(main_container),
        heading_style="ATX",
        bullets="-",
        strip=["img", "input", "form"],
    )
    return re.sub(r"\n{3,}", "\n\n", raw_md).strip()


async def goto_with_retry(page: Page, url: str, max_retries: int = 4) -> None:
    """Navigate with exponential backoff on 502/503/504 responses and timeouts."""
    for attempt in range(1, max_retries + 1):
        try:
            print(f"[INFO] Loading {url} (attempt {attempt}/{max_retries}) ...")
            response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            status = response.status if response else 0

            if status in (502, 503, 504, 429):
                raise RuntimeError(f"Server returned HTTP {status}")

            # Wait for actual page content instead of brittle networkidle
            await page.wait_for_selector("h1, main", timeout=20000)
            await page.wait_for_timeout(1500)
            return

        except Exception as exc:
            wait_sec = 5 * (2 ** (attempt - 1))  # 5s, 10s, 20s, 40s
            print(f"[WARN] Attempt {attempt} failed: {exc}")
            if attempt == max_retries:
                raise RuntimeError(
                    f"Failed to load {url} after {max_retries} attempts. "
                    "Site may be experiencing a 504 outage."
                ) from exc
            print(f"[INFO] Waiting {wait_sec}s before retry...")
            await asyncio.sleep(wait_sec)


async def scrape_collection_page(target_url: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = slugify_url(target_url)
    output_md = OUTPUT_DIR / f"{slug}.md"
    output_links_json = OUTPUT_DIR / f"{slug}_links.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = await context.new_page()

        # Block heavy assets so pages load faster and avoid gateway timeouts
        await page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )

        await goto_with_retry(page, target_url, max_retries=4)

        await dismiss_cookie_banner(page)
        await scroll_full_page(page)

        print("[INFO] Expanding accordions, FAQs, and 'Read More' sections...")
        await expand_all_accordions_and_read_more(page)

        print("[INFO] Clicking through 'Similar Properties' tabs...")
        similar_tabs = await extract_similar_tabs(page, page.url)

        final_url = page.url
        full_html = await page.content()
        if "504 Gateway Time-out" in full_html or "502 Bad Gateway" in full_html:
            raise RuntimeError(f"Received gateway error HTML from {target_url}; aborting save.")

        print("[INFO] Extracting and categorizing all clickable links...")
        categorized_links = await extract_all_clickable_links(page, final_url)

        await browser.close()

    # Merge any newly discovered links from clicked "Similar" tabs into main_project_or_nested_links
    existing_nested = {item["url"] for item in categorized_links["main_project_or_nested_links"]}
    existing_collections = {item["url"] for item in categorized_links["main_collection_or_filter_links"]}
    existing_chrome = {item["url"] for item in categorized_links["header_footer_links"]}

    for tab in similar_tabs:
        for item in tab["links"]:
            u = item["url"]
            if u in existing_nested or u in existing_collections or u in existing_chrome or u.rstrip("/") == final_url.rstrip("/"):
                continue
            existing_nested.add(u)
            categorized_links["main_project_or_nested_links"].append(
                {"label": f"[{tab['tab_label']}] {item['label']}", "url": u, "section": "similar_tab"}
            )

    soup = BeautifulSoup(full_html, "lxml")
    metadata = extract_metadata(soup, final_url, slug)
    main_markdown = extract_main_markdown(full_html, final_url)

    json_ld_raw = metadata.pop("json_ld_raw", [])
    metadata["word_count"] = len(main_markdown.split())
    metadata["nested_project_links_count"] = len(categorized_links["main_project_or_nested_links"])
    metadata["collection_links_count"] = len(categorized_links["main_collection_or_filter_links"])

    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()

    sections = [
        f"---\n{frontmatter}\n---",
        f"# {metadata['title']}\n\n**Source:** {final_url}",
        "## Page Content\n\n" + main_markdown,
    ]

    # Append clickable nested links section directly in Markdown for easy inspection & RAG citations
    link_md = ["## Clickable Links Discovered on This Page"]
    if categorized_links["main_project_or_nested_links"]:
        link_md.append("\n### Project & Nested Detail Links (Crawl Next)")
        for item in categorized_links["main_project_or_nested_links"]:
            link_md.append(f"- [{item['label']}]({item['url']})")

    if categorized_links["main_collection_or_filter_links"]:
        link_md.append("\n### Related Collection & Filter Links")
        for item in categorized_links["main_collection_or_filter_links"]:
            link_md.append(f"- [{item['label']}]({item['url']})")

    if categorized_links["pdf_or_asset_links"]:
        link_md.append("\n### Downloadable PDFs & Assets")
        for item in categorized_links["pdf_or_asset_links"]:
            link_md.append(f"- [{item['label']}]({item['url']})")

    sections.append("\n".join(link_md))

    if json_ld_raw:
        sections.append(
            "## Structured Schema Metadata (JSON-LD)\n\n```json\n"
            + json.dumps(json_ld_raw, indent=2, ensure_ascii=False)
            + "\n```"
        )

    output_md.write_text("\n\n---\n\n".join(sections) + "\n", encoding="utf-8")

    # Save structured link manifest for automated nested crawling
    manifest = {
        "source_url": final_url,
        "doc_id": metadata["doc_id"],
        "scraped_at": metadata["scraped_at"],
        "similar_tabs_discovered": similar_tabs,
        "links": categorized_links,
    }
    output_links_json.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[SUCCESS] Saved Markdown -> {output_md} ({metadata['word_count']} words)")
    print(f"[SUCCESS] Saved Clickable Links -> {output_links_json}")
    print(f"          - Project/Nested Links: {len(categorized_links['main_project_or_nested_links'])}")
    print(f"          - Collection/Filter Links: {len(categorized_links['main_collection_or_filter_links'])}")
    print(f"          - PDF/Asset Links: {len(categorized_links['pdf_or_asset_links'])}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape a DarGlobal collection page and all its clickable links.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Target DarGlobal collection URL")
    args = parser.parse_args()
    asyncio.run(scrape_collection_page(args.url))
