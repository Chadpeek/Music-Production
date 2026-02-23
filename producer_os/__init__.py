"""Top‑level package for Producer OS v2.

This package implements a portable, idempotent music pack organiser
focused on simplicity and safety.  The library exposes a GUI wizard
and a command‑line interface (CLI) that both call into the same
underlying engine.  The engine is responsible for scanning an
inbox directory, classifying samples into deterministic buckets,
moving or copying files into a structured hub directory, and
generating `.nfo` sidecar files to style folders in FL Studio.

The implementation is intentionally opinionated: it uses a small
set of bucket names and scoring heuristics and logs all decisions.
If you need more advanced behaviour you can extend the bucket
rules, adjust confidence thresholds or supply your own style
definitions via JSON.

Producer OS supports two configuration modes:

* **AppData mode (default)** – configuration files live under the
  user’s application data directory (e.g. ``%APPDATA%\ProducerOS`` on
  Windows or ``~/.config/ProducerOS`` on Linux/macOS).

* **Portable mode** – configuration files live in the same
  directory as the executable.  Portable mode is enabled either
  automatically when a ``portable.flag`` file sits next to the
  executable or explicitly via the ``--portable`` CLI flag.  The
  GUI wizard will also ask whether you want to enable portable
  storage on first run.

The public API surface consists of the following key classes and
functions:

* :class:`producer_os.config_service.ConfigService` – resolves the
  configuration directory, loads/saves configuration, and applies
  portable mode logic.
* :class:`producer_os.styles_service.StyleService` – loads
  ``bucket_styles.json``, performs case‑insensitive lookup with
  category fallback and a hard default, and writes `.nfo` files.
* :class:`producer_os.engine.ProducerOSEngine` – implements the
  core routing logic, classification, logging, undo support and
  repair operations.
* :mod:`producer_os.cli` – exposes a rich command‑line interface
  built on top of :class:`argparse` and backed by the engine.

You can import these symbols to integrate Producer OS into your
own applications, or run the included ``producer_os/cli.py``
module via ``python -m producer_os.cli`` to use the CLI.
"""

from .config_service import ConfigService  # noqa: F401
from .styles_service import StyleService  # noqa: F401
from .engine import ProducerOSEngine  # noqa: F401