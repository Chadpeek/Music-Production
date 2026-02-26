# Documentation

This folder contains the deeper technical documentation for Producer-OS.

Use the root `README.md` for installation and quick start.
Use the documents below for implementation details, troubleshooting, and release operations.

## Start Here

- `docs/CLASSIFICATION.md` - Hybrid WAV classifier behavior, confidence, reporting, and caching
- `docs/TROUBLESHOOTING.md` - Common install/runtime issues and fixes
- `docs/RELEASE_PROCESS.md` - CI/versioning/release workflow details
- `docs/SYSTEM_REQUIREMENTS.md` - Runtime, hardware, and build requirements
- `docs/CLI_REFERENCE.md` - CLI commands, flags, and benchmark usage
- `docs/ARCHITECTURE.md` - Engine/UI/services architecture and data flow

## Screenshots

GUI:

![Producer-OS GUI Screenshot](../assets/gui-screenshot.svg)

CLI:

![Producer-OS CLI Screenshot](../assets/cli-screenshot.svg)

## CLI Modes (Detailed Behavior)

The table below reflects current engine behavior.

| Command | Purpose | Writes logs/reports | Writes cache | Moves files | Writes `.nfo` |
|---|---|---:|---:|---:|---:|
| `analyze` | Classify and return report only | No | No | No | No |
| `dry-run` | Simulate routing and write logs/reports | Yes | No | No | No |
| `copy` | Copy files to hub | Yes | Yes | No | Yes |
| `move` | Move files to hub with audit trail | Yes | Yes | Yes | Yes |
| `repair-styles` | Repair/regenerate folder style files | Yes | Yes | No | Yes |
| `undo-last-run` | Revert the most recent `move` | Reads latest audit | No | Yes (revert) | No |
| `benchmark-classifier` | Read-only classifier audit + benchmark report | JSON benchmark report | Yes | No | No |

Reserved/stub CLI commands:

- `preview-styles`
- `doctor`

## Run Outputs

When log-writing modes run, Producer-OS creates a run directory under:

- `HUB\logs\<run_id>\`

Artifacts typically include:

- `run_report.json`
- `run_log.txt`
- `audit.csv` (move runs; used by `undo-last-run`)

## Configuration and Portable Mode

Configuration files:

- `config.json`
- `buckets.json`
- `bucket_styles.json`

Portable mode can be enabled by:

- placing `portable.flag` next to the app, or
- passing `--portable` in the CLI

Starter examples are in `examples/`.

## Project Docs (Root)

These remain at the repository root because they are standard GitHub-facing project docs:

- `CONTRIBUTING.md`
- `SECURITY.md`
- `SUPPORT.md`
- `CODE_OF_CONDUCT.md`
- `CHANGELOG.md`
- `RULES_AND_USAGE.md`
- `TESTING_GUIDE.md`
