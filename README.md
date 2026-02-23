# Producer OS v2

Producer OS is a safe‑by‑default music pack organiser designed for
producers.  It scans an inbox directory for sample packs, classifies
the files into deterministic buckets (808s, kicks, loops, MIDI, etc.),
and organises them into a structured hub directory ready to drop into
your DAW.  It writes `.nfo` sidecar files to style categories and
buckets in FL Studio, logs every action it takes, and provides an
undo capability.

This is a complete rewrite of the original Producer OS, focusing on
clean architecture, idempotency, portable mode support and an
open‑source friendly code base.  Both a GUI wizard and a command‑line
interface (CLI) are provided.

## Quickstart

1. Install the Python requirements (Python 3.8+ recommended)::

       pip install -r requirements.txt  # if provided

2. Generate some test data::

       python tests/generate_test_data.py --output ./sandbox

   This creates ``sandbox/inbox`` and ``sandbox/hub`` directories with
   dummy packs.

3. Run the CLI in analysis mode::

       python -m producer_os.cli analyze ./sandbox/inbox ./sandbox/hub

   You will see a JSON report summarising the classification of each
   file.  The CLI writes logs under ``hub/logs/<run_id>``.

4. Try a dry‑run or copy run::

       python -m producer_os.cli dry-run ./sandbox/inbox ./sandbox/hub
       python -m producer_os.cli copy ./sandbox/inbox ./sandbox/hub

   The hub directory will be populated with categories and buckets.

5. Undo the last move run::

       python -m producer_os.cli move ./sandbox/inbox ./sandbox/hub
       python -m producer_os.cli undo-last-run ./sandbox/inbox ./sandbox/hub

## Portable Mode

Producer OS supports two storage modes:

* **AppData mode (default)** – configuration files live in your user
  data directory, e.g. ``%APPDATA%\ProducerOS`` on Windows or
  ``~/.config/ProducerOS`` on Linux/macOS.

* **Portable mode** – configuration files live next to the
  executable.  Portable mode is enabled by creating a file named
  ``portable.flag`` in the hub directory or by passing
  ``--portable`` to the CLI.  The GUI wizard will also offer to
  enable portable mode on first run.

## Wizard

The GUI wizard guides new users through configuring Producer OS.
Although the wizard is not runnable in this headless environment, its
workflow is as follows:

1. **Inbox** – choose the inbox directory where new packs arrive.
2. **Hub** – choose the hub directory where organised samples live.
3. **Options** – pick the default run mode (analyze, dry‑run,
   copy, move), toggle overwriting of existing `.nfo` files, enable
   pack name normalization, edit ignore rules, toggle portable mode
   and reveal developer tools (verbose logging, scoring breakdown,
   debug JSON dump).
4. **Run** – review the summary and start organising.  A detailed
   report appears when finished.

All wizard actions correspond to CLI commands.  Preferences selected in
the wizard are stored in ``config.json`` using the same
configuration mechanism as the CLI.

## Command‑line Interface

The CLI exposes the same functionality as the wizard and can be used
for scripting or automation.  See ``python -m producer_os.cli --help``
for full usage.  The primary subcommands are:

| Command         | Description                                           |
|-----------------|-------------------------------------------------------|
| `analyze`       | Scan and classify packs, produce a report only        |
| `dry-run`       | Show what would happen without moving/copying files   |
| `copy`          | Copy files into the hub while preserving the inbox    |
| `move`          | Move files into the hub and record an audit trail     |
| `repair-styles` | Regenerate missing or misplaced `.nfo` files          |
| `preview-styles`| Reserved for future visual preview of styles          |
| `doctor`        | Reserved for self‑healing integrity checks            |
| `undo-last-run` | Undo the last move operation using the audit trail    |

All runs generate logs under ``hub/logs/<run_id>`` containing
``run_log.txt`` (human readable log), ``run_report.json`` (JSON
summary) and ``audit.csv`` (only for move runs).  You can inspect
these files to verify what happened.

## Bucket Styles

Producer OS uses ``bucket_styles.json`` to control the colour and
icon for each bucket and category.  The file lives in the
configuration directory (AppData or portable) and must have the
following shape:

```json
{
  "categories": {
    "Samples": {"Color": "$4863A0", "IconIndex": 10, "SortGroup": 0},
    ...
  },
  "buckets": {
    "808s": {"Color": "$CC0000", "IconIndex": 12, "SortGroup": 0},
    ...
  }
}
```

If a bucket is missing from the file a case‑insensitive lookup is
attempted and then the category style is used.  A hard default is
applied when no style is found.  The pack style reuses the bucket’s
colour and icon.

## Contributing

Contributions are welcome!  Please open a pull request or file an
issue if you encounter a bug or have a feature request.  See
``SUPPORT.md`` for more details on getting help.