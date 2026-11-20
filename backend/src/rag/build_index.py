"""Adaptive 3-Tier Chunker and Hybrid Index Builder.

File: backend/src/rag/build_index.py
Purpose: Chunks 88 Markdown knowledge docs using a 3-tier strategy (Compact Tier A,
         Atomic FAQ Tier B, Parent-Child Section Tier C), injects contextual headers,
         generates 65 Synthetic Entity Cards from SQLite, and indexes everything into
         both an embedded SQLite FTS5 full-text table and a local FastEmbed/Qdrant vector store.
Core Logic: Implements Anthropic's Contextual Retrieval paradigm and OpenAI/Google layout-aware
            chunking to achieve high-precision semantic and lexical retrieval.
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UNSTRUCTURED_DIR = PROJECT_ROOT / "data" / "unstructured"
DB_PATH = PROJECT_ROOT / "data" / "structured" / "db" / "real_estate.db"
INDEX_DIR = PROJECT_ROOT / "data" / "index"
RAG_DB_PATH = INDEX_DIR / "rag_index.db"
QDRANT_DIR = INDEX_DIR / "qdrant"


class DocumentChunk:
    """Represents an atomic indexed text unit with contextual provenance."""

    def __init__(
        self,
        chunk_id: str,
        doc_id: str,
        parent_id: Optional[str],
        source_platform: str,
        doc_category: str,
        title: str,
        heading: str,
        content: str,
        context_header: str,
        full_text: str,
        source_url: str,
        country: str,
        city: str,
        linked_entity_id: Optional[str],
        chunk_type: str,
    ):
        self.chunk_id = chunk_id
        self.doc_id = doc_id
        self.parent_id = parent_id or doc_id
        self.source_platform = source_platform
        self.doc_category = doc_category
        self.title = title
        self.heading = heading
        self.content = content
        self.context_header = context_header
        self.full_text = full_text
        self.source_url = source_url
        self.country = country
        self.city = city
        self.linked_entity_id = linked_entity_id
        self.chunk_type = chunk_type

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "parent_id": self.parent_id,
            "source_platform": self.source_platform,
            "doc_category": self.doc_category,
            "title": self.title,
            "heading": self.heading,
            "content": self.content,
            "context_header": self.context_header,
            "full_text": self.full_text,
            "source_url": self.source_url,
            "country": self.country,
            "city": self.city,
            "linked_entity_id": self.linked_entity_id,
            "chunk_type": self.chunk_type,
        }


def parse_markdown_file(file_path: Path) -> Tuple[Dict[str, Any], str]:
    """Extracts YAML frontmatter and body from markdown file."""
    content = file_path.read_text(encoding="utf-8")
    frontmatter = {}
    body = content

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if fm_match:
        try:
            frontmatter = yaml.safe_load(fm_match.group(1)) or {}
            body = fm_match.group(2)
        except Exception as e:
            print(f"Warning: Failed to parse YAML frontmatter in {file_path}: {e}")

    return frontmatter, body


def sanitize_markdown_body(body: str) -> str:
    """Removes web navigation bleed and trailing portal artifacts."""
    lines = body.split("\n")
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        # Drop navigation bleed link bullets
        if stripped.startswith("- Luxury Villas in ") or stripped.startswith("- Luxury Apartments in ") or stripped.startswith("- Luxury Properties in "):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def chunk_document(file_path: Path, frontmatter: Dict[str, Any], body: str) -> List[DocumentChunk]:
    """Applies the 3-Tier Adaptive Chunking strategy to a markdown document."""
    doc_id = frontmatter.get("doc_id", file_path.stem)
    platform = frontmatter.get("source_platform", "general")
    category = frontmatter.get("doc_category", "general")
    title = frontmatter.get("title", file_path.stem.replace("_", " ").title())
    country = str(frontmatter.get("country", "Multi"))
    city = str(frontmatter.get("city", "Multi"))
    linked_entity_id = frontmatter.get("linked_entity_id")
    source_urls = frontmatter.get("source_urls", [])
    primary_url = source_urls[0] if source_urls else "https://darglobal.co.uk"

    body = sanitize_markdown_body(body)
    word_count = len(body.split())

    chunks: List[DocumentChunk] = []

    def make_context_header(sec_heading: str) -> str:
        return (
            f"[CONTEXT] Platform: {platform} | Title: {title} | Category: {category} | "
            f"Entity: {linked_entity_id or 'General'} | Location: {city}, {country} | Section: {sec_heading}"
        )

    # Tier A: Compact Guides (<= 350 words)
    if word_count <= 350:
        header = make_context_header("Complete Guide")
        full_text = f"{header}\n\n{body}"
        chunk = DocumentChunk(
            chunk_id=f"{doc_id}_tier_a_0",
            doc_id=doc_id,
            parent_id=doc_id,
            source_platform=platform,
            doc_category=category,
            title=title,
            heading="Complete Guide",
            content=body,
            context_header=header,
            full_text=full_text,
            source_url=primary_url,
            country=country,
            city=city,
            linked_entity_id=linked_entity_id,
            chunk_type="tier_a",
        )
        return [chunk]

    # Tier B: FAQ Documents / Sections
    # Detect if document has Q&A structure
    is_faq = (
        category == "faq"
        or "faq" in file_path.stem.lower()
        or "**Q:" in body
        or "### Q:" in body
        or re.search(r"(?m)^Q:\s+", body)
    )

    if is_faq and ("**Q:" in body or "### Q:" in body or re.search(r"(?m)^Q:\s+", body)):
        # Split on Q: boundaries
        qa_pattern = r"(?=(\*\*Q:|\#\#\# Q:|(?m)^Q:\s+))"
        parts = re.split(qa_pattern, body)
        qa_blocks = []
        current_block = ""

        # First part might be intro overview
        if parts:
            intro = parts[0].strip()
            if len(intro.split()) >= 40:
                header = make_context_header("Overview")
                full_text = f"{header}\n\n{intro}"
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"{doc_id}_intro",
                        doc_id=doc_id,
                        parent_id=doc_id,
                        source_platform=platform,
                        doc_category=category,
                        title=title,
                        heading="Overview",
                        content=intro,
                        context_header=header,
                        full_text=full_text,
                        source_url=primary_url,
                        country=country,
                        city=city,
                        linked_entity_id=linked_entity_id,
                        chunk_type="tier_b_intro",
                    )
                )

        idx = 1
        while idx < len(parts):
            marker = parts[idx]
            block_content = parts[idx + 1] if idx + 1 < len(parts) else ""
            full_qa = f"{marker}{block_content}".strip()
            idx += 2

            if not full_qa or len(full_qa.split()) < 10:
                continue

            # Extract question line for heading
            q_line = full_qa.split("\n")[0].replace("**", "").replace("###", "").strip()
            if len(q_line) > 100:
                q_line = q_line[:97] + "..."

            header = make_context_header(q_line)
            full_text = f"{header}\n\n{full_qa}"
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{doc_id}_faq_{len(chunks)}",
                    doc_id=doc_id,
                    parent_id=doc_id,
                    source_platform=platform,
                    doc_category=category,
                    title=title,
                    heading=q_line,
                    content=full_qa,
                    context_header=header,
                    full_text=full_text,
                    source_url=primary_url,
                    country=country,
                    city=city,
                    linked_entity_id=linked_entity_id,
                    chunk_type="tier_b_faq",
                )
            )

        if chunks:
            return chunks

    # Tier C: Section-Hierarchical Chunking (Markdown Headers)
    # Split on ## or ###
    sections = re.split(r"(?m)(?=^##\s+)", body)
    if len(sections) <= 1:
        sections = re.split(r"(?m)(?=^###\s+)", body)

    for s_idx, sec in enumerate(sections):
        sec_text = sec.strip()
        if not sec_text or len(sec_text.split()) < 15:
            continue

        # Extract heading
        heading = f"Section {s_idx + 1}"
        lines = sec_text.split("\n")
        first_line = lines[0].strip()
        if first_line.startswith("#"):
            heading = first_line.lstrip("#").strip()

        # If section is very long (> 550 words), subdivide by paragraphs
        words = sec_text.split()
        if len(words) > 550:
            sub_paragraphs = sec_text.split("\n\n")
            current_sub = []
            current_count = 0
            sub_idx = 0
            for para in sub_paragraphs:
                p_words = len(para.split())
                if current_count + p_words > 450 and current_sub:
                    sub_text = "\n\n".join(current_sub).strip()
                    sub_heading = f"{heading} (Part {sub_idx + 1})"
                    header = make_context_header(sub_heading)
                    chunks.append(
                        DocumentChunk(
                            chunk_id=f"{doc_id}_s{s_idx}_p{sub_idx}",
                            doc_id=doc_id,
                            parent_id=doc_id,
                            source_platform=platform,
                            doc_category=category,
                            title=title,
                            heading=sub_heading,
                            content=sub_text,
                            context_header=header,
                            full_text=f"{header}\n\n{sub_text}",
                            source_url=primary_url,
                            country=country,
                            city=city,
                            linked_entity_id=linked_entity_id,
                            chunk_type="tier_c_section",
                        )
                    )
                    sub_idx += 1
                    current_sub = [para]
                    current_count = p_words
                else:
                    current_sub.append(para)
                    current_count += p_words
            if current_sub:
                sub_text = "\n\n".join(current_sub).strip()
                sub_heading = f"{heading} (Part {sub_idx + 1})" if sub_idx > 0 else heading
                header = make_context_header(sub_heading)
                chunks.append(
                    DocumentChunk(
                        chunk_id=f"{doc_id}_s{s_idx}_p{sub_idx}",
                        doc_id=doc_id,
                        parent_id=doc_id,
                        source_platform=platform,
                        doc_category=category,
                        title=title,
                        heading=sub_heading,
                        content=sub_text,
                        context_header=header,
                        full_text=f"{header}\n\n{sub_text}",
                        source_url=primary_url,
                        country=country,
                        city=city,
                        linked_entity_id=linked_entity_id,
                        chunk_type="tier_c_section",
                    )
                )
        else:
            header = make_context_header(heading)
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{doc_id}_s{s_idx}",
                    doc_id=doc_id,
                    parent_id=doc_id,
                    source_platform=platform,
                    doc_category=category,
                    title=title,
                    heading=heading,
                    content=sec_text,
                    context_header=header,
                    full_text=f"{header}\n\n{sec_text}",
                    source_url=primary_url,
                    country=country,
                    city=city,
                    linked_entity_id=linked_entity_id,
                    chunk_type="tier_c_section",
                )
            )

    return chunks


def generate_synthetic_entity_cards(db_path: Path = DB_PATH) -> List[DocumentChunk]:
    """Generates 65 Synthetic Entity Cards from SQLite serving views to bridge structured data into vector search."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cards: List[DocumentChunk] = []

    # 1. 33 DarGlobal Luxury Projects
    cursor.execute("""
    SELECT 
        project_id, project_name, brand_partner, parent_masterplan, country, city,
        district_or_location, price_display, completion_date, unit_configurations_json,
        area_sqm_min, area_sqm_max, freehold_for_foreigners, residency_eligible,
        tokenization_available, short_summary, project_url
    FROM v_darglobal_projects;
    """)
    for row in cursor.fetchall():
        pid = row["project_id"]
        pname = row["project_name"]
        partner = row["brand_partner"]
        country = row["country"]
        city = row["city"]
        price = row["price_display"]
        completion = row["completion_date"] or "TBD"
        summary = row["short_summary"] or ""
        url = row["project_url"]

        card_text = (
            f"# {pname} (DarGlobal Branded Project Card)\n\n"
            f"- **Brand Design Partner:** {partner}\n"
            f"- **Location:** {city}, {country} ({row['district_or_location']})\n"
            f"- **Masterplan:** {row['parent_masterplan'] or 'Stand-alone'}\n"
            f"- **Pricing:** {price}\n"
            f"- **Handover / Completion:** {completion}\n"
            f"- **Unit Configurations:** {row['unit_configurations_json']}\n"
            f"- **Area Range:** {row['area_sqm_min']} - {row['area_sqm_max']} SQM\n"
            f"- **Foreign Freehold Ownership:** {'100% Eligible' if row['freehold_for_foreigners'] else 'Restricted'}\n"
            f"- **Investor Residency / Golden Visa:** {'Eligible' if row['residency_eligible'] else 'No'}\n"
            f"- **Digital Tokenization:** {'Available' if row['tokenization_available'] else 'Not Enabled'}\n"
            f"- **Official Link:** {url}\n\n"
            f"**Overview:** {summary}"
        )

        header = (
            f"[CONTEXT] Platform: darglobal | Title: {pname} (Entity Card) | Category: project_card | "
            f"Entity: {pid} | Location: {city}, {country} | Section: Branded Residence Card"
        )

        cards.append(
            DocumentChunk(
                chunk_id=f"card_{pid}",
                doc_id=f"doc_{pid}",
                parent_id=pid,
                source_platform="darglobal",
                doc_category="project_card",
                title=f"{pname} - Project Entity Card",
                heading="Branded Residence Card",
                content=card_text,
                context_header=header,
                full_text=f"{header}\n\n{card_text}",
                source_url=url,
                country=country,
                city=city,
                linked_entity_id=pid,
                chunk_type="synthetic_card",
            )
        )

    # 2. 8 Wasalt Developer Projects
    cursor.execute("""
    SELECT 
        project_id, project_name, developer_name, country, city, district,
        project_type, starting_price_sar, available_units_count, wafi_registered,
        completion_status, project_url, description
    FROM v_wasalt_projects;
    """)
    for row in cursor.fetchall():
        pid = row["project_id"]
        pname = row["project_name"]
        dev = row["developer_name"] or "Wasalt Partner Developer"
        country = row["country"]
        city = row["city"]
        district = row["district"] or "Riyadh"
        price_sar = f"{row['starting_price_sar']:,.0f} SAR" if row["starting_price_sar"] else "Price upon inquiry"
        url = row["project_url"]

        card_text = (
            f"# {pname} (Wasalt Masterplan Project Card)\n\n"
            f"- **Master Developer:** {dev}\n"
            f"- **Location:** {district}, {city}, {country}\n"
            f"- **Project Typology:** {row['project_type']}\n"
            f"- **Starting Price:** {price_sar}\n"
            f"- **Available Units Remaining:** {row['available_units_count']}\n"
            f"- **Wafi Escrow Authority Registered:** {'Yes (Regulated)' if row['wafi_registered'] else 'No'}\n"
            f"- **Construction Status:** {row['completion_status']}\n"
            f"- **Official Link:** {url}\n\n"
            f"**Description:** {row['description'] or ''}"
        )

        header = (
            f"[CONTEXT] Platform: wasalt | Title: {pname} (Project Card) | Category: project_card | "
            f"Entity: {pid} | Location: {city}, {country} | Section: Developer Project Card"
        )

        cards.append(
            DocumentChunk(
                chunk_id=f"card_{pid}",
                doc_id=f"doc_{pid}",
                parent_id=pid,
                source_platform="wasalt",
                doc_category="project_card",
                title=f"{pname} - Masterplan Card",
                heading="Developer Project Card",
                content=card_text,
                context_header=header,
                full_text=f"{header}\n\n{card_text}",
                source_url=url,
                country=country,
                city=city,
                linked_entity_id=pid,
                chunk_type="synthetic_card",
            )
        )

    # 3. 15 Wasalt Auction Groups
    cursor.execute("""
    SELECT 
        auction_group_id, auction_title, auction_category, auction_type, city, status,
        total_assets, min_deposit_sar, starts_at, ends_at, sales_agent_name,
        auction_url, group_description, active_assets_count, lowest_lot_deposit_sar
    FROM v_wasalt_auctions;
    """)
    for row in cursor.fetchall():
        gid = row["auction_group_id"]
        title = row["auction_title"]
        cat = row["auction_category"]
        atype = row["auction_type"]
        status = row["status"]
        city = row["city"] or "Riyadh"
        deposit = f"{row['min_deposit_sar']:,.0f} SAR" if row["min_deposit_sar"] else "None specified"
        url = row["auction_url"]
        agent = row["sales_agent_name"] or "Authorized Liquidator"

        card_text = (
            f"# {title} (Auction Group Card)\n\n"
            f"- **Auction Group ID:** Group {gid}\n"
            f"- **Category:** {cat}\n"
            f"- **Legal Framework:** {atype} (Under REGA & Infath Rules)\n"
            f"- **Lifecycle Status:** {status.upper()}\n"
            f"- **Host Municipality:** {city}\n"
            f"- **Total Asset Lots:** {row['total_assets']} lots ({row['active_assets_count']} indexed)\n"
            f"- **Required e-Wallet Bidding Deposit:** {deposit}\n"
            f"- **Authorized Sales Agent:** {agent}\n"
            f"- **Bidding Schedule:** Starts {row['starts_at']} | Concludes {row['ends_at']}\n"
            f"- **Direct Auction URL:** {url}\n\n"
            f"**Announcement:** {row['group_description'] or ''}"
        )

        header = (
            f"[CONTEXT] Platform: wasalt | Title: {title} | Category: auction_card | "
            f"Entity: group_{gid} | Location: {city}, Saudi Arabia | Section: Auction Summary"
        )

        cards.append(
            DocumentChunk(
                chunk_id=f"card_auction_{gid}",
                doc_id=f"doc_auction_{gid}",
                parent_id=f"group_{gid}",
                source_platform="wasalt",
                doc_category="auction_card",
                title=f"{title} - Auction Card",
                heading="Auction Summary",
                content=card_text,
                context_header=header,
                full_text=f"{header}\n\n{card_text}",
                source_url=url,
                country="Saudi Arabia",
                city=city,
                linked_entity_id=gid,
                chunk_type="synthetic_card",
            )
        )

    # 4. 9 Residency & Market Rules
    cursor.execute("""
    SELECT 
        country, residency_program_name, min_investment_local, min_investment_usd_approx,
        off_plan_eligible, foreign_freehold_allowed, key_tax_benefits, special_notes, source_url
    FROM v_residency_rules;
    """)
    for row in cursor.fetchall():
        country = row["country"]
        prog = row["residency_program_name"] or f"{country} Real Estate Residency"
        min_local = row["min_investment_local"] or "Market Rate"
        min_usd = f"${row['min_investment_usd_approx']:,.0f} USD" if row["min_investment_usd_approx"] else "N/A"
        url = row["source_url"]

        card_text = (
            f"# {country} Real Estate Residency & Golden Visa Framework\n\n"
            f"- **Official Program Name:** {prog}\n"
            f"- **Country:** {country}\n"
            f"- **Minimum Investment (Local Currency):** {min_local}\n"
            f"- **Minimum Investment (USD Approx):** {min_usd}\n"
            f"- **Off-Plan Property Purchase Eligible:** {'Yes' if row['off_plan_eligible'] else 'No'}\n"
            f"- **Foreign Freehold Title Permitted:** {'Yes (100% Freehold)' if row['foreign_freehold_allowed'] else 'Restricted / Leasehold'}\n"
            f"- **Tax Advantages:** {row['key_tax_benefits'] or 'Standard local rates'}\n"
            f"- **Statutory Guidance & Rules:** {row['special_notes'] or 'Standard land registry rules apply'}\n"
            f"- **Reference URL:** {url}"
        )

        header = (
            f"[CONTEXT] Platform: regulatory | Title: {country} Residency Framework | Category: residency_card | "
            f"Entity: rules_{country} | Location: {country} | Section: Golden Visa Rules"
        )

        cid = f"card_residency_{country.lower().replace(' ', '_')}"
        cards.append(
            DocumentChunk(
                chunk_id=cid,
                doc_id=f"doc_{cid}",
                parent_id=f"residency_{country}",
                source_platform="regulatory",
                doc_category="residency_card",
                title=f"{country} Residency & Golden Visa Card",
                heading="Golden Visa Rules",
                content=card_text,
                context_header=header,
                full_text=f"{header}\n\n{card_text}",
                source_url=url,
                country=country,
                city=country,
                linked_entity_id=country,
                chunk_type="synthetic_card",
            )
        )

    conn.close()
    return cards


def build_indices() -> None:
    """Orchestrates document loading, chunking, synthetic card generation, and dual-index population."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    QDRANT_DIR.mkdir(parents=True, exist_ok=True)

    print("Step 1: Harvesting and chunking Markdown knowledge base...")
    all_chunks: List[DocumentChunk] = []

    md_files = list(UNSTRUCTURED_DIR.rglob("*.md"))
    print(f"Discovered {len(md_files)} markdown files in {UNSTRUCTURED_DIR}")

    for mf in md_files:
        fm, body = parse_markdown_file(mf)
        doc_chunks = chunk_document(mf, fm, body)
        all_chunks.extend(doc_chunks)

    print(f"Extracted {len(all_chunks)} semantic chunks from markdown files.")

    print("Step 2: Generating 65 Synthetic Entity Cards from SQLite serving views...")
    cards = generate_synthetic_entity_cards(DB_PATH)
    all_chunks.extend(cards)
    print(f"Generated {len(cards)} synthetic entity cards. Total chunks to index: {len(all_chunks)}")

    print(f"Step 3: Creating SQLite FTS5 Full-Text Search database at {RAG_DB_PATH}...")
    if RAG_DB_PATH.exists():
        RAG_DB_PATH.unlink()

    conn = sqlite3.connect(str(RAG_DB_PATH))
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE chunks (
        chunk_id TEXT PRIMARY KEY,
        doc_id TEXT NOT NULL,
        parent_id TEXT NOT NULL,
        source_platform TEXT NOT NULL,
        doc_category TEXT NOT NULL,
        title TEXT NOT NULL,
        heading TEXT NOT NULL,
        content TEXT NOT NULL,
        context_header TEXT NOT NULL,
        full_text TEXT NOT NULL,
        source_url TEXT NOT NULL,
        country TEXT NOT NULL,
        city TEXT NOT NULL,
        linked_entity_id TEXT,
        chunk_type TEXT NOT NULL
    );
    """)

    cursor.execute("""
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        chunk_id UNINDEXED,
        full_text,
        title,
        heading,
        country,
        city,
        source_platform,
        tokenize = 'porter unicode61'
    );
    """)

    cursor.executemany(
        """
        INSERT INTO chunks VALUES (
            :chunk_id, :doc_id, :parent_id, :source_platform, :doc_category,
            :title, :heading, :content, :context_header, :full_text,
            :source_url, :country, :city, :linked_entity_id, :chunk_type
        );
        """,
        [c.to_dict() for c in all_chunks],
    )

    cursor.executemany(
        """
        INSERT INTO chunks_fts (chunk_id, full_text, title, heading, country, city, source_platform)
        VALUES (:chunk_id, :full_text, :title, :heading, :country, :city, :source_platform);
        """,
        [c.to_dict() for c in all_chunks],
    )

    conn.commit()
    conn.close()
    print("SQLite FTS5 full-text index built successfully.")

    print("Step 4: Embedding chunks via FastEmbed ONNX (BAAI/bge-small-en-v1.5) and populating local Qdrant...")
    embedding_model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    qclient = QdrantClient(path=str(QDRANT_DIR))
    collection_name = "real_estate_chunks"

    # Recreate collection
    if qclient.collection_exists(collection_name):
        qclient.delete_collection(collection_name)

    qclient.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=384, distance=Distance.COSINE),
    )

    texts_to_embed = [c.full_text for c in all_chunks]
    print(f"Computing embeddings for {len(texts_to_embed)} chunks in batches...")

    batch_size = 64
    all_points: List[PointStruct] = []

    for i in range(0, len(all_chunks), batch_size):
        batch_chunks = all_chunks[i : i + batch_size]
        batch_texts = [c.full_text for c in batch_chunks]
        embeddings = list(embedding_model.embed(batch_texts))

        for j, (chunk, emb) in enumerate(zip(batch_chunks, embeddings)):
            pt_idx = i + j
            payload = chunk.to_dict()
            all_points.append(
                PointStruct(
                    id=pt_idx,
                    vector=emb.tolist(),
                    payload=payload,
                )
            )

    qclient.upsert(collection_name=collection_name, points=all_points)
    print(f"Successfully indexed {len(all_points)} vectors into local Qdrant at {QDRANT_DIR}.")
    print("Dual Hybrid Index Build Complete!")


if __name__ == "__main__":
    build_indices()
