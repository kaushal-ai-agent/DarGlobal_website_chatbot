"""
Wasalt Live Sale Listings Crawler (`wasalt_sale_scraper.py`)

What it does:
End-to-end asynchronous crawler that navigates Wasalt residential sale search listings, iterates through paginated search results, visits property and project detail pages (or uses Next.js JSON API), parses all attributes using `wasalt_parsers`, logs data completeness with `DiagnosticTracker`, and writes records to SQLite and CSV.

Important Modules & Functions:
- `playwright.async_api`: Drives pagination, cookie management, and DOM evaluation.
- `httpx.AsyncClient`: Fast async HTTP client used for fetching detail pages with warm cookies.
- `wasalt_parsers`: Parsing routines (`parse_search_page_cards`, `parse_property_detail`, `parse_project_and_units`, `parse_auction_detail`).
- `wasalt_utils`: Storage, state management, and diagnostic logging routines.
- `fetch_page(...)`: Hybrid fetching engine attempting fast HTTPX retrieval with Playwright fallback.
- `process_card(...)`: Concurrently fetches and parses detail pages for all discovered cards with retry backoff.
- `run_scraper(...)`: Master CLI runner managing search pagination, circuit breakers, and CSV/diagnostic export.

Core Logic (One-Liner):
Navigates Wasalt search pagination with Playwright, harvests listing cards, concurrently fetches detail pages with HTTPX/Playwright, and upserts parsed entities into SQLite with diagnostic tracking.
"""

import argparse
import asyncio
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import Browser, BrowserContext, Response, Route, async_playwright

# Resolve project root and ensure it is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_DIR = Path(__file__).resolve().parent
for p in [str(PROJECT_ROOT), str(SCRAPER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from wasalt_parsers import (
    parse_auction_detail,
    parse_project_and_units,
    parse_property_detail,
    parse_search_page_cards,
)
from wasalt_utils import (
    CHECKPOINT_DIR,
    CSV_DIR,
    DB_PATH,
    DIAG_REPORT_JSON,
    DIAG_SUMMARY_MD,
    FAILED_JSONL,
    TABLE_FILES,
    TABLES_DIR,
    DiagnosticTracker,
    append_jsonl,
    build_page_url,
    detect_block_or_error,
    export_sqlite_to_csvs,
    init_sqlite,
    load_state,
    save_state,
    upsert_sqlite_rows,
)

BASE_SEARCH_URL = "https://wasalt.sa/en/sale/search?countryId=1&propertyFor=sale&type=residential"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
BLOCKED_URL_PATTERNS = (
    "maps.googleapis.com",
    "maps.gstatic.com",
    "mapbox",
    "leaflet",
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "facebook.net",
    "hotjar",
    "clarity.ms",
)


async def block_heavy_resources(route: Route) -> None:
    req = route.request
    if req.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
        return
    if any(p in req.url.lower() for p in BLOCKED_URL_PATTERNS):
        await route.abort()
        return
    await route.continue_()


async def fetch_html_playwright(
    context: BrowserContext,
    url: str,
    click_see_more: bool = True,
    intercept_project_units: bool = False,
) -> tuple[str, list[Any]]:
    page = await context.new_page()
    intercepted_json: list[Any] = []

    async def on_response(resp: Response) -> None:
        if not intercept_project_units:
            return
        try:
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype and any(
                kw in resp.url.lower() for kw in ("unit", "project", "propert", "invent")
            ):
                data = await resp.json()
                intercepted_json.append(data)
        except Exception:
            pass

    if intercept_project_units:
        page.on("response", on_response)

    try:
        await page.route("**/*", block_heavy_resources)
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if resp and resp.status in (403, 429, 502, 503, 504):
            raise RuntimeError(f"Playwright HTTP {resp.status}")

        await page.wait_for_selector("body", timeout=15000)
        await page.wait_for_timeout(1500)

        # Dismiss popups/ads
        for sel in ["button:has-text('Accept')", "button[aria-label='Close']", "[role='dialog'] button[class*='close' i]"]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=400):
                    await btn.click(timeout=800)
            except Exception:
                continue

        if intercept_project_units:
            await page.evaluate("window.scrollTo(0, 900)")
            await page.wait_for_timeout(1400)

        if click_see_more:
            for sel in ["button[title='See more']", "button:has-text('See more')", "button:has-text('View more')"]:
                try:
                    btns = page.locator(sel)
                    count = min(await btns.count(), 10)
                    for i in range(count):
                        b = btns.nth(i)
                        if await b.is_visible(timeout=300):
                            await b.scroll_into_view_if_needed(timeout=800)
                            await b.click(timeout=1000)
                            await page.wait_for_timeout(200)
                except Exception:
                    continue

        html = await page.content()
        block_reason = detect_block_or_error(html)
        if block_reason:
            raise RuntimeError(f"Blocked/Challenge detected in Playwright: {block_reason}")
        return html, intercepted_json
    finally:
        await page.close()


async def fetch_page(
    client: httpx.AsyncClient,
    context: BrowserContext,
    url: str,
    rec_type: str,
    diag: DiagnosticTracker,
    force_playwright: bool = False,
) -> tuple[str, list[Any]]:
    # Projects always use Playwright to capture dynamic unit lists & XHR
    if rec_type == "project":
        return await fetch_html_playwright(context, url, click_see_more=True, intercept_project_units=True)

    if not force_playwright:
        try:
            resp = await client.get(url, follow_redirects=True, timeout=25.0)
            if resp.status_code == 200:
                block_reason = detect_block_or_error(resp.text)
                if not block_reason:
                    if rec_type == "search" or ("<h1" in resp.text and "__NEXT_DATA__" in resp.text):
                        return resp.text, []
                else:
                    diag.log_block(url, block_reason, stage="httpx_fast_path")
        except Exception:
            pass

    return await fetch_html_playwright(context, url, click_see_more=(rec_type != "search"), intercept_project_units=False)


async def process_card(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    context: BrowserContext,
    card: dict[str, Any],
    diag: DiagnosticTracker,
    force_playwright: bool,
) -> dict[str, list[dict[str, Any]]]:
    url = card["source_url"]
    rec_type = card["record_type"]
    out: dict[str, list[dict[str, Any]]] = {k: [] for k in TABLE_FILES.keys()}

    async with sem:
        await asyncio.sleep(random.uniform(0.8, 1.8))  # Gentle per-request jitter
        for attempt in range(1, 4):
            try:
                html, intercepted = await fetch_page(client, context, url, rec_type, diag, force_playwright)
                if rec_type == "property":
                    out["wasalt_properties"].append(parse_property_detail(html, url, card, diag))
                elif rec_type == "project":
                    proj_row, unit_rows = parse_project_and_units(html, url, card, intercepted, diag)
                    out["wasalt_projects"].append(proj_row)
                    out["wasalt_project_units"].extend(unit_rows)
                elif rec_type == "auction":
                    out["wasalt_auctions"].append(parse_auction_detail(html, url, card, diag))
                return out
            except Exception as exc:
                err_str = str(exc)
                if "Blocked/Challenge" in err_str or "403" in err_str or "429" in err_str:
                    diag.log_block(url, err_str, stage=f"attempt_{attempt}")
                if attempt == 3:
                    print(f"  [FAIL] {url} -> {err_str[:120]}")
                    append_jsonl(
                        FAILED_JSONL,
                        [{"url": url, "record_type": rec_type, "error": err_str, "ts": datetime.now(timezone.utc).isoformat()}],
                    )
                    return out
                await asyncio.sleep(4 * attempt + random.uniform(0.5, 2.0))
    return out


async def run_scraper(
    start_page: int,
    max_pages: int | None,
    concurrency: int,
    force_playwright: bool,
    test_mode: bool,
) -> None:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_sqlite(DB_PATH)
    diag = DiagnosticTracker()

    state = load_state()
    scraped_urls: set[str] = set() if test_mode else set(state.get("scraped_urls", []))
    current_page = start_page if test_mode else max(start_page, state.get("last_completed_page", 0) + 1)

    sem = asyncio.Semaphore(concurrency)

    try:
        async with httpx.AsyncClient(headers=HEADERS, http2=False) as client:
            async with async_playwright() as p:
                browser: Browser = await p.chromium.launch(headless=True)
                context: BrowserContext = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    user_agent=HEADERS["User-Agent"],
                    locale="en-US",
                )

                pages_processed = 0
                while True:
                    if max_pages is not None and pages_processed >= max_pages:
                        break

                    page_url = build_page_url(BASE_SEARCH_URL, current_page)
                    print(f"\n[INFO] Fetching search page {current_page}: {page_url}")

                    try:
                        search_html, _ = await fetch_page(client, context, page_url, "search", diag, force_playwright)
                    except Exception as exc:
                        print(f"[ERROR] Failed search page {current_page}: {exc}")
                        append_jsonl(FAILED_JSONL, [{"url": page_url, "record_type": "search_page", "error": str(exc)}])
                        break

                    cards, total_pages = parse_search_page_cards(search_html, page_url)
                    if not cards:
                        try:
                            search_html, _ = await fetch_html_playwright(context, page_url, click_see_more=False)
                            cards, total_pages = parse_search_page_cards(search_html, page_url)
                        except Exception as exc:
                            print(f"[ERROR] Playwright search fallback failed on page {current_page}: {exc}")
                            break
                        if not cards:
                            print(f"[INFO] 0 cards on page {current_page}. Stopping.")
                            break

                    if total_pages:
                        state["total_pages_detected"] = total_pages

                    new_cards = [c for c in cards if c["source_url"] not in scraped_urls]
                    if test_mode:
                        new_cards = new_cards[:8]

                    print(f"[INFO] Page {current_page}/{total_pages or '?'}: {len(cards)} cards found, {len(new_cards)} to scrape.")

                    results = await asyncio.gather(
                        *[process_card(sem, client, context, c, diag, force_playwright) for c in new_cards]
                    )

                    merged: dict[str, list[dict[str, Any]]] = {k: [] for k in TABLE_FILES.keys()}
                    for res in results:
                        for tbl, rows in res.items():
                            merged[tbl].extend(rows)

                    for tbl in ("wasalt_properties", "wasalt_projects", "wasalt_project_units", "wasalt_auctions"):
                        rows = merged[tbl]
                        if rows:
                            append_jsonl(TABLE_FILES[tbl], rows)
                            upsert_sqlite_rows(conn, tbl, rows)

                    # Only mark successfully scraped URLs as completed (so blocked URLs get retried later!)
                    succeeded_urls = {
                        r["source_url"]
                        for tbl in ("wasalt_properties", "wasalt_projects", "wasalt_auctions")
                        for r in merged[tbl]
                    }
                    scraped_urls.update(succeeded_urls)

                    if new_cards and not succeeded_urls:
                        print(f"[CIRCUIT BREAKER] 0/{len(new_cards)} saved on page {current_page} (likely WAF/rate-limit). Cooling down 60s and stopping without advancing checkpoint.")
                        await asyncio.sleep(60)
                        break

                    if not test_mode:
                        # Only advance page checkpoint if majority of cards succeeded
                        if not new_cards or len(succeeded_urls) >= max(1, len(new_cards) // 2):
                            state["last_completed_page"] = current_page
                        state["scraped_urls"] = list(scraped_urls)
                        state["updated_at"] = datetime.now(timezone.utc).isoformat()
                        save_state(state)

                    print(
                        f"[SAVED] Page {current_page} -> "
                        f"properties: {len(merged['wasalt_properties'])}, "
                        f"projects: {len(merged['wasalt_projects'])}, "
                        f"project_units: {len(merged['wasalt_project_units'])}, "
                        f"auctions: {len(merged['wasalt_auctions'])}"
                    )

                    pages_processed += 1
                    if total_pages and current_page >= total_pages:
                        break
                    current_page += 1
                    await asyncio.sleep(1.0)

                await browser.close()
    finally:
        # Always export CSVs and Diagnostic Reports even if stopped early with Ctrl+C
        export_sqlite_to_csvs(conn)
        conn.close()
        diag.save_reports()
        print(f"\n[DONE] SQLite DB:          {DB_PATH}")
        print(f"[DONE] CSV Tables:         {CSV_DIR}")
        print(f"[DONE] Diagnostic Summary: {DIAG_SUMMARY_MD}")
        print(f"[DONE] Diagnostic JSON:    {DIAG_REPORT_JSON}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Robust multi-table Wasalt scraper with automated field diagnostics.")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=3, help="Keep <= 3 to avoid Cloudflare challenges")
    parser.add_argument("--force-playwright", action="store_true")
    parser.add_argument("--test", action="store_true", help="Test mode: page 1, first 8 listings + diagnostic report")
    args = parser.parse_args()

    asyncio.run(
        run_scraper(
            start_page=args.start_page,
            max_pages=1 if args.test else args.max_pages,
            concurrency=args.concurrency,
            force_playwright=args.force_playwright,
            test_mode=args.test,
        )
    )
