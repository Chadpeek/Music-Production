import json
import os
from pathlib import Path

import sys
import pytest

# Add the src directory to sys.path so that producer_os can be imported
SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from producer_os.engine import ProducerOSEngine
from producer_os.styles_service import StyleService
from producer_os.bucket_service import BucketService


def create_dummy_inbox(tmp_path: Path) -> Path:
    """Create a dummy inbox with a single pack containing a few files."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    pack = inbox / "PackA"
    pack.mkdir()
    # Create files: one clearly 808, one kick, one unknown
    (pack / "808_sub.wav").write_text("dummy", encoding="utf-8")
    (pack / "kick.wav").write_text("dummy", encoding="utf-8")
    (pack / "unknown.txt").write_text("dummy", encoding="utf-8")
    return inbox


def load_default_styles() -> dict:
    """Load the bundled default style definitions."""
    here = Path(__file__).resolve().parents[1]
    style_path = here / "src" / "bucket_styles.json"
    assert style_path.exists(), f"Default styles file missing at {style_path}"
    return json.loads(style_path.read_text(encoding="utf-8"))


def test_nfo_placement_and_no_per_wav(tmp_path):
    inbox = create_dummy_inbox(tmp_path)
    hub = tmp_path / "hub"
    hub.mkdir()
    style_data = load_default_styles()
    style_service = StyleService(style_data)
    bucket_service = BucketService({})
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=bucket_service)
    # Run copy mode
    report = engine.run(mode="copy")
    # Expect at least one file processed
    assert report["files_processed"] > 0
    # Category nfo should exist for Samples (the primary category for 808s and kicks)
    category_nfo = hub / "Samples.nfo"
    assert category_nfo.exists(), "Category .nfo missing"
    # There should be no .nfo files next to individual audio files
    for root, dirs, files in os.walk(hub):
        for f in files:
            if f.endswith(".nfo"):
                continue
            # Ensure no file has a sibling nfo with same stem
            stem = Path(f).stem
            sibling_nfo = Path(root) / f"{stem}.nfo"
            assert not sibling_nfo.exists(), f"Per-file .nfo found for {f}"
    # Bucket and pack .nfo files exist in some directory under hub
    nfo_files = list(hub.rglob("*.nfo"))
    # Expect at least 3 distinct .nfo files: category, bucket, pack
    assert len(nfo_files) >= 3


def test_idempotency(tmp_path):
    inbox = create_dummy_inbox(tmp_path)
    hub = tmp_path / "hub"
    hub.mkdir()
    style_data = load_default_styles()
    style_service = StyleService(style_data)
    bucket_service = BucketService({})
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=bucket_service)
    # First run copies files
    report1 = engine.run(mode="copy")
    first_moved = report1["files_copied"]
    # Second run should not copy any additional files
    report2 = engine.run(mode="copy")
    second_moved = report2["files_copied"]
    assert second_moved == 0, "Second run should not copy any files"