# Release Process

This document explains the current CI/versioning/release automation used by Producer-OS.

## Overview

Producer-OS uses GitHub Actions plus `python-semantic-release` to automate version tags and Windows releases.

Primary workflows:

- `.github/workflows/python.yml` - CI (lint, type-check, tests, package build)
- `.github/workflows/version.yml` - auto versioning/tagging on `main`
- `.github/workflows/release.yml` - Windows release build + GitHub Release upload
- `.github/workflows/build.yml` - manual Windows build artifact (non-release)

## Versioning Rules (semantic-release)

Defined in `pyproject.toml`:

- `pyproject.toml` (`[project].version`) is the package version source of truth and is updated by `python-semantic-release`

- `feat` -> minor release
- `fix` -> patch release
- `perf` -> patch release
- `refactor` -> patch release
- `docs` / `chore` -> no release

Tags use the format:

- `vMAJOR.MINOR.PATCH` (example: `v0.1.3`)

## Automatic Release Flow (`main` push)

1. Push commits to `main`
2. `version.yml` runs on the push
3. Workflow checks whether `HEAD` already has a version tag
4. If no version tag exists on `HEAD`, it runs `semantic-release version`
5. If semantic-release creates a new version tag, `version.yml` resolves it
6. `version.yml` dispatches `release.yml` with the tag value
7. `release.yml` builds Windows artifacts and uploads them to the GitHub Release

## Why the Tag Check Exists

`version.yml` intentionally checks for an existing version tag on `HEAD` to prevent duplicate-tag failures when:

- a tag was already created for that commit
- CI is re-run
- multiple versioning paths overlap

This keeps auto-versioning idempotent and avoids common semantic-release tag push errors.

## Windows Release Workflow (`release.yml`)

Triggers:

- tag push matching `v*.*.*`
- manual `workflow_dispatch` with a `tag` input

What it does:

- checks out the repo (and selected tag for manual dispatch)
- installs Python + dependencies
- runs a minimal import preflight for release dependencies
- builds a standalone Windows app (Nuitka)
- runs a packaged GUI smoke test (launch + timed exit)
- runs a packaged tiny-analyze smoke test against `examples/synthetic_corpus`
- builds a portable ZIP
- builds an installer (Inno Setup)
- optionally signs Windows artifacts (portable EXE + installer) when signing secrets are configured
- verifies artifacts exist
- generates and uploads `SHA256SUMS.txt` for release artifacts
- generates and uploads `SIGNING_STATUS.txt` (signed vs unsigned transparency)
- generates and uploads `BUILD_INFO.txt` and `BUILD_TIMING.txt` (build provenance/timing)
- uploads assets to the GitHub Release
- enables GitHub-generated release notes (`generate_release_notes: true`)

## Manual Rebuild of an Existing Tag

Use `release.yml` manual dispatch when:

- a release build failed
- you need to rebuild artifacts for an existing tag
- you fixed packaging CI without changing the version tag

Input:

- `tag` (example: `v0.1.2`)

The workflow checks out the exact tag and rebuilds artifacts for that version.

Manual dispatch also overlays the latest shared CI build scripts from `origin/main` before building so packaging/build-script fixes can be applied to older tags without changing the release version.

## Manual Windows Build (Non-Release)

Use `build.yml` when you want a Windows EXE artifact without publishing a GitHub Release.

This workflow:

- runs on `workflow_dispatch`
- supports `ref` input (branch/tag/SHA)
- supports `build_profile` input (`dev` / `release`)
- builds a Windows standalone package with Nuitka
- runs full lint/tests (unlike `release.yml`)
- uploads a zip artifact to the workflow run
- uploads `BUILD_INFO.txt`, `BUILD_TIMING.txt`, and `SIGNING_STATUS.txt`

## Build Profiles (`build_windows_nuitka.ps1`)

The shared build script supports:

- `release` profile (used by `release.yml`)
- `dev` profile (available in `build.yml` for faster iteration)

The script also:

- performs an MSVC `cl.exe` preflight check (fast-fail locally)
- auto-discovers MSVC on GitHub-hosted runners when `cl.exe` is not on `PATH`
- excludes known upstream test modules from Nuitka standalone builds to reduce compile graph size
- enables parallel C compilation (`--jobs`) based on runner CPU count unless overridden

## Release Notes / Patch Notes

GitHub release notes are generated automatically by the release upload step in `release.yml`.

This reduces manual maintenance and ensures each release page contains patch notes.

## Release Checksums (SHA256)

The Windows release workflow generates a `SHA256SUMS.txt` file and uploads it to the GitHub Release alongside:

- portable ZIP
- installer EXE

This lets users verify downloads independently.

Example verification on Windows PowerShell:

```powershell
Get-FileHash .\ProducerOS-Setup-<version>.exe -Algorithm SHA256
Get-FileHash .\ProducerOS-<version>-portable-win64.zip -Algorithm SHA256
```

Compare the resulting hashes with the entries in `SHA256SUMS.txt`.

## Signing Transparency (`SIGNING_STATUS.txt`)

The release workflow also generates `SIGNING_STATUS.txt`, which records the Authenticode signature status for:

- packaged portable executable (`ProducerOS.exe`)
- installer executable (`ProducerOS-Setup-<version>.exe`)

This makes it obvious whether a release was signed or built in unsigned/placeholders mode.

## Packaged GUI + Tiny-Analyze Smoke Tests (CI)

The shared Windows build script (`.github/scripts/build_windows_nuitka.ps1`) performs post-build runtime checks of the packaged executable:

- verifies `qwindows.dll` exists
- runs `ProducerOS.exe` with `PRODUCER_OS_SMOKE_TEST=1`
- the GUI launches and exits automatically after a short timer
- runs `ProducerOS.exe` with `PRODUCER_OS_SMOKE_TINY_ANALYZE=1` against `examples/synthetic_corpus`
- verifies the tiny-analyze smoke JSON output and processed file count
- CI fails if startup crashes, hangs, or packaged runtime analyze behavior fails

This catches packaging/runtime issues that file-existence checks alone cannot catch.

## Build Timing and Provenance Artifacts

Windows build/release workflows upload:

- `BUILD_INFO.txt` (build profile, repo root, script source, git SHA/ref, Nuitka args)
- `BUILD_TIMING.txt` (Nuitka phase timing + workflow timing rows)

`BUILD_TIMING.txt` includes values such as:

- `nuitka_total_seconds`
- `python_level_compile_seconds`
- `scons_c_compile_and_link_seconds`
- workflow-level dependency/install and installer timings

Use these artifacts to identify whether build time is dominated by Python-level analysis, C compilation/linking, or installer time before making CI changes.

## Optional Code Signing (Placeholder Integration)

Producer-OS includes an optional `signtool`-based signing script:

- `.github/scripts/sign_windows_artifacts.ps1`

It is wired into the Windows build/release pipeline with a no-op fallback when signing is not configured.

### Environment / Secret Names

Configure these GitHub Actions secrets to enable signing:

- `WINDOWS_SIGN_ENABLE` (`1` to enable)
- `WINDOWS_SIGN_CERT_B64` (base64-encoded `.pfx`)
- `WINDOWS_SIGN_CERT_PASSWORD`
- `WINDOWS_SIGN_TIMESTAMP_URL` (optional; defaults to DigiCert timestamp URL)
- `WINDOWS_SIGNTOOL_PATH` (optional, if `signtool.exe` is not on PATH)

### Behavior

- Signing disabled or missing secrets:
  - workflow logs `Signing skipped (placeholders mode)`
  - build/release continues
- Signing enabled but signing fails:
  - workflow fails
- Release workflow publishes `SIGNING_STATUS.txt` so users can see signature state

### Local Dry Run (No Secrets)

You can verify the placeholder behavior locally:

```powershell
& ".github\scripts\sign_windows_artifacts.ps1" -Paths @("dist\build_gui_entry.dist\ProducerOS.exe")
```

To test actual signing locally, set the environment variables in your shell first and ensure `signtool.exe` is available.

## Practical Recommendations

- Prefer the automated version flow (`version.yml`) for normal releases
- Avoid manually creating tags unless you have a specific reason
- If a release build fails, re-run `release.yml` (manual dispatch) for the same tag
- Use `build.yml` with `build_profile=dev` for iteration; reserve `release.yml` for tagged releases
- Review `BUILD_TIMING.txt` before changing Nuitka inputs/exclusions
- Publish/check `SHA256SUMS.txt` with each release and verify local rebuilds when troubleshooting
- Keep commit messages aligned with Conventional Commits so version bumps happen predictably

## Related Files

- `.github/workflows/version.yml`
- `.github/workflows/release.yml`
- `.github/workflows/build.yml`
- `pyproject.toml`
