# Troubleshooting

This page covers common setup and runtime issues for Producer-OS.

## GUI Fails to Start (PySide6 Not Installed)

Symptom:

- Running `producer-os-gui` or `python -m producer_os gui` fails on a source install

Fix:

```powershell
pip install -e ".[gui]"
```

## Qt Platform Plugin Error (`qwindows.dll`)

Symptom (older/broken Windows portable builds):

- `Could not find the Qt platform plugin "windows"`
- `no Qt platform plugin could be initialized`

Cause:

- The Windows portable build was created without the required PySide6/Nuitka plugin assets.

Fix:

- Download the latest GitHub Release build
- If rebuilding locally/CI, ensure the PySide6 plugin is bundled (the current CI workflow already verifies `qwindows.dll`)

Related docs:

- `docs/RELEASE_PROCESS.md`

## `analyze` Mode Did Not Create Logs or Reports

This is expected.

`analyze` mode is a strict no-write mode:

- no logs
- no `run_report.json`
- no `feature_cache.json`
- no `.nfo` writes

Use `dry-run` if you want logs/reports without moving/copying files.

## Low-Confidence Classifications

Symptom:

- Files are still bucketed, but flagged `low_confidence`

This is expected behavior.
Producer-OS always selects a best bucket and records the top candidates in the reasoning payload.

What to do:

- Review `run_report.json`
- Check `top_3_candidates`
- Tune keyword mappings / bucket vocab
- Tune classifier thresholds/weights (advanced)

## Audio Features Seem Missing / Weak Classification

Possible cause:

- Optional audio dependencies are not installed or failed to import

Recommended source install:

```powershell
pip install -e ".[gui]"
```

For development:

```powershell
pip install -e ".[dev,gui]"
```

## Config Files Not Being Loaded

Check the mode/location first:

- Standard mode uses the platform config directory (Windows: `%APPDATA%\ProducerOS`)
- Portable mode uses local files when `portable.flag` exists or `--portable` is used

Files to verify:

- `config.json`
- `buckets.json`
- `bucket_styles.json`

Starter examples:

- `examples/config.example.json`
- `examples/buckets.json`
- `examples/bucket_styles.json`

## Release Was Not Tagged After a Push

Producer-OS uses semantic-release rules.
Not every commit type creates a version bump.

Version bump commit types:

- `feat` (minor)
- `fix` (patch)
- `perf` (patch)
- `refactor` (patch)

No version bump by default:

- `docs`
- `chore`

## Duplicate Tag / Release Race (Manual Tag + Auto Version)

Symptom:

- CI fails trying to push a tag that already exists

Cause:

- A tag was pushed manually while `version.yml` also tried to create the same version tag

Current behavior:

- `version.yml` checks for an existing version tag on `HEAD` and skips safely

Recommendation:

- Let `version.yml` own version tags unless you are intentionally rebuilding an existing tag via manual release dispatch
