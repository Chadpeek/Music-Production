"""Core engine for Producer OS (v2).

The :class:`ProducerOSEngine` exposes methods to scan an inbox
directory, classify audio files into deterministic buckets, move or
copy them into a structured hub directory, write `.nfo` sidecar
files with styling information, and generate logs and reports.

This file was reconstructed to remove embedded pseudo-code fragments
and restore valid, testable Python structure.

Design notes / safety defaults:
- Only WAV files are processed by default (matches the Producer OS focus).
- If destination exists, the file is skipped (avoids "(2)" spam).
- Low-confidence classifications still route to the best bucket, but are
  flagged in logs/reports for transparency.
- `repair_styles()` is conservative: it creates/updates required `.nfo`
  files but does NOT delete "orphan" `.nfo` files (to avoid removing any
  user-authored styling).

This engine is UI-agnostic and depends only on:
- :class:`producer_os.styles_service.StyleService`
- :class:`producer_os.bucket_service.BucketService`
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypedDict, TypeAlias

from .bucket_service import BucketService
from .styles_service import StyleService


class PackFileEntry(TypedDict):
    source: str
    dest: str
    bucket: str
    category: str
    confidence: float
    action: str
    reason: str


class PackReport(TypedDict, total=False):
    pack: str
    files: list[PackFileEntry]
    counts: dict[str, int]
    warnings: list[str]
    errors: list[str]


# ---------------------------------------------------------------------------
# Tuning constants (can be overridden by tuning.json via _load_tuning_overrides)
FOLDER_HINT_WEIGHT = 50
FILENAME_HINT_WEIGHT = 25
FOLDER_HINT_CAP = 80
FILENAME_HINT_CAP = 40

LOW_CONFIDENCE_THRESHOLD = 0.75
PARENT_FOLDER_LEVELS_TO_SCAN = 5

FEATURE_THRESHOLDS: Dict[str, float] = {
    "808_duration_min": 0.45,
    "kick_duration_max": 0.35,
    "lowfreq_ratio_808": 0.60,
    "lowfreq_ratio_hat_max": 0.20,
    "centroid_bright": 4000,
    "centroid_low": 300,
    "transient_kick_min": 3.0,
    "zcr_tonal_max": 0.08,
    "flatness_high": 0.3,
    "zcr_high": 0.1,
    "centroid_moderate_low": 1000,
    "centroid_moderate_high": 3000,
}

AUDIO_WEIGHTS: Dict[str, float] = {
    "duration": 10,
    "lowfreq": 15,
    "centroid": 10,
    "transient": 20,
    "zcr": 10,
    "flatness": 10,
}

PITCH_WEIGHTS: Dict[str, float] = {
    "median_f0_low": 20,
    "voiced_ratio": 10,
    "glide_bonus": 10,
    "stability_bonus": 10,
}


def _clamp(val: float, min_val: float, max_val: float) -> float:
    """Clamp val between min_val and max_val."""
    return max(min_val, min(max_val, val))


# A classification is represented as a tuple:
# (bucket_name or None, category_name, confidence, list of (bucket, score) candidates)
ClassificationResult: TypeAlias = Tuple[Optional[str], str, float, List[Tuple[str, int]]]


DEFAULT_UNSORTED_STYLE: Dict[str, Any] = {
    "Color": "$7f7f7f",
    "IconIndex": 0,
    "SortGroup": 0,
}


@dataclass
class ProducerOSEngine:
    """Producer OS engine responsible for file routing and styling."""

    inbox_dir: Path
    hub_dir: Path
    style_service: StyleService
    config: Dict[str, Any]
    bucket_service: BucketService = field(default_factory=BucketService)

    ignore_rules: Iterable[str] = field(default_factory=lambda: ["__MACOSX", ".DS_Store", "._"])
    confidence_threshold: float = 0.75

    # Bucket rules: maps bucket names to lists of substrings to search for
    BUCKET_RULES: Dict[str, List[str]] = field(
        default_factory=lambda: {
            "808s": ["808", "808s"],
            "Kicks": ["kick", "kicks"],
            "Snares": ["snare", "snares"],
            "Claps": ["clap", "claps"],
            "HiHats": ["hihat", "hi-hat", "hat", "hats"],
            "Percs": ["perc", "percs", "percussion"],
            "Cymbals": ["cymbal", "cymbals", "crash", "ride", "bell"],
            "Bass": ["bass"],
            "Leads": ["lead", "leads"],
            "Vox": ["vox", "vocal", "vocals", "acapella"],
            "FX": ["fx", "effect", "effects", "sweep", "sweeps", "riser", "risers", "impact", "impacts"],
            "DrumLoop": ["drumloop", "drum_loop", "drum loop", "drum-loop", "loop drum", "loop_drums"],
            "MelodyLoop": [
                "melodic loop",
                "melodyloop",
                "melody_loop",
                "melody loop",
                "loop melody",
                "melod",
                "chord",
                "chords",
                "guitar loop",
                "piano loop",
            ],
            "MIDI": [".mid", "mid file"],
        }
    )

    # Map buckets to categories
    CATEGORY_MAP: Dict[str, str] = field(
        default_factory=lambda: {
            "808s": "Samples",
            "Kicks": "Samples",
            "Snares": "Samples",
            "Claps": "Samples",
            "HiHats": "Samples",
            "Percs": "Samples",
            "Cymbals": "Samples",
            "Bass": "Samples",
            "Leads": "Samples",
            "Vox": "Samples",
            "FX": "Samples",
            "DrumLoop": "Loops",
            "MelodyLoop": "Loops",
            "MIDI": "MIDI",
        }
    )

    # Internal state
    _feature_cache: Dict[str, Any] = field(init=False, default_factory=dict)
    _tuning_loaded: bool = field(init=False, default=False)
    current_mode: str = field(init=False, default="analyze")

    def __post_init__(self) -> None:
        self.inbox_dir = Path(self.inbox_dir)
        self.hub_dir = Path(self.hub_dir)
        self._load_tuning_overrides()
        self._load_feature_cache()

    # ------------------------------------------------------------------
    # Tuning overrides and feature caching
    def _load_tuning_overrides(self) -> None:
        """Load overrides from tuning.json (config dir first, then hub dir)."""
        if self._tuning_loaded:
            return

        tuning_paths: List[Path] = []

        if isinstance(self.config, dict):
            for key in ("config_path", "config_dir", "tuning_path"):
                p = self.config.get(key)
                if p:
                    tuning_paths.append(Path(p) / "tuning.json")

        tuning_paths.append(self.hub_dir / "tuning.json")

        for path in tuning_paths:
            try:
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for key, value in data.items():
                            if key in globals():
                                if isinstance(value, (int, float)):
                                    globals()[key] = value
                                elif isinstance(value, dict) and isinstance(globals()[key], dict):
                                    globals()[key].update(value)
                    self._tuning_loaded = True
                    return
            except Exception:
                continue

    def _load_feature_cache(self) -> None:
        cache_path = self.hub_dir / "feature_cache.json"
        self._feature_cache = {}
        if cache_path.exists():
            try:
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._feature_cache = data
            except Exception:
                self._feature_cache = {}

    def _save_feature_cache(self) -> None:
        cache_path = self.hub_dir / "feature_cache.json"
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(self._feature_cache), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Discovery / ignore logic
    def _should_ignore(self, name: str) -> bool:
        for rule in self.ignore_rules:
            if name == rule or name.startswith(rule):
                return True
        return False

    def _wrap_loose_files(self) -> None:
        """Move loose files in the inbox root into a timestamped pack folder."""
        if not self.inbox_dir.exists():
            return
        loose_files = [p for p in self.inbox_dir.iterdir() if p.is_file() and not self._should_ignore(p.name)]
        if not loose_files:
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_folder = self.inbox_dir / f"Loose_{timestamp}"
        tmp_folder.mkdir(exist_ok=True)
        for file_path in loose_files:
            shutil.move(str(file_path), str(tmp_folder / file_path.name))

    def _discover_packs(self) -> List[Path]:
        """Return immediate child folders of inbox as packs."""
        if not self.inbox_dir.exists():
            return []
        packs: List[Path] = []
        for p in self.inbox_dir.iterdir():
            if p.is_dir() and not self._should_ignore(p.name):
                packs.append(p)
        return sorted(packs)

    # ------------------------------------------------------------------
    # Scoring helpers
    def _get_folder_hint_scores(self, file_path: Path) -> Dict[str, int]:
        scores: Dict[str, int] = {bucket: 0 for bucket in self.BUCKET_RULES.keys()}
        parts = list(file_path.parent.parts)[-PARENT_FOLDER_LEVELS_TO_SCAN:]
        for part in parts:
            part_lower = part.lower()
            for bucket, patterns in self.BUCKET_RULES.items():
                for pat in patterns:
                    if pat.lower() in part_lower:
                        scores[bucket] += FOLDER_HINT_WEIGHT
        for bucket in scores:
            scores[bucket] = min(scores[bucket], FOLDER_HINT_CAP)
        return scores

    def _get_filename_hint_scores(self, filename: str) -> Dict[str, int]:
        scores: Dict[str, int] = {bucket: 0 for bucket in self.BUCKET_RULES.keys()}
        lower_name = filename.lower()
        for bucket, patterns in self.BUCKET_RULES.items():
            count = 0
            for pat in patterns:
                if pat == ".mid":
                    continue
                if pat.lower() in lower_name:
                    count += 1
            if count:
                scores[bucket] = min(count * FILENAME_HINT_WEIGHT, FILENAME_HINT_CAP)
        return scores

    # ------------------------------------------------------------------
    # Audio feature extraction (optional dependencies)
    def _extract_features(self, file_path: Path) -> Dict[str, Any]:
        """Extract audio features from a WAV file (best-effort).

        Uses a persistent cache keyed by file path + size + mtime.
        If optional audio libs aren't installed, returns zeroed features.
        """
        try:
            stat = file_path.stat()
            key = f"{file_path}|{stat.st_size}|{int(stat.st_mtime)}"
        except Exception:
            key = str(file_path)

        if key in self._feature_cache:
            cached = self._feature_cache.get(key)
            if isinstance(cached, dict):
                return cached

        # Default zeroed features
        features: Dict[str, Any] = {
            "duration": 0.0,
            "low_freq_ratio": 0.0,
            "transient_strength": 0.0,
            "centroid_mean": 0.0,
            "centroid_early": 0.0,
            "zcr_mean": 0.0,
            "flatness_mean": 0.0,
            "voiced_ratio": 0.0,
            "median_f0": 0.0,
            "pitch_std": 0.0,
            "glide_detected": False,
            "glide_confidence": 0.0,
            "glide_drop": 0.0,
        }

        # Lazy imports so engine can run without audio deps installed
        try:
            import numpy as np  # type: ignore
            import librosa  # type: ignore
            import soundfile as sf  # type: ignore
        except Exception:
            self._feature_cache[key] = features
            return features

        try:
            data, sr = sf.read(str(file_path), always_2d=False)
            if isinstance(data, (list, tuple)):
                data = np.array(data)
            if getattr(data, "ndim", 1) == 2:
                data = np.mean(data, axis=1)
            y = data.astype(float)

            max_val = float(np.max(np.abs(y))) if float(np.max(np.abs(y))) > 0 else 1.0
            y_norm = y / max_val
            # Number of samples in the normalized signal
            n = len(y_norm)
            # Duration in seconds
            duration = float(n) / float(sr)
            features["duration"] = duration

            # ------------------------------------------------------------------
            # Frame parameters (adaptive for very short samples)
            # Avoid librosa warnings and improve analysis of tiny one‑shots.
            win = 2048
            # Clamp window length based on signal length
            if n <= 0:
                # Empty input; avoid negative or zero window sizes
                win = 1
            elif n < win:
                # Use the full signal length when it is shorter than the default window
                win = n  # must not exceed signal length

            # Keep hop length proportional and valid
            hop = min(512, max(1, win // 4))

            S = np.abs(librosa.stft(y_norm, n_fft=win, hop_length=hop, window="hann"))
            P = S**2
            freqs = librosa.fft_frequencies(sr=sr, n_fft=win)

            total_power = float(np.sum(P))
            low_mask = freqs < 120.0
            low_power = float(np.sum(P[low_mask, :])) if P.size > 0 else 0.0
            features["low_freq_ratio"] = float(low_power) / float(total_power + 1e-9)

            rms = librosa.feature.rms(S=S, hop_length=hop)[0]
            early_frames = int((0.08 * sr) / hop + 0.999)
            mid_frames = int((0.20 * sr) / hop + 0.999)
            if len(rms) > 0:
                peak_early = float(np.max(rms[: max(1, early_frames)]))
                start_mid = min(len(rms), early_frames)
                end_mid = min(len(rms), start_mid + max(1, mid_frames))
                median_mid = float(np.median(rms[start_mid:end_mid])) if end_mid > start_mid else float(np.median(rms))
                features["transient_strength"] = peak_early / (median_mid + 1e-9)

            centroid = librosa.feature.spectral_centroid(S=S, sr=sr, hop_length=hop)[0]
            features["centroid_mean"] = float(np.mean(centroid)) if centroid.size > 0 else 0.0
            early_centroid_frames = int((0.10 * sr) / hop + 0.999)
            features["centroid_early"] = (
                float(np.mean(centroid[: max(1, early_centroid_frames)]))
                if centroid.size > 0
                else features["centroid_mean"]
            )

            zcr = librosa.feature.zero_crossing_rate(y_norm, frame_length=win, hop_length=hop)[0]
            features["zcr_mean"] = float(np.mean(zcr)) if zcr.size > 0 else 0.0

            flatness = librosa.feature.spectral_flatness(S=S)[0]
            features["flatness_mean"] = float(np.mean(flatness)) if flatness.size > 0 else 0.0

            # Pitch (yin)
            try:
                # librosa.yin needs enough samples for low fmin (avoid inaccurate pitch detection warning)
                win = max(int(win), 2207)

                f0 = librosa.yin(y_norm, fmin=20, fmax=2000, sr=sr, frame_length=win, hop_length=hop)
                valid = ~np.isnan(f0)
                voiced_ratio = float(np.sum(valid)) / float(len(f0)) if len(f0) > 0 else 0.0
                features["voiced_ratio"] = voiced_ratio
                if bool(np.any(valid)):
                    vf0 = f0[valid]
                    features["median_f0"] = float(np.median(vf0))
                    s = 12.0 * np.log2(vf0 / 55.0)
                    features["pitch_std"] = float(np.std(s)) if s.size > 0 else 0.0
                glide_info = self._detect_glide(f0, sr, win, hop)
                features.update(glide_info)
            except Exception:
                pass

        except Exception:
            # Keep zeroed features on failure
            pass

        self._feature_cache[key] = features
        return features

    def _detect_glide(self, f0, sr: int, win: int, hop: int) -> Dict[str, Any]:
        """Detect pitch glide (best-effort). Requires numpy and scipy."""
        result = {"glide_detected": False, "glide_confidence": 0.0, "glide_drop": 0.0}

        try:
            import numpy as np  # type: ignore
        except Exception:
            return result

        try:
            if f0 is None or len(f0) == 0:
                return result
            valid_mask = ~np.isnan(f0)
            total_frames = len(f0)
            voiced_count = int(np.sum(valid_mask))
            voiced_ratio = voiced_count / total_frames if total_frames > 0 else 0.0
            duration = (total_frames * hop) / float(sr)

            if duration < 0.35 or voiced_ratio < 0.35:
                return result

            voiced_f0 = f0[valid_mask]
            t = np.arange(len(voiced_f0)) * (hop / float(sr))
            s = 12.0 * np.log2(voiced_f0 / 55.0)

            try:
                from scipy.ndimage import median_filter  # type: ignore

                s_med = median_filter(s, size=5, mode="nearest")
            except Exception:
                s_med = s

            s_smooth = np.convolve(s_med, np.ones(5) / 5, mode="same")

            n = len(s_smooth)
            if n < 15:
                return result

            start_idx = int(n * 0.10)
            end_idx = int(n * 0.90)
            if end_idx <= start_idx:
                return result

            t_u = t[start_idx:end_idx]
            s_u = s_smooth[start_idx:end_idx]

            max_pts = 100
            if len(t_u) > max_pts:
                idxs = np.linspace(0, len(t_u) - 1, max_pts, dtype=int)
                t_sub = t_u[idxs]
                s_sub = s_u[idxs]
            else:
                t_sub = t_u
                s_sub = s_u

            slopes = []
            for i in range(len(t_sub)):
                for j in range(i + 1, len(t_sub)):
                    dt = t_sub[j] - t_sub[i]
                    if dt > 0:
                        slopes.append((s_sub[j] - s_sub[i]) / dt)
            if not slopes:
                return result

            m = float(np.median(slopes))
            q = len(s_u)
            q20 = int(q * 0.2)
            start_med = float(np.median(s_u[: max(1, q20)]))
            end_med = float(np.median(s_u[-max(1, q20) :]))
            drop_st = start_med - end_med

            b = float(np.median(s_u - m * t_u))
            residuals = s_u - (m * t_u + b)
            mad = float(np.median(np.abs(residuals)))

            if drop_st >= 1.0 and m <= -2.0 and mad <= 0.35:
                A = _clamp(drop_st / 6.0, 0.0, 1.0)
                B = _clamp((-m) / 10.0, 0.0, 1.0)
                C = _clamp((voiced_ratio - 0.35) / 0.35, 0.0, 1.0)
                D = _clamp((0.35 - mad) / 0.35, 0.0, 1.0)
                conf = 0.35 * A + 0.25 * B + 0.20 * C + 0.20 * D
                result["glide_detected"] = True
                result["glide_confidence"] = float(conf)
                result["glide_drop"] = float(drop_st)

            return result
        except Exception:
            return result

    def _compute_audio_scores(self, features: Dict[str, Any]) -> Dict[str, float]:
        scores: Dict[str, float] = {bucket: 0.0 for bucket in self.BUCKET_RULES.keys()}

        duration = float(features.get("duration", 0.0) or 0.0)
        low_ratio = float(features.get("low_freq_ratio", 0.0) or 0.0)
        centroid_mean = float(features.get("centroid_mean", 0.0) or 0.0)
        centroid_early = float(features.get("centroid_early", 0.0) or 0.0)
        transient = float(features.get("transient_strength", 0.0) or 0.0)
        zcr_mean = float(features.get("zcr_mean", 0.0) or 0.0)
        flatness_mean = float(features.get("flatness_mean", 0.0) or 0.0)
        voiced_ratio = float(features.get("voiced_ratio", 0.0) or 0.0)
        median_f0 = float(features.get("median_f0", 0.0) or 0.0)
        pitch_std = float(features.get("pitch_std", 0.0) or 0.0)
        glide_detected = bool(features.get("glide_detected", False))
        glide_confidence = float(features.get("glide_confidence", 0.0) or 0.0)

        is_kick_like = (
            duration < FEATURE_THRESHOLDS["kick_duration_max"] and transient > FEATURE_THRESHOLDS["transient_kick_min"]
        )

        # 808
        if not is_kick_like:
            if duration > FEATURE_THRESHOLDS["808_duration_min"]:
                scores["808s"] += AUDIO_WEIGHTS["duration"]
            if low_ratio > FEATURE_THRESHOLDS["lowfreq_ratio_808"]:
                scores["808s"] += AUDIO_WEIGHTS["lowfreq"]
            if centroid_mean < FEATURE_THRESHOLDS["centroid_low"]:
                scores["808s"] += AUDIO_WEIGHTS["centroid"]
            if zcr_mean < FEATURE_THRESHOLDS["zcr_tonal_max"]:
                scores["808s"] += AUDIO_WEIGHTS["zcr"]
            if 30 <= median_f0 <= 100:
                scores["808s"] += PITCH_WEIGHTS["median_f0_low"]
            if voiced_ratio >= 0.5:
                scores["808s"] += PITCH_WEIGHTS["voiced_ratio"]
            if glide_detected:
                scores["808s"] += PITCH_WEIGHTS["glide_bonus"] * glide_confidence
            if pitch_std < 0.2:
                scores["808s"] += PITCH_WEIGHTS["stability_bonus"]

        # Kicks
        if duration < FEATURE_THRESHOLDS["kick_duration_max"]:
            scores["Kicks"] += AUDIO_WEIGHTS["duration"]
        if transient > FEATURE_THRESHOLDS["transient_kick_min"]:
            scores["Kicks"] += AUDIO_WEIGHTS["transient"]
        if centroid_early > FEATURE_THRESHOLDS["centroid_bright"]:
            scores["Kicks"] += AUDIO_WEIGHTS["centroid"]
        if voiced_ratio < 0.25:
            scores["Kicks"] += AUDIO_WEIGHTS["zcr"] * 0.5
        if duration < FEATURE_THRESHOLDS["kick_duration_max"] and transient > FEATURE_THRESHOLDS["transient_kick_min"]:
            scores["Kicks"] += AUDIO_WEIGHTS["transient"] + AUDIO_WEIGHTS["duration"]

        # HiHats / Cymbals
        if centroid_mean > FEATURE_THRESHOLDS["centroid_bright"]:
            scores["HiHats"] += AUDIO_WEIGHTS["centroid"]
            scores["Cymbals"] += AUDIO_WEIGHTS["centroid"]
        if low_ratio < FEATURE_THRESHOLDS["lowfreq_ratio_hat_max"]:
            scores["HiHats"] += AUDIO_WEIGHTS["lowfreq"]
            scores["Cymbals"] += AUDIO_WEIGHTS["lowfreq"]
        if duration < 0.40:
            scores["HiHats"] += AUDIO_WEIGHTS["duration"]
            scores["Cymbals"] += AUDIO_WEIGHTS["duration"]
        if flatness_mean > FEATURE_THRESHOLDS["flatness_high"]:
            scores["HiHats"] += AUDIO_WEIGHTS["flatness"]
            scores["Cymbals"] += AUDIO_WEIGHTS["flatness"]

        # Snares / Claps
        if flatness_mean > 0.2 and zcr_mean > FEATURE_THRESHOLDS["zcr_high"]:
            scores["Snares"] += AUDIO_WEIGHTS["flatness"]
            scores["Claps"] += AUDIO_WEIGHTS["flatness"]
        if transient > 2.0:
            scores["Snares"] += AUDIO_WEIGHTS["transient"] * 0.5
            scores["Claps"] += AUDIO_WEIGHTS["transient"] * 0.5
        if FEATURE_THRESHOLDS["centroid_moderate_low"] <= centroid_mean <= FEATURE_THRESHOLDS["centroid_moderate_high"]:
            scores["Snares"] += AUDIO_WEIGHTS["centroid"] * 0.5
            scores["Claps"] += AUDIO_WEIGHTS["centroid"] * 0.5

        # Percs
        if transient > 1.5 and duration < 0.5:
            scores["Percs"] += AUDIO_WEIGHTS["transient"]

        # Vox (rough)
        if voiced_ratio > 0.75 and centroid_mean < 2000:
            scores["Vox"] += AUDIO_WEIGHTS["duration"]

        # FX (rough)
        if flatness_mean > 0.5 and duration > 0.5:
            scores["FX"] += AUDIO_WEIGHTS["flatness"]

        return scores

    # ------------------------------------------------------------------
    # Classification
    def _classify_filename(self, filename: str) -> ClassificationResult:
        lower_name = filename.lower()
        scores: Dict[str, int] = {}
        for bucket, patterns in self.BUCKET_RULES.items():
            score = 0
            for pat in patterns:
                if pat == ".mid" and lower_name.endswith(".mid"):
                    score += 3
                else:
                    score += lower_name.count(pat.lower())
            if score > 0:
                scores[bucket] = score

        if not scores:
            return (None, "UNSORTED", 0.0, [])

        sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        total = sum(scores.values())
        best_bucket, best_score = sorted_scores[0]
        confidence = best_score / total if total > 0 else 0.0
        candidates = sorted_scores[:3]

        if confidence >= self.confidence_threshold:
            return (best_bucket, self.CATEGORY_MAP.get(best_bucket, "Samples"), confidence, candidates)

        return (None, "UNSORTED", confidence, candidates)

    def _classify_file(
        self, file_path: Path
    ) -> Tuple[Optional[str], str, float, List[Tuple[str, float]], bool, Dict[str, Any]]:
        """Return (bucket, category, confidence_ratio, top3_candidates, low_confidence, reason_dict)."""
        reason: Dict[str, Any] = {
            "folder_matches": [],
            "filename_matches": [],
            "audio_summary": {},
            "pitch_summary": {},
            "glide_summary": {},
        }

        if self._should_ignore(file_path.name):
            return (None, "UNSORTED", 0.0, [], False, reason)

        suffix = file_path.suffix.lower()

        # Producer OS safety default: only process WAV
        if suffix != ".wav":
            return (None, "UNSORTED", 0.0, [], False, reason)

        folder_scores = self._get_folder_hint_scores(file_path)
        filename_scores = self._get_filename_hint_scores(file_path.name)
        features = self._extract_features(file_path)
        audio_scores = self._compute_audio_scores(features)

        final_scores: Dict[str, float] = {}
        for bucket in self.BUCKET_RULES.keys():
            final_scores[bucket] = (
                float(folder_scores.get(bucket, 0))
                + float(filename_scores.get(bucket, 0))
                + float(audio_scores.get(bucket, 0))
            )

        positive = {b: s for b, s in final_scores.items() if s > 0}
        if not positive:
            reason["folder_matches"] = [(b, s) for b, s in folder_scores.items() if s > 0]
            reason["filename_matches"] = [(b, s) for b, s in filename_scores.items() if s > 0]
            reason["audio_summary"] = {
                k: features.get(k)
                for k in (
                    "duration",
                    "low_freq_ratio",
                    "transient_strength",
                    "centroid_mean",
                    "centroid_early",
                    "zcr_mean",
                    "flatness_mean",
                )
            }
            return (None, "UNSORTED", 0.0, [], False, reason)

        sorted_scores = sorted(positive.items(), key=lambda kv: kv[1], reverse=True)
        best_bucket, best_score = sorted_scores[0]
        top3 = sorted_scores[:3]
        sum_top3 = sum(score for _, score in top3)
        confidence_ratio = (best_score / sum_top3) if sum_top3 > 0 else 1.0
        low_confidence = confidence_ratio < LOW_CONFIDENCE_THRESHOLD
        category = self.CATEGORY_MAP.get(best_bucket, "Samples")

        reason["folder_matches"] = [(b, s) for b, s in folder_scores.items() if s > 0]
        reason["filename_matches"] = [(b, s) for b, s in filename_scores.items() if s > 0]
        reason["audio_summary"] = {
            "duration": features.get("duration"),
            "low_freq_ratio": features.get("low_freq_ratio"),
            "transient_strength": features.get("transient_strength"),
            "centroid_mean": features.get("centroid_mean"),
            "centroid_early": features.get("centroid_early"),
            "zcr_mean": features.get("zcr_mean"),
            "flatness_mean": features.get("flatness_mean"),
        }
        reason["pitch_summary"] = {
            "median_f0": features.get("median_f0"),
            "voiced_ratio": features.get("voiced_ratio"),
            "pitch_std": features.get("pitch_std"),
        }
        reason["glide_summary"] = {
            "glide_detected": features.get("glide_detected"),
            "glide_confidence": features.get("glide_confidence"),
            "glide_drop": features.get("glide_drop"),
        }

        return (best_bucket, category, float(confidence_ratio), top3, bool(low_confidence), reason)

    # ------------------------------------------------------------------
    # Hub structure + styling (sidecar nfo placement)
    def _ensure_hub_structure(self, category: str, bucket: str, pack_name: str) -> Tuple[Path, Path, Path]:
        """Return (category_dir, bucket_dir, pack_dir). Create/write styles in modifying modes only."""
        category_dir = self.hub_dir / category
        display_bucket = self.bucket_service.get_display_name(bucket)
        bucket_dir = category_dir / display_bucket
        pack_dir = bucket_dir / pack_name

        if self.current_mode not in {"copy", "move", "repair-styles"}:
            return category_dir, bucket_dir, pack_dir

        pack_dir.mkdir(parents=True, exist_ok=True)

        # Category .nfo lives in hub root (next to category folder)
        category_style = self.style_service.resolve_style(category, category)
        self.style_service.write_nfo(self.hub_dir, category, category_style)

        # Bucket .nfo lives in category folder (next to bucket folder)
        bucket_style = self.style_service.resolve_style(bucket, category)
        self.style_service.write_nfo(category_dir, display_bucket, bucket_style)

        # Pack .nfo lives in bucket folder (next to pack folder)
        pack_style = self.style_service.pack_style_from_bucket(bucket_style)
        self.style_service.write_nfo(bucket_dir, pack_name, pack_style)

        return category_dir, bucket_dir, pack_dir

    def _ensure_unsorted_structure(self, pack_name: str) -> Path:
        unsorted_dir = self.hub_dir / "UNSORTED" / pack_name

        if self.current_mode not in {"copy", "move", "repair-styles"}:
            return unsorted_dir

        unsorted_dir.mkdir(parents=True, exist_ok=True)
        # UNSORTED category .nfo (hub root)
        self.style_service.write_nfo(self.hub_dir, "UNSORTED", DEFAULT_UNSORTED_STYLE)
        # Pack .nfo inside UNSORTED
        self.style_service.write_nfo(self.hub_dir / "UNSORTED", pack_name, DEFAULT_UNSORTED_STYLE)
        return unsorted_dir

    # ------------------------------------------------------------------
    # Move/copy and logging
    def _move_or_copy(self, src: Path, dst: Path, mode: str) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if mode == "move":
            shutil.move(str(src), str(dst))
        elif mode == "copy":
            shutil.copy2(str(src), str(dst))

    def run(
        self,
        mode: str = "analyze",
        overwrite_nfo: bool = False,  # reserved for future; kept for UI compatibility
        normalize_pack_name: bool = False,  # reserved for future
        developer_options: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        """Execute a run.

        Hard rules (tests):
        - analyze: MUST NOT modify hub OR inbox (no logs, no cache write, no .nfo, no wrapping)
        - dry-run: may write logs/reports, but MUST NOT copy/move and MUST NOT write .nfo
        - copy/move: may copy/move, may write .nfo, may write cache/logs
        - repair-styles: may regenerate .nfo and must remove orphan .nfo

        Returns a report dict each run.
        """
        mode = (mode or "analyze").lower().strip()
        self.current_mode = mode

        write_hub = mode in {"copy", "move", "repair-styles"}  # .nfo + cache allowed
        write_logs = mode in {"dry-run", "copy", "move", "repair-styles"}  # analyze must not log
        do_transfer = mode in {"copy", "move"}

        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        report: Dict[str, Any] = {
            "run_id": run_id,
            "mode": mode,
            "timestamp": datetime.datetime.now().isoformat(),
            "files_processed": 0,
            "files_moved": 0,
            "files_copied": 0,
            "skipped_existing": 0,
            "failed": 0,
            "unsorted": 0,
            "packs": [],
        }

        # ANALYZE: absolutely no filesystem writes
        if mode == "analyze":
            packs = self._discover_packs()
            for pack_dir in packs:
                pack_report: PackReport = {
                    "pack": pack_dir.name,
                    "files": [],
                }

                for root, dirs, files in os.walk(pack_dir):
                    dirs[:] = [d for d in dirs if not self._should_ignore(d)]
                    files = [f for f in files if not self._should_ignore(f)]

                    for fname in files:
                        file_path = Path(root) / fname
                        if not file_path.is_file():
                            continue
                        if file_path.suffix.lower() != ".wav":
                            continue

                        bucket, category, confidence, candidates, low_confidence, _reason_dict = self._classify_file(
                            file_path
                        )
                        rel_path = file_path.relative_to(pack_dir)

                        if bucket is None:
                            dest_path = self.hub_dir / "UNSORTED" / pack_dir.name / rel_path
                            report["unsorted"] += 1
                            reason = "no matches"
                            if candidates:
                                reason = "; ".join([f"{b}:{int(s)}" for b, s in candidates])
                        else:
                            display_bucket = self.bucket_service.get_display_name(bucket)
                            dest_path = self.hub_dir / category / display_bucket / pack_dir.name / rel_path
                            reason = f"best match: {bucket}, confidence={confidence:.2f}"
                            if low_confidence:
                                reason += "; low confidence"
                            if candidates:
                                reason += "; candidates: " + ", ".join([f"{b}:{int(s)}" for b, s in candidates])

                        pack_report["files"].append(
                            {
                                "source": str(file_path),
                                "dest": str(dest_path),
                                "bucket": bucket or "UNSORTED",
                                "category": category,
                                "confidence": confidence,
                                "action": "NONE",
                                "reason": reason,
                            }
                        )
                        report["files_processed"] += 1

                report["packs"].append(pack_report)

            return report

        # For modes other than analyze, we can write logs/reports
        log_dir: Optional[Path] = None
        audit_path: Optional[Path] = None
        run_log_path: Optional[Path] = None
        report_path: Optional[Path] = None

        if write_logs:
            log_dir = self.hub_dir / "logs" / run_id
            log_dir.mkdir(parents=True, exist_ok=True)
            audit_path = log_dir / "audit.csv"
            run_log_path = log_dir / "run_log.txt"
            report_path = log_dir / "run_report.json"

        # Only wrap loose inbox files in modes allowed to touch inbox
        if mode in {"dry-run", "copy", "move"}:
            self._wrap_loose_files()

        if mode == "repair-styles":
            actions = self.repair_styles()
            report["repair_actions"] = actions
            if write_logs and report_path:
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            if write_hub:
                self._save_feature_cache()
            return report

        packs = self._discover_packs()

        audit_file = None
        audit_writer = None
        log_handle = open(run_log_path, "w", encoding="utf-8", buffering=1) if write_logs and run_log_path else None

        def _log(msg: str) -> None:
            # Always show in console
            print(msg)
            # Also write to run_log.txt if enabled
            if log_handle:
                log_handle.write(msg + "\n")
                log_handle.flush()

        _log(f"Producer OS run_id={run_id} mode={mode}")
        _log(f"Hub: {self.hub_dir}")
        _log(f"Packs discovered: {len(packs)}")

        try:
            if mode == "move" and audit_path:
                audit_file = open(audit_path, "w", newline="", encoding="utf-8")
                audit_writer = csv.writer(audit_file)
                audit_writer.writerow(["file", "pack", "category", "bucket", "confidence", "action", "reason"])

            for pack_dir in packs:
                transfer_pack_report: PackReport = {
                    "pack": pack_dir.name,
                    "files": [],
                }

                _log(f"Processing pack: {pack_dir.name}")

                for root, dirs, files in os.walk(pack_dir):
                    dirs[:] = [d for d in dirs if not self._should_ignore(d)]
                    files = [f for f in files if not self._should_ignore(f)]

                    for fname in files:
                        file_path = Path(root) / fname
                        if not file_path.is_file():
                            continue
                        if file_path.suffix.lower() != ".wav":
                            continue

                        rel_path = file_path.relative_to(pack_dir)
                        bucket, category, confidence, candidates, low_confidence, _reason_dict = self._classify_file(
                            file_path
                        )

                        if bucket is None:
                            dest_dir = (
                                self._ensure_unsorted_structure(pack_dir.name)
                                if write_hub
                                else (self.hub_dir / "UNSORTED" / pack_dir.name)
                            )
                            dest_path = dest_dir / rel_path
                            report["unsorted"] += 1
                            reason = "no matches"
                            if candidates:
                                reason = "; ".join([f"{b}:{int(s)}" for b, s in candidates])
                        else:
                            display_bucket = self.bucket_service.get_display_name(bucket)
                            if write_hub:
                                _, _, pack_dest_dir = self._ensure_hub_structure(category, bucket, pack_dir.name)
                            else:
                                pack_dest_dir = self.hub_dir / category / display_bucket / pack_dir.name
                            dest_path = pack_dest_dir / rel_path

                            reason = f"best match: {bucket}, confidence={confidence:.2f}"
                            if low_confidence:
                                reason += "; low confidence"
                            if candidates:
                                reason += "; candidates: " + ", ".join([f"{b}:{int(s)}" for b, s in candidates])

                        action = "NONE"
                        if do_transfer:
                            try:
                                if dest_path.exists():
                                    action = "SKIPPED"
                                    report["skipped_existing"] += 1
                                    reason += "; destination exists"
                                else:
                                    self._move_or_copy(file_path, dest_path, mode)
                                    action = mode.upper()
                                    if mode == "move":
                                        report["files_moved"] += 1
                                    else:
                                        report["files_copied"] += 1
                            except Exception as e:
                                action = "FAILED"
                                report["failed"] += 1
                                reason += f"; move/copy failed: {e}"

                        transfer_pack_report["files"].append(
                            {
                                "source": str(file_path),
                                "dest": str(dest_path),
                                "bucket": bucket or "UNSORTED",
                                "category": category,
                                "confidence": confidence,
                                "action": action,
                                "reason": reason,
                            }
                        )
                        report["files_processed"] += 1

                        if audit_writer:
                            audit_writer.writerow(
                                [
                                    str(file_path),
                                    pack_dir.name,
                                    category,
                                    bucket or "UNSORTED",
                                    f"{confidence:.2f}",
                                    action,
                                    reason,
                                ]
                            )

                _log(f"Finished pack: {pack_dir.name} files={len(transfer_pack_report['files'])}")

                report["packs"].append(transfer_pack_report)

            _log(
                f"Done. processed={report['files_processed']} "
                f"copied={report['files_copied']} moved={report['files_moved']} "
                f"failed={report['failed']} unsorted={report['unsorted']} "
                f"skipped={report['skipped_existing']}"
            )

        finally:
            if audit_file:
                audit_file.close()
            if log_handle:
                log_handle.close()
        if write_logs and report_path:
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        if write_hub:
            self._save_feature_cache()

        return report

    def undo_last_run(self) -> Dict[str, Any]:
        """Undo the most recent MOVE run using its audit.csv.

        Returns:
          - reverted_count: int
          - conflicts: list[dict]
        """
        logs_root = self.hub_dir / "logs"
        if not logs_root.exists():
            return {"reverted_count": 0, "conflicts": [], "error": "No logs found"}

        audit_files = sorted(logs_root.rglob("audit.csv"), key=os.path.getmtime, reverse=True)
        if not audit_files:
            return {"reverted_count": 0, "conflicts": [], "error": "No audit files found"}

        audit_path = audit_files[0]

        restored = 0
        conflicts: List[Dict[str, str]] = []

        quarantine_dir = self.hub_dir / "Quarantine" / "UndoConflicts"
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        with open(audit_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    if str(row.get("action", "")).upper() != "MOVE":
                        continue

                    src_path = Path(row["file"])
                    category = row.get("category", "UNSORTED")
                    bucket = row.get("bucket", "UNSORTED")
                    pack = row.get("pack", "")

                    # where it is now (hub)
                    if bucket != "UNSORTED":
                        display_bucket = self.bucket_service.get_display_name(bucket)
                        current_location = self.hub_dir / category / display_bucket / pack / src_path.name
                    else:
                        current_location = self.hub_dir / "UNSORTED" / pack / src_path.name

                    if not current_location.exists():
                        continue

                    dest_in_inbox = self.inbox_dir / src_path.name
                    if dest_in_inbox.exists():
                        qdest = quarantine_dir / src_path.name
                        shutil.move(str(current_location), str(qdest))
                        conflicts.append({"file": src_path.name, "quarantined_to": str(qdest)})
                    else:
                        shutil.move(str(current_location), str(dest_in_inbox))
                        restored += 1
                except Exception:
                    continue

        return {
            "reverted_count": restored,
            "conflicts": conflicts,
            "audit_file": str(audit_path),
        }

    def repair_styles(self) -> Dict[str, Any]:
        """Repair and regenerate missing/misplaced .nfo files.

        Hard rules (tests):
        - Must remove orphan .nfo files (no corresponding folder)
        - Must ignore hub internal folders like logs
        """
        actions = {"created": 0, "updated": 0, "removed": 0}

        # Build desired nfo set for categories/buckets/packs
        desired_nfos: set[Path] = set()

        for category_dir in self.hub_dir.iterdir():
            if not category_dir.is_dir():
                continue
            if self._should_ignore(category_dir.name):
                continue
            if category_dir.name.lower() == "logs":
                continue

            category = category_dir.name
            desired_nfos.add(self.hub_dir / f"{category}.nfo")

            for bucket_dir in category_dir.iterdir():
                if not bucket_dir.is_dir() or self._should_ignore(bucket_dir.name):
                    continue
                display_bucket = bucket_dir.name
                desired_nfos.add(category_dir / f"{display_bucket}.nfo")

                for pack_dir in bucket_dir.iterdir():
                    if not pack_dir.is_dir() or self._should_ignore(pack_dir.name):
                        continue
                    desired_nfos.add(bucket_dir / f"{pack_dir.name}.nfo")

        # Create/update desired nfos
        for nfo_path in desired_nfos:
            folder_name = nfo_path.stem
            parent_dir = nfo_path.parent

            if parent_dir == self.hub_dir:
                category = folder_name
                if category.upper() == "UNSORTED":
                    style = DEFAULT_UNSORTED_STYLE
                else:
                    style = self.style_service.resolve_style(category, category)
            else:
                grandparent = parent_dir.parent
                if grandparent == self.hub_dir:
                    category = parent_dir.name
                    display_bucket = folder_name
                    bucket_id = self.bucket_service.get_bucket_id(display_bucket) or display_bucket
                    style = self.style_service.resolve_style(bucket_id, category)
                else:
                    category = grandparent.name
                    display_bucket = parent_dir.name
                    bucket_id = self.bucket_service.get_bucket_id(display_bucket) or display_bucket
                    style = self.style_service.pack_style_from_bucket(
                        self.style_service.resolve_style(bucket_id, category)
                    )

            new_contents = self.style_service._nfo_contents(style)

            if nfo_path.exists():
                try:
                    old = nfo_path.read_text(encoding="utf-8").strip()
                except Exception:
                    old = ""
                if old != new_contents.strip():
                    self.style_service.write_nfo(parent_dir, folder_name, style)
                    actions["updated"] += 1
            else:
                self.style_service.write_nfo(parent_dir, folder_name, style)
                actions["created"] += 1

        # Remove orphan nfos (no matching folder next to them)
        for nfo in self.hub_dir.rglob("*.nfo"):
            # ignore anything under logs
            if "logs" in [p.lower() for p in nfo.parts]:
                continue

            if nfo.parent == self.hub_dir:
                expected_folder = self.hub_dir / nfo.stem
            else:
                expected_folder = nfo.parent / nfo.stem

            if not expected_folder.exists():
                try:
                    nfo.unlink()
                    actions["removed"] += 1
                except Exception:
                    pass

        return actions
