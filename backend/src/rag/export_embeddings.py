"""Pre-computes and caches normalized chunk embeddings into a compact NumPy file (.npz).

File: backend/src/rag/export_embeddings.py
Purpose: Eliminates disk lock contention from Qdrant Local by creating a 487.5 KB
         NumPy matrix for all 325 chunks. Enables instant in-memory vector dot-product
         cosine similarity in < 0.2ms.
"""

import pickle
import sqlite3
import sys
from pathlib import Path
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INDEX_DIR = PROJECT_ROOT / "data" / "index"
STORAGE_SQLITE = INDEX_DIR / "qdrant" / "collection" / "real_estate_chunks" / "storage.sqlite"
TARGET_NPZ = INDEX_DIR / "chunk_embeddings.npz"


def export_embeddings(source_path: Path = STORAGE_SQLITE, target_path: Path = TARGET_NPZ) -> None:
    """Reads embeddings and metadata from Qdrant storage.sqlite (read-only) and exports to NPZ."""
    if not source_path.exists():
        raise FileNotFoundError(f"Source storage not found at {source_path}")

    print(f"Reading vectors from {source_path}...")
    conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute("SELECT id, point FROM points")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        raise ValueError("No points found in storage.sqlite")

    chunk_ids = []
    vectors = []
    payloads = []

    for r in rows:
        pt = pickle.loads(r[1])
        cid = pt.payload.get("chunk_id") if pt.payload else str(r[0])
        chunk_ids.append(cid)
        vectors.append(pt.vector)
        payloads.append(pt.payload or {})

    # Convert to contiguous float32 array
    matrix = np.array(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.maximum(norms, 1e-9)

    # Save to compressed NPZ
    target_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        str(target_path),
        matrix=matrix,
        chunk_ids=np.array(chunk_ids),
        payloads=np.array(payloads, dtype=object),
    )

    print(f"Successfully exported {len(chunk_ids)} chunks to {target_path}")
    print(f"Matrix shape: {matrix.shape} | File size: {target_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    export_embeddings()
