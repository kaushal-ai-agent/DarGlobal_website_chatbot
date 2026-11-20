"""
Wasalt Homepage & Navigation Discovery Scraper (`wasalt_home_scraper.py`)

What it does:
Crawls the Wasalt English portal homepage (https://wasalt.sa/en) with network-level asset blocking for speed, handles popups, expands homepage FAQ accordions, and inventories all interactive links and buttons across headers, search tabs, city cards, developer showcases, and footer directories. Outputs clean Markdown (01_wasalt_homepage.md) and an interactive UI element graph (01_wasalt_homepage_links_and_buttons.json).

Important Modules & Functions:
- `playwright.async_api`: Headless browser control with route-level interception.
- `block_heavy_resources(route)`: Aborts requests for images, media, fonts, tracking scripts, and map SDKs.
- `dismiss_popups(page)`: Closes cookie dialogs and promotional popups.
- `expand_homepage_faqs(page)`: Iterates and expands accordion FAQ triggers to capture full Q&A text in the DOM.
- `extract_links_and_buttons(page, base_url)`: Evaluates DOM JavaScript to extract and deduplicate all anchor links and button triggers categorized by DOM section (header, main, footer).
- `extract_clean_markdown(html, base_url)`: Strips UI chrome and converts main section HTML to clean ATX Markdown.
- `scrape_wasalt_homepage()`: Main orchestrator managing navigation, DOM extraction, and data saving.

Core Logic (One-Liner):
Executes a lightweight, asset-blocked Playwright crawl on Wasalt homepage, expands FAQ elements, and extracts both a clean narrative Markdown summary and a comprehensive link/button inventory.
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
from playwright.async_api import Page, Route, async_playwright

# Resolve project root and ensure it is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_DIR = Path(__file__).resolve().parent
for p in [str(PROJECT_ROOT), str(SCRAPER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

TARGET_URL = "https://wasalt.sa/en"
OUTPUT_DIR = PROJECT_ROOT / "data" / "wasalt"
OUTPUT_MD = OUTPUT_DIR / "01_wasalt_homepage.md"
OUTPUT_JSON = OUTPUT_DIR / "01_wasalt_homepage_links_and_buttons.json"

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
BLOCKED_URL_PATTERNS = (
    "maps.googleapis.com",
    "maps.gstatic.com",
    "mapbox",
    "google-analytics",
    "googletagmanager",
    "facebook.net",
    "doubleclick",
    "hotjar",
    "clarity.ms",
    "intercom",
    "zendesk",
)


def clean_text(text: str) -> str:
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


async def block_heavy_resources(route: Route) -> None:
    req = route.request
    if req.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
        return
    url_lower = req.url.lower()
    if any(pat in url_lower for pat in BLOCKED_URL_PATTERNS):
        await route.abort()
        return
    await route.continue_()


async def dismiss_popups(page: Page) -> None:
    """Close cookie banners or promotional modals if present."""
    popup_selectors = [
        "button:has-text('Accept')",
        "button:has-text('ACCEPT')",
        "button:has-text('Got it')",
        "button[aria-label='Close']",
        "button[aria-label='close']",
        "[role='dialog'] button[class*='close' i]",
    ]
    for sel in popup_selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1200):
                await btn.click(timeout=1500)
                await page.wait_for_timeout(300)
        except Exception:
            continue


async def expand_homepage_faqs(page: Page) -> None:
    """Expand FAQ accordion items on the homepage so all Q&A text is captured."""
    faq_selectors = [
        "[class*='faq' i] button",
        "[class*='accordion' i] button",
        "[aria-expanded='false']",
    ]
    for sel in faq_selectors:
        try:
            loc = page.locator(sel)
            count = min(await loc.count(), 25)
            for i in range(count):
                el = loc.nth(i)
                try:
                    if await el.is_visible(timeout=400):
                        await el.click(timeout=800)
                        await page.wait_for_timeout(150)
                except Exception:
                    continue
        except Exception:
            continue


async def extract_links_and_buttons(page: Page, base_url: str) -> dict[str, Any]:
    """Extract every link (<a>) and interactive button (<button> / role='button') on the page."""
    raw = await page.evaluate(
        """() => {
            const links = [];
            for (const a of document.querySelectorAll('a[href]')) {
                const section = a.closest('header, nav') ? 'header'
                    : a.closest('footer') ? 'footer' : 'main';
                links.push({
                    text: (a.innerText || a.getAttribute('aria-label') || a.getAttribute('title') || '').trim(),
                    href: a.getAttribute('href') || '',
                    section
                });
            }

            const buttons = [];
            const btnNodes = document.querySelectorAll('button, [role="button"], input[type="submit"], input[type="button"]');
            for (const b of btnNodes) {
                const text = (b.innerText || b.getAttribute('aria-label') || b.getAttribute('title') || b.value || '').trim();
                const section = b.closest('header, nav') ? 'header'
                    : b.closest('footer') ? 'footer' : 'main';
                if (text) {
                    buttons.push({
                        text,
                        type: b.getAttribute('type') || b.tagName.toLowerCase(),
                        aria_label: b.getAttribute('aria-label') || null,
                        title: b.getAttribute('title') || null,
                        section
                    });
                }
            }
            return { links, buttons };
        }"""
    )

    unique_links: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in raw["links"]:
        url = normalize_url(item["href"], base_url)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique_links.append(
            {
                "label": clean_text(item["text"]) or urlparse(url).path or url,
                "url": url,
                "section": item["section"],
            }
        )

    unique_buttons: list[dict[str, Any]] = []
    seen_btns: set[tuple[str, str]] = set()
    for b in raw["buttons"]:
        label = clean_text(b["text"])
        key = (label.lower(), b["section"])
        if not label or key in seen_btns:
            continue
        seen_btns.add(key)
        unique_buttons.append(
            {
                "label": label,
                "section": b["section"],
                "type": b["type"],
                "title": b["title"],
            }
        )

    return {"links": unique_links, "buttons": unique_buttons}


def extract_clean_markdown(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.select("script, style, noscript, iframe, svg, img, picture, video"):
        tag.decompose()

    main = soup.find("main") or soup.body or soup
    for a in main.find_all("a", href=True):
        abs_url = normalize_url(a["href"], base_url)
        if abs_url:
            a["href"] = abs_url

    raw_md = md(str(main), heading_style="ATX", bullets="-", strip=["img", "input"])
    return re.sub(r"\n{3,}", "\n\n", raw_md).strip()


async def scrape_wasalt_homepage() -> None:
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
        await page.route("**/*", block_heavy_resources)

        print(f"[INFO] Loading {TARGET_URL} (images/media/fonts/maps blocked)...")
        response = await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=45000)
        if response and response.status >= 500:
            raise RuntimeError(f"Server returned HTTP {response.status}")

        await page.wait_for_selector("body", timeout=15000)
        await page.wait_for_timeout(1500)

        await dismiss_popups(page)
        await expand_homepage_faqs(page)

        final_url = page.url
        html = await page.content()
        ui_elements = await extract_links_and_buttons(page, final_url)
        await browser.close()

    soup = BeautifulSoup(html, "lxml")
    title = clean_text(soup.title.string) if soup.title and soup.title.string else "Wasalt Homepage"
    desc_tag = soup.find("meta", attrs={"name": "description"})
    meta_desc = clean_text(desc_tag["content"]) if desc_tag and desc_tag.get("content") else None

    body_md = extract_clean_markdown(html, final_url)

    metadata = {
        "doc_id": "ws_00_homepage",
        "source_platform": "wasalt",
        "page_type": "homepage",
        "title": title,
        "source_url": final_url,
        "meta_description": meta_desc,
        "word_count": len(body_md.split()),
        "links_count": len(ui_elements["links"]),
        "buttons_count": len(ui_elements["buttons"]),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()

    links_md = ["## All Extracted Links"]
    for item in ui_elements["links"]:
        links_md.append(f"- **[{item['section']}]** [{item['label']}]({item['url']})")

    buttons_md = ["## All Extracted Buttons"]
    for btn in ui_elements["buttons"]:
        buttons_md.append(f"- **[{btn['section']}]** `{btn['label']}` *(type: {btn['type']})*")

    full_md = "\n\n---\n\n".join(
        [
            f"---\n{frontmatter}\n---",
            f"# {title}\n\n**Source:** {final_url}",
            "## Homepage Content\n\n" + body_md,
            "\n".join(links_md),
            "\n".join(buttons_md),
        ]
    ) + "\n"

    OUTPUT_MD.write_text(full_md, encoding="utf-8")
    OUTPUT_JSON.write_text(
        json.dumps(
            {
                "source_url": final_url,
                "scraped_at": metadata["scraped_at"],
                **ui_elements,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"[SUCCESS] Markdown saved -> {OUTPUT_MD} ({metadata['word_count']} words)")
    print(f"[SUCCESS] Links & Buttons saved -> {OUTPUT_JSON} ({metadata['links_count']} links, {metadata['buttons_count']} buttons)")


if __name__ == "__main__":
    asyncio.run(scrape_wasalt_homepage())
