from __future__ import annotations

import json
from pathlib import Path

from producer_os.config_service import ConfigService


def test_bucket_hints_round_trip_and_defaults(tmp_path: Path) -> None:
    cfg = ConfigService(app_dir=tmp_path)

    # Missing file should return normalized default shape.
    default_hints = cfg.load_bucket_hints(cli_portable=True)
    assert default_hints == {"version": 1, "folder_keywords": {}, "filename_keywords": {}}

    payload = {
        "version": 1,
        "folder_keywords": {"808s": ["slides", "subbass"]},
        "filename_keywords": {"FX": ["impact", "riser"]},
    }
    cfg.save_bucket_hints(payload, cli_portable=True)
    path = cfg.get_bucket_hints_path(cli_portable=True)
    assert path.exists(), "bucket_hints.json must be written"

    loaded = cfg.load_bucket_hints(cli_portable=True)
    assert loaded == payload

    # Ensure file contents remain JSON object with stable keys.
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == 1
    assert "folder_keywords" in on_disk and "filename_keywords" in on_disk


def test_bucket_hints_invalid_payload_falls_back(tmp_path: Path) -> None:
    cfg = ConfigService(app_dir=tmp_path)
    path = cfg.get_bucket_hints_path(cli_portable=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    loaded = cfg.load_bucket_hints(cli_portable=True)
    assert loaded == {"version": 1, "folder_keywords": {}, "filename_keywords": {}}
