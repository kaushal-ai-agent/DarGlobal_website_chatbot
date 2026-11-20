import argparse
import json
import re
import sqlite3
import zlib
from pathlib import Path

from bs4 import BeautifulSoup

DEFAULT_RAW_DB = Path("data/wasalt/raw_cache/wasalt_sale_raw.db")
DEFAULT_PARSED_DB = Path("data/wasalt/pass_a/wasalt_pass_a.db")
DEFAULT_OUT_TXT = Path("data/wasalt/pass_a/diagnostics/inspected_raw.txt")


def extract_id_token(url_or_query: str) -> str:
    m = re.search(r"(\d+)(?:[/?#]|$)", url_or_query.strip())
    return m.group(1) if m else url_or_query.strip()


def search_raw_cache(raw_db_path: Path, target_url: str, max_snippets: int = 10) -> dict:
    result = {
        "target_url": target_url,
        "id_token": extract_id_token(target_url),
        "raw_db_exists": raw_db_path.exists(),
        "matching_pages": [],
        "snippets": [],
        "full_text_blocks": [],
    }
    if not raw_db_path.exists():
        return result

    conn = sqlite3.connect(raw_db_path)
    rows = conn.execute("SELECT page_num, url, raw_html_gz, fetched_at FROM raw_search_pages ORDER BY page_num ASC").fetchall()
    conn.close()

    needle_url = target_url.strip().lower()
    needle_id = result["id_token"].lower()

    for page_num, page_url, blob, fetched_at in rows:
        try:
            html = zlib.decompress(blob).decode("utf-8", errors="replace")
        except Exception as exc:
            result["snippets"].append(f"[Page {page_num}] Decompress error: {exc}")
            continue

        html_lower = html.lower()
        if needle_url in html_lower or (needle_id and re.search(rf"\b{re.escape(needle_id)}\b", html_lower)):
            result["matching_pages"].append({"page_num": page_num, "search_page_url": page_url, "fetched_at": fetched_at})

            # Find all occurrences of the URL or ID in raw HTML
            for term in dict.fromkeys([needle_url, needle_id]):
                if not term:
                    continue
                start = 0
                found_on_page = 0
                while found_on_page < max_snippets:
                    idx = html_lower.find(term, start)
                    if idx == -1:
                        break
                    s = max(0, idx - 250)
                    e = min(len(html), idx + 450)
                    result["snippets"].append(f"[Page {page_num} | match='{term}'] {html[s:e]}")
                    start = idx + len(term)
                    found_on_page += 1

            # Also extract readable text lines mentioning the URL or ID
            soup = BeautifulSoup(html, "lxml")
            for tag in soup.select("script, style, noscript"):
                tag.decompose()
            lines = [re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").splitlines() if ln.strip()]
            for i, ln in enumerate(lines):
                if needle_url in ln.lower() or (needle_id and re.search(rf"\b{re.escape(needle_id)}\b", ln.lower())):
                    ctx = " | ".join(lines[max(0, i - 2) : min(len(lines), i + 4)])
                    result["full_text_blocks"].append(f"[Page {page_num}] {ctx}")

    return result


def search_parsed_db(parsed_db_path: Path, target_url: str) -> list[dict]:
    if not parsed_db_path.exists():
        return []
    conn = sqlite3.connect(parsed_db_path)
    conn.row_factory = sqlite3.Row
    token = f"%{extract_id_token(target_url)}%"
    matches = []
    for tbl in ("wasalt_search_properties", "wasalt_search_projects", "wasalt_search_auctions"):
        try:
            cur = conn.execute(
                f"SELECT * FROM {tbl} WHERE source_url LIKE ? OR raw_card_text LIKE ? OR raw_card_json LIKE ?",
                (token, token, token),
            )
            for r in cur.fetchall():
                matches.append({"table": tbl, **dict(r)})
        except sqlite3.OperationalError:
            continue
    conn.close()
    return matches


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect compressed raw_cache.db for a specific URL or ID.")
    parser.add_argument("--url", required=True, help="Target URL or listing/auction ID to check")
    parser.add_argument("--raw-db", default=str(DEFAULT_RAW_DB), help="Path to raw_cache SQLite DB")
    parser.add_argument("--parsed-db", default=str(DEFAULT_PARSED_DB), help="Path to parsed SQLite DB")
    parser.add_argument("--out-txt", default=str(DEFAULT_OUT_TXT), help="Output text file path")
    args = parser.parse_args()

    raw_res = search_raw_cache(Path(args.raw_db), args.url)
    parsed_res = search_parsed_db(Path(args.parsed_db), args.url)

    print(f"Target URL : {raw_res['target_url']}")
    print(f"ID Token   : {raw_res['id_token']}")
    print(f"Raw Cache  : {'FOUND on ' + str(len(raw_res['matching_pages'])) + ' search page(s)' if raw_res['matching_pages'] else 'NOT FOUND in cached search HTML'}")
    print(f"Parsed DB  : {len(parsed_res)} matching row(s)")

    out_lines = [
        f"TARGET_URL: {raw_res['target_url']}",
        f"ID_TOKEN: {raw_res['id_token']}",
        f"MATCHING_SEARCH_PAGES: {json.dumps(raw_res['matching_pages'], ensure_ascii=False)}",
        f"PARSED_DB_MATCHES_COUNT: {len(parsed_res)}",
        "\n=== PARSED DB ROWS ===",
        json.dumps(parsed_res, indent=2, ensure_ascii=False),
        "\n=== READABLE TEXT CONTEXT BLOCKS ===",
        "\n".join(raw_res["full_text_blocks"][:30]) or "(None)",
        "\n=== RAW HTML SNIPPETS ===",
        "\n---\n".join(raw_res["snippets"][:20]) or "(None)",
    ]

    out_path = Path(args.out_txt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"Saved full inspection report -> {out_path}")

    for blk in raw_res["full_text_blocks"][:5]:
        print("  TEXT:", blk)


if __name__ == "__main__":
    main()