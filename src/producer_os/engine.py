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
import re
import shutil
import time
import threading
import uuid
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypedDict, TypeAlias

from .bucket_service import BucketService
from .styles_service import StyleService
from . import tuning


class PackFileEntry(TypedDict, total=False):
    source: str
    dest: str
    bucket: str
    chosen_bucket: str
    category: str
    confidence: float
    action: str
    reason: str
    confidence_ratio: float
    confidence_margin: float
    low_confidence: bool
    top_candidates: list[dict[str, Any]]
    top_3_candidates: list[dict[str, Any]]
    folder_matches: list[dict[str, Any]]
    filename_matches: list[dict[str, Any]]
    audio_summary: dict[str, Any]
    pitch_summary: dict[str, Any]
    glide_summary: dict[str, Any]


class PackReport(TypedDict, total=False):
    pack: str
    files: list[PackFileEntry]
    counts: dict[str, int]
    warnings: list[str]
    errors: list[str]


def _clamp(val: float, min_val: float, max_val: float) -> float:
    """Clamp val between min_val and max_val."""
    return max(min_val, min(max_val, val))


_HINT_SPLIT_RE = re.compile(r"[ _-]+")


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
    confidence_threshold: float = tuning.LOW_CONFIDENCE_THRESHOLD

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
    _audio_backend: Optional[Dict[str, Any]] = field(init=False, default=None)
    _audio_backend_checked: bool = field(init=False, default=False)
    _fft_low_mask_cache: Dict[Tuple[int, int, float], Tuple[Any, Any]] = field(init=False, default_factory=dict)
    _tuning_loaded: bool = field(init=False, default=False)
    _user_bucket_hints: Dict[str, Dict[str, List[str]]] = field(
        init=False,
        default_factory=lambda: {"folder_keywords": {}, "filename_keywords": {}},
    )
    _feature_cache_stats: Dict[str, Any] = field(init=False, default_factory=dict)
    _feature_cache_lock: Any = field(init=False, default_factory=threading.Lock, repr=False)
    current_mode: str = field(init=False, default="analyze")

    def __post_init__(self) -> None:
        self.inbox_dir = Path(self.inbox_dir)
        self.hub_dir = Path(self.hub_dir)
        self._reset_feature_cache_stats()
        self._load_tuning_overrides()
        self._load_user_bucket_hints()
        self._load_feature_cache()

    # ------------------------------------------------------------------
    # Tuning overrides and feature caching
    def _reset_feature_cache_stats(self) -> None:
        self._feature_cache_stats = {
            "hits": 0,
            "misses": 0,
            "reused": 0,
            "computed": 0,
            "saved_entries": 0,
            "persisted": False,
        }

    def _feature_cache_stats_snapshot(self) -> Dict[str, Any]:
        return {
            "hits": int(self._feature_cache_stats.get("hits", 0) or 0),
            "misses": int(self._feature_cache_stats.get("misses", 0) or 0),
            "reused": int(self._feature_cache_stats.get("reused", 0) or 0),
            "computed": int(self._feature_cache_stats.get("computed", 0) or 0),
            "saved_entries": int(self._feature_cache_stats.get("saved_entries", 0) or 0),
            "persisted": bool(self._feature_cache_stats.get("persisted", False)),
        }

    def _load_tuning_overrides(self) -> None:
        """Load overrides from tuning.json (config dir first, then hub dir)."""
        if self._tuning_loaded:
            return

        tuning_paths: List[Path] = []

        if isinstance(self.config, dict):
            config_dir = self.config.get("config_dir") or self.config.get("config_path")
            if config_dir:
                config_dir_path = Path(config_dir)
                tuning_paths.append(config_dir_path / "tuning.json")
            tuning_path = self.config.get("tuning_path")
            if tuning_path:
                tuning_path_obj = Path(tuning_path)
                tuning_paths.append(
                    tuning_path_obj if tuning_path_obj.suffix.lower() == ".json" else (tuning_path_obj / "tuning.json")
                )

        tuning_paths.append(self.hub_dir / "config" / "tuning.json")
        tuning_paths.append(self.hub_dir / "tuning.json")

        for path in tuning_paths:
            try:
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        tuning.apply_overrides(data)
                    self._tuning_loaded = True
                    return
            except Exception:
                continue

    def _load_user_bucket_hints(self) -> None:
        """Load additive user hint keywords from config/hub locations (best-effort)."""
        self._user_bucket_hints = {"folder_keywords": {}, "filename_keywords": {}}
        candidates: List[Path] = []
        cfg = self.config if isinstance(self.config, dict) else {}

        config_dir_value = cfg.get("config_dir")
        if config_dir_value:
            candidates.append(Path(str(config_dir_value)) / "bucket_hints.json")
        hints_path_value = cfg.get("bucket_hints_path")
        if hints_path_value:
            p = Path(str(hints_path_value))
            candidates.append(p if p.suffix.lower() == ".json" else (p / "bucket_hints.json"))
        inline_hints = cfg.get("bucket_hints")
        if isinstance(inline_hints, dict):
            self._user_bucket_hints = self._normalize_bucket_hints(inline_hints)
            return

        candidates.append(self.hub_dir / "config" / "bucket_hints.json")
        candidates.append(self.hub_dir / "bucket_hints.json")

        for path in candidates:
            try:
                if not path.exists():
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    self._user_bucket_hints = self._normalize_bucket_hints(payload)
                    return
            except Exception:
                continue

    def _normalize_bucket_hints(self, payload: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
        """Normalize additive bucket hints and ignore unknown bucket IDs."""
        normalized: Dict[str, Dict[str, List[str]]] = {
            "folder_keywords": {},
            "filename_keywords": {},
        }
        for kind in ("folder_keywords", "filename_keywords"):
            raw_map = payload.get(kind, {})
            if not isinstance(raw_map, dict):
                continue
            for bucket, values in raw_map.items():
                if bucket not in self.BUCKET_RULES:
                    continue
                if not isinstance(values, list):
                    continue
                seen: set[str] = set()
                out: List[str] = []
                for value in values:
                    if not isinstance(value, str):
                        continue
                    token = value.strip().lower()
                    if not token or token in seen:
                        continue
                    seen.add(token)
                    out.append(token)
                if out:
                    normalized[kind][bucket] = out
        return normalized

    def _iter_bucket_patterns(self, bucket: str, kind: str) -> List[str]:
        """Return canonical + additive user patterns for a bucket/kind."""
        base = list(self.BUCKET_RULES.get(bucket, []))
        hint_map = self._user_bucket_hints.get(kind, {}) if isinstance(self._user_bucket_hints, dict) else {}
        extras = hint_map.get(bucket, []) if isinstance(hint_map, dict) else []
        if not extras:
            return base
        merged: List[str] = []
        seen: set[str] = set()
        for pat in [*base, *extras]:
            p = str(pat).strip()
            if not p:
                continue
            key = p.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(p)
        return merged

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
            with self._feature_cache_lock:
                cache_snapshot = dict(self._feature_cache)
            cache_path.write_text(json.dumps(cache_snapshot), encoding="utf-8")
            self._feature_cache_stats["saved_entries"] = int(len(cache_snapshot))
            self._feature_cache_stats["persisted"] = True
        except Exception:
            pass

    def _get_audio_backend(self) -> Optional[Dict[str, Any]]:
        """Import and cache optional audio backend modules/functions once per engine."""
        if self._audio_backend_checked:
            return self._audio_backend

        self._audio_backend_checked = True
        try:
            import librosa  # type: ignore
            import numpy as np  # type: ignore
            import soundfile as sf  # type: ignore
        except Exception:
            self._audio_backend = None
            return None

        # Cache concrete function refs to avoid repeated lazy-loader lookups in hot paths.
        feature_mod = librosa.feature
        self._audio_backend = {
            "np": np,
            "sf": sf,
            "stft": librosa.stft,
            "fft_frequencies": librosa.fft_frequencies,
            "yin": librosa.yin,
            "rms": feature_mod.rms,
            "zero_crossing_rate": feature_mod.zero_crossing_rate,
        }
        return self._audio_backend

    def _get_fft_low_mask(self, sr: int, win: int) -> Tuple[Any, Any]:
        """Return cached FFT frequency bins and low-frequency mask for a sample rate/window."""
        cutoff = float(tuning.ANALYSIS_PARAMS["low_freq_cutoff_hz"])
        key = (int(sr), int(win), cutoff)
        cached = self._fft_low_mask_cache.get(key)
        if cached is not None:
            return cached

        backend = self._get_audio_backend()
        if backend is None:
            raise RuntimeError("Audio backend unavailable")
        fft_frequencies = backend["fft_frequencies"]
        freqs = fft_frequencies(sr=sr, n_fft=win)
        low_mask = freqs < cutoff
        self._fft_low_mask_cache[key] = (freqs, low_mask)
        return freqs, low_mask

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

    def _collect_pack_wavs(self, pack_dir: Path) -> Tuple[List[Tuple[Path, Path]], int]:
        """Collect WAV files for a pack in deterministic order and count skipped non-WAV files."""
        wavs: List[Tuple[Path, Path]] = []
        skipped_non_wav = 0
        for root, dirs, files in os.walk(pack_dir):
            dirs[:] = sorted([d for d in dirs if not self._should_ignore(d)])
            files = sorted([f for f in files if not self._should_ignore(f)])
            for fname in files:
                file_path = Path(root) / fname
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() != ".wav":
                    skipped_non_wav += 1
                    continue
                wavs.append((file_path, file_path.relative_to(pack_dir)))
        return wavs, skipped_non_wav

    def _classify_files_batch(
        self,
        file_paths: List[Path],
        workers: int = 1,
    ) -> List[Tuple[Optional[str], str, float, List[Tuple[str, float]], bool, Dict[str, Any]]]:
        """Classify a batch of files, preserving input order. Parallel when enabled and requested."""
        worker_count = max(1, int(workers))
        if (
            worker_count <= 1
            or not bool(getattr(tuning, "PARALLEL_EXTRACTION_ENABLED", False))
            or len(file_paths) <= 1
        ):
            return [self._classify_file(p) for p in file_paths]

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            return list(executor.map(self._classify_file, file_paths))

    # ------------------------------------------------------------------
    # Scoring helpers
    def _hint_tokens(self, text: str) -> List[str]:
        return [tok for tok in _HINT_SPLIT_RE.split((text or "").lower()) if tok]

    def _pattern_matches_text(self, pattern: str, raw_text_lower: str, tokens: List[str]) -> bool:
        if not pattern or pattern == ".mid":
            return False
        pat_lower = pattern.lower()
        pat_tokens = self._hint_tokens(pat_lower)
        if not pat_tokens:
            return False

        token_set = set(tokens)
        normalized_text = " ".join(tokens)
        compact_text = "".join(tokens)
        pat_norm = " ".join(pat_tokens)
        pat_compact = "".join(pat_tokens)

        if len(pat_tokens) == 1 and pat_tokens[0] in token_set:
            return True
        if pat_norm and pat_norm in normalized_text:
            return True
        if pat_compact and pat_compact in compact_text:
            return True
        # Fallback for partial patterns like "melod" and names like "808sPack".
        return pat_lower in raw_text_lower

    def _get_folder_hint_details(self, file_path: Path) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
        scores: Dict[str, int] = {bucket: 0 for bucket in self.BUCKET_RULES.keys()}
        matches: List[Dict[str, Any]] = []
        parts = list(file_path.parent.parts)[-tuning.PARENT_FOLDER_LEVELS_TO_SCAN:]
        for part in parts:
            part_lower = part.lower()
            tokens = self._hint_tokens(part)
            for bucket, patterns in self.BUCKET_RULES.items():
                for pat in self._iter_bucket_patterns(bucket, "folder_keywords"):
                    if self._pattern_matches_text(pat, part_lower, tokens):
                        previous = scores[bucket]
                        scores[bucket] = min(scores[bucket] + tuning.FOLDER_HINT_WEIGHT, tuning.FOLDER_HINT_CAP)
                        if scores[bucket] > previous:
                            is_user_hint = pat.lower() not in {p.lower() for p in patterns}
                            matches.append(
                                {
                                    "bucket": bucket,
                                    "keyword": pat,
                                    "source": "user_hint" if is_user_hint else "default_rule",
                                    "folder": part,
                                    "added": scores[bucket] - previous,
                                    "score_after": scores[bucket],
                                }
                            )
        return scores, matches

    def _get_folder_hint_scores(self, file_path: Path) -> Dict[str, int]:
        scores, _matches = self._get_folder_hint_details(file_path)
        return scores

    def _get_filename_hint_details(self, filename: str) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
        scores: Dict[str, int] = {bucket: 0 for bucket in self.BUCKET_RULES.keys()}
        matches: List[Dict[str, Any]] = []
        lower_name = filename.lower()
        tokens = self._hint_tokens(Path(filename).stem)
        for bucket, patterns in self.BUCKET_RULES.items():
            for pat in self._iter_bucket_patterns(bucket, "filename_keywords"):
                if pat == ".mid":
                    continue
                if self._pattern_matches_text(pat, lower_name, tokens):
                    previous = scores[bucket]
                    scores[bucket] = min(scores[bucket] + tuning.FILENAME_HINT_WEIGHT, tuning.FILENAME_HINT_CAP)
                    if scores[bucket] > previous:
                        is_user_hint = pat.lower() not in {p.lower() for p in patterns}
                        matches.append(
                            {
                                "bucket": bucket,
                                "keyword": pat,
                                "source": "user_hint" if is_user_hint else "default_rule",
                                "filename": filename,
                                "added": scores[bucket] - previous,
                                "score_after": scores[bucket],
                            }
                        )
        return scores, matches

    def _get_filename_hint_scores(self, filename: str) -> Dict[str, int]:
        scores, _matches = self._get_filename_hint_details(filename)
        return scores

    def _pitch_skip_reason(self, features: Dict[str, Any]) -> str:
        """Return a deterministic pitch skip reason for clearly percussive samples, else ''."""
        if not bool(getattr(tuning, "PITCH_GATING_ENABLED", False)):
            return ""
        thr = getattr(tuning, "PITCH_GATING_THRESHOLDS", {}) or {}
        duration = float(features.get("duration", 0.0) or 0.0)
        transient = float(features.get("transient_strength", 0.0) or 0.0)
        centroid_mean = float(features.get("centroid_mean", 0.0) or 0.0)
        low_ratio = float(features.get("low_freq_ratio", 0.0) or 0.0)
        zcr_mean = float(features.get("zcr_mean", 0.0) or 0.0)
        flatness_mean = float(features.get("flatness_mean", 0.0) or 0.0)
        rms_global = float(features.get("rms_global", 0.0) or 0.0)

        if rms_global <= 0.0:
            return "silence_or_zero_signal"

        hat_like = (
            duration <= float(thr.get("hat_duration_max", 0.22))
            and centroid_mean >= float(thr.get("hat_centroid_min", 5000.0))
            and low_ratio <= float(thr.get("hat_lowfreq_max", 0.12))
            and flatness_mean >= float(thr.get("hat_flatness_min", 0.40))
            and zcr_mean >= float(thr.get("hat_zcr_min", 0.12))
        )
        if hat_like:
            return "hat_like_percussive"

        kick_like = (
            duration <= float(thr.get("kick_duration_max", 0.22))
            and transient >= float(thr.get("kick_transient_min", 4.5))
            and centroid_mean >= float(thr.get("kick_centroid_min", 800.0))
        )
        if kick_like:
            return "kick_like_percussive"

        return ""

    # ------------------------------------------------------------------
    # Audio feature extraction (optional dependencies)
    def _extract_features(self, file_path: Path) -> Dict[str, Any]:
        """Extract audio features from a WAV file (best-effort).

        Uses a persistent cache keyed by file path + size + mtime.
        If optional audio libs aren't installed, returns zeroed features.
        """
        try:
            stat = file_path.stat()
            key = f"{file_path.resolve()}|{stat.st_size}|{stat.st_mtime}"
        except Exception:
            key = str(file_path.resolve() if isinstance(file_path, Path) else file_path)

        with self._feature_cache_lock:
            if key in self._feature_cache:
                cached = self._feature_cache.get(key)
                if isinstance(cached, dict):
                    self._feature_cache_stats["hits"] = int(self._feature_cache_stats.get("hits", 0)) + 1
                    self._feature_cache_stats["reused"] = int(self._feature_cache_stats.get("reused", 0)) + 1
                    return cached
            self._feature_cache_stats["misses"] = int(self._feature_cache_stats.get("misses", 0)) + 1

        # Default zeroed features
        features: Dict[str, Any] = {
            "duration": 0.0,
            "duration_seconds": 0.0,
            "sample_rate": 0,
            "num_samples": 0,
            "analysis_window": int(tuning.ANALYSIS_PARAMS["win"]),
            "analysis_hop": int(tuning.ANALYSIS_PARAMS["hop"]),
            "rms_global": 0.0,
            "rms_frame_mean": 0.0,
            "rms_frame_max": 0.0,
            "low_freq_ratio": 0.0,
            "low_freq_energy_ratio": 0.0,
            "transient_strength": 0.0,
            "centroid_mean": 0.0,
            "centroid_early": 0.0,
            "zcr_mean": 0.0,
            "flatness_mean": 0.0,
            "pitch_available": False,
            "pitch_skipped": False,
            "pitch_skip_reason": "",
            "pitch_gate_features": {},
            "f0_frames": 0,
            "voiced_frames": 0,
            "voiced_ratio": 0.0,
            "median_f0": 0.0,
            "semitone_std": 0.0,
            "pitch_std": 0.0,
            "glide_detected": False,
            "glide_confidence": 0.0,
            "glide_drop": 0.0,
        }

        backend = self._get_audio_backend()
        if backend is None:
            with self._feature_cache_lock:
                self._feature_cache[key] = features
                self._feature_cache_stats["computed"] = int(self._feature_cache_stats.get("computed", 0)) + 1
            return features
        np = backend["np"]
        sf = backend["sf"]
        stft = backend["stft"]
        yin = backend["yin"]
        rms_fn = backend["rms"]
        zcr_fn = backend["zero_crossing_rate"]

        try:
            data, sr = sf.read(str(file_path), always_2d=True, dtype="float32")
            if isinstance(data, (list, tuple)):
                data = np.array(data, dtype=np.float32)
            arr = np.asarray(data, dtype=np.float32)
            if arr.ndim == 2:
                if arr.shape[1] >= 2:
                    # Spec mono conversion: x = 0.5 * (L + R)
                    y = 0.5 * (arr[:, 0] + arr[:, 1])
                elif arr.shape[1] == 1:
                    y = arr[:, 0]
                else:
                    y = np.empty((0,), dtype=np.float32)
            else:
                y = arr
            y = np.ascontiguousarray(y, dtype=np.float32)

            n = int(len(y))
            features["sample_rate"] = int(sr)
            features["num_samples"] = n
            duration = (float(n) / float(sr)) if sr else 0.0
            features["duration"] = duration
            features["duration_seconds"] = duration

            eps = float(tuning.ANALYSIS_PARAMS["eps"])
            max_abs = float(np.max(np.abs(y))) if n > 0 else 0.0
            y_norm = y / (max_abs + eps)

            win = int(tuning.ANALYSIS_PARAMS["win"])
            hop = int(tuning.ANALYSIS_PARAMS["hop"])
            features["analysis_window"] = win
            features["analysis_hop"] = hop

            if n > 0:
                features["rms_global"] = float(np.sqrt(np.mean(np.square(y_norm))))

            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"n_fft=.*too large for input signal of length=.*",
                    category=UserWarning,
                )
                S = np.abs(stft(y_norm, n_fft=win, hop_length=hop, window="hann"))
            P = S**2
            freqs, low_mask = self._get_fft_low_mask(int(sr), win)

            total_power = float(np.sum(P))
            low_power = float(np.sum(P[low_mask, :])) if P.size > 0 else 0.0
            low_ratio = float(low_power) / float(total_power + eps)
            features["low_freq_ratio"] = low_ratio
            features["low_freq_energy_ratio"] = low_ratio

            rms = rms_fn(S=S, hop_length=hop)[0]
            features["rms_frame_mean"] = float(np.mean(rms)) if len(rms) > 0 else 0.0
            features["rms_frame_max"] = float(np.max(rms)) if len(rms) > 0 else 0.0
            early_frames = int((float(tuning.ANALYSIS_PARAMS["transient_early_seconds"]) * sr) / hop + 0.999)
            mid_frames = int((float(tuning.ANALYSIS_PARAMS["transient_mid_seconds"]) * sr) / hop + 0.999)
            if len(rms) > 0:
                peak_early = float(np.max(rms[: max(1, early_frames)]))
                start_mid = min(len(rms), early_frames)
                end_mid = min(len(rms), start_mid + max(1, mid_frames))
                median_mid = float(np.median(rms[start_mid:end_mid])) if end_mid > start_mid else float(np.median(rms))
                features["transient_strength"] = peak_early / (median_mid + eps)

            if S.size > 0:
                mag_sum = np.sum(S, axis=0)
                centroid = np.dot(freqs, S) / (mag_sum + eps)
            else:
                centroid = np.array([], dtype=np.float32)
            features["centroid_mean"] = float(np.mean(centroid)) if centroid.size > 0 else 0.0
            early_centroid_frames = int((float(tuning.ANALYSIS_PARAMS["centroid_early_seconds"]) * sr) / hop + 0.999)
            features["centroid_early"] = (
                float(np.mean(centroid[: max(1, early_centroid_frames)]))
                if centroid.size > 0
                else features["centroid_mean"]
            )

            zcr = zcr_fn(y_norm, frame_length=win, hop_length=hop)[0]
            features["zcr_mean"] = float(np.mean(zcr)) if zcr.size > 0 else 0.0

            if P.size > 0:
                flatness_amin = float(tuning.ANALYSIS_PARAMS.get("flatness_amin", 1e-10))
                P_safe = np.maximum(P, flatness_amin)
                geom_mean = np.exp(np.mean(np.log(P_safe), axis=0))
                arith_mean = np.mean(P_safe, axis=0)
                flatness = geom_mean / (arith_mean + eps)
            else:
                flatness = np.array([], dtype=np.float32)
            features["flatness_mean"] = float(np.mean(flatness)) if flatness.size > 0 else 0.0

            # Pitch (yin) with deterministic gating for clearly percussive samples
            try:
                pitch_skip_reason = self._pitch_skip_reason(features)
                features["pitch_gate_features"] = {
                    "duration": float(features.get("duration", 0.0) or 0.0),
                    "transient_strength": float(features.get("transient_strength", 0.0) or 0.0),
                    "centroid_mean": float(features.get("centroid_mean", 0.0) or 0.0),
                    "low_freq_ratio": float(features.get("low_freq_ratio", 0.0) or 0.0),
                    "zcr_mean": float(features.get("zcr_mean", 0.0) or 0.0),
                    "flatness_mean": float(features.get("flatness_mean", 0.0) or 0.0),
                }
                if pitch_skip_reason:
                    features["pitch_available"] = False
                    features["pitch_skipped"] = True
                    features["pitch_skip_reason"] = str(pitch_skip_reason)
                    raise ValueError(f"Pitch analysis skipped: {pitch_skip_reason}")
                yin_frame_length = max(int(win), int(tuning.PITCH_ANALYSIS_PARAMS["yin_frame_length_min"]))
                if n < yin_frame_length:
                    raise ValueError("Signal too short for YIN frame length")
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message=r"With fmin=.*less than two periods of fmin fit into the frame.*",
                        category=UserWarning,
                    )
                    f0 = yin(
                        y_norm,
                        fmin=float(tuning.PITCH_ANALYSIS_PARAMS["yin_fmin"]),
                        fmax=float(tuning.PITCH_ANALYSIS_PARAMS["yin_fmax"]),
                        sr=sr,
                        frame_length=yin_frame_length,
                        hop_length=hop,
                    )
                features["pitch_available"] = True
                valid = np.isfinite(f0) & (f0 > 0)
                features["f0_frames"] = int(len(f0))
                features["voiced_frames"] = int(np.sum(valid))
                voiced_ratio = float(np.sum(valid)) / float(len(f0)) if len(f0) > 0 else 0.0
                features["voiced_ratio"] = voiced_ratio
                if bool(np.any(valid)):
                    vf0 = f0[valid]
                    features["median_f0"] = float(np.median(vf0))
                    s = 12.0 * np.log2(vf0 / 55.0)
                    semitone_std = float(np.std(s)) if s.size > 0 else 0.0
                    features["semitone_std"] = semitone_std
                    features["pitch_std"] = semitone_std
                glide_info = self._detect_glide(f0, sr, yin_frame_length, hop)
                features.update(glide_info)
            except Exception:
                features["pitch_available"] = False

        except Exception:
            # Keep zeroed features on failure
            pass

        with self._feature_cache_lock:
            self._feature_cache[key] = features
            self._feature_cache_stats["computed"] = int(self._feature_cache_stats.get("computed", 0)) + 1
        return features

    def _detect_glide(self, f0, sr: int, win: int, hop: int) -> Dict[str, Any]:
        """Detect pitch glide (best-effort). Requires numpy and scipy."""
        result: Dict[str, Any] = {
            "glide_detected": False,
            "glide_confidence": 0.0,
            "glide_drop": 0.0,
            "glide_drop_st": 0.0,
            "glide_slope_st_per_sec": 0.0,
            "glide_residual_mad": 0.0,
            "glide_voiced_frames": 0,
            "glide_voiced_ratio": 0.0,
            "glide_duration": 0.0,
            "glide_guardrails_passed": False,
        }

        try:
            import numpy as np  # type: ignore
        except Exception:
            return result

        try:
            if f0 is None or len(f0) == 0:
                return result
            valid_mask = np.isfinite(f0) & (f0 > 0)
            total_frames = int(len(f0))
            voiced_count = int(np.sum(valid_mask))
            voiced_ratio = (float(voiced_count) / float(total_frames)) if total_frames > 0 else 0.0
            duration = (float(total_frames * hop) / float(sr)) if sr else 0.0
            result["glide_voiced_frames"] = voiced_count
            result["glide_voiced_ratio"] = voiced_ratio
            result["glide_duration"] = duration

            if duration < float(tuning.GLIDE_PARAMS["duration_min"]):
                return result
            if voiced_ratio < float(tuning.GLIDE_PARAMS["voiced_ratio_min"]):
                return result
            if voiced_count < int(tuning.GLIDE_PARAMS["min_voiced_frames"]):
                return result

            result["glide_guardrails_passed"] = True
            voiced_f0 = f0[valid_mask]
            voiced_idx = np.flatnonzero(valid_mask)
            t = voiced_idx.astype(float) * (hop / float(sr))
            s = 12.0 * np.log2(voiced_f0 / 55.0)

            try:
                from scipy.ndimage import median_filter  # type: ignore

                s_med = median_filter(s, size=int(tuning.GLIDE_PARAMS["median_filter_size"]), mode="nearest")
            except Exception:
                s_med = s

            mean_size = int(tuning.GLIDE_PARAMS["mean_filter_size"])
            s_smooth = np.convolve(s_med, np.ones(mean_size) / float(mean_size), mode="same")

            n = len(s_smooth)
            if n < int(tuning.GLIDE_PARAMS["min_voiced_frames"]):
                return result

            trim_ratio = float(tuning.GLIDE_PARAMS["trim_ratio"])
            start_idx = int(n * trim_ratio)
            end_idx = int(n * (1.0 - trim_ratio))
            if end_idx <= start_idx:
                return result

            t_u = t[start_idx:end_idx]
            s_u = s_smooth[start_idx:end_idx]
            if len(s_u) < 3:
                return result

            max_pts = int(tuning.GLIDE_PARAMS["theil_sen_max_points"])
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
            q20 = int(q * float(tuning.GLIDE_PARAMS["drop_window_ratio"]))
            start_med = float(np.median(s_u[: max(1, q20)]))
            end_med = float(np.median(s_u[-max(1, q20) :]))
            drop_st = start_med - end_med

            b = float(np.median(s_u - m * t_u))
            residuals = s_u - (m * t_u + b)
            res_med = float(np.median(residuals))
            mad = float(np.median(np.abs(residuals - res_med)))

            result["glide_slope_st_per_sec"] = m
            result["glide_drop"] = float(drop_st)
            result["glide_drop_st"] = float(drop_st)
            result["glide_residual_mad"] = mad

            if (
                drop_st >= float(tuning.GLIDE_PARAMS["drop_st_min"])
                and m <= float(tuning.GLIDE_PARAMS["slope_max_st_per_sec"])
                and mad <= float(tuning.GLIDE_PARAMS["mad_max"])
            ):
                A = _clamp(drop_st / float(tuning.GLIDE_PARAMS["conf_a_drop_scale"]), 0.0, 1.0)
                B = _clamp((-m) / float(tuning.GLIDE_PARAMS["conf_b_slope_scale"]), 0.0, 1.0)
                C = _clamp(
                    (voiced_ratio - float(tuning.GLIDE_PARAMS["voiced_ratio_min"])) / float(tuning.GLIDE_PARAMS["voiced_ratio_min"]),
                    0.0,
                    1.0,
                )
                D = _clamp(
                    (float(tuning.GLIDE_PARAMS["mad_max"]) - mad) / float(tuning.GLIDE_PARAMS["mad_max"]),
                    0.0,
                    1.0,
                )
                conf = (
                    float(tuning.GLIDE_CONF_WEIGHTS["A"]) * A
                    + float(tuning.GLIDE_CONF_WEIGHTS["B"]) * B
                    + float(tuning.GLIDE_CONF_WEIGHTS["C"]) * C
                    + float(tuning.GLIDE_CONF_WEIGHTS["D"]) * D
                )
                result["glide_detected"] = True
                result["glide_confidence"] = float(conf)

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

        is_kick_like = (
            duration < tuning.FEATURE_THRESHOLDS["kick_duration_max"] and transient > tuning.FEATURE_THRESHOLDS["transient_kick_min"]
        )
        tonal_808_like = (
            duration >= tuning.FEATURE_THRESHOLDS["808_duration_min"]
            and low_ratio >= tuning.FEATURE_THRESHOLDS["lowfreq_ratio_808"]
            and zcr_mean <= tuning.FEATURE_THRESHOLDS["zcr_tonal_max"]
        )
        hat_like_separation = (
            centroid_mean >= tuning.FEATURE_THRESHOLDS["centroid_bright"]
            and low_ratio <= tuning.FEATURE_THRESHOLDS["lowfreq_ratio_hat_max"]
            and flatness_mean >= tuning.FEATURE_THRESHOLDS["flatness_high"]
            and zcr_mean >= tuning.FEATURE_THRESHOLDS["zcr_high"]
        )

        # 808
        if not is_kick_like:
            if duration > tuning.FEATURE_THRESHOLDS["808_duration_min"]:
                scores["808s"] += tuning.AUDIO_WEIGHTS["duration"]
            if low_ratio > tuning.FEATURE_THRESHOLDS["lowfreq_ratio_808"]:
                scores["808s"] += tuning.AUDIO_WEIGHTS["lowfreq"]
            if centroid_mean < tuning.FEATURE_THRESHOLDS["centroid_low"]:
                scores["808s"] += tuning.AUDIO_WEIGHTS["centroid"]
            if zcr_mean < tuning.FEATURE_THRESHOLDS["zcr_tonal_max"]:
                scores["808s"] += tuning.AUDIO_WEIGHTS["zcr"]

        # Kicks
        if duration < tuning.FEATURE_THRESHOLDS["kick_duration_max"]:
            scores["Kicks"] += tuning.AUDIO_WEIGHTS["duration"]
        if transient > tuning.FEATURE_THRESHOLDS["transient_kick_min"]:
            scores["Kicks"] += tuning.AUDIO_WEIGHTS["transient"]
        if is_kick_like and not tonal_808_like and low_ratio > tuning.FEATURE_THRESHOLDS["kick_lowfreq_min"]:
            scores["Kicks"] += tuning.AUDIO_WEIGHTS["lowfreq"]
        if is_kick_like and not hat_like_separation and centroid_early > tuning.FEATURE_THRESHOLDS["kick_centroid_early_min"]:
            scores["Kicks"] += tuning.AUDIO_WEIGHTS["centroid"]
        if is_kick_like and not hat_like_separation and centroid_early > tuning.FEATURE_THRESHOLDS["centroid_bright"]:
            scores["Kicks"] += tuning.AUDIO_WEIGHTS["centroid"]
        if is_kick_like and not hat_like_separation:
            scores["Kicks"] += tuning.AUDIO_WEIGHTS["transient"] + tuning.AUDIO_WEIGHTS["duration"]

        # HiHats / Cymbals
        if centroid_mean > tuning.FEATURE_THRESHOLDS["centroid_bright"]:
            scores["HiHats"] += tuning.AUDIO_WEIGHTS["centroid"]
            scores["Cymbals"] += tuning.AUDIO_WEIGHTS["centroid"]
        if low_ratio < tuning.FEATURE_THRESHOLDS["lowfreq_ratio_hat_max"]:
            scores["HiHats"] += tuning.AUDIO_WEIGHTS["lowfreq"]
            scores["Cymbals"] += tuning.AUDIO_WEIGHTS["lowfreq"]
        if duration < tuning.FEATURE_THRESHOLDS["hat_duration_max"]:
            scores["HiHats"] += tuning.AUDIO_WEIGHTS["duration"]
            scores["Cymbals"] += tuning.AUDIO_WEIGHTS["duration"]
        if flatness_mean > tuning.FEATURE_THRESHOLDS["flatness_high"]:
            scores["HiHats"] += tuning.AUDIO_WEIGHTS["flatness"]
            scores["Cymbals"] += tuning.AUDIO_WEIGHTS["flatness"]
        if flatness_mean > tuning.FEATURE_THRESHOLDS["flatness_high"] and zcr_mean > tuning.FEATURE_THRESHOLDS["zcr_high"]:
            scores["HiHats"] += tuning.AUDIO_WEIGHTS["zcr"]
            scores["Cymbals"] += tuning.AUDIO_WEIGHTS["zcr"]

        # Snares / Claps
        snare_clap_spectral_ok = low_ratio < tuning.FEATURE_THRESHOLDS["snare_clap_lowfreq_max"]
        if (
            snare_clap_spectral_ok
            and flatness_mean > tuning.FEATURE_THRESHOLDS["snare_clap_flatness_min"]
            and zcr_mean > tuning.FEATURE_THRESHOLDS["zcr_high"]
        ):
            scores["Snares"] += tuning.AUDIO_WEIGHTS["flatness"]
            scores["Claps"] += tuning.AUDIO_WEIGHTS["flatness"]
        if (
            snare_clap_spectral_ok
            and transient > tuning.FEATURE_THRESHOLDS["snare_clap_transient_min"]
            and zcr_mean > tuning.FEATURE_THRESHOLDS["zcr_tonal_max"]
        ):
            scores["Snares"] += tuning.AUDIO_WEIGHTS["transient"] * 0.5
            scores["Claps"] += tuning.AUDIO_WEIGHTS["transient"] * 0.5
        if (
            snare_clap_spectral_ok
            and tuning.FEATURE_THRESHOLDS["centroid_moderate_low"] <= centroid_mean <= tuning.FEATURE_THRESHOLDS["centroid_moderate_high"]
        ):
            scores["Snares"] += tuning.AUDIO_WEIGHTS["centroid"] * 0.5
            scores["Claps"] += tuning.AUDIO_WEIGHTS["centroid"] * 0.5

        # Percs
        if (
            transient > tuning.FEATURE_THRESHOLDS["percs_transient_min"]
            and duration < tuning.FEATURE_THRESHOLDS["percs_duration_max"]
            and low_ratio < tuning.FEATURE_THRESHOLDS["percs_lowfreq_max"]
            and zcr_mean > tuning.FEATURE_THRESHOLDS["zcr_tonal_max"]
        ):
            scores["Percs"] += tuning.AUDIO_WEIGHTS["transient"]

        # Vox / FX (rough)
        if (
            centroid_mean < tuning.FEATURE_THRESHOLDS["vox_centroid_max"]
            and low_ratio < tuning.FEATURE_THRESHOLDS["vox_lowfreq_max"]
            and duration > tuning.FEATURE_THRESHOLDS["fx_duration_min"]
            and not tonal_808_like
        ):
            scores["Vox"] += tuning.AUDIO_WEIGHTS["duration"]

        if flatness_mean > tuning.FEATURE_THRESHOLDS["fx_flatness_min"] and duration > tuning.FEATURE_THRESHOLDS["fx_duration_min"]:
            scores["FX"] += tuning.AUDIO_WEIGHTS["flatness"]

        return scores

    def _compute_pitch_scores(self, features: Dict[str, Any]) -> Dict[str, float]:
        scores: Dict[str, float] = {bucket: 0.0 for bucket in self.BUCKET_RULES.keys()}

        if not bool(features.get("pitch_available", False)):
            return scores

        duration = float(features.get("duration", 0.0) or 0.0)
        transient = float(features.get("transient_strength", 0.0) or 0.0)
        low_ratio = float(features.get("low_freq_ratio", 0.0) or 0.0)
        centroid_mean = float(features.get("centroid_mean", 0.0) or 0.0)
        zcr_mean = float(features.get("zcr_mean", 0.0) or 0.0)
        flatness_mean = float(features.get("flatness_mean", 0.0) or 0.0)
        voiced_ratio = float(features.get("voiced_ratio", 0.0) or 0.0)
        median_f0 = float(features.get("median_f0", 0.0) or 0.0)
        pitch_std = float(features.get("semitone_std", features.get("pitch_std", 0.0)) or 0.0)
        glide_detected = bool(features.get("glide_detected", False))
        glide_confidence = float(features.get("glide_confidence", 0.0) or 0.0)

        is_kick_like = (
            duration < tuning.FEATURE_THRESHOLDS["kick_duration_max"] and transient > tuning.FEATURE_THRESHOLDS["transient_kick_min"]
        )
        hat_like_separation = (
            centroid_mean >= tuning.FEATURE_THRESHOLDS["centroid_bright"]
            and low_ratio <= tuning.FEATURE_THRESHOLDS["lowfreq_ratio_hat_max"]
            and flatness_mean >= tuning.FEATURE_THRESHOLDS["flatness_high"]
            and zcr_mean >= tuning.FEATURE_THRESHOLDS["zcr_high"]
        )
        tonal_808_like = (
            duration >= tuning.FEATURE_THRESHOLDS["808_duration_min"]
            and low_ratio >= tuning.FEATURE_THRESHOLDS["lowfreq_ratio_808"]
            and float(tuning.PITCH_ANALYSIS_PARAMS["median_f0_808_min"])
            <= median_f0
            <= float(tuning.PITCH_ANALYSIS_PARAMS["median_f0_808_max"])
            and voiced_ratio >= float(tuning.PITCH_ANALYSIS_PARAMS["voiced_ratio_808_min"])
        )

        # 808 pitch bonuses (single bucket; do not split to Bass)
        if not is_kick_like:
            if float(tuning.PITCH_ANALYSIS_PARAMS["median_f0_808_min"]) <= median_f0 <= float(tuning.PITCH_ANALYSIS_PARAMS["median_f0_808_max"]):
                scores["808s"] += tuning.PITCH_WEIGHTS["median_f0_low"]
            if voiced_ratio >= float(tuning.PITCH_ANALYSIS_PARAMS["voiced_ratio_808_min"]):
                scores["808s"] += tuning.PITCH_WEIGHTS["voiced_ratio"]
            if glide_detected:
                scores["808s"] += tuning.PITCH_WEIGHTS["glide_bonus"] * glide_confidence
            if pitch_std <= float(tuning.PITCH_ANALYSIS_PARAMS["pitch_stability_std_max"]):
                scores["808s"] += tuning.PITCH_WEIGHTS["stability_bonus"]

        # Kicks prefer low voiced ratio (pitch tracker likely unvoiced)
        if (
            is_kick_like
            and not hat_like_separation
            and voiced_ratio < float(tuning.PITCH_ANALYSIS_PARAMS["kick_voiced_ratio_max"])
        ):
            scores["Kicks"] += tuning.AUDIO_WEIGHTS["zcr"] * 0.5

        # Vox rough bonus only when pitch tracking is strongly voiced
        if (
            voiced_ratio > tuning.FEATURE_THRESHOLDS["vox_voiced_ratio_min"]
            and centroid_mean < tuning.FEATURE_THRESHOLDS["vox_centroid_max"]
            and low_ratio < tuning.FEATURE_THRESHOLDS["vox_lowfreq_max"]
            and not tonal_808_like
        ):
            scores["Vox"] += tuning.AUDIO_WEIGHTS["duration"]

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
            "folder_scores": {},
            "filename_scores": {},
            "audio_scores": {},
            "pitch_scores": {},
            "final_scores": {},
            "confidence_ratio": 0.0,
            "confidence_margin": 0.0,
            "low_confidence": False,
            "top_candidates": [],
        }

        if self._should_ignore(file_path.name):
            return (None, "UNSORTED", 0.0, [], False, reason)

        suffix = file_path.suffix.lower()

        # Producer OS safety default: only process WAV
        if suffix != ".wav":
            return (None, "UNSORTED", 0.0, [], False, reason)

        folder_scores, folder_matches = self._get_folder_hint_details(file_path)
        filename_scores, filename_matches = self._get_filename_hint_details(file_path.name)
        features = self._extract_features(file_path)
        audio_scores = self._compute_audio_scores(features)
        pitch_scores = self._compute_pitch_scores(features)

        final_scores: Dict[str, float] = {}
        for bucket in self.BUCKET_RULES.keys():
            final_scores[bucket] = (
                float(folder_scores.get(bucket, 0))
                + float(filename_scores.get(bucket, 0))
                + float(audio_scores.get(bucket, 0))
                + float(pitch_scores.get(bucket, 0))
            )

        sorted_scores = sorted(final_scores.items(), key=lambda kv: kv[1], reverse=True)
        best_bucket, best_score = sorted_scores[0]
        top3 = sorted_scores[:3]
        top3_scores = [float(score) for _, score in top3]
        while len(top3_scores) < 3:
            top3_scores.append(0.0)
        s1, s2, s3 = top3_scores[:3]
        confidence_ratio = float(s1) / max(1.0, float(s1 + s2 + s3))
        confidence_margin = float(s1 - s2)
        low_confidence = confidence_ratio < tuning.LOW_CONFIDENCE_THRESHOLD
        category = self.CATEGORY_MAP.get(best_bucket, "Samples")

        reason["folder_matches"] = folder_matches
        reason["filename_matches"] = filename_matches
        reason["folder_scores"] = {b: float(s) for b, s in folder_scores.items()}
        reason["filename_scores"] = {b: float(s) for b, s in filename_scores.items()}
        reason["audio_scores"] = {b: float(s) for b, s in audio_scores.items()}
        reason["pitch_scores"] = {b: float(s) for b, s in pitch_scores.items()}
        reason["final_scores"] = {b: float(s) for b, s in final_scores.items()}
        reason["confidence_ratio"] = float(confidence_ratio)
        reason["confidence_margin"] = float(confidence_margin)
        reason["low_confidence"] = bool(low_confidence)
        reason["top_candidates"] = [{"bucket": b, "score": float(s)} for b, s in top3]
        reason["top_3_candidates"] = list(reason["top_candidates"])
        reason["audio_summary"] = {
            "sample_rate": features.get("sample_rate"),
            "num_samples": features.get("num_samples"),
            "analysis_window": features.get("analysis_window"),
            "analysis_hop": features.get("analysis_hop"),
            "duration": features.get("duration"),
            "duration_seconds": features.get("duration_seconds"),
            "rms_global": features.get("rms_global"),
            "rms_frame_mean": features.get("rms_frame_mean"),
            "rms_frame_max": features.get("rms_frame_max"),
            "low_freq_ratio": features.get("low_freq_ratio"),
            "low_freq_energy_ratio": features.get("low_freq_energy_ratio"),
            "transient_strength": features.get("transient_strength"),
            "centroid_mean": features.get("centroid_mean"),
            "centroid_early": features.get("centroid_early"),
            "zcr_mean": features.get("zcr_mean"),
            "flatness_mean": features.get("flatness_mean"),
            "threshold_checks": {
                "duration_ge_808_min": float(features.get("duration", 0.0) or 0.0)
                >= float(tuning.FEATURE_THRESHOLDS["808_duration_min"]),
                "duration_le_kick_max": float(features.get("duration", 0.0) or 0.0)
                <= float(tuning.FEATURE_THRESHOLDS["kick_duration_max"]),
                "lowfreq_ge_808_min": float(features.get("low_freq_ratio", 0.0) or 0.0)
                >= float(tuning.FEATURE_THRESHOLDS["lowfreq_ratio_808"]),
                "lowfreq_le_hat_max": float(features.get("low_freq_ratio", 0.0) or 0.0)
                <= float(tuning.FEATURE_THRESHOLDS["lowfreq_ratio_hat_max"]),
                "centroid_ge_bright": float(features.get("centroid_mean", 0.0) or 0.0)
                >= float(tuning.FEATURE_THRESHOLDS["centroid_bright"]),
                "centroid_le_low": float(features.get("centroid_mean", 0.0) or 0.0)
                <= float(tuning.FEATURE_THRESHOLDS["centroid_low"]),
                "transient_ge_kick_min": float(features.get("transient_strength", 0.0) or 0.0)
                >= float(tuning.FEATURE_THRESHOLDS["transient_kick_min"]),
                "zcr_le_tonal_max": float(features.get("zcr_mean", 0.0) or 0.0)
                <= float(tuning.FEATURE_THRESHOLDS["zcr_tonal_max"]),
            },
        }
        reason["pitch_summary"] = {
            "pitch_available": features.get("pitch_available"),
            "pitch_skipped": features.get("pitch_skipped", False),
            "pitch_skip_reason": features.get("pitch_skip_reason", ""),
            "pitch_gate_features": dict(features.get("pitch_gate_features", {}) or {}),
            "f0_frames": features.get("f0_frames"),
            "voiced_frames": features.get("voiced_frames"),
            "median_f0": features.get("median_f0"),
            "voiced_ratio": features.get("voiced_ratio"),
            "semitone_std": features.get("semitone_std"),
            "pitch_std": features.get("pitch_std"),
            "threshold_checks": {
                "median_f0_in_808_range": float(tuning.PITCH_ANALYSIS_PARAMS["median_f0_808_min"])
                <= float(features.get("median_f0", 0.0) or 0.0)
                <= float(tuning.PITCH_ANALYSIS_PARAMS["median_f0_808_max"]),
                "voiced_ratio_ge_808_min": float(features.get("voiced_ratio", 0.0) or 0.0)
                >= float(tuning.PITCH_ANALYSIS_PARAMS["voiced_ratio_808_min"]),
                "pitch_stable": float(features.get("semitone_std", features.get("pitch_std", 0.0)) or 0.0)
                <= float(tuning.PITCH_ANALYSIS_PARAMS["pitch_stability_std_max"]),
            },
        }
        reason["glide_summary"] = {
            "glide_detected": features.get("glide_detected"),
            "glide_confidence": features.get("glide_confidence"),
            "glide_drop": features.get("glide_drop"),
            "glide_drop_st": features.get("glide_drop_st", features.get("glide_drop")),
            "glide_slope_st_per_sec": features.get("glide_slope_st_per_sec"),
            "glide_residual_mad": features.get("glide_residual_mad"),
            "glide_voiced_frames": features.get("glide_voiced_frames"),
            "glide_voiced_ratio": features.get("glide_voiced_ratio"),
            "glide_duration": features.get("glide_duration"),
            "glide_guardrails_passed": features.get("glide_guardrails_passed"),
            "threshold_checks": {
                "duration_ge_min": float(features.get("glide_duration", 0.0) or 0.0)
                >= float(tuning.GLIDE_PARAMS["duration_min"]),
                "voiced_ratio_ge_min": float(features.get("glide_voiced_ratio", 0.0) or 0.0)
                >= float(tuning.GLIDE_PARAMS["voiced_ratio_min"]),
                "voiced_frames_ge_min": int(features.get("glide_voiced_frames", 0) or 0)
                >= int(tuning.GLIDE_PARAMS["min_voiced_frames"]),
                "drop_st_ge_min": float(features.get("glide_drop_st", features.get("glide_drop", 0.0)) or 0.0)
                >= float(tuning.GLIDE_PARAMS["drop_st_min"]),
                "slope_le_max": float(features.get("glide_slope_st_per_sec", 0.0) or 0.0)
                <= float(tuning.GLIDE_PARAMS["slope_max_st_per_sec"]),
                "mad_le_max": float(features.get("glide_residual_mad", 0.0) or 0.0)
                <= float(tuning.GLIDE_PARAMS["mad_max"]),
            },
        }

        return (best_bucket, category, float(confidence_ratio), top3, bool(low_confidence), reason)

    def _format_reason_text(
        self,
        bucket: Optional[str],
        confidence: float,
        candidates: List[Tuple[str, float]],
        low_confidence: bool,
    ) -> str:
        if bucket is None:
            if candidates:
                return "; ".join([f"{b}:{round(float(s), 2)}" for b, s in candidates])
            return "no matches"

        reason = f"best match: {bucket}, confidence={confidence:.2f}"
        if low_confidence:
            reason += "; low confidence"
        if candidates:
            reason += "; candidates: " + ", ".join([f"{b}:{round(float(s), 2)}" for b, s in candidates])
        return reason

    def _build_pack_file_entry(
        self,
        source: Path,
        dest: Path,
        bucket: Optional[str],
        category: str,
        confidence: float,
        action: str,
        reason_text: str,
        reason_dict: Dict[str, Any],
    ) -> PackFileEntry:
        entry: PackFileEntry = {
            "source": str(source),
            "dest": str(dest),
            "bucket": bucket or "UNSORTED",
            "chosen_bucket": bucket or "UNSORTED",
            "category": category,
            "confidence": float(confidence),
            "action": action,
            "reason": reason_text,
            "confidence_ratio": float(reason_dict.get("confidence_ratio", confidence) or 0.0),
            "confidence_margin": float(reason_dict.get("confidence_margin", 0.0) or 0.0),
            "low_confidence": bool(reason_dict.get("low_confidence", False)),
            "top_candidates": list(reason_dict.get("top_candidates", [])),
            "top_3_candidates": list(reason_dict.get("top_3_candidates", reason_dict.get("top_candidates", []))),
            "folder_matches": list(reason_dict.get("folder_matches", [])),
            "filename_matches": list(reason_dict.get("filename_matches", [])),
            "audio_summary": dict(reason_dict.get("audio_summary", {})),
            "pitch_summary": dict(reason_dict.get("pitch_summary", {})),
            "glide_summary": dict(reason_dict.get("glide_summary", {})),
        }
        return entry

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
        developer_options: Optional[Dict[str, Any]] = None,
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
        self._reset_feature_cache_stats()
        developer_options = developer_options or {}
        worker_count = max(
            1,
            int(
                developer_options.get(
                    "workers",
                    getattr(tuning, "PARALLEL_WORKERS_DEFAULT", 1),
                )
                or 1
            ),
        )

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
            "files_skipped_non_wav": 0,
            "unsorted": 0,
            "packs": [],
            "feature_cache_stats": self._feature_cache_stats_snapshot(),
            "workers": worker_count,
        }

        # ANALYZE: absolutely no filesystem writes
        if mode == "analyze":
            packs = self._discover_packs()
            for pack_dir in packs:
                pack_report: PackReport = {
                    "pack": pack_dir.name,
                    "files": [],
                }
                wav_items, skipped_non_wav = self._collect_pack_wavs(pack_dir)
                report["files_skipped_non_wav"] += skipped_non_wav
                results = self._classify_files_batch([p for p, _ in wav_items], workers=worker_count)
                for (file_path, rel_path), (bucket, category, confidence, candidates, low_confidence, reason_dict) in zip(
                    wav_items, results
                ):
                    if bucket is None:
                        dest_path = self.hub_dir / "UNSORTED" / pack_dir.name / rel_path
                        report["unsorted"] += 1
                        reason = self._format_reason_text(bucket, confidence, candidates, low_confidence)
                    else:
                        display_bucket = self.bucket_service.get_display_name(bucket)
                        dest_path = self.hub_dir / category / display_bucket / pack_dir.name / rel_path
                        reason = self._format_reason_text(bucket, confidence, candidates, low_confidence)

                    pack_report["files"].append(
                        self._build_pack_file_entry(
                            source=file_path,
                            dest=dest_path,
                            bucket=bucket,
                            category=category,
                            confidence=confidence,
                            action="NONE",
                            reason_text=reason,
                            reason_dict=reason_dict,
                        )
                    )
                    report["files_processed"] += 1

                report["packs"].append(pack_report)

            report["feature_cache_stats"] = self._feature_cache_stats_snapshot()
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
            report["feature_cache_stats"] = self._feature_cache_stats_snapshot()
            if write_logs and report_path:
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            if write_hub:
                self._save_feature_cache()
                report["feature_cache_stats"] = self._feature_cache_stats_snapshot()
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
                wav_items, skipped_non_wav = self._collect_pack_wavs(pack_dir)
                report["files_skipped_non_wav"] += skipped_non_wav
                results = self._classify_files_batch([p for p, _ in wav_items], workers=worker_count)

                for (file_path, rel_path), (bucket, category, confidence, candidates, low_confidence, reason_dict) in zip(
                    wav_items, results
                ):
                    if bucket is None:
                        dest_dir = (
                            self._ensure_unsorted_structure(pack_dir.name)
                            if write_hub
                            else (self.hub_dir / "UNSORTED" / pack_dir.name)
                        )
                        dest_path = dest_dir / rel_path
                        report["unsorted"] += 1
                        reason = self._format_reason_text(bucket, confidence, candidates, low_confidence)
                    else:
                        display_bucket = self.bucket_service.get_display_name(bucket)
                        if write_hub:
                            _, _, pack_dest_dir = self._ensure_hub_structure(category, bucket, pack_dir.name)
                        else:
                            pack_dest_dir = self.hub_dir / category / display_bucket / pack_dir.name
                        dest_path = pack_dest_dir / rel_path
                        reason = self._format_reason_text(bucket, confidence, candidates, low_confidence)

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
                        self._build_pack_file_entry(
                            source=file_path,
                            dest=dest_path,
                            bucket=bucket,
                            category=category,
                            confidence=confidence,
                            action=action,
                            reason_text=reason,
                            reason_dict=reason_dict,
                        )
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
            report["feature_cache_stats"] = self._feature_cache_stats_snapshot()
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        if write_hub:
            self._save_feature_cache()
            report["feature_cache_stats"] = self._feature_cache_stats_snapshot()
        else:
            report["feature_cache_stats"] = self._feature_cache_stats_snapshot()

        return report

    # ------------------------------------------------------------------
    # Benchmark / audit reporting (read-only classification analysis)
    def build_benchmark_report(
        self,
        report: Dict[str, Any],
        *,
        top_confusions: int = 20,
        max_files: Optional[int] = None,
        runtime_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Build a classifier benchmark/audit report from a run report."""
        entries: List[Dict[str, Any]] = []
        for pack in report.get("packs", []):
            pack_name = str(pack.get("pack", ""))
            for file_entry in pack.get("files", []) or []:
                if not isinstance(file_entry, dict):
                    continue
                row = dict(file_entry)
                row["_pack"] = pack_name
                entries.append(row)

        if max_files is not None and max_files >= 0:
            entries = entries[: max_files]

        total = len(entries)
        low_conf_entries = [e for e in entries if bool(e.get("low_confidence", False))]

        bucket_counts: Dict[str, int] = {}
        low_conf_bucket_counts: Dict[str, int] = {}
        confusion_counts: Dict[Tuple[str, str], int] = {}
        misfits: List[Dict[str, Any]] = []

        for entry in entries:
            chosen = str(entry.get("chosen_bucket") or entry.get("bucket") or "UNSORTED")
            bucket_counts[chosen] = bucket_counts.get(chosen, 0) + 1
            if bool(entry.get("low_confidence", False)):
                low_conf_bucket_counts[chosen] = low_conf_bucket_counts.get(chosen, 0) + 1

            top3 = entry.get("top_3_candidates") or entry.get("top_candidates") or []
            runner_up = None
            if isinstance(top3, list):
                valid_top = [c for c in top3 if isinstance(c, dict) and c.get("bucket") is not None]
                if len(valid_top) >= 2:
                    runner_up = str(valid_top[1].get("bucket"))
                if bool(entry.get("low_confidence", False)):
                    misfits.append(
                        {
                            "pack": entry.get("_pack", ""),
                            "source": str(entry.get("source", "")),
                            "chosen_bucket": chosen,
                            "top_3_candidates": [
                                {"bucket": str(c.get("bucket")), "score": float(c.get("score", 0.0) or 0.0)}
                                for c in valid_top[:3]
                            ],
                            "confidence_ratio": float(entry.get("confidence_ratio", 0.0) or 0.0),
                            "confidence_margin": float(entry.get("confidence_margin", 0.0) or 0.0),
                            "low_confidence": bool(entry.get("low_confidence", False)),
                        }
                    )
            if runner_up:
                pair = (chosen, runner_up)
                confusion_counts[pair] = confusion_counts.get(pair, 0) + 1

        bucket_distribution = []
        for bucket, count in sorted(bucket_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            bucket_distribution.append(
                {
                    "bucket": bucket,
                    "count": int(count),
                    "percent": round((float(count) / float(total) * 100.0) if total else 0.0, 4),
                }
            )

        low_conf_by_bucket = []
        for bucket, count in sorted(bucket_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            low_count = int(low_conf_bucket_counts.get(bucket, 0))
            low_conf_by_bucket.append(
                {
                    "bucket": bucket,
                    "count": low_count,
                    "rate": round((float(low_count) / float(count)) if count else 0.0, 4),
                }
            )

        confusion_pairs = [
            {"chosen": chosen, "runner_up": runner_up, "count": int(count)}
            for (chosen, runner_up), count in sorted(
                confusion_counts.items(),
                key=lambda kv: (-kv[1], kv[0][0], kv[0][1]),
            )[: max(1, int(top_confusions))]
        ]

        misfits_sorted = sorted(
            misfits,
            key=lambda e: (
                float(e.get("confidence_ratio", 1.0)),
                float(e.get("confidence_margin", 9999.0)),
                str(e.get("source", "")),
            ),
        )

        benchmark: Dict[str, Any] = {
            "version": 1,
            "timestamp": datetime.datetime.now().isoformat(),
            "inbox": str(self.inbox_dir.resolve()),
            "hub": str(self.hub_dir.resolve()),
            "files_classified": int(total),
            "files_skipped_non_wav": int(report.get("files_skipped_non_wav", 0) or 0),
            "errors": int(report.get("failed", 0) or 0),
            "runtime_seconds": round(float(runtime_seconds or 0.0), 6),
            "low_confidence": {
                "count": int(len(low_conf_entries)),
                "rate": round((float(len(low_conf_entries)) / float(total)) if total else 0.0, 6),
            },
            "bucket_distribution": bucket_distribution,
            "low_confidence_by_bucket": low_conf_by_bucket,
            "confusion_pairs": confusion_pairs,
            "representative_misfits": misfits_sorted[: max(10, min(100, int(top_confusions) * 2))],
            "feature_cache_stats": dict(report.get("feature_cache_stats") or self._feature_cache_stats_snapshot()),
            "tuning_snapshot": {
                "folder_hint_weight": int(tuning.FOLDER_HINT_WEIGHT),
                "filename_hint_weight": int(tuning.FILENAME_HINT_WEIGHT),
                "low_confidence_threshold": float(tuning.LOW_CONFIDENCE_THRESHOLD),
            },
        }
        return benchmark

    def run_benchmark(
        self,
        *,
        output_path: Optional[Path] = None,
        top_confusions: int = 20,
        max_files: Optional[int] = None,
        workers: int = 1,
        save_feature_cache: bool = True,
    ) -> Dict[str, Any]:
        """Run a read-only recursive classification benchmark and write a JSON report."""
        # workers is accepted for forward compatibility (Phase 2 parallel extraction)
        worker_count = max(1, int(workers))

        started = time.perf_counter()
        analyze_report = self.run(mode="analyze", developer_options={"workers": worker_count})
        benchmark = self.build_benchmark_report(
            analyze_report,
            top_confusions=top_confusions,
            max_files=max_files,
            runtime_seconds=(time.perf_counter() - started),
        )

        if save_feature_cache:
            self._save_feature_cache()
            benchmark["feature_cache_stats"] = self._feature_cache_stats_snapshot()

        if output_path is not None:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(benchmark, indent=2), encoding="utf-8")

        return benchmark

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
