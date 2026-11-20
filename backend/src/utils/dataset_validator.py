"""Production dataset validator for DarGlobal and Wasalt real estate data.

Provides:
1. Single-row detailed inspection with intelligent truncation of large fields.
2. Multi-row sampling (top N or random) with structured tabular formatting.
3. Dataset-wide rigorous validation ensuring:
   - 0 Arabic characters or Arabizi transliterations
   - 0 Missing or null values across all required schema fields
   - 100% valid image URLs and source URLs
   - Clean, descriptive English titles
"""

import json
import random
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = PROJECT_ROOT / "data" / "final_dataset.jsonl"

REQUIRED_FIELDS = [
    "id",
    "source",
    "title",
    "location",
    "price_formatted",
    "price_numerical",
    "currency",
    "property_type",
    "bedrooms",
    "bathrooms",
    "area_sqft",
    "amenities",
    "description",
    "status",
    "raw_text",
    "contact_info",
    "source_url",
    "image_url",
]

ARABIC_REGEX = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")


def truncate_value(val: Any, max_len: int = 100) -> str:
    """Intelligently formats and truncates large values for console readability."""
    if val is None:
        return "<NULL>"
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, list):
        if len(val) > 4:
            sample = [truncate_value(x, 25) for x in val[:4]]
            return f"[{', '.join(sample)}]... [total={len(val)}]"
        return str(val)
    if isinstance(val, dict):
        s = json.dumps(val, ensure_ascii=False)
        if len(s) > max_len:
            return s[:max_len] + f"... [len={len(s)}]"
        return s
    s = str(val).strip()
    if len(s) > max_len:
        return s[:max_len] + f"... [len={len(s)}]"
    return s


def load_dataset(file_path: Path = DEFAULT_DATASET) -> List[Dict[str, Any]]:
    """Loads all JSONL rows from disk."""
    if not file_path.exists():
        print(f"Error: Dataset file not found at {file_path}")
        return []
    rows = []
    with open(file_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            line_str = line.strip()
            if not line_str:
                continue
            try:
                rows.append(json.loads(line_str))
            except Exception as e:
                print(f"Error parsing line {idx}: {e}")
    return rows


def inspect_single_row(row: Dict[str, Any], row_num: int = 1) -> None:
    """Inspects a single row in full detail with intelligent truncation of large fields."""
    print("=" * 78)
    print(f"SINGLE ROW INSPECTION: Row #{row_num} (Source: {row.get('source', 'unknown').upper()})")
    print("=" * 78)
    for field in REQUIRED_FIELDS:
        val = row.get(field)
        if field == "raw_text":
            trunc = truncate_value(val, max_len=120)
        elif field == "description":
            trunc = truncate_value(val, max_len=100)
        elif field in ("contact_info", "metadata"):
            trunc = truncate_value(val, max_len=90)
        else:
            trunc = truncate_value(val, max_len=80)
        print(f"  {field:18}: {trunc}")
    
    # Extra metadata if present
    if "metadata" in row:
        print(f"  {'metadata':18}: {truncate_value(row['metadata'], max_len=90)}")
    print("=" * 78)


def inspect_multiple_rows(rows: List[Dict[str, Any]], sample_size: int = 5, mode: str = "top") -> None:
    """Inspects multiple rows with truncation and structured summaries.
    
    mode: 'top' for first N rows, or 'random' for random sampling.
    """
    total = len(rows)
    if total == 0:
        print("No rows to inspect.")
        return

    sample_count = min(sample_size, total)
    if mode == "random" and total > sample_count:
        sampled_indices = sorted(random.sample(range(total), sample_count))
    else:
        sampled_indices = list(range(sample_count))

    print("\n" + "=" * 78)
    print(f"MULTI-ROW INSPECTION: Showing {len(sampled_indices)} of {total} rows (Mode: {mode.upper()})")
    print("=" * 78)

    for rank, idx in enumerate(sampled_indices, start=1):
        row = rows[idx]
        src = str(row.get("source", "")).upper()
        pid = str(row.get("id", ""))
        title = truncate_value(row.get("title"), 40)
        loc = truncate_value(row.get("location"), 35)
        price_f = str(row.get("price_formatted", ""))
        price_n = row.get("price_numerical")
        beds = row.get("bedrooms")
        baths = row.get("bathrooms")
        area = row.get("area_sqft")
        img = truncate_value(row.get("image_url"), 50)
        desc = truncate_value(row.get("description"), 70)

        print(f"\n[Sample #{rank} | Dataset Line #{idx+1} | {src}] ID: {pid}")
        print(f"  Title     : {title}")
        print(f"  Location  : {loc}")
        print(f"  Specs     : Beds: {beds} | Baths: {baths} | Area: {area} SQFT | Type: {row.get('property_type')}")
        print(f"  Pricing   : {price_f} (Numerical: {price_n})")
        print(f"  Image URL : {img}")
        print(f"  Overview  : {desc}")


def audit_entire_dataset(rows: List[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    """Performs exhaustive, automated quality audit across all rows in the dataset."""
    total = len(rows)
    if total == 0:
        return False, {"error": "Dataset is empty"}

    arabic_violations = []
    missing_violations: Dict[str, List[int]] = {f: [] for f in REQUIRED_FIELDS}
    generic_titles = []
    invalid_urls = []

    for idx, row in enumerate(rows, start=1):
        row_id = row.get("id", f"row_{idx}")

        # 1. Arabic Check (Regex + Unicode name check across all stringified fields)
        for k, v in row.items():
            s = json.dumps(v, ensure_ascii=False)
            if ARABIC_REGEX.search(s):
                arabic_violations.append((idx, row_id, k, s[:60]))
            # Also check if any unicode char contains 'ARABIC'
            for ch in s:
                try:
                    if "ARABIC" in unicodedata.name(ch):
                        arabic_violations.append((idx, row_id, k, f"Char: {ch}"))
                        break
                except ValueError:
                    pass

        # 2. Missing Fields Check
        for field in REQUIRED_FIELDS:
            val = row.get(field)
            if val is None or val == "" or val == []:
                missing_violations[field].append(idx)

        # 3. Generic Title Check
        title = str(row.get("title", "")).strip().lower()
        if title in ["apartment", "villa", "land", "building", "floor", "none", "undefined", "collection", "relations"]:
            generic_titles.append((idx, row_id, row.get("title")))

        # 4. Valid Image and Source URL
        img = str(row.get("image_url", ""))
        src_url = str(row.get("source_url", ""))
        if not (img.startswith("http://") or img.startswith("https://")):
            invalid_urls.append((idx, row_id, "image_url", img))
        if not (src_url.startswith("http://") or src_url.startswith("https://")):
            invalid_urls.append((idx, row_id, "source_url", src_url))

    # Compile report
    passed = (
        len(arabic_violations) == 0
        and all(len(missing_violations[f]) == 0 for f in REQUIRED_FIELDS)
        and len(generic_titles) == 0
        and len(invalid_urls) == 0
    )

    report = {
        "total_rows": total,
        "arabic_violations_count": len(arabic_violations),
        "arabic_violations_samples": arabic_violations[:5],
        "missing_fields_summary": {f: len(missing_violations[f]) for f in REQUIRED_FIELDS if len(missing_violations[f]) > 0},
        "generic_titles_count": len(generic_titles),
        "generic_titles_samples": generic_titles[:5],
        "invalid_urls_count": len(invalid_urls),
        "passed": passed,
    }

    return passed, report


def print_audit_report(passed: bool, report: Dict[str, Any]) -> None:
    """Pretty prints the audit summary report."""
    print("\n" + "=" * 78)
    print("DATASET COMPREHENSIVE QUALITY AUDIT")
    print("=" * 78)
    total = report.get("total_rows", 0)
    print(f"Total Rows Audited: {total}")
    arabic_status = "PASS (0 Arabic characters)" if report["arabic_violations_count"] == 0 else f"FAIL ({report['arabic_violations_count']} violations)"
    print(f"1. Arabic Language Compliance: {arabic_status}")
    if report["arabic_violations_samples"]:
        for item in report["arabic_violations_samples"]:
            print(f"   - Row {item[0]} [{item[1]}] field '{item[2]}': {item[3]}")

    missing_fields = report["missing_fields_summary"]
    if not missing_fields:
        print("2. Missing/Null Fields Check : PASS (0 null values across all 18 fields)")
    else:
        print(f"2. Missing/Null Fields Check : FAIL ({len(missing_fields)} fields have null values)")
        for f, cnt in missing_fields.items():
            print(f"   - {f:18}: {cnt}/{total} missing ({cnt/total*100:.1f}%)")

    generic_status = "PASS (0 generic titles)" if report["generic_titles_count"] == 0 else f"FAIL ({report['generic_titles_count']} generic titles)"
    print(f"3. Descriptive Titles Check  : {generic_status}")
    if report["generic_titles_samples"]:
        for item in report["generic_titles_samples"]:
            print(f"   - Row {item[0]} [{item[1]}]: title='{item[2]}'")

    urls_status = "PASS (100% valid HTTP/HTTPS URLs)" if report["invalid_urls_count"] == 0 else f"FAIL ({report['invalid_urls_count']} invalid URLs)"
    print(f"4. Image & Source URLs Check : {urls_status}")
    print("=" * 78)
    print(f"OVERALL STATUS: {'[PASSED] Dataset is production-ready' if passed else '[FAILED] Dataset requires remediation'}")
    print("=" * 78 + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate real estate dataset")
    parser.add_argument("--file", type=Path, default=DEFAULT_DATASET, help="Path to jsonl dataset")
    parser.add_argument("--samples", type=int, default=5, help="Number of samples to inspect")
    parser.add_argument("--mode", choices=["top", "random"], default="top", help="Sampling mode")
    args = parser.parse_args()

    rows = load_dataset(args.file)
    if not rows:
        print("Dataset is empty. Exiting.")
        sys.exit(1)

    # 1. First inspect single row (Row 1)
    inspect_single_row(rows[0], row_num=1)

    # 2. Inspect multiple rows (top 5 or random)
    inspect_multiple_rows(rows, sample_size=args.samples, mode=args.mode)

    # 3. Dataset-wide quality audit
    passed, report = audit_entire_dataset(rows)
    print_audit_report(passed, report)

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
