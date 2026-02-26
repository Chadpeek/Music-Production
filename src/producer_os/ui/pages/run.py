from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from producer_os.ui.pages.base import BaseWizardPage
from producer_os.ui.widgets import StatChip, StatusBadge, set_widget_role

_TOKEN_SPLIT_RE = re.compile(r"[ _-]+")


class RunPage(BaseWizardPage):
    analyzeRequested = Signal()
    runRequested = Signal()
    saveReportRequested = Signal()
    hintSaveRequested = Signal(str, str, str, str)  # source, kind(filename|folder), bucket, token

    def __init__(self, action: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            "Step 4 - Run & Review",
            "Analyze or execute routing, then inspect the results, review low-confidence items, and export the run report.",
            parent,
        )
        self._action_name = action
        self._report: dict[str, Any] = {}
        self._rows_all: list[dict[str, Any]] = []
        self._row_index_by_source: dict[str, dict[str, Any]] = {}
        self._bucket_choices: list[str] = []
        self._manual_overrides: dict[str, dict[str, Any]] = {}
        self._saved_hints: list[dict[str, Any]] = []
        self._preview_stale = False

        action_card = self.add_card("Execution Controls", "Run an analysis first to verify routing before moving files.")
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)

        self.analyze_btn = QPushButton("Analyze")
        set_widget_role(self.analyze_btn, "ghost")
        self.analyze_btn.clicked.connect(self.analyzeRequested.emit)
        action_row.addWidget(self.analyze_btn)

        self.run_btn = QPushButton(self._run_button_label(action))
        set_widget_role(self.run_btn, "primary")
        self.run_btn.clicked.connect(self.runRequested.emit)
        action_row.addWidget(self.run_btn)
        action_row.addStretch(1)

        self.status_badge = StatusBadge("Ready")
        action_row.addWidget(self.status_badge)
        action_card.body_layout.addLayout(action_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        action_card.body_layout.addWidget(self.progress_bar)

        review_card = self.add_card("Results Review", "Summary, pack breakdown, and low-confidence review queue.")
        self.tabs = QTabWidget()
        review_card.body_layout.addWidget(self.tabs)

        self._build_summary_tab()
        self._build_review_tab()
        self._build_preview_tab()

        export_card = self.add_card("Report Export")
        export_row = QHBoxLayout()
        export_row.setContentsMargins(0, 0, 0, 0)
        export_row.setSpacing(8)
        self.save_report_btn = QPushButton("Save run report...")
        set_widget_role(self.save_report_btn, "ghost")
        self.save_report_btn.setVisible(False)
        self.save_report_btn.clicked.connect(self.saveReportRequested.emit)
        export_row.addWidget(self.save_report_btn)

        self.review_feedback_label = QLabel("")
        self.review_feedback_label.setObjectName("MutedLabel")
        self.review_feedback_label.setWordWrap(True)
        export_row.addWidget(self.review_feedback_label, 1)
        export_card.body_layout.addLayout(export_row)

    # ------------------------------------------------------------------
    # UI construction
    def _build_summary_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        stats = QHBoxLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(10)
        self.processed_chip = StatChip("Processed", "0")
        self.moved_chip = StatChip("Moved", "0")
        self.copied_chip = StatChip("Copied", "0")
        self.unsorted_chip = StatChip("Unsorted", "0")
        for chip in (self.processed_chip, self.moved_chip, self.copied_chip, self.unsorted_chip):
            stats.addWidget(chip)
        layout.addLayout(stats)

        self.summary_label = QLabel("No results yet.")
        self.summary_label.setObjectName("MutedLabel")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.log_edit = QTextEdit()
        self.log_edit.setObjectName("LogOutput")
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(240)
        layout.addWidget(self.log_edit, 1)

        self.tabs.addTab(tab, "Summary")

    def _build_review_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)
        self.review_search = QLineEdit()
        self.review_search.setPlaceholderText("Filter by file, pack, bucket, or token...")
        self.review_search.textChanged.connect(self._apply_review_filters)
        filter_row.addWidget(self.review_search, 1)

        self.review_bucket_filter = QComboBox()
        self.review_bucket_filter.addItem("All buckets")
        self.review_bucket_filter.currentTextChanged.connect(self._apply_review_filters)
        filter_row.addWidget(self.review_bucket_filter)

        self.review_pack_filter = QComboBox()
        self.review_pack_filter.addItem("All packs")
        self.review_pack_filter.currentTextChanged.connect(self._apply_review_filters)
        filter_row.addWidget(self.review_pack_filter)

        self.review_low_only = QCheckBox("Low confidence only")
        self.review_low_only.setChecked(True)
        self.review_low_only.toggled.connect(self._apply_review_filters)
        filter_row.addWidget(self.review_low_only)
        layout.addLayout(filter_row)

        self.review_count_label = QLabel("No review rows.")
        self.review_count_label.setObjectName("MutedLabel")
        self.review_count_label.setWordWrap(True)
        layout.addWidget(self.review_count_label)

        self.review_table = QTableWidget(0, 8)
        self.review_table.setHorizontalHeaderLabels(
            ["Pack", "File", "Chosen", "Confidence", "Margin", "Top 3", "Override", "Hint Action"]
        )
        self.review_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.review_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.review_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.review_table.setAlternatingRowColors(True)
        self.review_table.setSortingEnabled(True)
        header = self.review_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, header.ResizeMode.Stretch)
        header.setSectionResizeMode(6, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, header.ResizeMode.ResizeToContents)
        self.review_table.itemSelectionChanged.connect(self._update_review_details)
        layout.addWidget(self.review_table, 1)

        self.review_details = QTextEdit()
        self.review_details.setObjectName("LogOutput")
        self.review_details.setReadOnly(True)
        self.review_details.setMinimumHeight(180)
        layout.addWidget(self.review_details)

        self.tabs.addTab(tab, "Low Confidence Review")

    def _build_preview_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.preview_stale_label = QLabel("Preview reflects the latest analyze/dry-run report.")
        self.preview_stale_label.setObjectName("MutedLabel")
        self.preview_stale_label.setWordWrap(True)
        layout.addWidget(self.preview_stale_label)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(8)
        self.preview_search = QLineEdit()
        self.preview_search.setPlaceholderText("Filter preview by file, pack, bucket, destination...")
        self.preview_search.textChanged.connect(self._apply_preview_filters)
        filter_row.addWidget(self.preview_search, 1)

        self.preview_bucket_filter = QComboBox()
        self.preview_bucket_filter.addItem("All buckets")
        self.preview_bucket_filter.currentTextChanged.connect(self._apply_preview_filters)
        filter_row.addWidget(self.preview_bucket_filter)

        self.preview_pack_filter = QComboBox()
        self.preview_pack_filter.addItem("All packs")
        self.preview_pack_filter.currentTextChanged.connect(self._apply_preview_filters)
        filter_row.addWidget(self.preview_pack_filter)

        self.preview_low_only = QCheckBox("Low confidence only")
        self.preview_low_only.setChecked(False)
        self.preview_low_only.toggled.connect(self._apply_preview_filters)
        filter_row.addWidget(self.preview_low_only)

        self.preview_changed_only = QCheckBox("Changed by user only")
        self.preview_changed_only.setChecked(False)
        self.preview_changed_only.toggled.connect(self._apply_preview_filters)
        filter_row.addWidget(self.preview_changed_only)
        layout.addLayout(filter_row)

        self.preview_table = QTableWidget(0, 8)
        self.preview_table.setHorizontalHeaderLabels(
            ["Pack", "File", "Bucket", "Category", "Action", "Low?", "Destination Preview", "Source"]
        )
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setSortingEnabled(True)
        header = self.preview_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, header.ResizeMode.Stretch)
        header.setSectionResizeMode(7, header.ResizeMode.Stretch)
        layout.addWidget(self.preview_table, 1)

        self.tabs.addTab(tab, "Apply Preview")

    # ------------------------------------------------------------------
    # Public page API
    def _run_button_label(self, action: str) -> str:
        action = action.lower().strip()
        return "Run (Move)" if action == "move" else "Run (Copy)"

    def set_action(self, action: str) -> None:
        self._action_name = action
        self.run_btn.setText(self._run_button_label(action))

    def set_busy(self, busy: bool, mode: str | None = None) -> None:
        self.analyze_btn.setEnabled(not busy)
        self.run_btn.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        self.progress_bar.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress_bar.setValue(0)
            self.status_badge.set_status("Ready", kind="neutral", pulsing=False)
            self._update_preview_stale_state()
            return
        if mode == "analyze":
            self.status_badge.set_status("Analyzing", kind="analyzing", pulsing=True)
        else:
            self.status_badge.set_status("Running", kind="running", pulsing=True)

    def clear_results(self) -> None:
        for chip in (self.processed_chip, self.moved_chip, self.copied_chip, self.unsorted_chip):
            chip.set_value("0")
        self.summary_label.setText("Working...")
        self.log_edit.clear()
        self.review_details.clear()
        self.review_count_label.setText("No review rows.")
        self.review_table.setRowCount(0)
        self.preview_table.setRowCount(0)
        self._report = {}
        self._rows_all = []
        self._row_index_by_source = {}
        self._manual_overrides = {}
        self._saved_hints = []
        self._preview_stale = False
        self.review_feedback_label.setText("")
        self.save_report_btn.setVisible(False)
        self.status_badge.set_status("Working", kind="running", pulsing=True)
        self.preview_stale_label.setText("Preview reflects the latest analyze/dry-run report.")

    def set_results(self, report: dict, log_lines: list[str], bucket_choices: Optional[list[str]] = None) -> None:
        self._report = dict(report or {})
        self.processed_chip.set_value(str(self._report.get("files_processed", 0)))
        self.moved_chip.set_value(str(self._report.get("files_moved", 0)))
        self.copied_chip.set_value(str(self._report.get("files_copied", 0)))
        self.unsorted_chip.set_value(str(self._report.get("unsorted", 0)))

        self._rows_all = self._flatten_rows(self._report)
        self._row_index_by_source = {str(row.get("source", "")): row for row in self._rows_all}

        self._bucket_choices = sorted(
            set(bucket_choices or [])
            | {str(row.get("chosen_bucket", "")) for row in self._rows_all if row.get("chosen_bucket")}
            | {
                str(c.get("bucket"))
                for row in self._rows_all
                for c in (row.get("top_3_candidates") or [])
                if isinstance(c, dict) and c.get("bucket")
            }
        )
        self._refresh_bucket_filter()
        self._refresh_pack_filters()

        self._update_summary_label()
        self._rebuild_pack_breakdown()
        self._apply_review_filters()
        self._apply_preview_filters()
        self._update_preview_stale_state()
        self.save_report_btn.setVisible(True)
        self.status_badge.set_status("Completed", kind="success", pulsing=False)

    def get_manual_review_overlay(self) -> dict[str, Any]:
        if not self._manual_overrides and not self._saved_hints:
            return {}
        return {
            "version": 1,
            "overrides": sorted(
                self._manual_overrides.values(),
                key=lambda row: (str(row.get("source", "")), str(row.get("override_bucket", ""))),
            ),
            "saved_hints": list(self._saved_hints),
        }

    def set_review_feedback(self, message: str, success: bool = True) -> None:
        self.review_feedback_label.setText(message)
        self.review_feedback_label.setProperty("state", "success" if success else "warning")

    def record_saved_hint(self, source: str, kind: str, bucket: str, token: str) -> None:
        entry = {
            "kind": kind,
            "bucket": bucket,
            "token": token,
            "source": source,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        if any(
            h.get("kind") == kind and h.get("bucket") == bucket and h.get("token") == token and h.get("source") == source
            for h in self._saved_hints
        ):
            return
        self._saved_hints.append(entry)
        self._preview_stale = True
        self._apply_preview_filters()
        self._update_summary_label()
        self._update_preview_stale_state()

    # ------------------------------------------------------------------
    # Review row handling
    def _flatten_rows(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for pack in report.get("packs", []) or []:
            pack_name = str((pack or {}).get("pack", ""))
            for f in (pack or {}).get("files", []) or []:
                if not isinstance(f, dict):
                    continue
                source = str(f.get("source", ""))
                chosen = str(f.get("chosen_bucket") or f.get("bucket") or "UNSORTED")
                row = {
                    "pack": pack_name,
                    "source": source,
                    "dest": str(f.get("dest", "")),
                    "category": str(f.get("category", "")),
                    "action": str(f.get("action", "")),
                    "file": Path(source).name if source else "",
                    "chosen_bucket": chosen,
                    "original_bucket": chosen,
                    "override_bucket": "",
                    "effective_bucket": chosen,
                    "confidence_ratio": float(f.get("confidence_ratio", f.get("confidence", 0.0)) or 0.0),
                    "confidence_margin": float(f.get("confidence_margin", 0.0) or 0.0),
                    "low_confidence": bool(f.get("low_confidence", False)),
                    "top_3_candidates": list(f.get("top_3_candidates") or f.get("top_candidates") or []),
                    "folder_matches": list(f.get("folder_matches") or []),
                    "filename_matches": list(f.get("filename_matches") or []),
                    "audio_summary": dict(f.get("audio_summary") or {}),
                    "pitch_summary": dict(f.get("pitch_summary") or {}),
                    "glide_summary": dict(f.get("glide_summary") or {}),
                }
                rows.append(row)
        return rows

    def _refresh_bucket_filter(self) -> None:
        for combo, all_label in (
            (self.review_bucket_filter, "All buckets"),
            (self.preview_bucket_filter, "All buckets"),
        ):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label)
            for bucket in self._bucket_choices:
                if bucket:
                    combo.addItem(bucket)
            idx = combo.findText(current)
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)

    def _refresh_pack_filters(self) -> None:
        packs = sorted({str(row.get("pack", "")) for row in self._rows_all if row.get("pack")})
        for combo, all_label in ((self.review_pack_filter, "All packs"), (self.preview_pack_filter, "All packs")):
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label)
            for pack in packs:
                combo.addItem(pack)
            idx = combo.findText(current)
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)

    def _apply_review_filters(self) -> None:
        query = (self.review_search.text() or "").strip().lower()
        bucket_filter = self.review_bucket_filter.currentText()
        pack_filter = self.review_pack_filter.currentText()
        low_only = self.review_low_only.isChecked()

        filtered: list[dict[str, Any]] = []
        for row in self._rows_all:
            effective_bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")
            if low_only and not bool(row.get("low_confidence", False)):
                continue
            if bucket_filter and bucket_filter != "All buckets" and effective_bucket != bucket_filter:
                continue
            if pack_filter and pack_filter != "All packs" and str(row.get("pack", "")) != pack_filter:
                continue
            if query:
                hay = " ".join(
                    [
                        str(row.get("pack", "")),
                        str(row.get("file", "")),
                        str(row.get("chosen_bucket", "")),
                        str(effective_bucket),
                        " ".join(str(c.get("bucket", "")) for c in (row.get("top_3_candidates") or []) if isinstance(c, dict)),
                    ]
                ).lower()
                if query not in hay:
                    continue
            filtered.append(row)

        self._render_review_table(filtered)
        total = len(self._rows_all)
        low_total = sum(1 for r in self._rows_all if bool(r.get("low_confidence", False)))
        self.review_count_label.setText(
            f"Showing {len(filtered)} row(s). Total files: {total}. Low-confidence: {low_total}. "
            f"Manual overrides: {len(self._manual_overrides)}."
        )

    def _apply_preview_filters(self) -> None:
        query = (self.preview_search.text() or "").strip().lower()
        bucket_filter = self.preview_bucket_filter.currentText()
        pack_filter = self.preview_pack_filter.currentText()
        low_only = self.preview_low_only.isChecked()
        changed_only = self.preview_changed_only.isChecked()

        rows: list[dict[str, Any]] = []
        for row in self._rows_all:
            source = str(row.get("source", ""))
            effective_bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")
            if low_only and not bool(row.get("low_confidence", False)):
                continue
            if changed_only and source not in self._manual_overrides:
                continue
            if bucket_filter and bucket_filter != "All buckets" and effective_bucket != bucket_filter:
                continue
            if pack_filter and pack_filter != "All packs" and str(row.get("pack", "")) != pack_filter:
                continue
            if query:
                hay = " ".join(
                    [
                        str(row.get("pack", "")),
                        str(row.get("file", "")),
                        effective_bucket,
                        str(row.get("category", "")),
                        str(row.get("dest", "")),
                        str(row.get("source", "")),
                    ]
                ).lower()
                if query not in hay:
                    continue
            rows.append(row)

        self._render_preview_table(rows)

    def _render_review_table(self, rows: list[dict[str, Any]]) -> None:
        self.review_table.setSortingEnabled(False)
        self.review_table.setRowCount(0)

        for row in rows:
            r = self.review_table.rowCount()
            self.review_table.insertRow(r)
            source = str(row.get("source", ""))
            effective_bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")

            items = [
                QTableWidgetItem(str(row.get("pack", ""))),
                QTableWidgetItem(str(row.get("file", ""))),
                QTableWidgetItem(effective_bucket),
                QTableWidgetItem(f"{float(row.get('confidence_ratio', 0.0) or 0.0):.4f}"),
                QTableWidgetItem(f"{float(row.get('confidence_margin', 0.0) or 0.0):.2f}"),
                QTableWidgetItem(self._top3_text(row)),
            ]
            for col, item in enumerate(items):
                item.setData(Qt.ItemDataRole.UserRole, source)
                if bool(row.get("low_confidence", False)):
                    item.setData(Qt.ItemDataRole.UserRole + 1, True)
                self.review_table.setItem(r, col, item)

            override_combo = QComboBox()
            override_combo.addItems(self._bucket_choices or [effective_bucket])
            combo_bucket = effective_bucket
            idx = override_combo.findText(combo_bucket)
            override_combo.setCurrentIndex(max(0, idx))
            override_combo.setProperty("source", source)
            override_combo.currentTextChanged.connect(
                lambda value, cb=override_combo: self._on_override_combo_changed(str(cb.property("source") or ""), value)
            )
            self.review_table.setCellWidget(r, 6, override_combo)

            hint_btn = QPushButton("Hints…")
            set_widget_role(hint_btn, "ghost")
            hint_btn.setProperty("source", source)
            hint_btn.clicked.connect(lambda _=False, btn=hint_btn: self._open_hint_menu(btn))
            self.review_table.setCellWidget(r, 7, hint_btn)

        self.review_table.setSortingEnabled(True)
        if self.review_table.rowCount() > 0:
            self.review_table.selectRow(0)
        else:
            self.review_details.setPlainText("No rows match the current filter.")

    def _render_preview_table(self, rows: list[dict[str, Any]]) -> None:
        self.preview_table.setSortingEnabled(False)
        self.preview_table.setRowCount(0)
        for row in rows:
            r = self.preview_table.rowCount()
            self.preview_table.insertRow(r)
            effective_bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")
            low_conf = bool(row.get("low_confidence", False))
            values = [
                str(row.get("pack", "")),
                str(row.get("file", "")),
                effective_bucket,
                str(row.get("category", "")),
                str(row.get("action", "")),
                "yes" if low_conf else "no",
                str(row.get("dest", "")),
                str(row.get("source", "")),
            ]
            for c, value in enumerate(values):
                self.preview_table.setItem(r, c, QTableWidgetItem(value))
        self.preview_table.setSortingEnabled(True)

    def _update_preview_stale_state(self) -> None:
        self._preview_stale = bool(self._manual_overrides or self._saved_hints)
        if self._preview_stale:
            self.preview_stale_label.setText(
                "Preview is stale because manual overrides or saved hints changed after the last analyze/run. "
                "Rerun Analyze before copy/move."
            )
            if not self.progress_bar.isVisible():
                self.run_btn.setEnabled(False)
        else:
            self.preview_stale_label.setText("Preview reflects the latest analyze/dry-run report.")
            if not self.progress_bar.isVisible():
                self.run_btn.setEnabled(True)

    def _top3_text(self, row: dict[str, Any]) -> str:
        parts: list[str] = []
        for c in row.get("top_3_candidates", []) or []:
            if not isinstance(c, dict):
                continue
            parts.append(f"{c.get('bucket')}={float(c.get('score', 0.0) or 0.0):.1f}")
        return ", ".join(parts)

    def _on_override_combo_changed(self, source: str, bucket: str) -> None:
        row = self._row_index_by_source.get(source)
        if row is None or not bucket:
            return
        original_bucket = str(row.get("original_bucket") or row.get("chosen_bucket") or "")
        row["override_bucket"] = "" if bucket == original_bucket else bucket
        row["effective_bucket"] = bucket

        if bucket == original_bucket:
            self._manual_overrides.pop(source, None)
        else:
            self._manual_overrides[source] = {
                "source": source,
                "original_bucket": original_bucket,
                "override_bucket": bucket,
                "reason": "user_review",
                "timestamp": datetime.datetime.now().isoformat(),
            }

        self._rewrite_table_bucket_cells()
        self._rebuild_pack_breakdown()
        self._preview_stale = True
        self._apply_preview_filters()
        self._update_summary_label()
        self._update_review_details()
        self._update_preview_stale_state()

    def _rewrite_table_bucket_cells(self) -> None:
        for r in range(self.review_table.rowCount()):
            item = self.review_table.item(r, 2)
            if item is None:
                continue
            source = str(item.data(Qt.ItemDataRole.UserRole) or "")
            row = self._row_index_by_source.get(source)
            if row is None:
                continue
            item.setText(str(row.get("effective_bucket") or row.get("chosen_bucket") or ""))

    def _open_hint_menu(self, button: QPushButton) -> None:
        source = str(button.property("source") or "")
        row = self._row_index_by_source.get(source)
        if row is None:
            return

        target_bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")
        menu = QMenu(button)
        filename_menu = menu.addMenu("Add filename hint")
        folder_menu = menu.addMenu("Add folder hint")

        filename_tokens = self._filename_tokens(row)
        folder_tokens = self._folder_tokens(row)
        if not filename_tokens:
            act = filename_menu.addAction("(No tokens)")
            act.setEnabled(False)
        else:
            for token in filename_tokens:
                act = filename_menu.addAction(token)
                act.triggered.connect(
                    lambda checked=False, s=source, b=target_bucket, t=token: self.hintSaveRequested.emit(
                        s, "filename", b, t
                    )
                )

        if not folder_tokens:
            act = folder_menu.addAction("(No tokens)")
            act.setEnabled(False)
        else:
            for token in folder_tokens:
                act = folder_menu.addAction(token)
                act.triggered.connect(
                    lambda checked=False, s=source, b=target_bucket, t=token: self.hintSaveRequested.emit(s, "folder", b, t)
                )

        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _filename_tokens(self, row: dict[str, Any]) -> list[str]:
        name = Path(str(row.get("file", ""))).stem
        tokens = [t.strip().lower() for t in _TOKEN_SPLIT_RE.split(name) if t.strip()]
        return sorted(set(tokens))

    def _folder_tokens(self, row: dict[str, Any]) -> list[str]:
        source = str(row.get("source", ""))
        if not source:
            return []
        try:
            parent_parts = list(Path(source).parent.parts)[-5:]
        except Exception:
            return []
        tokens: set[str] = set()
        for part in parent_parts:
            for tok in _TOKEN_SPLIT_RE.split(str(part).lower()):
                tok = tok.strip()
                if tok:
                    tokens.add(tok)
        return sorted(tokens)

    def _selected_row(self) -> Optional[dict[str, Any]]:
        items = self.review_table.selectedItems()
        if not items:
            return None
        source = str(items[0].data(Qt.ItemDataRole.UserRole) or "")
        return self._row_index_by_source.get(source)

    def _update_review_details(self) -> None:
        row = self._selected_row()
        if row is None:
            if self.review_table.rowCount() == 0:
                self.review_details.setPlainText("No review rows available.")
            return

        details = {
            "pack": row.get("pack"),
            "file": row.get("file"),
            "source": row.get("source"),
            "original_bucket": row.get("original_bucket"),
            "effective_bucket": row.get("effective_bucket"),
            "low_confidence": row.get("low_confidence"),
            "confidence_ratio": row.get("confidence_ratio"),
            "confidence_margin": row.get("confidence_margin"),
            "top_3_candidates": row.get("top_3_candidates"),
            "folder_matches": row.get("folder_matches"),
            "filename_matches": row.get("filename_matches"),
            "audio_summary": row.get("audio_summary"),
            "pitch_summary": row.get("pitch_summary"),
            "glide_summary": row.get("glide_summary"),
        }
        self.review_details.setPlainText(json.dumps(details, indent=2))

    def _rebuild_pack_breakdown(self) -> None:
        lines: list[str] = []
        packs: dict[str, dict[str, int]] = {}
        for row in self._rows_all:
            pack = str(row.get("pack", ""))
            bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "UNSORTED")
            counts = packs.setdefault(pack, {})
            counts[bucket] = counts.get(bucket, 0) + 1
        for pack_name in sorted(packs):
            counts = packs[pack_name]
            counts_str = ", ".join(f"{bucket}: {count}" for bucket, count in sorted(counts.items()))
            lines.append(f"{pack_name}: {counts_str}")
        self.log_edit.setPlainText("\n".join(lines))

    def _update_summary_label(self) -> None:
        report = self._report or {}
        parts = []
        if report:
            parts.append(f"Processed {report.get('files_processed', 0)} file(s)")
            parts.append(f"Moved {report.get('files_moved', 0)}")
            parts.append(f"Copied {report.get('files_copied', 0)}")
            parts.append(f"Unsorted {report.get('unsorted', 0)}")
            if report.get("failed", 0):
                parts.append(f"Failed {report.get('failed', 0)}")
            if report.get("files_skipped_non_wav", 0):
                parts.append(f"Skipped non-WAV {report.get('files_skipped_non_wav', 0)}")
            if self._manual_overrides:
                parts.append(f"Manual overrides {len(self._manual_overrides)} (rerun before copy/move)")
            cache_stats = report.get("feature_cache_stats") or {}
            if isinstance(cache_stats, dict):
                parts.append(
                    "Cache hits/misses "
                    f"{int(cache_stats.get('hits', 0) or 0)}/{int(cache_stats.get('misses', 0) or 0)}"
                )
        self.summary_label.setText(" | ".join(parts) if parts else "No results.")
