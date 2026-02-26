import json
import os
from pathlib import Path

import numpy as np
import soundfile as sf

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
    engine.run(mode="copy")
    # Second run should not copy any additional files
    report2 = engine.run(mode="copy")
    second_moved = report2["files_copied"]
    assert second_moved == 0, "Second run should not copy any files"


# ----------------------------------------------------------------------
# New tests for hybrid classification

def generate_sine(duration: float, sr: int = 44100, freq: float = 60.0) -> np.ndarray:
    """Generate a pure sine wave for the given duration and frequency."""
    t = np.linspace(0.0, duration, int(sr * duration), False)
    return np.sin(2 * np.pi * freq * t)


def generate_transient_kick(duration: float = 0.3, sr: int = 44100, freq: float = 60.0) -> np.ndarray:
    """Generate a kick-like transient: strong attack and rapid decay."""
    t = np.linspace(0.0, duration, int(sr * duration), False)
    envelope = np.exp(-t * 8.0)
    x = envelope * np.sin(2 * np.pi * freq * t)
    # Emphasize the initial attack
    attack_len = int(0.01 * sr)
    if attack_len > 0:
        x[:attack_len] *= 4.0
    return x


def generate_glide(duration: float = 1.0, sr: int = 44100, f_start: float = 65.0, f_end: float = 45.0) -> np.ndarray:
    """Generate a decaying sine with a downward pitch glide."""
    t = np.linspace(0.0, duration, int(sr * duration), False)
    # Exponential frequency glide
    ratio = f_end / f_start
    inst_freq = f_start * (ratio ** (t / duration))
    # Integrate instantaneous frequency to phase
    phase = 2 * np.pi * np.cumsum(inst_freq) / sr
    x = np.sin(phase) * np.exp(-t)  # apply decay envelope
    return x


def generate_silence(duration: float = 0.5, sr: int = 22050) -> np.ndarray:
    """Generate deterministic silence for ambiguous/low-confidence classification tests."""
    return np.zeros(int(sr * duration), dtype=np.float32)


def generate_hat_noise(duration: float = 0.12, sr: int = 22050) -> np.ndarray:
    """Generate deterministic bright/noisy hi-hat-like audio."""
    rng = np.random.default_rng(0)
    x = rng.standard_normal(int(sr * duration)).astype(np.float32)
    env = np.exp(-np.linspace(0.0, 6.0, x.size, dtype=np.float32))
    return (x * env).astype(np.float32)


def test_folder_hint_detection(tmp_path):
    """Files in folders containing bucket keywords should receive strong folder hint."""
    inbox = tmp_path / "inbox"
    pack = inbox / "808sPack"
    pack.mkdir(parents=True)
    sr = 22050
    x = generate_sine(0.5, sr)
    (pack / "01.wav").parent.mkdir(parents=True, exist_ok=True)
    sf.write(pack / "01.wav", x, sr)
    hub = tmp_path / "hub"
    hub.mkdir()
    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))
    report = engine.run(mode="copy")
    # Inspect the reported bucket for our file
    packs = report["packs"]
    found_bucket = None
    for p in packs:
        for f in p["files"]:
            if f["source"].endswith("01.wav"):
                found_bucket = f["bucket"]
    assert found_bucket == "808s", f"Folder hint failed: expected '808s', got {found_bucket}"

def test_audio_override_kick(tmp_path):
    """Kick-like audio should override folder hint for 808s when evidence strong."""
    inbox = tmp_path / "inbox"
    pack = inbox / "808sFolder"
    pack.mkdir(parents=True)
    sr = 22050
    x = generate_transient_kick(duration=0.3, sr=sr, freq=60.0)
    sf.write(pack / "kicktest.wav", x, sr)
    hub = tmp_path / "hub"
    hub.mkdir()
    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))
    report = engine.run(mode="copy")
    found_bucket = None
    for p in report["packs"]:
        for f in p["files"]:
            if f["source"].endswith("kicktest.wav"):
                found_bucket = f["bucket"]
    assert found_bucket == "Kicks", f"Audio override failed: expected 'Kicks', got {found_bucket}"


def test_glide_detection(tmp_path):
    """Tonal glide audio should be classified as 808s and detect glide."""
    inbox = tmp_path / "inbox"
    pack = inbox / "pack"
    pack.mkdir(parents=True)
    sr = 22050
    x = generate_glide(duration=1.0, sr=sr, f_start=65.0, f_end=45.0)
    sf.write(pack / "glide.wav", x, sr)
    hub = tmp_path / "hub"
    hub.mkdir()
    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))
    # Use internal classification to inspect glide detection details
    file_path = pack / "glide.wav"
    bucket, category, confidence, candidates, low_confidence, reason = engine._classify_file(file_path)
    assert bucket == "808s", f"Glide detection bucket mismatch: expected '808s', got {bucket}"
    glide_summary = reason.get("glide_summary", {})
    assert glide_summary.get("glide_detected", False), "Glide not detected for glide sample"


def test_low_confidence_marking(tmp_path):
    """Ambiguous noise should result in low confidence but still choose a bucket."""
    inbox = tmp_path / "inbox"
    pack = inbox / "noisePack"
    pack.mkdir(parents=True)
    # Generate deterministic silence (ambiguous across buckets, should force low confidence)
    sr = 22050
    x = generate_silence(duration=0.5, sr=sr)
    sf.write(pack / "noise.wav", x, sr)
    hub = tmp_path / "hub"
    hub.mkdir()
    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))
    bucket, category, confidence, candidates, low_confidence, reason = engine._classify_file(pack / "noise.wav")
    assert low_confidence, "Noise sample should be marked as low confidence"
    # Ensure a bucket is still chosen (not None)
    assert bucket is not None, "Noise sample should still choose a bucket"
    assert len(candidates) == 3, "Top 3 candidates should always be returned"
    assert "confidence_margin" in reason, "Reason payload must include confidence margin"


def test_wav_only_analysis_ignores_non_wav(tmp_path):
    """Only .wav files should be analyzed/processed."""
    inbox = tmp_path / "inbox"
    pack = inbox / "Pack"
    pack.mkdir(parents=True)
    sr = 22050
    sf.write(pack / "tone.wav", generate_sine(0.4, sr=sr, freq=440.0), sr)
    (pack / "tone.mp3").write_text("not real audio", encoding="utf-8")

    hub = tmp_path / "hub"
    hub.mkdir()
    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))

    report = engine.run(mode="analyze")
    processed = [f for p in report["packs"] for f in p["files"]]
    assert len(processed) == 1, f"Expected only one WAV to be processed, got {len(processed)}"
    assert processed[0]["source"].endswith("tone.wav")


def test_run_report_contains_hybrid_reasoning_fields(tmp_path):
    """run_report.json must contain full per-file hybrid reasoning fields."""
    inbox = tmp_path / "inbox"
    pack = inbox / "808s"
    pack.mkdir(parents=True)
    sr = 22050
    sf.write(pack / "01.wav", generate_sine(0.7, sr=sr, freq=55.0), sr)

    hub = tmp_path / "hub"
    hub.mkdir()
    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))

    report = engine.run(mode="copy")
    run_id = report["run_id"]
    run_report_path = hub / "logs" / run_id / "run_report.json"
    assert run_report_path.exists(), "run_report.json must be created in copy mode"

    report_data = json.loads(run_report_path.read_text(encoding="utf-8"))
    entries = [f for p in report_data.get("packs", []) for f in p.get("files", []) if f["source"].endswith("01.wav")]
    assert entries, "Expected report entry for 01.wav"
    entry = entries[0]

    for key in (
        "bucket",
        "chosen_bucket",
        "confidence_ratio",
        "confidence_margin",
        "low_confidence",
        "top_candidates",
        "top_3_candidates",
        "folder_matches",
        "filename_matches",
        "audio_summary",
        "pitch_summary",
        "glide_summary",
    ):
        assert key in entry, f"Missing required report field: {key}"
    assert len(entry["top_candidates"]) == 3, "Top 3 candidates must be logged"
    assert len(entry["top_3_candidates"]) == 3, "Top 3 candidate alias must be logged"
    assert "feature_cache_stats" in report_data, "run_report.json must include feature cache stats"
    assert isinstance(report_data["feature_cache_stats"], dict)


def test_feature_cache_json_created_and_reused(tmp_path):
    """Feature cache should persist and load by absolute_path|size|mtime key."""
    inbox = tmp_path / "inbox"
    pack = inbox / "cachePack"
    pack.mkdir(parents=True)
    sr = 22050
    file_path = pack / "tone.wav"
    sf.write(file_path, generate_sine(0.6, sr=sr, freq=70.0), sr)

    hub = tmp_path / "hub"
    hub.mkdir()
    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))
    engine.run(mode="copy")

    cache_path = hub / "feature_cache.json"
    assert cache_path.exists(), "feature_cache.json must be written in copy mode"
    cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert isinstance(cache_data, dict) and cache_data, "feature_cache.json should contain entries"

    stat = file_path.stat()
    expected_key = f"{file_path.resolve()}|{stat.st_size}|{stat.st_mtime}"
    assert expected_key in cache_data, "Feature cache key must use absolute_path|size|mtime"

    engine2 = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))
    assert expected_key in engine2._feature_cache, "New engine instance should load persisted feature cache"
    cached_duration = engine2._feature_cache[expected_key]["duration"]
    features = engine2._extract_features(file_path)
    assert features["duration"] == cached_duration, "Cached features should be reused for unchanged file"


def test_run_reports_feature_cache_stats_and_analyze_is_not_persisted(tmp_path):
    inbox = tmp_path / "inbox"
    pack = inbox / "statsPack"
    pack.mkdir(parents=True)
    sr = 22050
    sf.write(pack / "tone.wav", generate_sine(0.5, sr=sr, freq=80.0), sr)
    hub = tmp_path / "hub"
    hub.mkdir()

    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))
    analyze_report = engine.run(mode="analyze")
    stats = analyze_report.get("feature_cache_stats", {})
    assert isinstance(stats, dict), "Analyze report must include feature_cache_stats"
    assert stats.get("persisted") is False, "Analyze mode must not persist feature cache"
    assert analyze_report.get("files_skipped_non_wav", 0) == 0

    copy_report = engine.run(mode="copy")
    stats_copy = copy_report.get("feature_cache_stats", {})
    assert isinstance(stats_copy, dict)
    assert "hits" in stats_copy and "misses" in stats_copy and "computed" in stats_copy
    assert isinstance(stats_copy.get("persisted"), bool)


def test_benchmark_report_json_written_with_expected_fields(tmp_path):
    inbox = tmp_path / "inbox"
    pack = inbox / "benchPack"
    pack.mkdir(parents=True)
    sr = 22050
    sf.write(pack / "tone.wav", generate_sine(0.5, sr=sr, freq=55.0), sr)
    sf.write(pack / "kick.wav", generate_transient_kick(duration=0.2, sr=sr, freq=60.0), sr)
    (pack / "skip.txt").write_text("ignore", encoding="utf-8")

    hub = tmp_path / "hub"
    hub.mkdir()
    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))
    out_path = hub / "logs" / "benchmark_report.json"

    benchmark = engine.run_benchmark(output_path=out_path, top_confusions=5)
    assert out_path.exists(), "benchmark-classifier JSON output must be written"
    written = json.loads(out_path.read_text(encoding="utf-8"))

    for payload in (benchmark, written):
        for key in (
            "version",
            "timestamp",
            "inbox",
            "hub",
            "files_classified",
            "files_skipped_non_wav",
            "low_confidence",
            "bucket_distribution",
            "confusion_pairs",
            "representative_misfits",
            "feature_cache_stats",
            "tuning_snapshot",
        ):
            assert key in payload, f"Missing benchmark field: {key}"
        assert payload["files_classified"] == 2
        assert payload["files_skipped_non_wav"] == 1
        assert isinstance(payload["feature_cache_stats"], dict)


def test_user_bucket_hints_are_additive_and_logged(tmp_path):
    inbox = tmp_path / "inbox"
    pack = inbox / "neutralPack"
    pack.mkdir(parents=True)
    sr = 22050
    sf.write(pack / "ultraslidez.wav", generate_silence(duration=0.4, sr=sr), sr)
    hub = tmp_path / "hub"
    hub.mkdir()

    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(
        inbox,
        hub,
        style_service,
        config={
            "bucket_hints": {
                "version": 1,
                "folder_keywords": {},
                "filename_keywords": {"808s": ["ultraslidez"], "NotABucket": ["ignored"]},
            }
        },
        bucket_service=BucketService({}),
    )
    bucket, _category, _confidence, _candidates, _low_conf, reason = engine._classify_file(pack / "ultraslidez.wav")
    assert bucket == "808s", "User filename hint should influence classification additively"
    filename_matches = reason.get("filename_matches", [])
    assert any(
        m.get("keyword") == "ultraslidez" and m.get("bucket") == "808s" and m.get("source") == "user_hint"
        for m in filename_matches
    ), "User hints should be logged in filename_matches"


def test_hybrid_idempotent_second_run(tmp_path):
    """Second copy run should be idempotent for synthesized WAV input."""
    inbox = tmp_path / "inbox"
    pack = inbox / "idempotentPack"
    pack.mkdir(parents=True)
    sr = 22050
    sf.write(pack / "glide.wav", generate_glide(duration=0.8, sr=sr, f_start=65.0, f_end=50.0), sr)

    hub = tmp_path / "hub"
    hub.mkdir()
    style_service = StyleService(load_default_styles())
    engine = ProducerOSEngine(inbox, hub, style_service, config={}, bucket_service=BucketService({}))

    report1 = engine.run(mode="copy")
    report2 = engine.run(mode="copy")

    assert report1["files_copied"] >= 1
    assert report2["files_copied"] == 0, "Second run should not duplicate copies"
    assert report2["skipped_existing"] >= 1, "Second run should skip existing destinations"
    assert len(list(hub.rglob("glide.wav"))) == 1, "No duplicate destination files should be created"


def test_pitch_gating_skips_yin_for_bright_hat_like_audio(tmp_path):
    inbox = tmp_path / "inbox"
    pack = inbox / "hats"
    pack.mkdir(parents=True)
    sr = 22050
    file_path = pack / "hat.wav"
    sf.write(file_path, generate_hat_noise(duration=0.12, sr=sr), sr)

    hub = tmp_path / "hub"
    hub.mkdir()
    engine = ProducerOSEngine(inbox, hub, StyleService(load_default_styles()), config={}, bucket_service=BucketService({}))

    features = engine._extract_features(file_path)
    assert features.get("pitch_available") is False
    assert features.get("pitch_skipped") is True
    assert features.get("pitch_skip_reason") in {"hat_like_percussive", "kick_like_percussive"}

    _bucket, _cat, _conf, _cand, _low, reason = engine._classify_file(file_path)
    pitch_summary = reason.get("pitch_summary", {})
    assert pitch_summary.get("pitch_skipped") is True
    assert "pitch_skip_reason" in pitch_summary


def test_pitch_gating_does_not_skip_tonal_glide_808(tmp_path):
    inbox = tmp_path / "inbox"
    pack = inbox / "808s"
    pack.mkdir(parents=True)
    sr = 22050
    file_path = pack / "glide.wav"
    sf.write(file_path, generate_glide(duration=0.7, sr=sr, f_start=60.0, f_end=50.0), sr)

    hub = tmp_path / "hub"
    hub.mkdir()
    engine = ProducerOSEngine(inbox, hub, StyleService(load_default_styles()), config={}, bucket_service=BucketService({}))

    features = engine._extract_features(file_path)
    assert features.get("pitch_skipped") is False
    assert features.get("pitch_available") is True
    assert float(features.get("voiced_ratio", 0.0) or 0.0) > 0.0


def test_parallel_workers_preserve_deterministic_order_and_results(tmp_path):
    inbox = tmp_path / "inbox"
    pack = inbox / "mixedPack"
    pack.mkdir(parents=True)
    sr = 22050
    sf.write(pack / "01_glide.wav", generate_glide(duration=0.7, sr=sr, f_start=65.0, f_end=50.0), sr)
    sf.write(pack / "02_kick.wav", generate_transient_kick(duration=0.2, sr=sr, freq=60.0), sr)
    sf.write(pack / "03_hat.wav", generate_hat_noise(duration=0.12, sr=sr), sr)
    sf.write(pack / "04_tone.wav", generate_sine(0.5, sr=sr, freq=55.0), sr)

    hub1 = tmp_path / "hub_seq"
    hub2 = tmp_path / "hub_par"
    hub1.mkdir()
    hub2.mkdir()
    style = StyleService(load_default_styles())

    eng_seq = ProducerOSEngine(inbox, hub1, style, config={}, bucket_service=BucketService({}))
    eng_par = ProducerOSEngine(inbox, hub2, style, config={}, bucket_service=BucketService({}))

    r_seq = eng_seq.run(mode="analyze", developer_options={"workers": 1})
    r_par = eng_par.run(mode="analyze", developer_options={"workers": 4})

    seq_files = [f for p in r_seq["packs"] for f in p["files"]]
    par_files = [f for p in r_par["packs"] for f in p["files"]]
    assert [f["source"] for f in seq_files] == [f["source"] for f in par_files]
    assert [f["chosen_bucket"] for f in seq_files] == [f["chosen_bucket"] for f in par_files]
    assert [f["top_3_candidates"] for f in seq_files] == [f["top_3_candidates"] for f in par_files]
