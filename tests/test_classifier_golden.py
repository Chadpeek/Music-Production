from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from producer_os.bucket_service import BucketService
from producer_os.engine import ProducerOSEngine
from producer_os.styles_service import StyleService


def _load_default_styles() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[1]
    return json.loads((repo_root / "src" / "bucket_styles.json").read_text(encoding="utf-8"))


def _load_cases() -> list[dict[str, Any]]:
    fixture = Path(__file__).resolve().parent / "fixtures" / "classifier_golden_cases.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _generate_signal(spec: dict[str, Any], sr: int = 22050) -> np.ndarray:
    kind = str(spec.get("kind", "sine"))
    duration = float(spec.get("duration", 0.5))
    n = max(1, int(sr * duration))
    t = np.linspace(0.0, duration, n, False, dtype=np.float64)

    if kind == "sine":
        freq = float(spec.get("freq", 60.0))
        return np.sin(2 * np.pi * freq * t).astype(np.float32)

    if kind == "kick":
        freq = float(spec.get("freq", 60.0))
        env = np.exp(-t * 10.0)
        x = env * np.sin(2 * np.pi * freq * t)
        attack_len = min(n, max(1, int(sr * 0.005)))
        x[:attack_len] *= 5.0
        return x.astype(np.float32)

    if kind == "glide":
        f_start = float(spec.get("f_start", 60.0))
        f_end = float(spec.get("f_end", 50.0))
        ratio = f_end / max(1e-9, f_start)
        inst_freq = f_start * (ratio ** (t / max(duration, 1e-9)))
        phase = 2 * np.pi * np.cumsum(inst_freq) / sr
        x = np.sin(phase) * np.exp(-t)
        return x.astype(np.float32)

    if kind == "hat_noise":
        rng = np.random.default_rng(0)
        x = rng.standard_normal(n).astype(np.float32)
        env = np.exp(-np.linspace(0.0, 6.0, n, dtype=np.float32))
        return (x * env).astype(np.float32)

    if kind == "ambiguous":
        x = 0.25 * np.sin(2 * np.pi * 200.0 * t) + 0.25 * np.sin(2 * np.pi * 400.0 * t)
        return x.astype(np.float32)

    raise ValueError(f"Unknown signal kind: {kind}")


def _assert_case(case: dict[str, Any], result: tuple[Any, Any, Any, Any, Any, Any]) -> None:
    bucket, _category, confidence, candidates, low_confidence, reason = result
    expected = dict(case.get("expected") or {})

    assert bucket == expected["chosen_bucket"], f"{case['id']}: chosen bucket changed"
    assert bool(low_confidence) is bool(expected["low_confidence"]), f"{case['id']}: low_confidence changed"

    top3_expected = list(expected.get("top3_buckets") or [])
    top3_actual = [str(b) for b, _s in list(candidates)[:3]]
    assert top3_actual == top3_expected, f"{case['id']}: top-3 order changed ({top3_actual} != {top3_expected})"

    conf_min, conf_max = [float(v) for v in expected.get("confidence_ratio_range", [0.0, 1.0])]
    conf_ratio = float(reason.get("confidence_ratio", confidence) or 0.0)
    assert conf_min <= conf_ratio <= conf_max, f"{case['id']}: confidence ratio {conf_ratio} out of range"

    glide_detected = bool((reason.get("glide_summary") or {}).get("glide_detected", False))
    assert glide_detected is bool(expected.get("glide_detected", False)), f"{case['id']}: glide_detected changed"


def test_classifier_golden_regression_cases(tmp_path):
    inbox = tmp_path / "inbox"
    hub = tmp_path / "hub"
    inbox.mkdir()
    hub.mkdir()

    style_service = StyleService(_load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))

    for case in _load_cases():
        folder = inbox / str(case["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        file_path = folder / str(case["filename"])
        x = _generate_signal(dict(case["signal"]))
        sf.write(file_path, x, 22050)
        result = engine._classify_file(file_path)
        _assert_case(case, result)
