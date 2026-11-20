"""
DarGlobal Homepage & Navigation Graph Scraper (`darglobal_home_scraper.py`)

What it does:
Scrapes the official DarGlobal luxury real estate homepage (https://darglobal.co.uk/), extracts full page DOM content, interacts with the dynamic hamburger drawer navigation to uncover sub-categories, extracts footer links, and parses corporate/contact metadata (phones, WhatsApp, JSON-LD schemas). Outputs clean Markdown (01_homepage.md) and structured link graph JSON (homepage_links.json).

Important Modules & Functions:
- `playwright.async_api`: Asynchronous headless browser driving page rendering, dynamic scroll, and drawer clicks.
- `bs4.BeautifulSoup` & `markdownify`: HTML cleansing, semantic element decomposition, and Markdown formatting.
- `dismiss_cookie_banner(page)`: Automatically dismisses cookie consent dialogs to prevent DOM obstruction.
- `scroll_full_page(page)`: Incrementally scrolls down to fire lazy-load observers, then scrolls back up.
- `extract_hamburger_menu(page)`: Clicks the hamburger button, hovers/clicks expandable categories (Building Communities, Real Estate Developments, Asset Management, Collaborations, Newsroom) to discover hidden sub-links.
- `extract_metadata(soup, final_url)`: Extracts SEO titles, OpenGraph tags, canonical URLs, contact channels, and JSON-LD structured blocks.
- `extract_main_markdown(html)`: Converts main content container to clean ATX Markdown with absolute URLs.
- `scrape_darglobal_homepage()`: Main orchestrator driving the entire fetch, parse, and file output cycle.

Core Logic (One-Liner):
Uses Playwright to interactively render DarGlobal homepage, expand hidden navigation menus, dismiss banners, and compile both a rich RAG-ready Markdown document and a complete link graph JSON manifest.
"""

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

TARGET_URL = "https://darglobal.co.uk/"
OUTPUT_DIR = PROJECT_ROOT / "data" / "darglobal"
OUTPUT_MD = OUTPUT_DIR / "01_homepage.md"
OUTPUT_LINKS_JSON = OUTPUT_DIR / "homepage_links.json"


def clean_text(text: str) -> str:
    """Collapse excessive whitespace while preserving readability."""
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_url(href: str, base_url: str = TARGET_URL) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "#")):
        return None
    if href.startswith(("tel:", "mailto:")):
        return href
    return urljoin(base_url, href)


async def dismiss_cookie_banner(page: Page) -> None:
    """Accept or dismiss the cookie banner so it doesn't pollute text or block clicks."""
    selectors = [
        "button:has-text('ACCEPT')",
        "button:has-text('Accept')",
        "a:has-text('ACCEPT')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def scroll_full_page(page: Page) -> None:
    """Scroll down in steps to trigger all lazy-loaded sections and animations."""
    total_height = await page.evaluate("document.body.scrollHeight")
    step = 600
    current = 0
    while current < total_height:
        await page.evaluate(f"window.scrollTo(0, {current})")
        await page.wait_for_timeout(350)
        current += step
        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height > total_height:
            total_height = new_height
    # Scroll back to top so header/hamburger menu is accessible
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)


async def extract_hamburger_menu(page: Page) -> dict[str, Any]:
    """
    Click the hamburger menu and hover/click each parent category
    (e.g., BUILDING COMMUNITIES, REAL ESTATE DEVELOPMENTS, COLLABORATIONS)
    to capture dynamically rendered sub-links.
    """
    menu_data: dict[str, Any] = {
        "categories": {},
        "direct_links": [],
        "social_links": [],
    }

    # Locate the top-left hamburger button
    hamburger_selectors = [
        "header button[aria-label*='menu' i]",
        "header button",
        "button[class*='menu' i]",
        "div[class*='hamburger' i]",
        "svg[class*='menu' i]",
    ]
    opened = False
    for sel in hamburger_selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click()
                await page.wait_for_timeout(800)
                # Confirm drawer opened by checking for known menu text
                if await page.locator("text=BUILDING COMMUNITIES").first.is_visible(timeout=2000):
                    opened = True
                    break
        except Exception:
            continue

    if not opened:
        print("[WARN] Could not automatically open hamburger menu drawer.")
        return menu_data

    # Known expandable parent categories seen in the navigation drawer
    expandable_parents = [
        "BUILDING COMMUNITIES",
        "REAL ESTATE DEVELOPMENTS",
        "ASSET MANAGEMENT",
        "COLLABORATIONS",
        "NEWSROOM",
    ]

    for parent in expandable_parents:
        try:
            parent_loc = page.locator(f"text={parent}").first
            if await parent_loc.is_visible(timeout=1500):
                await parent_loc.hover()
                await parent_loc.click()
                await page.wait_for_timeout(600)

                # Extract all visible links currently inside the menu overlay
                sub_links = await page.evaluate(
                    """(parentText) => {
                        const results = [];
                        const anchors = document.querySelectorAll('nav a, [role="dialog"] a, [class*="menu" i] a, [class*="drawer" i] a, [class*="sidebar" i] a');
                        for (const a of anchors) {
                            const rect = a.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) continue;
                            const text = (a.innerText || '').trim();
                            const href = a.getAttribute('href') || '';
                            if (text && href && text.toUpperCase() !== parentText) {
                                results.push({ text, href, x: rect.x });
                            }
                        }
                        return results;
                    }""",
                    parent,
                )

                # Right-column sub-items typically have a larger x-coordinate than the left nav column
                cleaned_subs = []
                seen = set()
                for item in sub_links:
                    url = normalize_url(item["href"])
                    if not url or url in seen:
                        continue
                    # Heuristic: keep links that appear in the right sub-panel (x > 350px on 1440px viewport)
                    if item["x"] > 350:
                        seen.add(url)
                        cleaned_subs.append({"label": clean_text(item["text"]), "url": url})

                menu_data["categories"][parent] = cleaned_subs
        except Exception as exc:
            print(f"[WARN] Failed expanding menu category '{parent}': {exc}")

    # Capture direct links & social links from the open drawer
    drawer_links = await page.evaluate(
        """() => {
            const out = [];
            const anchors = document.querySelectorAll('nav a, [role="dialog"] a, [class*="menu" i] a, [class*="drawer" i] a');
            for (const a of anchors) {
                const rect = a.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                out.push({
                    text: (a.innerText || a.getAttribute('aria-label') || '').trim(),
                    href: a.getAttribute('href') || ''
                });
            }
            return out;
        }"""
    )

    seen_direct = set()
    social_domains = ("instagram.com", "facebook.com", "twitter.com", "x.com", "linkedin.com", "linktr.ee", "youtube.com")
    for item in drawer_links:
        url = normalize_url(item["href"])
        if not url or url in seen_direct:
            continue
        seen_direct.add(url)
        label = clean_text(item["text"])
        if any(dom in url.lower() for dom in social_domains):
            menu_data["social_links"].append({"label": label or urlparse(url).netloc, "url": url})
        elif label:
            menu_data["direct_links"].append({"label": label, "url": url})

    # Close drawer (press Escape)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(400)
    return menu_data


def extract_metadata(soup: BeautifulSoup, final_url: str) -> dict[str, Any]:
    """Extract SEO, OpenGraph, JSON-LD, and contact metadata useful for RAG."""
    title = clean_text(soup.title.string) if soup.title and soup.title.string else "DarGlobal Homepage"

    def meta_content(attr: str, val: str) -> str | None:
        tag = soup.find("meta", attrs={attr: val})
        return clean_text(tag["content"]) if tag and tag.get("content") else None

    canonical_tag = soup.find("link", rel="canonical")
    canonical_url = canonical_tag["href"] if canonical_tag and canonical_tag.get("href") else final_url

    # Extract JSON-LD structured data blocks if present
    json_ld_blocks = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            parsed = json.loads(script.string or "")
            json_ld_blocks.append(parsed)
        except Exception:
            continue

    # Extract phone & WhatsApp contact links
    phones = sorted({a["href"].replace("tel:", "").strip() for a in soup.find_all("a", href=re.compile(r"^tel:", re.I))})
    whatsapp = sorted({a["href"].strip() for a in soup.find_all("a", href=re.compile(r"whatsapp\.com|wa\.me", re.I))})

    return {
        "doc_id": "dg_00_homepage",
        "source_platform": "darglobal",
        "page_type": "homepage",
        "doc_category": "corporate",
        "title": title,
        "source_url": final_url,
        "canonical_url": canonical_url,
        "meta_description": meta_content("name", "description"),
        "og_title": meta_content("property", "og:title"),
        "og_description": meta_content("property", "og:description"),
        "language": soup.html.get("lang", "en") if soup.html else "en",
        "contact_phones": phones,
        "contact_whatsapp": whatsapp,
        "has_json_ld": len(json_ld_blocks) > 0,
        "json_ld_raw": json_ld_blocks,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def extract_footer_sections(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Extract structured footer columns and governance/legal links."""
    footer = soup.find("footer")
    if not footer:
        return []

    links = []
    seen = set()
    for a in footer.find_all("a", href=True):
        url = normalize_url(a["href"])
        if not url or url in seen:
            continue
        seen.add(url)
        label = clean_text(a.get_text(" ", strip=True)) or clean_text(a.get("aria-label", ""))
        if not label:
            label = urlparse(url).netloc
        links.append({"label": label, "url": url})
    return links


def extract_main_markdown(html: str) -> str:
    """Convert the main body HTML to clean Markdown without nav/footer/cookie noise."""
    soup = BeautifulSoup(html, "lxml")

    # Remove non-content elements
    for tag in soup.select("script, style, noscript, iframe, svg, header, footer"):
        tag.decompose()

    # Remove cookie consent banner if still in DOM
    for div in soup.find_all(["div", "section"]):
        txt = div.get_text(" ", strip=True)
        if "We value our privacy" in txt and len(txt) < 400:
            div.decompose()

    main_container = soup.find("main") or soup.body or soup

    # Convert relative hrefs to absolute URLs so every link in Markdown is a valid source
    for a in main_container.find_all("a", href=True):
        abs_url = normalize_url(a["href"])
        if abs_url:
            a["href"] = abs_url

    raw_md = md(
        str(main_container),
        heading_style="ATX",
        bullets="-",
        strip=["img", "button", "input", "form"],
    )

    # Clean up excessive blank lines
    cleaned_md = re.sub(r"\n{3,}", "\n\n", raw_md).strip()
    return cleaned_md


async def scrape_darglobal_homepage() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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

        print(f"[INFO] Loading {TARGET_URL} ...")
        await page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(1500)

        await dismiss_cookie_banner(page)
        await scroll_full_page(page)

        final_url = page.url
        full_html = await page.content()

        # Open hamburger menu to discover hidden sub-navigation links
        print("[INFO] Extracting interactive hamburger menu...")
        menu_data = await extract_hamburger_menu(page)

        await browser.close()

    soup = BeautifulSoup(full_html, "lxml")
    metadata = extract_metadata(soup, final_url)
    main_markdown = extract_main_markdown(full_html)
    footer_links = extract_footer_sections(soup)

    # Collect all unique discovered URLs for the next scraping step
    all_page_links = {}
    for a in soup.find_all("a", href=True):
        url = normalize_url(a["href"])
        if url and url.startswith("http"):
            label = clean_text(a.get_text(" ", strip=True))
            all_page_links.setdefault(url, label or url)
    for cat_links in menu_data["categories"].values():
        for item in cat_links:
            all_page_links.setdefault(item["url"], item["label"])
    for item in menu_data["direct_links"]:
        all_page_links.setdefault(item["url"], item["label"])

    # Prepare YAML frontmatter (exclude bulky raw JSON-LD from YAML header)
    json_ld_raw = metadata.pop("json_ld_raw", [])
    metadata["word_count"] = len(main_markdown.split())
    metadata["discovered_links_count"] = len(all_page_links)

    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()

    # Build final Markdown document
    sections = [
        f"---\n{frontmatter}\n---",
        f"# {metadata['title']}\n\n**Source:** {final_url}",
        "## Main Page Content\n\n" + main_markdown,
    ]

    # Append Navigation Menu Hierarchy (crucial for chatbot & next-step crawling)
    nav_lines = ["## Site Navigation Menu (Interactive Drawer)"]
    if menu_data["categories"]:
        for cat, items in menu_data["categories"].items():
            nav_lines.append(f"\n### {cat}")
            if items:
                for item in items:
                    nav_lines.append(f"- [{item['label']}]({item['url']})")
            else:
                nav_lines.append("- *(No sub-links detected)*")
    if menu_data["direct_links"]:
        nav_lines.append("\n### Top-Level Menu Links")
        for item in menu_data["direct_links"]:
            nav_lines.append(f"- [{item['label']}]({item['url']})")
    sections.append("\n".join(nav_lines))

    # Append Footer & Governance Links
    if footer_links:
        footer_lines = ["## Footer Collections, Corporate & Governance Links"]
        for item in footer_links:
            footer_lines.append(f"- [{item['label']}]({item['url']})")
        sections.append("\n".join(footer_lines))

    # Append Contact & Social Metadata
    contact_lines = ["## Contact & Social Channels"]
    for phone in metadata["contact_phones"]:
        contact_lines.append(f"- **Phone:** `{phone}`")
    for wa in metadata["contact_whatsapp"]:
        contact_lines.append(f"- **WhatsApp:** {wa}")
    for soc in menu_data["social_links"]:
        contact_lines.append(f"- **Social ({soc['label']}):** {soc['url']}")
    sections.append("\n".join(contact_lines))

    # Append JSON-LD if found
    if json_ld_raw:
        sections.append(
            "## Structured Schema Metadata (JSON-LD)\n\n```json\n"
            + json.dumps(json_ld_raw, indent=2, ensure_ascii=False)
            + "\n```"
        )

    final_markdown = "\n\n---\n\n".join(sections) + "\n"
    OUTPUT_MD.write_text(final_markdown, encoding="utf-8")

    # Save link manifest for Step 2 (crawling sub-pages one by one)
    manifest = {
        "source_url": final_url,
        "scraped_at": metadata["scraped_at"],
        "hamburger_menu": menu_data,
        "footer_links": footer_links,
        "all_unique_links": [{"url": u, "label": l} for u, l in sorted(all_page_links.items())],
    }
    OUTPUT_LINKS_JSON.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[SUCCESS] Saved Markdown -> {OUTPUT_MD} ({metadata['word_count']} words)")
    print(f"[SUCCESS] Saved Link Manifest -> {OUTPUT_LINKS_JSON} ({len(all_page_links)} unique URLs)")


if __name__ == "__main__":
    asyncio.run(scrape_darglobal_homepage())
