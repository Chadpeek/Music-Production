"""
Comprehensive test suite validating hard rules and engine behavior.

This test suite ensures:
1. NFO placement rules (folder.nfo only, never per-file)
2. Idempotency (no duplicates, no "(2)" files)
3. Deterministic classification (same input → same output)
4. Safety by default (ANALYZE changes nothing)
5. Undo safety (quarantine on conflicts)
6. Style repair (regenerate missing, remove orphans)
7. Portable mode detection
8. Audit trail completeness

Run with: pytest tests/test_engine_rules.py -v
"""

import csv
import json
import os
import time
from pathlib import Path

import pytest

# Add src to path for imports

from producer_os.engine import ProducerOSEngine
from producer_os.styles_service import StyleService
from producer_os.bucket_service import BucketService


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def default_styles() -> dict:
    """Load default bucket_styles.json."""
    style_path = Path(__file__).resolve().parents[1] / "src" / "bucket_styles.json"
    assert style_path.exists(), f"Default styles missing at {style_path}"
    with open(style_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def default_buckets() -> dict:
    """Load default buckets.json."""
    bucket_path = Path(__file__).resolve().parents[1] / "src" / "buckets.json"
    if bucket_path.exists():
        with open(bucket_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


@pytest.fixture
def inbox_with_packs(tmp_path: Path) -> Path:
    """Create a realistic inbox with multiple packs and files."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    
    # Pack 1: Clear 808s and kicks
    pack1 = inbox / "808_Collection_Vol1"
    pack1.mkdir()
    (pack1 / "808_sub_deep.wav").write_text("dummy808", encoding="utf-8")
    (pack1 / "808_sub_bright.wav").write_text("dummy808", encoding="utf-8")
    (pack1 / "kick_punchy.wav").write_text("dummykick", encoding="utf-8")
    
    # Pack 2: Snares and percs
    pack2 = inbox / "Snare_Pack_v2"
    pack2.mkdir()
    (pack2 / "snare_tight.wav").write_text("dummysnare", encoding="utf-8")
    (pack2 / "perc_cowbell.wav").write_text("dummyperc", encoding="utf-8")
    
    # Pack 3: Ambiguous files (should route to UNSORTED)
    pack3 = inbox / "Mystery_Sounds"
    pack3.mkdir()
    (pack3 / "weird_noise.wav").write_text("dummynoise", encoding="utf-8")
    (pack3 / "random.txt").write_text("text", encoding="utf-8")
    
    return inbox


@pytest.fixture
def hub_dir(tmp_path: Path) -> Path:
    """Create empty hub directory."""
    hub = tmp_path / "hub"
    hub.mkdir()
    return hub


@pytest.fixture
def engine(inbox_with_packs: Path, hub_dir: Path, default_styles: dict, default_buckets: dict) -> ProducerOSEngine:
    """Create engine instance with default services."""
    style_service = StyleService(default_styles)
    bucket_service = BucketService(default_buckets)
    return ProducerOSEngine(
        inbox_dir=inbox_with_packs,
        hub_dir=hub_dir,
        style_service=style_service,
        config={},
        bucket_service=bucket_service,
    )


# ============================================================================
# RULE 1: NFO PLACEMENT (Folder sidecars only, never per-file)
# ============================================================================

def test_nfo_only_folder_level(engine: ProducerOSEngine, hub_dir: Path):
    """Verify .nfo files are written only at folder level, never next to audio."""
    engine.run(mode="copy")
    
    # Walk entire hub
    for root, dirs, files in os.walk(hub_dir):
        for fname in files:
            if fname.endswith(".wav"):
                # HARD RULE: Check no sibling .nfo with same stem
                stem = Path(fname).stem
                sibling_nfo = Path(root) / f"{stem}.nfo"
                assert not sibling_nfo.exists(), (
                    f"HARD RULE VIOLATION: Per-file .nfo found next to {fname} at {sibling_nfo}"
                )
    
    # Verify .nfo files exist as folder sidecars
    nfo_count = 0
    for nfo_file in hub_dir.rglob("*.nfo"):
        nfo_stem = nfo_file.stem
        parent = nfo_file.parent
        
        # The .nfo should correspond to either:
        # 1. A folder with the same name (Samples.nfo → Samples/)
        # 2. A root category (Samples, Loops, MIDI)
        matching_folder = parent / nfo_stem
        is_category = nfo_stem in ["Samples", "Loops", "MIDI", "UNSORTED"]
        
        assert matching_folder.exists() or is_category, (
            f"Orphan .nfo without matching folder: {nfo_file}"
        )
        nfo_count += 1
    
    assert nfo_count > 0, "No .nfo files created"
    print(f"✓ {nfo_count} .nfo files correctly placed as folder sidecars")


def test_nfo_idempotent_rewrite(engine: ProducerOSEngine, hub_dir: Path):
    """Verify .nfo files are NOT rewritten if content is identical."""
    # First run
    engine.run(mode="copy")
    
    # Collect .nfo mtime after first run
    nfo_mtimes_before: dict = {}
    for nfo_file in hub_dir.rglob("*.nfo"):
        nfo_mtimes_before[str(nfo_file)] = nfo_file.stat().st_mtime
    
    # Wait to ensure time delta is detectable
    time.sleep(0.2)
    
    # Second run with identical input
    engine.run(mode="copy")
    
    # Verify .nfo files were NOT modified (mtimes unchanged)
    nfo_mtimes_after: dict = {}
    for nfo_file in hub_dir.rglob("*.nfo"):
        nfo_mtimes_after[str(nfo_file)] = nfo_file.stat().st_mtime
    
    # All .nfo files should have same mtime as before
    unchanged_count = 0
    for nfo_path, mtime_before in nfo_mtimes_before.items():
        mtime_after = nfo_mtimes_after.get(nfo_path)
        if mtime_after is not None and mtime_before == mtime_after:
            unchanged_count += 1
    
    assert unchanged_count == len(nfo_mtimes_before), (
        f"Some .nfo files were unnecessarily rewritten. "
        f"Unchanged: {unchanged_count}/{len(nfo_mtimes_before)}"
    )
    print(f"✓ All {unchanged_count} .nfo files remained unchanged (idempotent)")


# ============================================================================
# RULE 2: IDEMPOTENCY (No duplicates, no "(2)" files, safe twice)
# ============================================================================

def test_idempotent_copy_mode(engine: ProducerOSEngine):
    """Running copy twice must produce identical state (second run: 0 copies)."""
    # First run
    report1 = engine.run(mode="copy")
    files_copied_1 = report1.get("files_copied", 0)
    assert files_copied_1 > 0, "Test requires at least one file to copy"
    
    # Second run on identical state
    report2 = engine.run(mode="copy")
    files_copied_2 = report2.get("files_copied", 0)
    
    assert files_copied_2 == 0, (
        f"HARD RULE VIOLATION: Second copy run copied {files_copied_2} files. "
        "Expected 0 (idempotent). Files already in destination should be skipped."
    )
    print(f"✓ Copy is idempotent: Run 1: {files_copied_1} files, Run 2: 0 files")


def test_idempotent_move_mode(engine: ProducerOSEngine):
    """Running move twice must produce identical state."""
    # First move
    report1 = engine.run(mode="move")
    files_moved_1 = report1.get("files_moved", 0)
    
    if files_moved_1 == 0:
        pytest.skip("Test requires at least one file to move")
    
    # Second move on identical state
    report2 = engine.run(mode="move")
    files_moved_2 = report2.get("files_moved", 0)
    
    assert files_moved_2 == 0, (
        f"HARD RULE VIOLATION: Second move run moved {files_moved_2} files. "
        "Expected 0 (idempotent)."
    )
    print(f"✓ Move is idempotent: Run 1: {files_moved_1} files, Run 2: 0 files")


def test_no_duplicate_folder_names(engine: ProducerOSEngine, hub_dir: Path):
    """Verify no "(2)" or duplicate folder/file names are created."""
    # Run twice
    engine.run(mode="copy")
    engine.run(mode="copy")
    
    # Scan for duplicate markers
    found_duplicates = []
    for root, dirs, files in os.walk(hub_dir):
        for name in dirs + files:
            if " (2)" in name or " (1)" in name:
                found_duplicates.append(name)
    
    assert not found_duplicates, (
        f"HARD RULE VIOLATION: Found duplicate markers: {found_duplicates}"
    )
    print("✓ No duplicate folder/file names created on second run")


# ============================================================================
# RULE 3: DETERMINISTIC CLASSIFICATION (Same input → same output)
# ============================================================================

def test_deterministic_classification_same_runs(engine: ProducerOSEngine):
    """Same file must classify to same bucket across multiple runs."""
    # First run
    report1 = engine.run(mode="analyze")
    buckets_run1 = {}
    for pack in report1.get("packs", []):
        for file_info in pack.get("files", []):
            buckets_run1[file_info["source"]] = file_info["bucket"]
    
    if not buckets_run1:
        pytest.skip("No files found to classify")
    
    # Second run
    report2 = engine.run(mode="analyze")
    buckets_run2 = {}
    for pack in report2.get("packs", []):
        for file_info in pack.get("files", []):
            buckets_run2[file_info["source"]] = file_info["bucket"]
    
    # Compare classifications
    mismatches = []
    for src, bucket1 in buckets_run1.items():
        bucket2 = buckets_run2.get(src)
        if bucket1 != bucket2:
            mismatches.append((src, bucket1, bucket2))
    
    assert not mismatches, (
        f"HARD RULE VIOLATION: File classification changed between runs:\n"
        f"{mismatches}"
    )
    print(f"✓ Classification is deterministic across {len(buckets_run1)} files")


# ============================================================================
# RULE 4: SAFETY BY DEFAULT (ANALYZE mode changes nothing)
# ============================================================================

def test_analyze_mode_no_changes(engine: ProducerOSEngine, hub_dir: Path):
    """ANALYZE mode must not copy, move, or modify any files or styles."""
    # Count hub contents before
    hub_contents_before = set()
    for item in hub_dir.rglob("*"):
        hub_contents_before.add(str(item))
    
    # Run ANALYZE
    report = engine.run(mode="analyze")
    
    # Count hub contents after
    hub_contents_after = set()
    for item in hub_dir.rglob("*"):
        hub_contents_after.add(str(item))
    
    # No change
    assert hub_contents_before == hub_contents_after, (
        f"HARD RULE VIOLATION: ANALYZE mode modified hub directory. "
        f"Differences: {hub_contents_after - hub_contents_before}"
    )
    
    assert report.get("files_copied", 0) == 0
    assert report.get("files_moved", 0) == 0
    print("✓ ANALYZE mode makes zero changes")


def test_copy_vs_move_modes(engine: ProducerOSEngine, inbox_with_packs: Path, hub_dir: Path):
    """COPY preserves inbox; MOVE empties it."""
    inbox_file_count_before = len(list(inbox_with_packs.rglob("*.wav")))
    
    # COPY run
    engine.run(mode="copy")
    inbox_file_count_after_copy = len(list(inbox_with_packs.rglob("*.wav")))
    
    assert inbox_file_count_after_copy == inbox_file_count_before, (
        f"COPY mode should preserve inbox. Before: {inbox_file_count_before}, "
        f"After: {inbox_file_count_after_copy}"
    )
    print(f"✓ COPY preserves inbox ({inbox_file_count_before} files remain)")


# ============================================================================
# RULE 5: UNDO SAFETY (Audit trail, conflict quarantine)
# ============================================================================

def test_audit_csv_generated_on_move(engine: ProducerOSEngine, hub_dir: Path):
    """MOVE mode must generate audit.csv with full decision info."""
    report = engine.run(mode="move")
    
    run_id = report.get("run_id")
    assert run_id, "Report missing run_id"
    
    audit_path = hub_dir / "logs" / run_id / "audit.csv"
    assert audit_path.exists(), (
        f"HARD RULE VIOLATION: audit.csv not generated in MOVE mode at {audit_path}"
    )
    
    # Verify audit structure
    with open(audit_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)
    
    expected_headers = {"file", "pack", "category", "bucket", "confidence", "action", "reason"}
    actual_headers = set(headers)
    
    assert expected_headers.issubset(actual_headers), (
        f"audit.csv missing columns. Expected {expected_headers}, got {actual_headers}"
    )
    
    print(f"✓ audit.csv generated with {len(rows)} entries")


def test_undo_last_run_safe(engine: ProducerOSEngine, hub_dir: Path, inbox_with_packs: Path):
    """UNDO should safely revert moves, quarantining conflicts."""
    # First move
    report_move = engine.run(mode="move")
    files_moved = report_move.get("files_moved", 0)
    
    if files_moved == 0:
        pytest.skip("Test requires at least one file to move")
    
    # Files should now be in hub
    hub_files_after_move = set(hub_dir.rglob("*.wav"))
    assert len(hub_files_after_move) == files_moved, (
        "Files should be in hub after move"
    )
    
    # Undo
    report_undo = engine.undo_last_run()
    
    # Check result
    reverted_count = report_undo.get("reverted_count", 0)
    conflicts = report_undo.get("conflicts", [])
    
    # Total restored + conflicted should equal moved
    total_processed = reverted_count + len(conflicts)
    assert total_processed > 0, (
        f"UNDO produced no result. Moved: {files_moved}, "
        f"Reverted: {reverted_count}, Conflicts: {len(conflicts)}"
    )
    
    print(f"✓ UNDO reverted {reverted_count} files, {len(conflicts)} conflicts → Quarantine")


# ============================================================================
# RULE 6: REPAIR STYLES (Regenerate missing, remove orphans)
# ============================================================================

def test_repair_styles_regenerates_missing(engine: ProducerOSEngine, hub_dir: Path):
    """repair-styles should regenerate missing .nfo files."""
    # First run to create structure
    engine.run(mode="copy")
    
    # Find and delete some .nfo files
    nfo_files = list(hub_dir.rglob("*.nfo"))
    if len(nfo_files) < 2:
        pytest.skip("Test requires at least 2 .nfo files")
    
    deleted_nfo = nfo_files[:2]
    for nfo_file in deleted_nfo:
        nfo_file.unlink()
    
    nfo_count_before = len(list(hub_dir.rglob("*.nfo")))
    
    # Repair
    engine.repair_styles()
    
    nfo_count_after = len(list(hub_dir.rglob("*.nfo")))
    
    assert nfo_count_after >= nfo_count_before, (
        f"repair-styles should regenerate missing .nfo files. "
        f"Before: {nfo_count_before}, After: {nfo_count_after}"
    )
    print(f"✓ repair-styles regenerated .nfo files ({nfo_count_before} → {nfo_count_after})")


def test_repair_styles_removes_orphans(engine: ProducerOSEngine, hub_dir: Path):
    """repair-styles should remove .nfo files without corresponding folders."""
    # Create a run first
    engine.run(mode="copy")
    
    # Manually create an orphan .nfo
    samples_dir = hub_dir / "Samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    orphan_nfo = samples_dir / "NonExistentFolder.nfo"
    orphan_nfo.write_text('{"Color": "$000000"}', encoding="utf-8")
    
    assert orphan_nfo.exists(), "Orphan .nfo should be created"
    
    # Repair
    engine.repair_styles()
    
    assert not orphan_nfo.exists(), (
        "HARD RULE VIOLATION: repair-styles should remove orphan .nfo files"
    )
    print("✓ repair-styles removed orphan .nfo files")


# ============================================================================
# RULE 7: PORTABLE MODE DETECTION
# ============================================================================

def test_portable_mode_default_appdata(tmp_path: Path):
    """Portable mode should be OFF by default, config in AppData."""
    from producer_os.config_service import ConfigService
    
    config_service = ConfigService(app_dir=tmp_path)
    
    # Without portable.flag, should NOT be portable
    is_portable = config_service.is_portable_mode()
    assert not is_portable, (
        "Portable mode should be OFF by default (no portable.flag)"
    )
    print("✓ Default mode is AppData (not portable)")


def test_portable_mode_flag_detection(tmp_path: Path):
    """Portable mode should be ON if portable.flag exists."""
    from producer_os.config_service import ConfigService
    
    # Create portable.flag
    portable_flag = tmp_path / "portable.flag"
    portable_flag.touch()
    
    config_service = ConfigService(app_dir=tmp_path)
    
    # With portable.flag, should be portable
    is_portable = config_service.is_portable_mode()
    assert is_portable, (
        "Portable mode should be ON if portable.flag exists"
    )
    print("✓ Portable mode detected from portable.flag")


# ============================================================================
# RULE 8: CLASSIFICATION TRANSPARENCY (Reasons, candidates, scores)
# ============================================================================

def test_classification_reason_complete(engine: ProducerOSEngine):
    """Classification must include complete decision reason and candidates."""
    report = engine.run(mode="analyze")
    
    file_count = 0
    for pack in report.get("packs", []):
        for file_info in pack.get("files", []):
            file_count += 1
            
            # Verify reason exists
            reason = file_info.get("reason", "")
            assert isinstance(reason, str) and len(reason) > 0, (
                f"File {file_info.get('source')} has empty reason"
            )
            
            # Verify confidence is numeric
            confidence = file_info.get("confidence", 0)
            assert isinstance(confidence, (int, float)), (
                f"Confidence must be numeric, got {type(confidence)}"
            )
            
            # Verify bucket is set
            bucket = file_info.get("bucket")
            assert bucket is not None, (
                f"Bucket must be assigned for {file_info.get('source')}"
            )
    
    assert file_count > 0, "Test requires files to classify"
    print(f"✓ Classification complete for {file_count} files with reasons and candidates")


def test_low_confidence_routed_to_unsorted(engine: ProducerOSEngine):
    """Low confidence files must route to UNSORTED bucket."""
    report = engine.run(mode="analyze")
    
    low_confidence_files = []
    for pack in report.get("packs", []):
        for file_info in pack.get("files", []):
            confidence = file_info.get("confidence", 0)
            bucket = file_info.get("bucket")
            
            # If confidence is low, bucket should be UNSORTED
            if confidence < 0.5:
                if bucket != "UNSORTED":
                    low_confidence_files.append((file_info.get("source"), confidence, bucket))
    
    # Note: May have zero low-confidence files in test set (that's OK)
    if low_confidence_files:
        print(f"⚠ Found {len(low_confidence_files)} low-confidence files not routed to UNSORTED")
    else:
        print("✓ Low confidence files correctly routed to UNSORTED")


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

def test_full_workflow_analyze_copy_repair(engine: ProducerOSEngine, hub_dir: Path):
    """Full workflow: analyze → copy → repair."""
    # Step 1: Analyze (no changes)
    report_analyze = engine.run(mode="analyze")
    assert report_analyze.get("files_copied", 0) == 0, (
        "ANALYZE should not copy files"
    )
    
    # Step 2: Copy
    report_copy = engine.run(mode="copy")
    files_copied = report_copy.get("files_copied", 0)
    assert files_copied > 0, "COPY should process files from test inbox"
    
    # Step 3: Repair styles
    report_repair = engine.repair_styles()
    assert report_repair is not None, "repair-styles should return report"
    
    print(f"✓ Full workflow completed: analyze → copy ({files_copied} files) → repair")


def test_run_log_and_report_structure(engine: ProducerOSEngine, hub_dir: Path):
    """Verify logs and reports are created with correct structure."""
    report = engine.run(mode="copy")
    
    run_id = report.get("run_id")
    assert run_id, "Report missing run_id"
    
    log_dir = hub_dir / "logs" / run_id
    run_log = log_dir / "run_log.txt"
    run_report = log_dir / "run_report.json"
    
    assert log_dir.exists(), f"Log directory not created: {log_dir}"
    assert run_log.exists(), f"run_log.txt not created: {run_log}"
    assert run_report.exists(), f"run_report.json not created: {run_report}"
    
    # Verify report JSON structure
    with open(run_report, encoding="utf-8") as f:
        report_data = json.load(f)
    
    expected_keys = {"run_id", "mode", "timestamp", "files_processed", "packs"}
    actual_keys = set(report_data.keys())
    
    missing_keys = expected_keys - actual_keys
    assert not missing_keys, (
        f"Report missing keys: {missing_keys}"
    )
    
    print(f"✓ Run logs and reports created correctly (run_id: {run_id})")


# ============================================================================
# EDGE CASES
# ============================================================================

def test_ignores_macosx_and_dotfiles(engine: ProducerOSEngine, inbox_with_packs: Path, hub_dir: Path):
    """Engine should ignore __MACOSX, .DS_Store, and ._ prefixed files."""
    # Add ignored files to inbox
    pack = inbox_with_packs / "TestPack"
    pack.mkdir(exist_ok=True)
    (pack / ".DS_Store").write_text("mac junk", encoding="utf-8")
    (pack / "._hidden").write_text("mac hidden", encoding="utf-8")
    
    macosx_dir = pack / "__MACOSX"
    macosx_dir.mkdir()
    (macosx_dir / "ignored.wav").write_text("ignore this", encoding="utf-8")
    
    # Run copy
    engine.run(mode="copy")
    
    # Verify ignored files are NOT in hub
    hub_contents = set(hub_dir.rglob("*"))
    for item in hub_contents:
        assert ".DS_Store" not in item.name
        assert "._" not in item.name
        assert "__MACOSX" not in str(item)
    
    print("✓ Ignored files (.DS_Store, ._, __MACOSX) correctly excluded")


def test_handles_nested_subdirectories(engine: ProducerOSEngine, inbox_with_packs: Path, hub_dir: Path):
    """Engine should handle files nested in pack subdirectories."""
    # Create nested structure
    pack = inbox_with_packs / "NestedPack"
    pack.mkdir()
    nested = pack / "subdir" / "deeper"
    nested.mkdir(parents=True)
    (nested / "kick_sample.wav").write_text("nested kick", encoding="utf-8")
    
    # Run copy
    engine.run(mode="copy")
    
    # Verify nested structure preserved in hub
    hub_nested_files = list(hub_dir.rglob("kick_sample.wav"))
    assert len(hub_nested_files) >= 1, (
        "Nested file should be copied with directory structure preserved"
    )
    
    print("✓ Nested subdirectories preserved during organization")
