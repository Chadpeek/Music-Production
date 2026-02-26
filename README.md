<p align="center">
  <img src="assets/banner.png" alt="Producer OS Banner" />
</p>

<h1 align="center">Producer-OS</h1>

<p align="center">
  <strong>Deterministic sample organizer for music producers, with a safety-first Python engine, GUI, and CLI.</strong>
</p>

<p align="center">
  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/Python-3.11%2B-blue" alt="Python 3.11+" />
  </a>
  <a href="https://www.gnu.org/licenses/gpl-3.0.en.html">
    <img src="https://img.shields.io/badge/License-GPL--3.0-green" alt="GPL-3.0 License" />
  </a>
  <a href="https://github.com/KidChadd/Producer-OS/actions/workflows/python.yml">
    <img src="https://img.shields.io/github/actions/workflow/status/KidChadd/Producer-OS/python.yml?label=CI" alt="CI" />
  </a>
  <a href="https://github.com/KidChadd/Producer-OS/releases">
    <img src="https://img.shields.io/github/v/release/KidChadd/Producer-OS?label=Latest" alt="Latest Release" />
  </a>
</p>

## Overview

Producer-OS is a rule-based organizer for music production libraries and incoming sample packs.
It uses a shared Python engine across both a desktop GUI (PySide6) and a CLI.

Current v2 classification is focused on `.wav` files and uses a deterministic hybrid pipeline (folder hints, filename hints, audio features, pitch/glide analysis) designed for explainability and repeatability.

## Key Features

- Deterministic hybrid WAV classification (no ML)
- Shared engine for GUI and CLI
- Confidence scoring with low-confidence flagging and top-3 candidates
- Explainable per-file reasoning in `run_report.json` (log-writing modes)
- Feature caching via `feature_cache.json`
- Safety-first modes (`analyze`, `dry-run`, `copy`, `move`)
- Audit trail support with `undo-last-run`
- JSON config + bucket/style mappings with schema validation
- Portable mode support (`portable.flag` or `--portable`)
- Windows portable ZIP and installer releases

## Installation

### Windows Release (Recommended)

Download the latest Windows builds from GitHub Releases:

- Portable ZIP (`ProducerOS-<version>-portable-win64.zip`)
- Installer (`ProducerOS-Setup-<version>.exe`)

Releases: `https://github.com/KidChadd/Producer-OS/releases`

### Install From Source

GUI + CLI:

```powershell
git clone https://github.com/KidChadd/Producer-OS.git
cd Producer-OS

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e ".[gui]"
```

CLI-only:

```powershell
pip install -e .
```

## Quick Start

### CLI

```powershell
producer-os --help
producer-os analyze C:\path\to\inbox C:\path\to\hub
producer-os dry-run C:\path\to\inbox C:\path\to\hub --verbose
producer-os copy C:\path\to\inbox C:\path\to\hub
producer-os move C:\path\to\inbox C:\path\to\hub
```

### GUI

```powershell
producer-os-gui
```

### Module Entry

```powershell
python -m producer_os --help
python -m producer_os gui
```

## Documentation

### Technical Docs (`docs/`)

- `docs/README.md` - documentation index and detailed CLI mode behavior
- `docs/CLASSIFICATION.md` - hybrid WAV classifier, confidence, reporting, cache
- `docs/TROUBLESHOOTING.md` - common setup/runtime issues and fixes
- `docs/RELEASE_PROCESS.md` - versioning and release workflow details

### Project Docs (Root)

- `RULES_AND_USAGE.md`
- `TESTING_GUIDE.md`
- `CONTRIBUTING.md`
- `SUPPORT.md`
- `SECURITY.md`
- `CODE_OF_CONDUCT.md`
- `CHANGELOG.md`

## License

Licensed under GPL-3.0-only.
See `LICENSE` for details.

## Star History

<a href="https://www.star-history.com/#KidChadd/Producer-OS&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=KidChadd/Producer-OS&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=KidChadd/Producer-OS&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=KidChadd/Producer-OS&type=date&legend=top-left" />
 </picture>
</a>
