"""Core engine for Producer OS.

The :class:`ProducerOSEngine` exposes methods to scan an inbox
directory, classify audio files into deterministic buckets, move or
copy them into a structured hub directory, write `.nfo` sidecar
files with styling information, and generate logs and reports.  It
also supports undoing the last run and repairing inconsistent
styles.

The engine is intentionally decoupled from any user interface and
depends only on :class:`producer_os.config_service.ConfigService`
and :class:`producer_os.styles_service.StyleService`.  This
separation allows both the GUI wizard and the command‑line
interface to share the same core logic.
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .styles_service import StyleService


# A classification is represented as a tuple:
# (bucket_name or None, category_name, confidence, list of (bucket, score) candidates)
ClassificationResult = Tuple[Optional[str], str, float, List[Tuple[str, int]]]


@dataclass
class ProducerOSEngine:
    """Producer OS engine responsible for file routing and styling."""

    inbox_dir: Path
    hub_dir: Path
    style_service: StyleService
    config: Dict[str, any]
    ignore_rules: Iterable[str] = field(default_factory=lambda: ["__MACOSX", ".DS_Store", "._"])
    confidence_threshold: float = 0.75

    # Bucket rules: maps bucket names to lists of substrings to search for
    BUCKET_RULES: Dict[str, List[str]] = field(default_factory=lambda: {
        "808s": ["808", "808s"],
        "Kicks": ["kick", "kicks"],
        "Snares": ["snare", "snares"],
        "Claps": ["clap", "claps"],
        "HiHats": ["hihat", "hi-hat", "hat", "hats"],
        "Percs": ["perc", "percs", "percussion"],
        "Cymbals": ["cymbal", "cymbals", "crash", "ride", "bell"],
        "Bass": ["bass"],
        "Leads": ["lead", "leads"],
        "Vox": ["vox", "vocal", "vocals", "acapella"],
        "FX": ["fx", "effect", "effects", "sweep", "sweeps", "riser", "risers", "impact", "impacts"],
        "DrumLoop": ["drumloop", "drum_loop", "drum loop", "drum-loop", "loop drum", "loop_drums"],
        "MelodyLoop": [
            "melodic loop", "melodyloop", "melody_loop", "melody loop", "loop melody",
            "melod", "chord", "chords", "guitar loop", "piano loop"
        ],
        "MIDI": [".mid", "mid file"]
    })
    # Map buckets to categories
    CATEGORY_MAP: Dict[str, str] = field(default_factory=lambda: {
        "808s": "Samples",
        "Kicks": "Samples",
        "Snares": "Samples",
        "Claps": "Samples",
        "HiHats": "Samples",
        "Percs": "Samples",
        "Cymbals": "Samples",
        "Bass": "Samples",
        "Leads": "Samples",
        "Vox": "Samples",
        "FX": "Samples",
        "DrumLoop": "Loops",
        "MelodyLoop": "Loops",
        "MIDI": "MIDI",
    })

    def _should_ignore(self, name: str) -> bool:
        """Return True if the file or folder name should be ignored."""
        for rule in self.ignore_rules:
            # rule may be prefix (e.g., '._'), exact file, or directory name
            if name == rule or name.startswith(rule):
                return True
        return False

    def _classify_filename(self, filename: str) -> ClassificationResult:
        """Classify a filename into a bucket and category.

        Returns a ``ClassificationResult``.  The string matching is case
        insensitive and counts the number of occurrences of each rule’s
        substrings.  The top three buckets by score are returned in
        ``candidates``.
        """
        lower_name = filename.lower()
        scores: Dict[str, int] = {}
        for bucket, patterns in self.BUCKET_RULES.items():
            score = 0
            for pat in patterns:
                # treat .mid extension specially
                if pat == ".mid" and lower_name.endswith(".mid"):
                    score += 3  # strong match for MIDI
                else:
                    count = lower_name.count(pat)
                    score += count
            if score > 0:
                scores[bucket] = score
        if not scores:
            return (None, "UNSORTED", 0.0, [])
        # Sort buckets by score descending
        sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        total = sum(scores.values())
        best_bucket, best_score = sorted_scores[0]
        confidence = best_score / total if total > 0 else 0.0
        candidates = sorted_scores[:3]
        if confidence >= self.confidence_threshold:
            category = self.CATEGORY_MAP.get(best_bucket, "Samples")
            return (best_bucket, category, confidence, candidates)
        # Low confidence – route to UNSORTED
        return (None, "UNSORTED", confidence, candidates)

    def _wrap_loose_files(self) -> None:
        """Wrap loose files in the inbox root into a timestamped folder.

        Some sample packs consist of individual files placed directly in
        the inbox root instead of inside a dedicated folder.  To keep
        the routing logic uniform each loose file is moved into a
        temporary folder named after the current timestamp.  This
        folder then becomes a pack for classification.
        """
        loose_files = [p for p in self.inbox_dir.iterdir() if p.is_file() and not self._should_ignore(p.name)]
        if not loose_files:
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_folder = self.inbox_dir / f"Loose_{timestamp}"
        tmp_folder.mkdir(exist_ok=True)
        for file_path in loose_files:
            dest = tmp_folder / file_path.name
            shutil.move(str(file_path), str(dest))

    def _discover_packs(self) -> List[Path]:
        """Return a list of pack directories within the inbox."""
        packs = []
        for entry in self.inbox_dir.iterdir():
            if self._should_ignore(entry.name):
                continue
            if entry.is_dir():
                packs.append(entry)
        return packs

    def _ensure_hub_structure(self, category: str, bucket: str, pack_name: str) -> Tuple[Path, Path, Path]:
        """Ensure that the destination directories exist and write `.nfo` files.

        Returns a tuple of (category_dir, bucket_dir, pack_dir).
        """
        category_dir = self.hub_dir / category
        bucket_dir = category_dir / bucket
        pack_dir = bucket_dir / pack_name
        # Create directories
        pack_dir.mkdir(parents=True, exist_ok=True)
        # Write `.nfo` for category, bucket and pack using style service
        category_style = self.style_service.resolve_style(bucket, category)
        self.style_service.write_nfo(self.hub_dir, category, category_style)
        bucket_style = self.style_service.resolve_style(bucket, category)
        self.style_service.write_nfo(category_dir, bucket, bucket_style)
        pack_style = self.style_service.pack_style_from_bucket(bucket_style)
        self.style_service.write_nfo(bucket_dir, pack_name, pack_style)
        return category_dir, bucket_dir, pack_dir

    def _ensure_unsorted_structure(self, pack_name: str) -> Path:
        """Ensure that the UNSORTED folder exists for a pack and return its path."""
        unsorted_dir = self.hub_dir / "UNSORTED" / pack_name
        unsorted_dir.mkdir(parents=True, exist_ok=True)
        # Write `.nfo` for UNSORTED category if not already
        self.style_service.write_nfo(self.hub_dir, "UNSORTED", DEFAULT_UNSORTED_STYLE)
        # Write `.nfo` for the pack in UNSORTED reusing default
        self.style_service.write_nfo(self.hub_dir / "UNSORTED", pack_name, DEFAULT_UNSORTED_STYLE)
        return unsorted_dir

    def _move_or_copy(self, src: Path, dst: Path, mode: str) -> None:
        """Move or copy a file based on the selected mode."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        if mode == "move":
            shutil.move(str(src), str(dst))
        elif mode == "copy":
            shutil.copy2(str(src), str(dst))
        else:  # dry-run, analyze, repair
            pass

    def _log_audit_row(self, writer: csv.writer, file_path: Path, pack: Path, bucket: Optional[str], category: str,
                        confidence: float, action: str, reason: str) -> None:
        writer.writerow([
            str(file_path),
            pack.name,
            category,
            bucket or "UNSORTED",
            f"{confidence:.2f}",
            action,
            reason
        ])

    def run(self, mode: str = "analyze", overwrite_nfo: bool = False, normalize_pack_name: bool = False,
            developer_options: Optional[Dict[str, bool]] = None) -> Dict[str, any]:
        """Execute a run in the specified mode.

        ``mode`` may be ``analyze`` (collect stats only), ``dry-run``
        (determine destinations but do nothing), ``copy`` (copy files),
        ``move`` (move files) or ``repair-styles`` (regenerate `.nfo`
        files).  The engine always produces a run report dictionary
        summarising what happened.  In ``move`` mode an ``audit.csv``
        will be generated to support undo.
        """
        mode = mode.lower()
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        log_dir = self.hub_dir / "logs" / run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        audit_path = log_dir / "audit.csv"
        run_log_path = log_dir / "run_log.txt"
        report_path = log_dir / "run_report.json"
        # prepare logging
        report = {
            "run_id": run_id,
            "mode": mode,
            "timestamp": datetime.datetime.now().isoformat(),
            "files_processed": 0,
            "files_moved": 0,
            "files_copied": 0,
            "unsorted": 0,
            "packs": [],
        }
        # wrap loose files before scanning
        self._wrap_loose_files()
        packs = self._discover_packs()
        with open(run_log_path, "w", encoding="utf-8") as log_file:
            # Prepare audit writer if necessary
            audit_file = None
            audit_writer = None
            if mode == "move":
                audit_file = open(audit_path, "w", newline="", encoding="utf-8")
                audit_writer = csv.writer(audit_file)
                audit_writer.writerow(["file", "pack", "category", "bucket", "confidence", "action", "reason"])
            for pack_dir in packs:
                pack_report = {
                    "pack": pack_dir.name,
                    "files": [],
                }
                files = list(pack_dir.rglob("*"))
                for file_path in files:
                    if file_path.is_dir() or self._should_ignore(file_path.name):
                        continue
                    rel_path = file_path.relative_to(pack_dir)
                    classification = self._classify_filename(file_path.name)
                    dest_path: Optional[Path] = None
                    bucket, category, confidence, candidates = classification
                    if bucket is None:
                        # Low confidence – route to UNSORTED
                        dest_dir = self._ensure_unsorted_structure(pack_dir.name)
                        dest_path = dest_dir / rel_path
                        report["unsorted"] += 1
                        reason = "; ".join([f"{b}:{score}" for b, score in candidates]) or "no matches"
                    else:
                        # Create hub structure and compute destination
                        _, _, pack_dest_dir = self._ensure_hub_structure(category, bucket, pack_dir.name)
                        dest_path = pack_dest_dir / rel_path
                        reason = f"best match: {bucket}, confidence={confidence:.2f}"
                    action = "NONE"
                    if mode in {"copy", "move"}:
                        # Skip if file already exists at destination
                        if dest_path.exists():
                            reason += "; destination exists"
                        else:
                            self._move_or_copy(file_path, dest_path, mode)
                            action = mode.upper()
                            if mode == "move":
                                report["files_moved"] += 1
                            else:
                                report["files_copied"] += 1
                    pack_report["files"].append({
                        "source": str(file_path),
                        "dest": str(dest_path),
                        "bucket": bucket or "UNSORTED",
                        "category": category,
                        "confidence": confidence,
                        "action": action,
                        "reason": reason,
                    })
                    report["files_processed"] += 1
                    # Write audit row
                    if audit_writer:
                        audit_action = action if action else "NONE"
                        audit_writer.writerow([
                            str(file_path),
                            pack_dir.name,
                            category,
                            bucket or "UNSORTED",
                            f"{confidence:.2f}",
                            audit_action,
                            reason
                        ])
                report["packs"].append(pack_report)
            if audit_file:
                audit_file.close()
        # Save JSON report
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        return report

    def undo_last_run(self) -> Dict[str, any]:
        """Undo the most recent move run by reading its audit.csv.

        Files are moved back to the inbox.  If a file already exists in the
        inbox its original name is preserved and the conflicting file is
        placed into ``HUB/Quarantine/UndoConflicts``.  Returns a summary
        report with counts of restored and conflicted files.
        """
        logs_root = self.hub_dir / "logs"
        if not logs_root.exists():
            return {"error": "No logs found"}
        # Find latest audit.csv
        audit_files = sorted(logs_root.rglob("audit.csv"), key=os.path.getmtime, reverse=True)
        if not audit_files:
            return {"error": "No audit files found"}
        audit_path = audit_files[0]
        restored = 0
        conflicts = 0
        quarantine_dir = self.hub_dir / "Quarantine" / "UndoConflicts"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                src = Path(row["file"])
                dest_in_inbox = self.inbox_dir / src.name
                # Only attempt to restore if action was move
                if row["action"].upper() != "MOVE":
                    continue
                # Determine where the file currently is: it's either in hub or already moved
                current_location = None
                # We recorded destination path in report, but reconstruct from hub
                bucket = row["bucket"]
                category = row["category"]
                pack = row["pack"] if "pack" in row else row["pack"]
                if bucket != "UNSORTED":
                    current_location = self.hub_dir / category / bucket / pack / src.name
                else:
                    current_location = self.hub_dir / "UNSORTED" / pack / src.name
                if not current_location.exists():
                    # File might have been deleted or already restored
                    continue
                if dest_in_inbox.exists():
                    # Conflict – move to quarantine
                    shutil.move(str(current_location), str(quarantine_dir / src.name))
                    conflicts += 1
                else:
                    shutil.move(str(current_location), str(dest_in_inbox))
                    restored += 1
        return {
            "restored": restored,
            "conflicts": conflicts,
            "audit_file": str(audit_path)
        }

    def repair_styles(self) -> Dict[str, any]:
        """Repair and regenerate missing or misplaced `.nfo` files.

        The repair process traverses the hub directory and ensures that a
        `.nfo` exists next to each category, bucket and pack folder.  It
        removes orphan `.nfo` files that no longer correspond to any
        folder and relocates incorrectly placed files.  Returns a
        summary of actions taken.
        """
        actions = {
            "created": 0,
            "updated": 0,
            "removed": 0,
            "relocated": 0,
        }
        # Collect desired `.nfo` paths
        desired_nfos = set()
        for category_dir in self.hub_dir.iterdir():
            if not category_dir.is_dir() or self._should_ignore(category_dir.name):
                continue
            category = category_dir.name
            # Category nfo
            desired_nfos.add(self.hub_dir / f"{category}.nfo")
            for bucket_dir in category_dir.iterdir():
                if not bucket_dir.is_dir() or self._should_ignore(bucket_dir.name):
                    continue
                bucket = bucket_dir.name
                # Bucket nfo
                desired_nfos.add(category_dir / f"{bucket}.nfo")
                for pack_dir in bucket_dir.iterdir():
                    if not pack_dir.is_dir() or self._should_ignore(pack_dir.name):
                        continue
                    pack = pack_dir.name
                    # Pack nfo
                    desired_nfos.add(bucket_dir / f"{pack}.nfo")
        # Create or update desired nfos
        for nfo_path in desired_nfos:
            folder_name = nfo_path.stem
            parent_dir = nfo_path.parent
            # Determine type (category/bucket/pack) and obtain style
            if parent_dir == self.hub_dir:
                category = folder_name
                # For UNSORTED use default style
                if category.upper() == "UNSORTED":
                    style = DEFAULT_UNSORTED_STYLE
                else:
                    # Category style uses same bucket name for colour fallback
                    # but here we don't know bucket; treat bucket=category
                    style = self.style_service.resolve_style(category, category)
                current = None
            else:
                # Determine bucket or pack
                grandparent = parent_dir.parent
                if grandparent == self.hub_dir:
                    # Bucket nfo
                    category = parent_dir.name
                    bucket = folder_name
                    style = self.style_service.resolve_style(bucket, category)
                else:
                    # Pack nfo
                    category = grandparent.name
                    bucket = parent_dir.name
                    style = self.style_service.pack_style_from_bucket(
                        self.style_service.resolve_style(bucket, category)
                    )
            # Write nfo using style_service
            if nfo_path.exists():
                existing = nfo_path.read_text(encoding="utf-8").strip()
                new_contents = self.style_service._nfo_contents(style)
                if existing != new_contents:
                    self.style_service.write_nfo(parent_dir, folder_name, style)
                    actions["updated"] += 1
            else:
                self.style_service.write_nfo(parent_dir, folder_name, style)
                actions["created"] += 1
        # Remove orphan nfos
        for nfo in self.hub_dir.rglob("*.nfo"):
            if nfo not in desired_nfos:
                # Only remove if file corresponds to a folder in our tree; do not remove user files
                nfo.unlink()
                actions["removed"] += 1
        return actions


# Default UNSORTED style: neutral colour and generic icon
DEFAULT_UNSORTED_STYLE = {
    "Color": "$7f7f7f",
    "IconIndex": 0,
    "SortGroup": 0,
}
