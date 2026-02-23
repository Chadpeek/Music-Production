"""Configuration management for Producer OS.

This module centralises all logic related to finding and loading
configuration files.  It supports both AppData and portable
installation modes, resolves the appropriate configuration
directory, and exposes helper functions to read/write JSON
configuration files with JSON schema validation.

The configuration is stored in a JSON file called ``config.json``.
Clients can override the configuration location by passing
``--portable`` to the CLI or by placing a ``portable.flag`` file
alongside the running script/binary.  Portable mode always
prioritises using files from the application directory.

When running in AppData mode on Windows the configuration lives
under ``%APPDATA%\ProducerOS``; on other systems the directory
defaults to ``$XDG_CONFIG_HOME/ProducerOS`` or ``~/.config/ProducerOS``.

JSON schema validation is performed using the built‑in
``jsonschema`` library if available; if validation fails a
``ValueError`` is raised with an explanation of the invalid
configuration.  When the schema cannot be loaded or ``jsonschema``
is unavailable, validation is skipped.

Example usage::

    from producer_os.config_service import ConfigService

    config_service = ConfigService(app_dir=Path(__file__).parent)
    cfg = config_service.load_config()
    cfg["inbox"] = "C:/Samples/Inbox"
    config_service.save_config(cfg)
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


def _get_appdata_root(app_name: str = "ProducerOS") -> Path:
    """Return the platform‑specific base directory for config files."""
    system = platform.system().lower()
    if system == "windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / app_name
        # Fallback to user profile
        return Path.home() / f"AppData/Roaming/{app_name}"
    # On Linux/macOS use XDG_CONFIG_HOME or ~/.config
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / app_name
    return Path.home() / ".config" / app_name


def _load_json(file_path: Path) -> Any:
    if not file_path.exists():
        return None
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(data: Any, file_path: Path) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _validate_json(data: Any, schema_path: Path) -> None:
    """Validate JSON against a schema if the jsonschema library is available."""
    if jsonschema is None:
        return
    try:
        schema = _load_json(schema_path)
        if schema:
            jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"Invalid configuration: {exc.message}")


@dataclass
class ConfigService:
    """Resolve and manage Producer OS configuration."""

    app_dir: Path
    portable_flag_filename: str = "portable.flag"
    config_filename: str = "config.json"
    styles_filename: str = "bucket_styles.json"
    schema_dirname: str = "schemas"
    schema_names: Tuple[str, str] = ("config.schema.json", "styles.schema.json")
    _cached_mode: Optional[bool] = field(default=None, init=False, repr=False)

    def _portable_flag_exists(self) -> bool:
        return (self.app_dir / self.portable_flag_filename).exists()

    def detect_mode(self, cli_portable: bool = False) -> bool:
        """Return ``True`` if portable mode should be used.

        Portable mode is selected if any of the following conditions hold:

        * A ``portable.flag`` file exists in the application directory.
        * ``cli_portable`` is truthy.

        This check is performed once per instance and cached so that
        subsequent calls return the same result.
        """
        if self._cached_mode is None:
            self._cached_mode = cli_portable or self._portable_flag_exists()
        return self._cached_mode

    def get_config_dir(self, cli_portable: bool = False) -> Path:
        """Return the resolved configuration directory."""
        if self.detect_mode(cli_portable=cli_portable):
            return self.app_dir
        return _get_appdata_root()

    def get_config_path(self, cli_portable: bool = False) -> Path:
        return self.get_config_dir(cli_portable) / self.config_filename

    def get_styles_path(self, cli_portable: bool = False) -> Path:
        return self.get_config_dir(cli_portable) / self.styles_filename

    def get_schema_path(self, schema_name: str) -> Path:
        return self.app_dir / self.schema_dirname / schema_name

    def load_config(self, cli_portable: bool = False) -> Dict[str, Any]:
        """Load configuration from the resolved path, validating against schema."""
        cfg_path = self.get_config_path(cli_portable)
        cfg: Dict[str, Any] = {}
        data = _load_json(cfg_path)
        if data is not None:
            cfg = data
        # Validate configuration
        schema_path = self.get_schema_path(self.schema_names[0])
        if schema_path.exists():
            try:
                _validate_json(cfg, schema_path)
            except ValueError as exc:
                # Provide a friendly message and default to empty config
                print(f"Warning: {exc}. Falling back to defaults.")
                cfg = {}
        return cfg

    def save_config(self, config: Dict[str, Any], cli_portable: bool = False) -> None:
        """Write configuration to disk, validating against the schema first."""
        schema_path = self.get_schema_path(self.schema_names[0])
        if schema_path.exists():
            _validate_json(config, schema_path)
        _save_json(config, self.get_config_path(cli_portable))

    def load_styles(self, cli_portable: bool = False) -> Dict[str, Any]:
        """Load bucket styles JSON with validation."""
        styles_path = self.get_styles_path(cli_portable)
        data = _load_json(styles_path) or {}
        schema_path = self.get_schema_path(self.schema_names[1])
        if schema_path.exists():
            try:
                _validate_json(data, schema_path)
            except ValueError as exc:
                print(f"Warning: {exc}. Ignoring invalid styles.")
                data = {}
        return data

    def save_styles(self, styles: Dict[str, Any], cli_portable: bool = False) -> None:
        """Save bucket styles to disk after validation."""
        schema_path = self.get_schema_path(self.schema_names[1])
        if schema_path.exists():
            _validate_json(styles, schema_path)
        _save_json(styles, self.get_styles_path(cli_portable))