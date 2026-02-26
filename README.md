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
The GUI now includes a low-confidence review workflow so you can inspect and correct uncertain classifications before re-running copy/move operations.

## Key Features

- Deterministic hybrid WAV classification (no ML)
- Shared engine for GUI and CLI
- Confidence scoring with low-confidence flagging and top-3 candidates
- GUI low-confidence review queue with override + hint-save workflow
- Explainable per-file reasoning in `run_report.json` (log-writing modes)
- Feature caching via `feature_cache.json`
- `benchmark-classifier` command for distribution/confusion audits and tuning
- Preview-before-apply GUI tab for safer review before `copy`/`move`
- Built-in troubleshooting panel (logs/config/report/dependency checks)
- Safety-first modes (`analyze`, `dry-run`, `copy`, `move`)
- Audit trail support with `undo-last-run`
- JSON config + bucket/style mappings with schema validation
- Portable mode support (`portable.flag` or `--portable`)
- Windows portable ZIP and installer releases with automated release notes

## System Requirements

See [`docs/SYSTEM_REQUIREMENTS.md`](docs/SYSTEM_REQUIREMENTS.md) for runtime, hardware, and build requirements.

## Installation

### Windows Release (Recommended)

Download the latest Windows builds from [GitHub Releases](https://github.com/KidChadd/Producer-OS/releases):

- Portable ZIP (`ProducerOS-<version>-portable-win64.zip`)
- Installer (`ProducerOS-Setup-<version>.exe`)

Releases: [github.com/KidChadd/Producer-OS/releases](https://github.com/KidChadd/Producer-OS/releases)

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

See [`docs/SYSTEM_REQUIREMENTS.md`](docs/SYSTEM_REQUIREMENTS.md) before source installs on new machines.

## Quick Start

### CLI

```powershell
producer-os --help
producer-os analyze C:\path\to\inbox C:\path\to\hub
producer-os dry-run C:\path\to\inbox C:\path\to\hub --verbose
producer-os copy C:\path\to\inbox C:\path\to\hub
producer-os move C:\path\to\inbox C:\path\to\hub
producer-os benchmark-classifier C:\path\to\inbox C:\path\to\hub
```

### GUI

```powershell
producer-os-gui
```

Recommended first run:

- Start with `analyze`
- Review the `Low Confidence Review` tab
- Save hints/overrides, then rerun before `copy` or `move`

### Module Entry

```powershell
python -m producer_os --help
python -m producer_os gui
```

## Screenshots

### GUI

![Producer-OS GUI Screenshot](assets/gui-screenshot.svg)

### CLI

![Producer-OS CLI Screenshot](assets/cli-screenshot.svg)

## Documentation

### Technical Docs (`docs/`)

- [`docs/SYSTEM_REQUIREMENTS.md`](docs/SYSTEM_REQUIREMENTS.md) - system, runtime, and build requirements
- [`docs/README.md`](docs/README.md) - documentation index and detailed CLI mode behavior
- [`docs/CLASSIFICATION.md`](docs/CLASSIFICATION.md) - hybrid WAV classifier, confidence, reporting, cache
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) - common setup/runtime issues and fixes
- [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md) - versioning and release workflow details
- [`docs/CLI_REFERENCE.md`](docs/CLI_REFERENCE.md) - CLI commands, flags, and benchmark usage
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) - engine/UI/services architecture and data flow

### Project Docs (Root)

- [`RULES_AND_USAGE.md`](RULES_AND_USAGE.md)
- [`TESTING_GUIDE.md`](TESTING_GUIDE.md)
- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`SUPPORT.md`](SUPPORT.md)
- [`SECURITY.md`](SECURITY.md)
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)
- [`CHANGELOG.md`](CHANGELOG.md)

## License

Licensed under GPL-3.0-only.
See [`LICENSE`](LICENSE) for details.

## Star History

<a href="https://www.star-history.com/#KidChadd/Producer-OS&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=KidChadd/Producer-OS&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=KidChadd/Producer-OS&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=KidChadd/Producer-OS&type=date&legend=top-left" />
 </picture>
</a>
