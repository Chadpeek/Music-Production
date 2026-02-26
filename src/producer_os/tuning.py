"""Centralized tuning constants for deterministic hybrid WAV classification.

All scoring weights, thresholds, and analysis parameters should be defined
here and referenced by the engine (single source of truth).
"""

from __future__ import annotations

from typing import Any, Dict

# ---------------------------------------------------------------------------
# User-facing tuning knobs (requested defaults)
FOLDER_HINT_WEIGHT = 20
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
    "centroid_bright": 4000.0,
    "centroid_low": 300.0,
    "transient_kick_min": 3.0,
    "zcr_tonal_max": 0.08,
    # Additional thresholds used by current engine scoring logic
    "flatness_high": 0.3,
    "zcr_high": 0.1,
    "centroid_moderate_low": 1000.0,
    "centroid_moderate_high": 3000.0,
    "hat_duration_max": 0.40,
    "kick_lowfreq_min": 0.20,
    "kick_centroid_early_min": 1200.0,
    "snare_clap_flatness_min": 0.20,
    "snare_clap_transient_min": 2.0,
    "snare_clap_lowfreq_max": 0.45,
    "percs_transient_min": 1.5,
    "percs_duration_max": 0.50,
    "percs_lowfreq_max": 0.45,
    "vox_voiced_ratio_min": 0.75,
    "vox_centroid_max": 2000.0,
    "vox_lowfreq_max": 0.40,
    "fx_flatness_min": 0.50,
    "fx_duration_min": 0.50,
}

AUDIO_WEIGHTS: Dict[str, float] = {
    "duration": 10,
    "lowfreq": 15,
    "centroid": 10,
    "transient": 35,
    "zcr": 10,
    "flatness": 10,
}

PITCH_WEIGHTS: Dict[str, float] = {
    "median_f0_low": 20,
    "voiced_ratio": 10,
    "glide_bonus": 10,
    "stability_bonus": 10,
}

# Deterministic pitch-analysis gating (Phase 2)
PITCH_GATING_ENABLED = True
PITCH_GATING_THRESHOLDS: Dict[str, float] = {
    "hat_duration_max": 0.22,
    "hat_centroid_min": 5000.0,
    "hat_lowfreq_max": 0.12,
    "hat_flatness_min": 0.40,
    "hat_zcr_min": 0.12,
    "kick_duration_max": 0.22,
    "kick_transient_min": 4.5,
    "kick_voiced_ratio_force_skip_max": 0.05,
    "kick_centroid_min": 800.0,
}

# Parallel extraction rollout (opt-in; CLI --workers controls actual value)
PARALLEL_EXTRACTION_ENABLED = True
PARALLEL_WORKERS_DEFAULT = 1

# ---------------------------------------------------------------------------
# Internal deterministic analysis parameters (also centralized here)
ANALYSIS_PARAMS: Dict[str, float] = {
    "eps": 1e-9,
    "win": 2048,
    "hop": 512,
    "low_freq_cutoff_hz": 120.0,
    "flatness_amin": 1e-10,
    "transient_early_seconds": 0.08,
    "transient_mid_seconds": 0.20,
    "centroid_early_seconds": 0.10,
}

PITCH_ANALYSIS_PARAMS: Dict[str, float] = {
    "yin_fmin": 20.0,
    "yin_fmax": 2000.0,
    "yin_frame_length_min": 2207,
    "median_f0_808_min": 30.0,
    "median_f0_808_max": 100.0,
    "voiced_ratio_808_min": 0.50,
    "pitch_stability_std_max": 0.20,
    "kick_voiced_ratio_max": 0.25,
}

GLIDE_PARAMS: Dict[str, float] = {
    "duration_min": 0.35,
    "voiced_ratio_min": 0.35,
    "min_voiced_frames": 15,
    "median_filter_size": 5,
    "mean_filter_size": 5,
    "trim_ratio": 0.10,
    "drop_st_min": 1.0,
    "slope_max_st_per_sec": -2.0,
    "mad_max": 0.35,
    "theil_sen_max_points": 100,
    "drop_window_ratio": 0.20,
    "conf_a_drop_scale": 6.0,
    "conf_b_slope_scale": 10.0,
}

GLIDE_CONF_WEIGHTS: Dict[str, float] = {
    "A": 0.35,
    "B": 0.25,
    "C": 0.20,
    "D": 0.20,
}


def apply_overrides(data: Dict[str, Any]) -> None:
    """Merge numeric/dict tuning overrides into module globals (best-effort)."""
    if not isinstance(data, dict):
        return

    module_globals = globals()
    for key, value in data.items():
        if key not in module_globals:
            continue
        current = module_globals[key]
        if isinstance(current, dict) and isinstance(value, dict):
            current.update(value)
        elif isinstance(current, (int, float)) and isinstance(value, (int, float)):
            module_globals[key] = value
