"""Bucket renaming service for Producer OS.

Producer OS uses fixed bucket identifiers internally (e.g. ``"808s"``),
but allows users to rename buckets to customise the folder names that
appear in their hub directory.  The rename mapping is stored in a
``buckets.json`` file alongside the configuration and styles files.

The :class:`BucketService` loads this mapping and provides helper
methods to translate between internal bucket IDs and user‑visible
display names.  When no mapping is present for a given bucket ID
the ID itself is returned.  The mapping is case sensitive on the
identifier side but case insensitive when looking up display names.

Structure of ``buckets.json``::

    {
      "808s": "808",        # bucket ID -> display/folder name
      "Kicks": "Kicks",
      "Snares": "Snares",
      ...
    }

This file is validated against ``buckets.schema.json`` on load.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class BucketService:
    """Translate between internal bucket identifiers and display names."""

    mapping: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalise keys to ensure we always store original mapping
        # exactly as provided; values are preserved as given.
        self._inverse: Dict[str, str] = {}
        for bucket_id, display in self.mapping.items():
            self._inverse[display.lower()] = bucket_id

    def get_display_name(self, bucket_id: str) -> str:
        """Return the user‑visible display name for a bucket ID.

        If no mapping exists the bucket ID is returned unchanged.
        """
        return self.mapping.get(bucket_id, bucket_id)

    def get_bucket_id(self, display_name: str) -> Optional[str]:
        """Return the bucket ID for a given display name.

        Matching is case insensitive.  If the display name cannot be
        resolved to a known ID, ``None`` is returned.
        """
        return self._inverse.get(display_name.lower())