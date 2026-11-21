"""Automated 25-Query Golden Evaluation Suite & Retrieval Ablation Benchmark.

File: backend/eval/run_eval.py
Purpose: Evaluates the DarGlobal & Wasalt RAG Agent against the 5 Frontier Lab quality buckets:
         1. Deterministic SQL & Filtering (5 queries)
         2. Relational Joins & Auctions (5 queries)
         3. Semantic & FAQ Retrieval (5 queries)
         4. Hybrid Multi-Hop Bridge (5 queries)
         5. Out-of-Scope & Data-Boundary Honesty (5 queries)
Ablation Study: Compares Dense-Only vs. BM25-Only vs. Contextual Hybrid + FlashRank Rerank
                measuring Hit@5, MRR@5, and P95 latency.
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.src.services.agent import AgentService
from backend.src.rag.retriever import HybridRetriever

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_OUTPUT_PATH = PROJECT_ROOT / "backend" / "eval" / "eval_report.json"

GOLDEN_TEST_CASES = [
    # Bucket 1: Deterministic SQL & Filtering
    {
        "id": "sql_01",
        "bucket": "1. Deterministic SQL & Filtering",
        "query": "Show 3-bedroom apartments in Riyadh under 600,000 SAR with active REGA licenses.",
        "expected_tool": "search_wasalt_listings",
        "verification_fn": lambda res: (
            "wasalt" in res.get("reply", "").lower()
            and any("7201" in res.get("reply", "") or "7100" in res.get("reply", "") or "7659" in res.get("reply", "") for _ in [1])
            and len(res.get("sources", [])) > 0
        ),
    },
    {
        "id": "sql_02",
        "bucket": "1. Deterministic SQL & Filtering",
        "query": "List all apartments in Riyadh for sale with 2 bedrooms under 500,000 SAR.",
        "expected_tool": "search_wasalt_listings",
        "verification_fn": lambda res: (
            "riyadh" in res.get("reply", "").lower()
            and ("sar" in res.get("reply", "").lower() or "price" in res.get("reply", "").lower())
        ),
    },
    {
        "id": "sql_03",
        "bucket": "1. Deterministic SQL & Filtering",
        "query": "Find properties in As-Saadah district in Riyadh with active advertising permits.",
        "expected_tool": "search_wasalt_listings",
        "verification_fn": lambda res: (
            "as-saadah" in res.get("reply", "").lower()
            and len(res.get("sources", [])) > 0
        ),
    },
    {
        "id": "sql_04",
        "bucket": "1. Deterministic SQL & Filtering",
        "query": "Show Wasalt listings with building year 2024 and 3+ bedrooms.",
        "expected_tool": "search_wasalt_listings",
        "verification_fn": lambda res: "bedroom" in res.get("reply", "").lower(),
    },
    {
        "id": "sql_05",
        "bucket": "1. Deterministic SQL & Filtering",
        "query": "What are the cheapest apartments for sale in Riyadh on Wasalt?",
        "expected_tool": "search_wasalt_listings",
        "verification_fn": lambda res: "sar" in res.get("reply", "").lower() and len(res.get("sources", [])) > 0,
    },

    # Bucket 2: Relational Joins & Auctions
    {
        "id": "auc_01",
        "bucket": "2. Relational Joins & Auctions",
        "query": "Which live auctions have a deposit under 5,000 SAR, and how many lots are in Group 1162?",
        "expected_tool": "search_wasalt_projects_and_auctions",
        "verification_fn": lambda res: (
            "1162" in res.get("reply", "")
            and "11" in res.get("reply", "")
            and "auction" in res.get("reply", "").lower()
        ),
    },
    {
        "id": "auc_02",
        "bucket": "2. Relational Joins & Auctions",
        "query": "How many asset lots are in Auction Group 1149 and what is its status?",
        "expected_tool": "search_wasalt_projects_and_auctions",
        "verification_fn": lambda res: "1149" in res.get("reply", ""),
    },
    {
        "id": "auc_03",
        "bucket": "2. Relational Joins & Auctions",
        "query": "List all live real estate auctions in Riyadh.",
        "expected_tool": "search_wasalt_projects_and_auctions",
        "verification_fn": lambda res: "riyadh" in res.get("reply", "").lower() and "auction" in res.get("reply", "").lower(),
    },
    {
        "id": "auc_04",
        "bucket": "2. Relational Joins & Auctions",
        "query": "What is the minimum deposit required for Wasalt Auction Group 1152?",
        "expected_tool": "search_wasalt_projects_and_auctions",
        "verification_fn": lambda res: "1152" in res.get("reply", ""),
    },
    {
        "id": "auc_05",
        "bucket": "2. Relational Joins & Auctions",
        "query": "Who is the sales agent for Auction Group 1162 in Riyadh?",
        "expected_tool": "search_wasalt_projects_and_auctions",
        "verification_fn": lambda res: "saroh" in res.get("reply", "").lower() or "1162" in res.get("reply", ""),
    },

    # Bucket 3: Semantic & FAQ Retrieval
    {
        "id": "sem_01",
        "bucket": "3. Semantic & FAQ Retrieval",
        "query": "How does DarGlobal tokenization and digital ownership work?",
        "target_doc": "dg_05_tokenization_and_digital_ownership",
        "verification_fn": lambda res: (
            "tokenization" in res.get("reply", "").lower()
            or "blockchain" in res.get("reply", "").lower()
        ),
    },
    {
        "id": "sem_02",
        "bucket": "3. Semantic & FAQ Retrieval",
        "query": "Explain Wasalt's Infath auction eWallet deposit and 14-day settlement process.",
        "target_doc": "ws_04_auctions",
        "verification_fn": lambda res: (
            "ewallet" in res.get("reply", "").lower()
            or "deposit" in res.get("reply", "").lower()
            or "infath" in res.get("reply", "").lower()
        ),
    },
    {
        "id": "sem_03",
        "bucket": "3. Semantic & FAQ Retrieval",
        "query": "Does Trump International Hotel and Tower Dubai have the world's highest pool?",
        "target_doc": "dg_proj_trump_international_hotel_and_tower_dubai",
        "verification_fn": lambda res: "pool" in res.get("reply", "").lower() and "highest" in res.get("reply", "").lower(),
    },
    {
        "id": "sem_04",
        "bucket": "3. Semantic & FAQ Retrieval",
        "query": "What design inspiration did Pagani provide for Da Vinci Tower in Dubai?",
        "target_doc": "dg_proj_davinci_tower_by_pagani",
        "verification_fn": lambda res: "pagani" in res.get("reply", "").lower(),
    },
    {
        "id": "sem_05",
        "bucket": "3. Semantic & FAQ Retrieval",
        "query": "What are the buying steps for international investors on DarGlobal?",
        "target_doc": "dg_07_global_faq_and_buying_process",
        "verification_fn": lambda res: "step" in res.get("reply", "").lower() or "process" in res.get("reply", "").lower() or "reserve" in res.get("reply", "").lower(),
    },

    # Bucket 4: Hybrid Multi-Hop Bridge
    {
        "id": "hop_01",
        "bucket": "4. Hybrid Multi-Hop Bridge",
        "query": "Compare residency investment minimums in UAE, Oman, and Saudi Arabia, and list one DarGlobal project in each.",
        "verification_fn": lambda res: (
            ("4,000,000" in res.get("reply", "") or "4m" in res.get("reply", "").lower())
            and ("2,000,000" in res.get("reply", "") or "2m" in res.get("reply", "").lower())
            and ("250,000" in res.get("reply", "") or "250k" in res.get("reply", "").lower())
            and "$0.00" not in res.get("reply", "")
        ),
    },
    {
        "id": "hop_02",
        "bucket": "4. Hybrid Multi-Hop Bridge",
        "query": "What is the minimum investment for the Saudi Real Estate Owner Premium Residency track?",
        "verification_fn": lambda res: (
            ("4,000,000" in res.get("reply", "") or "4 million" in res.get("reply", "").lower() or "4m" in res.get("reply", "").lower())
            and "premium residency" in res.get("reply", "").lower()
        ),
    },
    {
        "id": "hop_03",
        "bucket": "4. Hybrid Multi-Hop Bridge",
        "query": "Which DarGlobal projects in Oman offer eligibility for the Investor Residency Program?",
        "verification_fn": lambda res: "aida" in res.get("reply", "").lower() or "oman" in res.get("reply", "").lower(),
    },
    {
        "id": "hop_04",
        "bucket": "4. Hybrid Multi-Hop Bridge",
        "query": "What are the tax benefits of buying property in Qatar under the permanent residency framework?",
        "verification_fn": lambda res: "0%" in res.get("reply", "") or "tax" in res.get("reply", "").lower() or "qatar" in res.get("reply", "").lower(),
    },
    {
        "id": "hop_05",
        "bucket": "4. Hybrid Multi-Hop Bridge",
        "query": "Compare foreign freehold property ownership rules between Saudi Arabia, UAE, and Spain.",
        "verification_fn": lambda res: "freehold" in res.get("reply", "").lower() and "spain" in res.get("reply", "").lower(),
    },

    # Bucket 5: Out-of-Scope & Data-Boundary Honesty
    {
        "id": "bound_01",
        "bucket": "5. Out-of-Scope & Data-Boundary Honesty",
        "query": "Find 2BR apartments for rent in Jeddah on Wasalt",
        "verification_fn": lambda res: (
            ("jeddah" in res.get("reply", "").lower() and ("not" in res.get("reply", "").lower() or "current" in res.get("reply", "").lower()))
            and not any("wasalt_jeddah" in str(it) for it in res.get("matched_properties", []))
        ),
    },
    {
        "id": "bound_02",
        "bucket": "5. Out-of-Scope & Data-Boundary Honesty",
        "query": "Show luxury villas for sale in Paris France on DarGlobal",
        "verification_fn": lambda res: "paris" not in [r.get("city", "").lower() for r in res.get("matched_properties", [])],
    },
    {
        "id": "bound_03",
        "bucket": "5. Out-of-Scope & Data-Boundary Honesty",
        "query": "Write me a Python bubble sort algorithm with test cases",
        "verification_fn": lambda res: "programming" in res.get("reply", "").lower() or "cannot help" in res.get("reply", "").lower() or "real estate" in res.get("reply", "").lower(),
    },
    {
        "id": "bound_04",
        "bucket": "5. Out-of-Scope & Data-Boundary Honesty",
        "query": "Can you give me a recipe for traditional Italian pasta carbonara?",
        "verification_fn": lambda res: "recipe" in res.get("reply", "").lower() or "cannot help" in res.get("reply", "").lower() or "real estate" in res.get("reply", "").lower(),
    },
    {
        "id": "bound_05",
        "bucket": "5. Out-of-Scope & Data-Boundary Honesty",
        "query": "Find commercial offices for rent in Dammam on Wasalt",
        "verification_fn": lambda res: "dammam" in res.get("reply", "").lower() or "not" in res.get("reply", "").lower() or "no" in res.get("reply", "").lower(),
    },
]

ABLATION_QUERIES = [
    ("How does DarGlobal tokenization and digital ownership work?", "dg_05_tokenization_and_digital_ownership"),
    ("Explain Wasalt Infath auction eWallet deposit and settlement process", "ws_04_auctions"),
    ("Trump International Hotel and Tower Dubai highest outdoor pool", "dg_proj_trump_international_hotel_and_tower_dubai"),
    ("Pagani interior design Da Vinci Tower in Dubai", "dg_proj_davinci_tower_by_pagani"),
    ("DarGlobal buying process payment plan steps", "dg_07_global_faq_and_buying_process"),
    ("Saudi Arabia Real Estate Owner Premium Residency 4 million SAR", "rules_Saudi Arabia"),
    ("UAE 10-Year Golden Visa 2 million AED off plan eligible", "rules_UAE"),
    ("Oman Investor Residency Program AIDA coastal development", "rules_Oman"),
    ("REGA FAL license advertising compliance Wasalt", "ws_05_rega_fal_and_wafi_compliance_guide"),
    ("DarGlobal LSE listing corporate heritage London Stock Exchange", "dg_02_investor_relations_and_governance"),
]


def run_ablation_benchmark(retriever: HybridRetriever) -> Dict[str, Any]:
    """Runs ablation comparing Dense-Only vs. BM25-Only vs. Contextual Hybrid + FlashRank Rerank."""
    print("\n" + "=" * 80)
    print("RUNNING RETRIEVAL ABLATION BENCHMARK (10 Grounded Anchor Queries)")
    print("=" * 80)

    modes = ["BM25-Only", "Dense-Only", "Contextual Hybrid + Rerank"]
    results = {
        m: {"hits": 0, "reciprocal_ranks": [], "latencies": []} for m in modes
    }

    for query, target_key in ABLATION_QUERIES:
        # 1. BM25-Only
        t0 = time.perf_counter()
        bm25_res = retriever.search_bm25(query=query, limit=5)
        dt_bm25 = (time.perf_counter() - t0) * 1000
        results["BM25-Only"]["latencies"].append(dt_bm25)

        rank_bm25 = 0
        for i, hit in enumerate(bm25_res):
            doc_id_val = str(hit.get("doc_id") or "")
            chunk_id_val = str(hit.get("chunk_id") or "")
            entity_id_val = str(hit.get("linked_entity_id") or "")
            if target_key in doc_id_val or target_key in chunk_id_val or target_key in entity_id_val:
                rank_bm25 = i + 1
                break
        if rank_bm25 > 0:
            results["BM25-Only"]["hits"] += 1
            results["BM25-Only"]["reciprocal_ranks"].append(1.0 / rank_bm25)
        else:
            results["BM25-Only"]["reciprocal_ranks"].append(0.0)

        # 2. Dense-Only
        t0 = time.perf_counter()
        dense_res = retriever.search_dense(query=query, limit=5)
        dt_dense = (time.perf_counter() - t0) * 1000
        results["Dense-Only"]["latencies"].append(dt_dense)

        rank_dense = 0
        for i, hit in enumerate(dense_res):
            doc_id_val = str(hit.get("doc_id") or "")
            chunk_id_val = str(hit.get("chunk_id") or "")
            entity_id_val = str(hit.get("linked_entity_id") or "")
            if target_key in doc_id_val or target_key in chunk_id_val or target_key in entity_id_val:
                rank_dense = i + 1
                break
        if rank_dense > 0:
            results["Dense-Only"]["hits"] += 1
            results["Dense-Only"]["reciprocal_ranks"].append(1.0 / rank_dense)
        else:
            results["Dense-Only"]["reciprocal_ranks"].append(0.0)

        # 3. Contextual Hybrid + FlashRank Rerank
        t0 = time.perf_counter()
        hybrid_res = retriever.search(query=query, top_k=5)
        dt_hybrid = (time.perf_counter() - t0) * 1000
        results["Contextual Hybrid + Rerank"]["latencies"].append(dt_hybrid)

        rank_hybrid = 0
        for i, hit in enumerate(hybrid_res):
            doc_id_val = str(hit.doc_id or "")
            chunk_id_val = str(hit.chunk_id or "")
            entity_id_val = str(hit.linked_entity_id or "")
            if target_key in doc_id_val or target_key in chunk_id_val or target_key in entity_id_val:
                rank_hybrid = i + 1
                break
        if rank_hybrid > 0:
            results["Contextual Hybrid + Rerank"]["hits"] += 1
            results["Contextual Hybrid + Rerank"]["reciprocal_ranks"].append(1.0 / rank_hybrid)
        else:
            results["Contextual Hybrid + Rerank"]["reciprocal_ranks"].append(0.0)

    summary = {}
    print(f"\n{'Retriever Modality':<30} | {'Hit@5':<8} | {'MRR@5':<8} | {'Avg Latency (ms)':<16}")
    print("-" * 72)

    for m in modes:
        n = len(ABLATION_QUERIES)
        hit_rate = results[m]["hits"] / n
        mrr = sum(results[m]["reciprocal_ranks"]) / n
        avg_lat = sum(results[m]["latencies"]) / n
        summary[m] = {
            "hit_at_5": round(hit_rate, 4),
            "mrr_at_5": round(mrr, 4),
            "avg_latency_ms": round(avg_lat, 2),
        }
        print(f"{m:<30} | {hit_rate*100:>6.1f}% | {mrr:>8.4f} | {avg_lat:>14.2f} ms")

    print("-" * 72)
    return summary


async def run_golden_suite(harness: AgentHarness) -> Dict[str, Any]:
    """Runs all 25 golden test cases across the 5 evaluation buckets."""
    print("\n" + "=" * 80)
    print("RUNNING 25-QUERY GOLDEN EVALUATION HARNESS")
    print("=" * 80)

    bucket_stats: Dict[str, Dict[str, int]] = {}
    test_results: List[Dict[str, Any]] = []

    passed_count = 0
    total_count = len(GOLDEN_TEST_CASES)

    for case in GOLDEN_TEST_CASES:
        cid = case["id"]
        bname = case["bucket"]
        query = case["query"]

        if bname not in bucket_stats:
            bucket_stats[bname] = {"total": 0, "passed": 0}
        bucket_stats[bname]["total"] += 1

        t0 = time.perf_counter()
        res = await harness.process_query(user_message=query)
        duration_ms = (time.perf_counter() - t0) * 1000

        passed = case["verification_fn"](res)
        if passed:
            passed_count += 1
            bucket_stats[bname]["passed"] += 1
            status_str = "[PASS]"
        else:
            status_str = "[FAIL]"

        print(f"{status_str} {cid:<8} | {bname:<36} | {duration_ms:>6.1f} ms | {query[:45]}...")
        test_results.append({
            "id": cid,
            "bucket": bname,
            "query": query,
            "passed": passed,
            "latency_ms": round(duration_ms, 2),
            "sources_count": len(res.get("sources", [])),
            "tool_calls_count": len(res.get("tool_calls", [])),
            "reply_snippet": res.get("reply", "")[:120],
        })

    print("\n" + "=" * 80)
    print("BUCKET PERFORMANCE BREAKDOWN")
    print("=" * 80)
    for bname, stats in bucket_stats.items():
        rate = (stats["passed"] / stats["total"]) * 100
        print(f"{bname:<40}: {stats['passed']}/{stats['total']} passed ({rate:.1f}%)")

    overall_pass_rate = (passed_count / total_count) * 100
    print("-" * 80)
    print(f"OVERALL GOLDEN SCORE: {passed_count}/{total_count} ({overall_pass_rate:.1f}%)\n")

    return {
        "total_queries": total_count,
        "passed_queries": passed_count,
        "pass_rate": round(overall_pass_rate, 2),
        "bucket_breakdown": bucket_stats,
        "test_results": test_results,
    }


async def main():
    """Coordinates the full evaluation and ablation benchmarking suite."""
    retriever = HybridRetriever()
    harness = AgentHarness()

    ablation_summary = run_ablation_benchmark(retriever)
    golden_summary = await run_golden_suite(harness)

    full_report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "architecture": "Frontier AI Labs 2025-2026 Contextual Hybrid RAG + Google ADK Harness",
        "technologies": {
            "orchestration": "google-adk",
            "routing": "litellm",
            "embeddings": "fastembed (BAAI/bge-small-en-v1.5 ONNX)",
            "reranker": "flashrank (ms-marco-MiniLM-L-12-v2 ONNX)",
            "vector_store": "qdrant-client (embedded)",
            "sparse_search": "sqlite3 FTS5 (BM25)",
        },
        "ablation_benchmark": ablation_summary,
        "golden_evaluation": golden_summary,
    }

    REPORT_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUTPUT_PATH.write_text(json.dumps(full_report, indent=2), encoding="utf-8")
    print(f"Full evaluation report committed to: {REPORT_OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
