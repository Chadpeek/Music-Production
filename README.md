<p align="center">
  <img src="assets/banner.png" alt="Producer OS Banner" />
</p>

<h1 align="center">Producer-OS</h1>

<p align="center">
<strong>Rule-based sample pack organizer and music production file manager built with Python.</strong>
</p>

<p align="center">
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python 3.11+" />
  </a>
  <a href="https://www.gnu.org/licenses/gpl-3.0.en.html">
    <img src="https://img.shields.io/badge/License-GPL--3.0-green" alt="GPL-3.0 License" />
  </a>
  <img src="https://img.shields.io/github/actions/workflow/status/KidChadd/Producer-OS/python.yml?label=CI" alt="CI" />
  <img src="https://img.shields.io/github/v/release/KidChadd/Producer-OS?label=Latest" alt="Latest Release" />
</p>

---

> Current version is shown in the **Latest Release** badge above (GitHub Releases).

Producer-OS is a safety-first, rule-driven file organizer for music producers.  
It organizes sample packs, audio files, MIDI packs, presets, and DAW project files using structured JSON rules and schema validation.

Built with Python and PySide6, it provides:

- A desktop GUI application  
- A command-line interface (CLI) for automation workflows  

---

# What Is Producer-OS?

Producer-OS is a rule-based sample pack organizer designed for serious music production environments.

It helps producers manage:

- Drum kits  
- Sample packs  
- WAV files  
- MIDI packs  
- FL Studio projects  
- Presets and production assets  

Instead of relying only on file extensions or folder names, Producer-OS evaluates files using configurable JSON rules validated by JSON schemas.

Unmatched files are routed to **UNSORTED**.  
Unsafe or flagged files are routed to **Quarantine**.  
Every action is logged and traceable.

---

# Core Features

- Rule-based sorting engine  
- Shared engine powering both GUI and CLI  
- JSON-driven configuration  
- JSON schema validation before execution  
- PySide6 desktop GUI  
- CLI for scripted and headless workflows  
- Detailed logging of file decisions  
- Automatic UNSORTED and Quarantine routing  
- Modular architecture  

---

# Installation

```powershell
git clone https://github.com/KidChadd/Producer-OS.git
cd Producer-OS

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e ".[gui]"
```

For a CLI-only install, use `pip install -e .` instead.

---

# Run

## CLI

```powershell
producer-os --help
producer-os dry-run -h
```

## GUI

```powershell
producer-os-gui
```

## Module Entry (Optional)

```powershell
python -m producer_os --help
python -m producer_os gui
python -m producer_os.cli --help
```

---

# Example CLI Usage

## Dry-run (no changes)

```powershell
producer-os dry-run C:\path\to\inbox C:\path\to\hub --verbose
```

## Copy into hub (non-destructive)

```powershell
producer-os copy C:\path\to\inbox C:\path\to\hub
```

## Move into hub (destructive, logged)

```powershell
producer-os move C:\path\to\inbox C:\path\to\hub
```

## Analyze (report only)

```powershell
producer-os analyze C:\path\to\inbox C:\path\to\hub
```

---

# Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e ".[dev,gui]"

ruff check src tests
mypy src/producer_os
pytest -q
```

---

# Build (wheel + sdist)

```powershell
python -m pip install --upgrade build
python -m build
```

Artifacts are written to `dist/`.

---

# Configuration System

Producer-OS uses structured JSON configuration files:

- `config.json`
- `buckets.json`
- `bucket_styles.json`

By default, these are stored in the platform config directory (for example,
`%APPDATA%\ProducerOS` on Windows). Use `--portable` or place a `portable.flag`
file next to the app to keep configuration local to the app/hub folder.

Starter examples are provided in `examples/`.

All configurations are validated against JSON schemas before execution.
Invalid configurations block execution.

---

# Safety Model

Producer-OS enforces:

- No file deletion by default  
- Schema validation before sorting  
- Routing of unmatched files to UNSORTED  
- Routing of unsafe files to Quarantine  
- Full logging of file actions  

---

# Requirements

- Python 3.11+  
- Desktop environment capable of running PySide6  
- Dependencies managed via `pyproject.toml`  

---

# Continuous Integration

GitHub Actions workflows include:

- `python.yml` (push/PR on `main`): Ruff, Mypy, Pytest, wheel/sdist build
- `build.yml` (manual): Windows EXE build artifact (Nuitka)
- `version.yml` (push to `main`): semantic-release version/tag automation + Windows release workflow dispatch
- `release.yml` (tag push `v*.*.*` or manual): Windows portable zip + installer release asset upload

---

# Release Automation

Producer-OS uses an automated release pipeline:

1. Push to `main`
2. `version.yml` runs semantic-release (Conventional Commits)
3. If a new version is needed, a tag like `v0.1.3` is created automatically
4. `release.yml` builds Windows artifacts and uploads them to the GitHub Release

GitHub release patch notes are generated automatically during the release upload step.

Notes:

- `feat` commits trigger a minor release
- `fix`, `perf`, and `refactor` commits trigger a patch release
- `docs` / `chore` commits do not create a new version tag
- `release.yml` also supports manual reruns for an existing tag (workflow dispatch)

---

# Documentation

- `RULES_AND_USAGE.md`  
- `TESTING_GUIDE.md`  
- `SUPPORT.md`  
- `CONTRIBUTING.md`  
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`

---

## ⭐ Star History

<a href="https://www.star-history.com/#KidChadd/Producer-OS&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=KidChadd/Producer-OS&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=KidChadd/Producer-OS&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=KidChadd/Producer-OS&type=date&legend=top-left" />
 </picture>
</a>

---

# License

GNU General Public License v3.0 (GPL-3.0)

See `LICENSE` for full details.
