from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from producer_os.bucket_service import BucketService
from producer_os.engine import ProducerOSEngine
from producer_os.styles_service import StyleService


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows path edge cases are Windows-specific")


def _load_default_styles() -> dict:
    repo_root = Path(__file__).resolve().parents[1]
    return json.loads((repo_root / "src" / "bucket_styles.json").read_text(encoding="utf-8"))


def _sine(duration: float = 0.4, sr: int = 22050, freq: float = 60.0) -> np.ndarray:
    t = np.linspace(0.0, duration, int(sr * duration), False)
    return np.sin(2 * np.pi * freq * t).astype(np.float32)


def _engine(inbox: Path, hub: Path) -> ProducerOSEngine:
    return ProducerOSEngine(
        inbox,
        hub,
        StyleService(_load_default_styles()),
        config={},
        bucket_service=BucketService({}),
    )


def test_unicode_and_nested_paths_copy_mode(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    hub = tmp_path / "hub"
    nested = inbox / "Päck_日本語" / "Sub Folder_01" / "Drüms"
    nested.mkdir(parents=True)
    sf.write(nested / "kick_á.wav", _sine(freq=70.0), 22050)
    sf.write(nested / "hat_ß.wav", _sine(freq=7000.0), 22050)
    hub.mkdir()

    report = _engine(inbox, hub).run(mode="copy")
    assert report["files_processed"] == 2
    assert report["failed"] == 0
    assert any(p.name == "kick_á.wav" for p in hub.rglob("*.wav"))
    assert any(p.name == "hat_ß.wav" for p in hub.rglob("*.wav"))


def test_duplicate_filenames_in_different_packs_remain_distinct(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    hub = tmp_path / "hub"
    (inbox / "PackA").mkdir(parents=True)
    (inbox / "PackB").mkdir(parents=True)
    sf.write(inbox / "PackA" / "same.wav", _sine(freq=55.0), 22050)
    sf.write(inbox / "PackB" / "same.wav", _sine(freq=65.0), 22050)
    hub.mkdir()

    report = _engine(inbox, hub).run(mode="copy")
    assert report["files_processed"] == 2
    assert len(list(hub.rglob("same.wav"))) == 2


def test_locked_file_handling_is_safe_via_permission_error_simulation(tmp_path: Path, monkeypatch) -> None:
    inbox = tmp_path / "inbox"
    hub = tmp_path / "hub"
    pack = inbox / "PackLocked"
    pack.mkdir(parents=True)
    file_path = pack / "kick.wav"
    sf.write(file_path, _sine(freq=60.0), 22050)
    hub.mkdir()
    engine = _engine(inbox, hub)

    original_move_or_copy = engine._move_or_copy
    calls = {"count": 0}

    def _fail_once(src: Path, dst: Path, mode: str) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("Simulated locked file")
        original_move_or_copy(src, dst, mode)

    monkeypatch.setattr(engine, "_move_or_copy", _fail_once)
    report = engine.run(mode="copy")

    assert report["failed"] >= 1
    assert file_path.exists(), "Source file should remain untouched on copy failure"
    assert any(
        "failed" in str(f.get("action", "")).lower() or "move/copy failed" in str(f.get("reason", "")).lower()
        for p in report["packs"]
        for f in p.get("files", [])
    )


def test_long_path_behavior_safe(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    hub = tmp_path / "hub"
    hub.mkdir()

    # Build a deep path; if the platform/runner disallows it, mark xfail rather than failing the suite.
    current = inbox
    for i in range(20):
        current = current / ("very_long_folder_name_" + str(i).zfill(2) + "_x" * 4)
    try:
        current.mkdir(parents=True)
        sf.write(current / "longpath.wav", _sine(freq=58.0), 22050)
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.xfail(f"Long path creation unsupported in this Windows environment: {exc}")

    report = _engine(inbox, hub).run(mode="analyze")
    assert report["files_processed"] >= 1
