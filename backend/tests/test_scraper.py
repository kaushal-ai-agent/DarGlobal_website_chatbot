"""Tests for Web Scraper, Checkpoint Manager, and Stream Batching."""

import json
import pytest
from pathlib import Path
from backend.src.services.scraper import CheckpointManager, JSONLBatchWriter, RealEstateScraper
from backend.src.models.schemas import PropertyItem


def test_checkpoint_initialization_and_saving(tmp_path: Path):
    """Verifies that checkpoint state initializes and persists correctly."""
    state_file = tmp_path / "checkpoints" / "scraper_state.json"
    mgr = CheckpointManager(state_path=state_file)
    assert mgr.total_records == 0
    assert mgr.wasalt_page == 1
    assert mgr.darglobal_index == 0

    mgr.record_wasalt_item("slug-test-1")
    mgr.record_darglobal_item("https://darglobal.co.uk/test-proj", 2)
    assert mgr.total_records == 2
    assert mgr.is_wasalt_processed("slug-test-1")
    assert mgr.is_darglobal_processed("https://darglobal.co.uk/test-proj")

    # Re-instantiate to verify disk read
    mgr2 = CheckpointManager(state_path=state_file)
    assert mgr2.total_records == 2
    assert mgr2.is_wasalt_processed("slug-test-1")


def test_jsonl_batch_writer(tmp_path: Path):
    """Verifies O(1) space streaming batch writing."""
    dataset_file = tmp_path / "dataset.jsonl"
    writer = JSONLBatchWriter(file_path=dataset_file, batch_size=2)

    item1 = PropertyItem(
        id="test-1",
        source="wasalt",
        title="Test Villa",
        location="Riyadh",
        source_url="https://wasalt.com/test-1",
    )
    item2 = PropertyItem(
        id="test-2",
        source="darglobal",
        title="Test Tower",
        location="Dubai",
        source_url="https://darglobal.co.uk/test-2",
    )

    writer.write_item(item1)
    # File not created yet (buffer = 1 < 2)
    assert not dataset_file.exists() or dataset_file.stat().st_size == 0

    writer.write_item(item2)
    # Flushed automatically at batch_size=2
    assert dataset_file.exists()
    lines = dataset_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["id"] == "test-1"
