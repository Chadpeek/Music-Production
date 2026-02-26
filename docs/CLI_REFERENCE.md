# CLI Reference

This document lists the current Producer-OS CLI commands, arguments, and behavior.

## Entrypoints

Examples:

```powershell
producer-os --help
python -m producer_os --help
```

GUI from module entry:

```powershell
python -m producer_os gui
```

## Common Notes

- Classification/routing is `.wav`-only.
- `--portable` forces portable config mode unless `portable.flag` is already present.
- `--workers` is supported and defaults to `1`.
- `analyze` is no-write; `dry-run` writes logs/reports but no transfers.

## Commands

## `analyze`

Read-only classification report (no logs, no cache save, no `.nfo` writes).

```powershell
producer-os analyze <inbox> <hub> [--portable] [--verbose] [--workers N]
```

## `dry-run`

Simulated routing with logs/reports written to `HUB\logs\<run_id>\`.

```powershell
producer-os dry-run <inbox> <hub> [--portable] [--verbose] [--workers N]
```

## `copy`

Copies files into the hub, writes reports, cache, and folder styles.

```powershell
producer-os copy <inbox> <hub> [--portable] [--verbose] [--workers N]
```

## `move`

Moves files into the hub and records an audit trail for `undo-last-run`.

```powershell
producer-os move <inbox> <hub> [--portable] [--verbose] [--workers N]
```

## `repair-styles`

Regenerates missing/misplaced folder style `.nfo` files in the hub.

```powershell
producer-os repair-styles <hub> [--portable]
```

## `undo-last-run`

Reverts the most recent `move` using the latest `audit.csv`.

```powershell
producer-os undo-last-run <hub> [--portable]
```

## `benchmark-classifier`

Read-only recursive classifier audit with console summary + JSON report.

```powershell
producer-os benchmark-classifier <inbox> <hub> [--portable] [--output PATH] [--top-confusions N] [--max-files N] [--workers N] [--verbose] [--compare PATH]
```

Outputs:

- console summary (bucket distribution, low-confidence rate, top confusion pairs)
- JSON benchmark report (default: `HUB\logs\benchmark_report.json`)

Key report sections:

- `bucket_distribution`
- `low_confidence`
- `low_confidence_by_bucket`
- `confusion_pairs`
- `representative_misfits`
- `feature_cache_stats`
- `tuning_snapshot`

## Reserved / Placeholder Commands

These commands currently return a not-implemented error:

- `preview-styles`
- `doctor`

## Examples

```powershell
# Read-only inspect an inbox
producer-os analyze C:\Samples\Inbox C:\ProducerHub

# Benchmark tuning changes and write JSON report
producer-os benchmark-classifier C:\Samples\Inbox C:\ProducerHub --top-confusions 25 --output C:\ProducerHub\logs\bench.json

# Compare current benchmark to a previous report
producer-os benchmark-classifier C:\Samples\Inbox C:\ProducerHub --compare C:\ProducerHub\logs\bench_prev.json

# Run copy mode with 4 workers (parallel extraction is deterministic and order-preserving)
producer-os copy C:\Samples\Inbox C:\ProducerHub --workers 4
```
