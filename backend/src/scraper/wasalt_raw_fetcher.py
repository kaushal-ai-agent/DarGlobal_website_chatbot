"""
Wasalt Stealth Raw Search Fetcher (`wasalt_raw_fetcher.py`)

What it does:
High-speed, anti-detection web crawler that harvests raw Wasalt search pages without triggering Cloudflare blocks. Uses `patchright` (stealth Playwright fork) with persistent browser profiles for initial session warmup and cookie clearance, then switches to `curl_cffi` (impersonating real Chrome TLS/JA3 fingerprints) for ultra-fast async fetching. Caches all compressed HTML into SQLite (`data/wasalt/raw_cache/wasalt_sale_raw.db`).

Important Modules & Functions:
- `patchright.async_api`: Stealth Chromium engine with real Chrome browser channels and persistent profile state.
- `curl_cffi.AsyncSession`: High-throughput HTTP client with authentic browser TLS fingerprint impersonation (`impersonate="chrome"`).
- `TokenBucket`: O(1) async rate limiter with organic jitter preventing IP rate limiting.
- `PassASession`: Hybrid session manager that warms up cookies in Patchright, synchronizes `cf_clearance`, and routes requests through `curl_cffi` with automatic single-flight re-warmup on blocks.
- `run_pass_a_fetcher(args)`: CLI orchestrator managing concurrency, pagination discovery, batch commits to `RawCacheDB`, and graceful SIGINT/SIGTERM shutdowns.

Core Logic (One-Liner):
Clears Cloudflare Turnstile with stealth Patchright browser sessions, synchronizes session cookies to TLS-impersonating `curl_cffi`, and streams compressed raw search pages into SQLite.
"""

import argparse
import asyncio
import json
import random
import signal
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

try:
    from curl_cffi import AsyncSession
except ImportError:
    from curl_cffi.requests import AsyncSession

from patchright.async_api import BrowserContext, Route, async_playwright

# Resolve project root and ensure it is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRAPER_DIR = Path(__file__).resolve().parent
for p in [str(PROJECT_ROOT), str(SCRAPER_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from wasalt_raw_store import (
    DATA_ROOT,
    DEFAULT_RAW_DB,
    RawCacheDB,
    detect_block_or_error,
    log_error_context,
    quick_inspect_search_html,
    setup_logger,
)

BASE_SEARCH_URL = "https://wasalt.sa/en/sale/search?countryId=1&propertyFor=sale&type=residential"
WARMUP_URL = "https://wasalt.sa/en"
PROFILE_DIR = DATA_ROOT / "checkpoints" / "patchright_profile"

BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
BLOCKED_URL_PATTERNS = (
    "maps.googleapis.com",
    "maps.gstatic.com",
    "mapbox",
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "facebook.net",
    "hotjar",
    "clarity.ms",
)


def build_page_url(base_url: str, page_num: int) -> str:
    parsed = urlparse(base_url)
    qs = parse_qs(parsed.query)
    qs["page"] = [str(page_num)]
    flat_qs = {k: v[0] for k, v in qs.items()}
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{urlencode(flat_qs)}"


# ---------------------------------------------------------------------------
# O(1) Async Token Bucket Rate Limiter
# ---------------------------------------------------------------------------

class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float = 2.0) -> None:
        self.rate = max(0.1, rate_per_sec)
        self.capacity = max(1.0, capacity)
        self.tokens = self.capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    break
                wait_s = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait_s)
        # Small organic jitter so requests don't fire on exact clock ticks
        await asyncio.sleep(random.uniform(0.05, 0.25))


# ---------------------------------------------------------------------------
# Hybrid Session: Patchright Warmup -> curl_cffi Chrome TLS Impersonation
# ---------------------------------------------------------------------------

async def _block_heavy(route: Route) -> None:
    req = route.request
    if req.resource_type in BLOCKED_RESOURCE_TYPES or any(p in req.url.lower() for p in BLOCKED_URL_PATTERNS):
        await route.abort()
        return
    await route.continue_()


class PassASession:
    def __init__(self, logger, headless: bool = False, proxy: str | None = None) -> None:
        self.logger = logger
        self.headless = headless
        self.proxy = proxy
        self._pw = None
        self._context: BrowserContext | None = None
        self._http: AsyncSession | None = None
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_ts: float = 0.0
        self._cookies: dict[str, str] = {}

    async def start(self) -> None:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()

        # Patchright best practice: real Chrome channel, no_viewport=True, NO custom UA injection
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(PROFILE_DIR),
            "headless": self.headless,
            "no_viewport": True,
        }
        if self.proxy:
            launch_kwargs["proxy"] = {"server": self.proxy}

        try:
            self._context = await self._pw.chromium.launch_persistent_context(channel="chrome", **launch_kwargs)
            self.logger.info("Launched Patchright with local Chrome channel (headless=%s)", self.headless)
        except Exception as exc:
            self.logger.warning("Local Chrome channel unavailable (%s); falling back to bundled Chromium.", exc)
            self._context = await self._pw.chromium.launch_persistent_context(**launch_kwargs)

        await self.warmup(WARMUP_URL)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
        if self._context is not None:
            await self._context.close()
        if self._pw is not None:
            await self._pw.stop()

    async def _rebuild_http(self) -> None:
        if self._http is not None:
            await self._http.close()
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        self._http = AsyncSession(
            impersonate="chrome",
            headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": WARMUP_URL,
            },
            cookies=self._cookies,
            proxies=proxies,
            timeout=30,
        )

    async def warmup(self, target_url: str) -> None:
        assert self._context is not None
        page = await self._context.new_page()
        try:
            self.logger.info("Warming session / clearing WAF on %s ...", target_url)
            await page.route("**/*", _block_heavy)
            resp = await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else 0
            self.logger.debug("Warmup initial HTTP status=%s on %s", status, target_url)

            # Wait up to 20s for Cloudflare JS/Turnstile challenge to auto-resolve
            for step in range(10):
                await page.wait_for_timeout(2000)
                html = await page.content()
                if detect_block_or_error(html) is None or "wasalt" in (await page.title()).lower():
                    break
                self.logger.debug("Warmup waiting for challenge clearance (step %d/10)...", step + 1)

            raw_cookies = await self._context.cookies()
            self._cookies = {c["name"]: c["value"] for c in raw_cookies if "wasalt" in c.get("domain", "")}
            self._last_refresh_ts = time.monotonic()
            self.logger.info(
                "Session synced: %d cookies captured (cf_clearance=%s)",
                len(self._cookies),
                "cf_clearance" in self._cookies,
            )
            await self._rebuild_http()
        finally:
            await page.close()

    async def refresh_if_stale(self, trigger_url: str, cooldown_sec: float = 20.0) -> None:
        """Single-flight lock so concurrent workers don't stampede Cloudflare refreshes."""
        async with self._refresh_lock:
            since_last = time.monotonic() - self._last_refresh_ts
            if since_last < cooldown_sec:
                self.logger.debug("Skipping redundant refresh (last refresh %.1fs ago).", since_last)
                return
            self.logger.warning("Triggering WAF session refresh via Patchright (trigger=%s)...", trigger_url)
            await asyncio.sleep(random.uniform(2.0, 4.0))
            await self.warmup(trigger_url)

    async def fetch_via_patchright(self, url: str) -> tuple[int, str, str]:
        assert self._context is not None
        page = await self._context.new_page()
        try:
            await page.route("**/*", _block_heavy)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else 0
            for _ in range(6):
                await page.wait_for_timeout(1500)
                html = await page.content()
                if detect_block_or_error(html) is None:
                    return (200 if status == 403 else status), html, "patchright"
            html = await page.content()
            return status, html, "patchright"
        finally:
            await page.close()

    async def fetch_search_page(self, url: str, page_num: int, force_browser: bool = False) -> tuple[int, str, str]:
        """Returns (http_status, html, fetch_method) or raises RuntimeError with full diagnostic context."""
        if force_browser:
            status, html, method = await self.fetch_via_patchright(url)
            reason = detect_block_or_error(html)
            if status != 200 or reason:
                raise RuntimeError(f"patchright_failed status={status} reason={reason}")
            return status, html, method

        assert self._http is not None
        t0 = time.monotonic()
        resp = await self._http.get(url, allow_redirects=True)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        cf_ray = resp.headers.get("cf-ray")
        cf_mitigated = resp.headers.get("cf-mitigated")

        if resp.status_code == 200:
            reason = detect_block_or_error(resp.text)
            if reason is None:
                self.logger.debug(
                    "HTTP 200 page=%d bytes=%d latency=%dms cf_ray=%s",
                    page_num, len(resp.text), elapsed_ms, cf_ray,
                )
                return 200, resp.text, "curl_cffi"
            raise RuntimeError(f"gatekeeper_rejected reason={reason} cf_ray={cf_ray} latency={elapsed_ms}ms")

        raise RuntimeError(
            f"http_error status={resp.status_code} cf_mitigated={cf_mitigated} cf_ray={cf_ray} latency={elapsed_ms}ms"
        )


# ---------------------------------------------------------------------------
# Producer-Consumer Orchestrator (Bounded Queue + Batch Commits + Circuit Breaker)
# ---------------------------------------------------------------------------

async def run_pass_a_fetcher(args: argparse.Namespace) -> None:
    logger = setup_logger("fetch_wasalt_search_raw", verbose=args.verbose)
    db = RawCacheDB(Path(args.db))
    db.set_meta("base_search_url", args.base_url)

    stop_event = asyncio.Event()

    def _handle_signal(*_: Any) -> None:
        logger.warning("Interrupt signal received — draining in-flight tasks and flushing batch cleanly...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except Exception:
            pass

    session = PassASession(logger=logger, headless=args.headless, proxy=args.proxy)
    bucket = TokenBucket(rate_per_sec=args.rps, capacity=max(2.0, float(args.concurrency)))

    write_lock = asyncio.Lock()
    write_buffer: list[dict[str, Any]] = []
    stats_counters = {"saved": 0, "failed": 0, "consecutive_blocks": 0}
    start_ts = time.monotonic()

    async def flush_buffer(force: bool = False) -> None:
        async with write_lock:
            if not write_buffer or (not force and len(write_buffer) < args.batch_size):
                return
            batch = list(write_buffer)
            write_buffer.clear()
        db.upsert_pages_batch(batch)
        elapsed = max(time.monotonic() - start_ts, 0.1)
        rate = stats_counters["saved"] / elapsed
        logger.info(
            "BATCH_COMMITTED count=%d | total_saved=%d | failed=%d | avg_rate=%.2f pages/s",
            len(batch), stats_counters["saved"], stats_counters["failed"], rate,
        )

    try:
        await session.start()

        # Step 1: Discover total_pages from page 1 (or cached DB metadata)
        cached_pages = db.get_cached_page_nums()
        logger.info("Existing cache status: %s", json.dumps(db.get_stats()))

        discovered_total = db.get_stats()["max_total_pages_seen"]
        if discovered_total is None or 1 not in cached_pages:
            p1_url = build_page_url(args.base_url, 1)
            logger.info("Probing page 1 to discover total pagination: %s", p1_url)
            try:
                status, html, method = await session.fetch_search_page(p1_url, 1, force_browser=args.force_browser)
            except Exception as exc:
                log_error_context(logger, "probe_page_1_curl", p1_url, exc, page_num=1)
                await session.refresh_if_stale(p1_url, cooldown_sec=0.0)
                status, html, method = await session.fetch_via_patchright(p1_url)
                reason = detect_block_or_error(html)
                if status != 200 or reason:
                    raise RuntimeError(f"Page 1 probe failed even via Patchright: status={status} reason={reason}")

            cards_cnt, total_pages, has_next = quick_inspect_search_html(html)
            raw_bytes = html.encode("utf-8")
            comp_bytes = zlib.compress(raw_bytes, level=6)
            db.upsert_pages_batch(
                [{
                    "page_num": 1,
                    "url": p1_url,
                    "http_status": status,
                    "cards_detected": cards_cnt,
                    "total_pages_detected": total_pages,
                    "has_next_data": has_next,
                    "raw_html_gz": comp_bytes,
                    "raw_size_bytes": len(raw_bytes),
                    "compressed_size_bytes": len(comp_bytes),
                    "fetch_method": method,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }]
            )
            cached_pages.add(1)
            discovered_total = total_pages or discovered_total or 1
            logger.info(
                "Page 1 cached (%s): cards=%d, total_pages=%s, has_next_data=%s, size=%dKB->%dKB",
                method, cards_cnt, discovered_total, has_next, len(raw_bytes) // 1024, len(comp_bytes) // 1024,
            )

        # Step 2: Build target work list (O(1) skip of cached pages)
        if args.retry_failed:
            pending_pages = [p for p in db.get_failed_page_nums() if p not in cached_pages]
            logger.info("Retry-failed mode: %d failed pages queued.", len(pending_pages))
        else:
            end_page = args.end_page if args.end_page is not None else discovered_total
            if args.max_pages is not None:
                # Cap total NEW pages fetched in this run
                all_candidates = [p for p in range(args.start_page, end_page + 1) if p not in cached_pages]
                pending_pages = all_candidates[: args.max_pages]
            else:
                pending_pages = [p for p in range(args.start_page, end_page + 1) if p not in cached_pages]
            logger.info(
                "Plan: start=%d, end=%d, already_cached=%d, pending_this_run=%d",
                args.start_page, end_page, len(cached_pages), len(pending_pages),
            )

        if not pending_pages:
            logger.info("Nothing pending to fetch. All target pages are already cached locally.")
            return

        # Step 3: Bounded Producer-Consumer Queue (O(C) queue RAM)
        queue: asyncio.Queue[int | None] = asyncio.Queue(maxsize=args.concurrency * 2)

        async def worker(worker_id: int) -> None:
            while True:
                page_num = await queue.get()
                if page_num is None:
                    queue.task_done()
                    return

                if stop_event.is_set():
                    queue.task_done()
                    continue

                url = build_page_url(args.base_url, page_num)
                last_exc: Exception | None = None
                last_status: int | None = None
                succeeded = False

                for attempt in range(1, args.retries + 1):
                    if stop_event.is_set():
                        break
                    await bucket.acquire()
                    try:
                        status, html, method = await session.fetch_search_page(
                            url, page_num, force_browser=args.force_browser
                        )
                        cards_cnt, total_pages, has_next = quick_inspect_search_html(html)
                        raw_bytes = html.encode("utf-8")
                        comp_bytes = zlib.compress(raw_bytes, level=6)

                        record = {
                            "page_num": page_num,
                            "url": url,
                            "http_status": status,
                            "cards_detected": cards_cnt,
                            "total_pages_detected": total_pages,
                            "has_next_data": has_next,
                            "raw_html_gz": comp_bytes,
                            "raw_size_bytes": len(raw_bytes),
                            "compressed_size_bytes": len(comp_bytes),
                            "fetch_method": method,
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                        }
                        async with write_lock:
                            write_buffer.append(record)
                            stats_counters["saved"] += 1
                            stats_counters["consecutive_blocks"] = 0

                        await flush_buffer(force=False)
                        succeeded = True
                        break
                    except Exception as exc:
                        last_exc = exc
                        err_msg = str(exc)
                        is_block = any(k in err_msg for k in ("403", "429", "gatekeeper_rejected", "cf_mitigated"))
                        if "status=" in err_msg:
                            try:
                                last_status = int(err_msg.split("status=")[1].split()[0])
                            except Exception:
                                last_status = None

                        log_error_context(
                            logger,
                            stage=f"worker_{worker_id}_attempt_{attempt}",
                            url=url,
                            exc=exc,
                            page_num=page_num,
                            http_status=last_status,
                            is_block=is_block,
                        )

                        if is_block and attempt < args.retries:
                            await session.refresh_if_stale(url)
                            # On final retry, try Patchright directly if curl_cffi still blocked
                            if attempt == args.retries - 1:
                                try:
                                    status, html, method = await session.fetch_via_patchright(url)
                                    reason = detect_block_or_error(html)
                                    if status == 200 and reason is None:
                                        cards_cnt, total_pages, has_next = quick_inspect_search_html(html)
                                        raw_bytes = html.encode("utf-8")
                                        comp_bytes = zlib.compress(raw_bytes, level=6)
                                        async with write_lock:
                                            write_buffer.append(
                                                {
                                                    "page_num": page_num,
                                                    "url": url,
                                                    "http_status": status,
                                                    "cards_detected": cards_cnt,
                                                    "total_pages_detected": total_pages,
                                                    "has_next_data": has_next,
                                                    "raw_html_gz": comp_bytes,
                                                    "raw_size_bytes": len(raw_bytes),
                                                    "compressed_size_bytes": len(comp_bytes),
                                                    "fetch_method": method,
                                                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                                                }
                                            )
                                            stats_counters["saved"] += 1
                                            stats_counters["consecutive_blocks"] = 0
                                        await flush_buffer(force=False)
                                        succeeded = True
                                        break
                                except Exception as pw_exc:
                                    last_exc = pw_exc
                                    log_error_context(logger, "patchright_fallback", url, pw_exc, page_num=page_num)

                        backoff = (2.5 ** attempt) + random.uniform(0.5, 2.0)
                        await asyncio.sleep(backoff)

                if not succeeded and not stop_event.is_set():
                    stats_counters["failed"] += 1
                    stats_counters["consecutive_blocks"] += 1
                    db.record_failure(
                        page_num=page_num,
                        url=url,
                        http_status=last_status,
                        error_type=type(last_exc).__name__ if last_exc else "UnknownError",
                        error_message=str(last_exc) if last_exc else "Unknown failure",
                        attempts=args.retries,
                    )
                    if stats_counters["consecutive_blocks"] >= args.circuit_breaker:
                        logger.error(
                            "CIRCUIT_BREAKER_TRIPPED consecutive_failures=%d -> halting cleanly to protect IP.",
                            stats_counters["consecutive_blocks"],
                        )
                        stop_event.set()

                queue.task_done()

        workers = [asyncio.create_task(worker(i)) for i in range(args.concurrency)]

        for p_num in pending_pages:
            if stop_event.is_set():
                break
            await queue.put(p_num)

        for _ in workers:
            await queue.put(None)

        await asyncio.gather(*workers)
        await flush_buffer(force=True)

    finally:
        await flush_buffer(force=True)
        await session.close()
        final_stats = db.get_stats()
        db.close()
        logger.info("RUN_COMPLETE | Cache Summary: %s", json.dumps(final_stats))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pass A: Fetch & locally cache all Wasalt search pages (Run Once).")
    parser.add_argument("--base-url", default=BASE_SEARCH_URL, help="Target Wasalt search URL")
    parser.add_argument("--db", default=str(DEFAULT_RAW_DB), help="SQLite raw cache DB path")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=None, help="Override detected max page")
    parser.add_argument("--max-pages", type=int, default=None, help="Cap new pages fetched in this run (e.g. 5 for test)")
    parser.add_argument("--concurrency", type=int, default=3, help="Concurrent async workers (2-4 recommended)")
    parser.add_argument("--rps", type=float, default=1.2, help="Global max requests per second (Token Bucket)")
    parser.add_argument("--batch-size", type=int, default=10, help="Pages per atomic SQLite WAL commit")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--circuit-breaker", type=int, default=6, help="Consecutive failures before halting cleanly")
    parser.add_argument("--retry-failed", action="store_true", help="Retry only pages in failed_search_pages")
    parser.add_argument("--force-browser", action="store_true", help="Fetch all pages via Patchright instead of curl_cffi")
    parser.add_argument("--headless", action="store_true", help="Run Patchright headless (headed recommended for Cloudflare)")
    parser.add_argument("--proxy", type=str, default=None, help="Optional proxy URL (http://user:pass@host:port)")
    parser.add_argument("--verbose", action="store_true", help="Show DEBUG logs in console")
    asyncio.run(run_pass_a_fetcher(parser.parse_args()))
