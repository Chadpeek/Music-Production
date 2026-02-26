# System Requirements

This document lists the runtime and development requirements for Producer-OS.

Use this page to verify whether a machine is suitable for:

- running the Windows release build (portable ZIP or installer)
- running from source (CLI and/or GUI)
- developing or building Windows release artifacts

## Supported / Target Platforms

### End Users (Recommended)

- Windows 10 or Windows 11 (64-bit)
- Latest packaged releases from GitHub (`portable` ZIP or `installer`)

Producer-OS release artifacts are built and tested on `windows-latest` in GitHub Actions.

### Source Install (Advanced)

- Python `3.11+` (required)
- Primary tested environment: Windows (CI runs on Windows)

Source installs may work on other platforms, but Windows is the primary supported target for packaged GUI releases and release automation.

## Minimum vs Recommended Hardware

These are practical guidelines for sample-library workflows (not strict hard limits).

### Minimum (Small libraries / testing)

- CPU: 2 cores
- RAM: 4 GB
- Storage: 2 GB free (app + temp files + logs)
- Display: 1280x720

### Recommended (Real sample libraries)

- CPU: 4+ cores
- RAM: 8-16 GB
- Storage: 10+ GB free (especially for `copy` mode and large libraries)
- SSD strongly recommended for faster scanning/classification
- Display: 1920x1080 for the best GUI review workflow experience

## Runtime Requirements (Windows Releases)

If you use the downloadable Windows release:

- No separate Python install is required
- No manual dependency installation is required
- Internet is not required after download (unless you use network paths)

Portable ZIP / Installer notes:

- Portable mode can be enabled with `portable.flag`
- The app may write logs/reports/config files depending on mode
- `analyze` mode remains no-write (no `run_report.json`, no `feature_cache.json`)

## Runtime Requirements (Source Install)

### Required (CLI Core)

- Python `3.11+`
- `pip`
- Dependencies from `pyproject.toml`:
  - `numpy`
  - `librosa`
  - `soundfile`
  - `jsonschema`

Install:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
```

### Optional (GUI)

For the desktop app, install GUI extras:

- `PySide6`
- `pyqtdarktheme`

Install:

```powershell
pip install -e ".[gui]"
```

### Optional (Development / CI parity)

Developer extras include:

- `pytest`
- `ruff`
- `mypy`
- `nuitka`
- `zstandard`
- `python-semantic-release`

Install:

```powershell
pip install -e ".[dev,gui]"
```

## Audio and File Requirements

### Classification Input Support

- Hybrid audio classification is currently implemented for `.wav` files only
- Non-`.wav` files are ignored by the classifier

### Safety / File Mutation Rules

Producer-OS is designed to be safe by mode:

- `analyze`: no writes
- `dry-run`: writes logs/reports only (no file moves/copies)
- `copy`: copies files to the hub (source files remain)
- `move`: moves files to the hub with audit support

Producer-OS does not modify WAV audio content during analysis/classification.

## Permissions and Path Requirements

Producer-OS needs permission to:

- read the input sample folders
- write to the hub/log locations for log-writing modes
- create/update config files in standard or portable config locations

Recommended:

- use local NTFS paths for best performance
- avoid cloud-synced folders while benchmarking large libraries (can add latency)

Windows path considerations:

- Unicode file/folder names are supported
- Very long paths may depend on system long-path support and policy settings

## Disk Usage Expectations

Disk usage depends on mode:

- `analyze`: minimal (no reports/cache written)
- `dry-run`: logs and reports only
- `copy`: duplicates files into the hub (requires significant free space)
- `move`: no duplication of the final library, but requires space for logs/audit artifacts

Feature caching:

- `feature_cache.json` improves repeat-run performance
- Cache size grows with the number of analyzed WAV files

## Network Requirements

Runtime network access is not required for local use.

Network access may be used for:

- downloading releases
- cloning the repo
- CI/release operations (maintainers)

## Build and Release Requirements (Maintainers)

If you build Windows artifacts locally/CI:

- Windows (64-bit)
- Python `3.12` is used in current Windows build/release workflows
- Inno Setup (for installer builds in CI via action)
- Nuitka (current workflows pin `2.8.10`)

### Optional Code Signing

Code signing is supported via CI placeholder integration (`signtool`) and secrets.

Without signing configured:

- builds still complete
- Windows may show SmartScreen/unknown publisher warnings

See:

- [`docs/RELEASE_PROCESS.md`](RELEASE_PROCESS.md)
- [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)

## Recommended Verification Checklist

Before deploying Producer-OS on a new machine:

1. Confirm Windows 10/11 64-bit (or Python `3.11+` for source install)
2. Confirm enough free disk space for your chosen mode (`copy` needs the most)
3. For source installs, install `.[gui]` if you need the GUI
4. Run `producer-os --help` (CLI) or `producer-os-gui` (GUI)
5. Test `analyze` on a small folder before `copy`/`move`

## Related Docs

- [`docs/README.md`](README.md)
- [`docs/CLI_REFERENCE.md`](CLI_REFERENCE.md)
- [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- [`docs/RELEASE_PROCESS.md`](RELEASE_PROCESS.md)
