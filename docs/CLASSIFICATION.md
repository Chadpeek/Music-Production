# Hybrid WAV Classification

This document describes the current deterministic WAV classification pipeline used by `ProducerOSEngine`.

## Scope

- Classification is currently `.wav`-only.
- Non-WAV files are ignored by the classification path.
- The classifier is deterministic (no ML, no randomness).

## Classification Inputs

Producer-OS combines multiple signals for each WAV file:

1. Parent folder hints
2. Filename hints
3. Deterministic audio features
4. Deterministic pitch tracking and glide detection

The engine always selects a best bucket, even when confidence is low.

## Hint Scoring

### Parent Folder Hints

- Scans recent parent folders (configurable depth)
- Tokenizes folder names
- Applies bucket keyword matching using the project bucket vocabulary
- Applies capped scores per bucket

### Filename Hints

- Tokenizes/lowercases the filename stem
- Applies bucket keyword matching using the same bucket vocabulary
- Applies capped scores per bucket

## Audio Feature Extraction (WAV)

Audio feature extraction is best-effort and deterministic when audio dependencies are available (`numpy`, `librosa`, `soundfile`).

Computed features include:

- duration
- RMS (global and frame-based)
- transient strength
- spectral centroid (mean and early)
- low-frequency energy ratio
- zero-crossing rate
- spectral flatness

If optional audio dependencies are unavailable or analysis fails for a file:

- the engine does not crash
- zero/default feature values are used
- classification still proceeds using available signals

## Pitch Tracking and Glide Detection

Pitch analysis uses a deterministic YIN-based path when available.

Computed pitch-related fields include:

- `pitch_available`
- `voiced_ratio`
- `median_f0`
- `semitone_std` / stability
- glide metrics and glide detection result

Glide detection is designed with guardrails to avoid common false positives on short percussive hits.

## Confidence and Candidate Ranking

For each analyzed WAV, the engine computes:

- chosen bucket
- confidence ratio
- confidence margin
- low-confidence flag
- top 3 candidates (bucket + score)

Low-confidence files are still assigned a bucket, but flagged for review.

## Explainability and Reporting

The engine records detailed reasoning per file, including:

- folder matches
- filename matches
- audio summary
- pitch summary
- glide summary
- candidate scores and confidence fields

### `run_report.json`

`run_report.json` is written in log-writing modes (`dry-run`, `copy`, `move`, `repair-styles`) and includes the detailed classification reasoning payload.

`analyze` mode is intentionally no-write and returns a report in memory instead of writing `run_report.json`.

## Feature Cache

Extracted audio features are cached in:

- `feature_cache.json`

The cache is used to avoid re-analyzing unchanged files.
The cache key includes file identity metadata (path/size/mtime) to keep reuse deterministic.

Run reports and benchmark reports also include `feature_cache_stats` (hits/misses/reused/computed/persisted).

Compatibility note:

- The current cache file remains a plain key->feature map for backward compatibility.
- Cache metadata/versioning is tracked in reports/docs rather than changing the on-disk cache format (for now).

## Idempotency and Safety

- The engine avoids duplicate destination naming spam by skipping existing destinations
- Classification is deterministic for the same inputs/config
- Audio content is never modified by classification
- No per-file `.nfo` files are created by the classifier logic itself

## Tuning

Classifier weights and thresholds are centralized in:

- `src/producer_os/tuning.py`

Optional overrides can be loaded from a `tuning.json` file (when present and valid).
Malformed override files fail gracefully.

## User Hint Overrides

Producer-OS also supports additive user hint keywords via:

- `bucket_hints.json`

These hints extend (but do not replace) the built-in bucket keyword vocabulary and are logged in reasoning fields as `source: "user_hint"`.
