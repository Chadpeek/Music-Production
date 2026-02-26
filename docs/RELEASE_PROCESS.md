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
- runs Ruff and tests
- builds a standalone Windows app (Nuitka)
- builds a portable ZIP
- builds an installer (Inno Setup)
- verifies artifacts exist
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

## Manual Windows Build (Non-Release)

Use `build.yml` when you want a Windows EXE artifact without publishing a GitHub Release.

This workflow:

- runs on `workflow_dispatch`
- builds a Windows standalone package with Nuitka
- uploads a zip artifact to the workflow run

## Release Notes / Patch Notes

GitHub release notes are generated automatically by the release upload step in `release.yml`.

This reduces manual maintenance and ensures each release page contains patch notes.

## Practical Recommendations

- Prefer the automated version flow (`version.yml`) for normal releases
- Avoid manually creating tags unless you have a specific reason
- If a release build fails, re-run `release.yml` (manual dispatch) for the same tag
- Keep commit messages aligned with Conventional Commits so version bumps happen predictably

## Related Files

- `.github/workflows/version.yml`
- `.github/workflows/release.yml`
- `.github/workflows/build.yml`
- `pyproject.toml`
