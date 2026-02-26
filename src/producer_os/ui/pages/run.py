from __future__ import annotations

import datetime
import json
import re
import wave
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from producer_os.ui.pages.base import BaseWizardPage
from producer_os.ui.widgets import NoWheelComboBox, StatChip, StatusBadge, repolish, set_widget_role

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except Exception:  # pragma: no cover - optional runtime dependency path
    QAudioOutput = None  # type: ignore[assignment]
    QMediaPlayer = None  # type: ignore[assignment]

try:
    import soundfile as _sf  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional runtime dependency path
    _sf = None

_TOKEN_SPLIT_RE = re.compile(r"[ _-]+")
_REVIEW_WIDGET_THRESHOLD = 500

_PHASE_LABELS: list[tuple[str, str]] = [
    ("scan", "Scanning"),
    ("classify", "Classifying"),
    ("route", "Routing"),
    ("write", "Writing"),
]


class _WaveformPreview(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._peaks: list[float] = []
        self._progress: float = 0.0
        self._status_text = "Select a row to preview audio."
        self.setObjectName("WaveformPreview")
        self.setMinimumHeight(84)

    def set_waveform(self, peaks: list[float], status_text: str = "") -> None:
        self._peaks = [max(0.0, min(1.0, float(p))) for p in peaks]
        if status_text:
            self._status_text = status_text
        self._progress = 0.0
        self.update()

    def set_status_text(self, text: str) -> None:
        self._status_text = str(text or "")
        self.update()

    def set_progress_fraction(self, frac: float) -> None:
        self._progress = max(0.0, min(1.0, float(frac or 0.0)))
        self.update()

    def clear(self, text: str = "No preview available.") -> None:
        self._peaks = []
        self._progress = 0.0
        self._status_text = text
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        bg = self.palette().base().color()
        border = self.palette().mid().color()
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 8, 8)

        if not self._peaks:
            painter.setPen(QPen(self.palette().mid().color()))
            painter.drawText(rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignCenter, self._status_text)
            return

        content = rect.adjusted(10, 8, -10, -20)
        center_y = content.center().y()
        width = max(1, content.width())
        height = max(1, content.height())

        wave_color = self.palette().highlight().color()
        wave_color.setAlpha(180)
        progress_color = self.palette().highlight().color()
        progress_x = content.left() + int(width * self._progress)

        painter.setPen(QPen(wave_color, 1))
        peak_count = len(self._peaks)
        for i, peak in enumerate(self._peaks):
            x = content.left() + int(i * width / max(1, peak_count - 1))
            half_h = max(1, int((height * 0.5) * peak))
            painter.drawLine(x, center_y - half_h, x, center_y + half_h)

        painter.setPen(QPen(progress_color, 2))
        painter.drawLine(progress_x, content.top(), progress_x, content.bottom())

        painter.setPen(QPen(self.palette().mid().color()))
        painter.drawText(rect.adjusted(10, 0, -10, -4), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, self._status_text)


class _BucketBadgeDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = str(opt.text or "")
        draw_text = text.removeprefix("\u25cf ").strip()
        fg = opt.palette.text().color()
        if opt.palette is not None:
            fg = opt.palette.text().color()
        custom_fg = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(custom_fg, QColor):
            fg = custom_fg
        elif hasattr(custom_fg, "color"):
            try:
                fg = custom_fg.color()
            except Exception:
                pass

        style = opt.widget.style() if opt.widget else None
        if style is not None:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)

        painter.save()
        rect = opt.rect.adjusted(8, 2, -6, -2)
        dot_size = 8
        dot_y = rect.center().y() - dot_size // 2
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fg)
        painter.drawEllipse(rect.left(), dot_y, dot_size, dot_size)
        painter.setPen(QPen(opt.palette.text().color()))
        text_rect = rect.adjusted(dot_size + 8, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, draw_text)
        painter.restore()


class _ConfidenceChipDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else None
        if style is not None:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)

        bg_brush = index.data(Qt.ItemDataRole.BackgroundRole)
        fg_brush = index.data(Qt.ItemDataRole.ForegroundRole)
        bg = QColor(80, 80, 80, 40)
        fg = opt.palette.text().color()
        try:
            if hasattr(bg_brush, "color"):
                bg = bg_brush.color()
            if hasattr(fg_brush, "color"):
                fg = fg_brush.color()
        except Exception:
            pass

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        chip_rect = opt.rect.adjusted(6, 4, -6, -4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(chip_rect, 8, 8)
        painter.setPen(QPen(fg))
        painter.drawText(chip_rect, Qt.AlignmentFlag.AlignCenter, str(index.data() or ""))
        painter.restore()


class _Top3BadgeDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else None
        if style is not None:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)
        text = str(index.data() or "")
        parts = [p for p in text.split("] ") if p]
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = opt.rect.left() + 6
        y = opt.rect.top() + 4
        max_right = opt.rect.right() - 6
        fm = painter.fontMetrics()
        for i, raw in enumerate(parts[:3]):
            part = raw if raw.endswith("]") else f"{raw}]"
            w = fm.horizontalAdvance(part) + 12
            h = max(18, fm.height() + 4)
            if x + w > max_right:
                ellipsis = "..."
                painter.setPen(QPen(opt.palette.mid().color()))
                painter.drawText(opt.rect.adjusted(x - opt.rect.left(), 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, ellipsis)
                break
            rect = option.rect.adjusted(x - option.rect.left(), y - option.rect.top(), 0, 0)
            rect.setWidth(w)
            rect.setHeight(h)
            painter.setPen(QPen(opt.palette.mid().color()))
            painter.setBrush(QColor(255, 255, 255, 0))
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(QPen(opt.palette.text().color()))
            painter.drawText(rect.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, part)
            x += w + 6
        painter.restore()


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
        self._has_live_logs = False
        self._review_table_widget_mode = True
        self._bucket_color_map: dict[str, str] = {}
        self._timeline_labels: list[QLabel] = []
        self._timeline_phase_keys: list[str] = []
        self._phase_progress: dict[str, dict[str, Any]] = {}
        self._active_mode: str | None = None
        self._review_sort_column: int = 0
        self._review_sort_order = Qt.SortOrder.AscendingOrder
        self._preview_sort_column: int = 0
        self._preview_sort_order = Qt.SortOrder.AscendingOrder
        self._pending_filter_restore: dict[str, Any] = {}
        self._restoring_layout_prefs = False
        self._prefs = QSettings("KidChadd", "Producer OS")
        self._waveform_cache: dict[str, dict[str, Any]] = {}
        self._audio_duration_ms = 0
        self._current_audio_source = ""
        self._audio_player = None
        self._audio_output = None
        self._suppress_autoplay_once = False
        self._reset_phase_progress()

        action_card = self.add_card("Execution Controls", "Run an analysis first to verify routing before moving files.")
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(10)

        self.analyze_btn = QPushButton("Analyze")
        set_widget_role(self.analyze_btn, "ghost")
        self.analyze_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.analyze_btn.clicked.connect(self.analyzeRequested.emit)
        action_row.addWidget(self.analyze_btn)

        self.run_btn = QPushButton(self._run_button_label(action))
        set_widget_role(self.run_btn, "primary")
        self.run_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
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
        self.save_report_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.save_report_btn.setVisible(False)
        self.save_report_btn.clicked.connect(self.saveReportRequested.emit)
        export_row.addWidget(self.save_report_btn)

        self.review_feedback_label = QLabel("")
        self.review_feedback_label.setObjectName("MutedLabel")
        self.review_feedback_label.setWordWrap(True)
        export_row.addWidget(self.review_feedback_label, 1)
        export_card.body_layout.addLayout(export_row)

        self._init_audio_preview_runtime()
        self._wire_layout_pref_listeners()
        self._restore_layout_prefs()
        self._sync_batch_review_controls()

    # ------------------------------------------------------------------
    # UI construction
    def _build_summary_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        self.timeline_row = QHBoxLayout()
        self.timeline_row.setContentsMargins(0, 0, 0, 0)
        self.timeline_row.setSpacing(8)
        for phase_key, phase_label in _PHASE_LABELS:
            chip = QLabel(phase_label)
            chip.setObjectName("TimelineStep")
            chip.setProperty("state", "pending")
            chip.setProperty("phaseKey", phase_key)
            chip.setToolTip(phase_label)
            self.timeline_row.addWidget(chip)
            self._timeline_labels.append(chip)
            self._timeline_phase_keys.append(phase_key)
        self.timeline_row.addStretch(1)
        layout.addLayout(self.timeline_row)

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

        self.run_legend_label = QLabel(
            "Confidence: high (green), medium (blue), low (amber). Top-3 candidates are shown as compact tags."
        )
        self.run_legend_label.setObjectName("FieldHint")
        self.run_legend_label.setWordWrap(True)
        layout.addWidget(self.run_legend_label)

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

        self.review_bucket_filter = NoWheelComboBox()
        self.review_bucket_filter.addItem("All buckets")
        self.review_bucket_filter.currentTextChanged.connect(self._apply_review_filters)
        filter_row.addWidget(self.review_bucket_filter)

        self.review_pack_filter = NoWheelComboBox()
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

        self.review_filter_hint_label = QLabel(
            "Tip: bucket/pack filters narrow the review queue and re-enable inline override controls on large audits."
        )
        self.review_filter_hint_label.setObjectName("FieldHint")
        self.review_filter_hint_label.setWordWrap(True)
        layout.addWidget(self.review_filter_hint_label)

        batch_row = QHBoxLayout()
        batch_row.setContentsMargins(0, 0, 0, 0)
        batch_row.setSpacing(6)
        self.review_batch_override_btn = QPushButton("Mark selected as…")
        set_widget_role(self.review_batch_override_btn, "ghost")
        self.review_batch_override_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowRight))
        self.review_batch_override_btn.clicked.connect(self._open_batch_override_menu)
        batch_row.addWidget(self.review_batch_override_btn)

        self.review_batch_hint_btn = QPushButton("Apply hint to selected…")
        set_widget_role(self.review_batch_hint_btn, "ghost")
        self.review_batch_hint_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView))
        self.review_batch_hint_btn.clicked.connect(self._open_batch_hint_menu_from_button)
        batch_row.addWidget(self.review_batch_hint_btn)

        self.review_filter_selected_pack_btn = QPushButton("Only selected pack")
        set_widget_role(self.review_filter_selected_pack_btn, "ghost")
        self.review_filter_selected_pack_btn.clicked.connect(self._filter_to_selected_pack)
        batch_row.addWidget(self.review_filter_selected_pack_btn)

        self.review_filter_selected_bucket_btn = QPushButton("Only selected bucket")
        set_widget_role(self.review_filter_selected_bucket_btn, "ghost")
        self.review_filter_selected_bucket_btn.clicked.connect(self._filter_to_selected_bucket)
        batch_row.addWidget(self.review_filter_selected_bucket_btn)

        self.review_clear_filters_btn = QPushButton("Clear filters")
        set_widget_role(self.review_clear_filters_btn, "ghost")
        self.review_clear_filters_btn.clicked.connect(self._clear_review_filters)
        batch_row.addWidget(self.review_clear_filters_btn)

        batch_row.addStretch(1)
        self.review_selection_count_label = QLabel("0 selected")
        self.review_selection_count_label.setObjectName("MutedLabel")
        batch_row.addWidget(self.review_selection_count_label)
        layout.addLayout(batch_row)

        self.review_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.review_splitter.setChildrenCollapsible(False)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.review_table = QTableWidget(0, 8)
        self.review_table.setHorizontalHeaderLabels(
            ["Pack", "File", "Chosen", "Confidence", "Margin", "Top 3", "Override", "Hint Action"]
        )
        self.review_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.review_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.review_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.review_table.setAlternatingRowColors(True)
        self.review_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        # Sorting a QTableWidget with thousands of rows plus per-row cell widgets
        # (combo boxes/buttons) is unstable and very slow on Windows. Keep review
        # filtering fast/stable and leave sorting disabled here.
        self.review_table.setSortingEnabled(False)
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
        self.review_table.customContextMenuRequested.connect(self._open_review_context_menu)
        left_layout.addWidget(self.review_table, 1)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.review_selected_file_label = QLabel("Select a row to inspect details.")
        self.review_selected_file_label.setObjectName("SectionTitle")
        self.review_selected_file_label.setWordWrap(True)
        right_layout.addWidget(self.review_selected_file_label)

        self.review_selected_meta_label = QLabel("")
        self.review_selected_meta_label.setObjectName("FieldHint")
        self.review_selected_meta_label.setWordWrap(True)
        right_layout.addWidget(self.review_selected_meta_label)

        media_row = QHBoxLayout()
        media_row.setContentsMargins(0, 0, 0, 0)
        media_row.setSpacing(6)
        self.review_audio_play_btn = QPushButton("Play")
        set_widget_role(self.review_audio_play_btn, "ghost")
        self.review_audio_play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.review_audio_play_btn.clicked.connect(self._toggle_audio_playback)
        media_row.addWidget(self.review_audio_play_btn)
        self.review_audio_stop_btn = QPushButton("Stop")
        set_widget_role(self.review_audio_stop_btn, "ghost")
        self.review_audio_stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.review_audio_stop_btn.clicked.connect(self._stop_audio_playback)
        media_row.addWidget(self.review_audio_stop_btn)
        self.review_audio_autoplay = QCheckBox("Auto-play on select")
        self.review_audio_autoplay.setChecked(True)
        self.review_audio_autoplay.toggled.connect(lambda _checked: self._save_layout_prefs())
        media_row.addWidget(self.review_audio_autoplay)
        media_row.addStretch(1)
        self.review_audio_status_label = QLabel("Audio preview ready")
        self.review_audio_status_label.setObjectName("MutedLabel")
        media_row.addWidget(self.review_audio_status_label)
        right_layout.addLayout(media_row)

        self.review_waveform = _WaveformPreview()
        right_layout.addWidget(self.review_waveform)

        detail_controls = QHBoxLayout()
        detail_controls.setContentsMargins(0, 0, 0, 0)
        detail_controls.setSpacing(6)
        self.review_detail_override_combo = NoWheelComboBox()
        self.review_detail_override_combo.setEnabled(False)
        self.review_detail_override_combo.currentTextChanged.connect(self._on_detail_override_changed)
        detail_controls.addWidget(self.review_detail_override_combo, 1)
        self.review_detail_filename_hint_btn = QPushButton("Add filename hint…")
        set_widget_role(self.review_detail_filename_hint_btn, "ghost")
        self.review_detail_filename_hint_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.review_detail_filename_hint_btn.setEnabled(False)
        self.review_detail_filename_hint_btn.clicked.connect(lambda: self._open_selected_hint_menu("filename"))
        detail_controls.addWidget(self.review_detail_filename_hint_btn)
        self.review_detail_folder_hint_btn = QPushButton("Add folder hint…")
        set_widget_role(self.review_detail_folder_hint_btn, "ghost")
        self.review_detail_folder_hint_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.review_detail_folder_hint_btn.setEnabled(False)
        self.review_detail_folder_hint_btn.clicked.connect(lambda: self._open_selected_hint_menu("folder"))
        detail_controls.addWidget(self.review_detail_folder_hint_btn)
        right_layout.addLayout(detail_controls)

        self.review_details = QTextEdit()
        self.review_details.setObjectName("LogOutput")
        self.review_details.setReadOnly(True)
        self.review_details.setMinimumHeight(180)
        right_layout.addWidget(self.review_details, 1)

        self.review_splitter.addWidget(left_panel)
        self.review_splitter.addWidget(right_panel)
        self.review_splitter.setStretchFactor(0, 3)
        self.review_splitter.setStretchFactor(1, 2)
        self.review_splitter.setSizes([760, 360])
        layout.addWidget(self.review_splitter, 1)

        self._review_bucket_delegate = _BucketBadgeDelegate(self.review_table)
        self._review_conf_delegate = _ConfidenceChipDelegate(self.review_table)
        self._review_top3_delegate = _Top3BadgeDelegate(self.review_table)
        self.review_table.setItemDelegateForColumn(2, self._review_bucket_delegate)
        self.review_table.setItemDelegateForColumn(3, self._review_conf_delegate)
        self.review_table.setItemDelegateForColumn(5, self._review_top3_delegate)

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

        self.preview_bucket_filter = NoWheelComboBox()
        self.preview_bucket_filter.addItem("All buckets")
        self.preview_bucket_filter.currentTextChanged.connect(self._apply_preview_filters)
        filter_row.addWidget(self.preview_bucket_filter)

        self.preview_pack_filter = NoWheelComboBox()
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

        self._preview_bucket_delegate = _BucketBadgeDelegate(self.preview_table)
        self.preview_table.setItemDelegateForColumn(2, self._preview_bucket_delegate)

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
        self._active_mode = mode if busy else None
        self.analyze_btn.setEnabled(not busy)
        self.run_btn.setEnabled(not busy)
        self.progress_bar.setVisible(busy)
        self.progress_bar.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress_bar.setValue(0)
            self.status_badge.set_status("Ready", kind="neutral", pulsing=False)
            if not self._report:
                self._reset_phase_progress()
                self._refresh_timeline_labels()
                self._set_timeline_state(active_index=None, completed_upto=None)
            self._update_preview_stale_state()
            return
        self._reset_phase_progress()
        self._refresh_timeline_labels()
        if mode == "analyze":
            self.status_badge.set_status("Analyzing", kind="analyzing", pulsing=True)
        else:
            self.status_badge.set_status("Running", kind="running", pulsing=True)
        self._set_timeline_state(active_index=0, completed_upto=-1)

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
        self._has_live_logs = False
        self._reset_phase_progress()
        self.review_feedback_label.setText("")
        self.save_report_btn.setVisible(False)
        self.status_badge.set_status("Working", kind="running", pulsing=True)
        self.preview_stale_label.setText("Preview reflects the latest analyze/dry-run report.")
        self.review_details.setPlainText("No review rows available yet. Run Analyze to populate the review queue.")
        self.review_waveform.clear("No preview available yet. Run Analyze and select a row.")
        self.review_audio_status_label.setText("Audio preview ready")
        self._stop_audio_playback()
        self._sync_batch_review_controls()
        self._refresh_timeline_labels()
        self._set_timeline_state(active_index=0, completed_upto=-1)

    def append_log_line(self, line: str) -> None:
        text = str(line or "").rstrip("\r\n")
        if not text:
            return
        self.log_edit.append(text)
        self._has_live_logs = True
        self._advance_timeline_from_log(text)

    def set_results(
        self,
        report: dict,
        log_lines: list[str],
        bucket_choices: Optional[list[str]] = None,
        bucket_colors: Optional[dict[str, str]] = None,
    ) -> None:
        self._report = dict(report or {})
        self._bucket_color_map = {str(k): str(v) for k, v in (bucket_colors or {}).items()}
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
        self._apply_restored_filter_values_once()

        self._update_summary_label()
        if not self._has_live_logs:
            self._rebuild_pack_breakdown()
        self._apply_review_filters()
        self._apply_preview_filters()
        self._update_preview_stale_state()
        self.save_report_btn.setVisible(True)
        self.status_badge.set_status("Completed", kind="success", pulsing=False)
        self._phase_progress["classify"].update(
            {"event": "done", "files_done": int(self._report.get("files_processed", 0) or 0)}
        )
        self._phase_progress["route"].update(
            {
                "event": "done",
                "moved": int(self._report.get("files_moved", 0) or 0),
                "copied": int(self._report.get("files_copied", 0) or 0),
                "unsorted": int(self._report.get("unsorted", 0) or 0),
            }
        )
        self._phase_progress["write"].update({"event": "done", "message": "Completed"})
        self._refresh_timeline_labels()
        self._set_timeline_state(active_index=None, completed_upto=len(self._timeline_labels) - 1)
        self._save_layout_prefs()

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
        repolish(self.review_feedback_label)

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
    # Timeline / visual helpers
    def _set_timeline_state(self, active_index: int | None, completed_upto: int | None) -> None:
        if not self._timeline_labels:
            return
        for idx, label in enumerate(self._timeline_labels):
            if completed_upto is None and active_index is None:
                state = "pending"
            elif completed_upto is not None and idx <= completed_upto:
                state = "done"
            elif active_index is not None and idx == active_index:
                state = "active"
            else:
                state = "pending"
            if label.property("state") != state:
                label.setProperty("state", state)
                repolish(label)
        self._refresh_timeline_labels()

    def _advance_timeline_from_log(self, text: str) -> None:
        if not self._timeline_labels:
            return
        lower = text.strip().lower()
        if not lower:
            return
        if "processing pack:" in lower:
            self._set_timeline_state(active_index=1, completed_upto=0)
            return
        if "finished pack:" in lower:
            if (self._active_mode or "").lower() == "analyze":
                self._set_timeline_state(active_index=1, completed_upto=1)
            else:
                self._set_timeline_state(active_index=2, completed_upto=1)
            return
        if "run_report.json" in lower or "feature_cache" in lower:
            self._set_timeline_state(active_index=3, completed_upto=2)

    def _reset_phase_progress(self) -> None:
        self._phase_progress = {
            phase: {
                "phase": phase,
                "event": "pending",
                "packs_total": 0,
                "packs_done": 0,
                "files_total": 0,
                "files_done": 0,
                "moved": 0,
                "copied": 0,
                "unsorted": 0,
                "message": "",
            }
            for phase, _ in _PHASE_LABELS
        }

    def _format_timeline_suffix(self, phase: str) -> str:
        info = self._phase_progress.get(phase) or {}
        event = str(info.get("event", "pending") or "pending")
        packs_total = int(info.get("packs_total", 0) or 0)
        packs_done = int(info.get("packs_done", 0) or 0)
        files_total = int(info.get("files_total", 0) or 0)
        files_done = int(info.get("files_done", 0) or 0)

        if phase == "scan":
            if packs_total:
                return f" · {packs_done}/{packs_total}"
            if event == "done":
                return " · done"
            return ""

        if phase == "classify":
            if files_total:
                return f" · {files_done}/{files_total}"
            if files_done:
                return f" · {files_done}"
            if packs_total:
                return f" · packs {packs_done}/{packs_total}"
            if event == "done":
                return " · done"
            return ""

        if phase == "route":
            moved = int(info.get("moved", 0) or 0)
            copied = int(info.get("copied", 0) or 0)
            unsorted = int(info.get("unsorted", 0) or 0)
            if moved or copied or unsorted or event == "done":
                return f" · M{moved} C{copied} U{unsorted}"
            return ""

        if phase == "write":
            message = str(info.get("message", "") or "").strip()
            if message:
                return f" · {message}"
            if event == "done":
                return " · done"
            return ""

        return ""

    def _refresh_timeline_labels(self) -> None:
        if not self._timeline_labels:
            return
        label_map = {phase: label for phase, label in zip(self._timeline_phase_keys, self._timeline_labels)}
        for phase_key, phase_label in _PHASE_LABELS:
            label = label_map.get(phase_key)
            if label is None:
                continue
            text = f"{phase_label}{self._format_timeline_suffix(phase_key)}"
            if label.text() != text:
                label.setText(text)

    def update_progress_event(self, payload: dict[str, Any]) -> None:
        phase = str((payload or {}).get("phase") or "").strip().lower()
        event = str((payload or {}).get("event") or "").strip().lower()
        if phase not in {p for p, _ in _PHASE_LABELS} or not event:
            return

        info = self._phase_progress.setdefault(phase, {})
        info.update({k: v for k, v in dict(payload or {}).items() if k != "phase"})
        info["phase"] = phase
        info["event"] = event
        self._refresh_timeline_labels()

        idx_map = {key: idx for idx, key in enumerate(self._timeline_phase_keys)}
        idx = idx_map.get(phase)
        if idx is None:
            return
        if event in {"start", "progress"}:
            self._set_timeline_state(active_index=idx, completed_upto=idx - 1)
            return
        if event == "done":
            if idx >= len(self._timeline_labels) - 1:
                self._set_timeline_state(active_index=None, completed_upto=idx)
            else:
                self._set_timeline_state(active_index=idx + 1, completed_upto=idx)

    def apply_density(self, density: str) -> None:
        super().apply_density(density)
        compact = str(density).strip().lower() == "compact"
        row_h = 26 if compact else 30
        for table in (self.review_table, self.preview_table):
            try:
                table.verticalHeader().setDefaultSectionSize(row_h)
                table.setIconSize(table.iconSize())  # force style refresh on some platforms
            except Exception:
                pass
        try:
            self.log_edit.setMinimumHeight(200 if compact else 240)
            self.review_details.setMinimumHeight(150 if compact else 180)
            self.review_waveform.setMinimumHeight(72 if compact else 84)
        except Exception:
            pass

    def _qcolor_from_style_text(self, value: str) -> Optional[QColor]:
        text = (value or "").strip()
        if not text:
            return None
        if text.startswith("$"):
            text = "#" + text[1:]
        elif not text.startswith("#"):
            text = "#" + text
        color = QColor(text)
        return color if color.isValid() else None

    def _bucket_color(self, bucket: str) -> Optional[QColor]:
        return self._qcolor_from_style_text(self._bucket_color_map.get(str(bucket), ""))

    def _apply_bucket_label_style(self, item: QTableWidgetItem, bucket: str) -> None:
        color = self._bucket_color(bucket)
        if color is None:
            item.setText(str(bucket))
            return
        item.setText(f"\u25cf {bucket}")
        item.setForeground(color)
        item.setToolTip(f"{bucket} ({self._bucket_color_map.get(bucket, '')})")

    def _style_confidence_item(self, item: QTableWidgetItem, ratio: float, low_confidence: bool) -> None:
        ratio = float(ratio or 0.0)
        if low_confidence or ratio < 0.50:
            label = "LOW"
            bg = QColor(245, 158, 66, 38)
            fg = QColor(235, 144, 33)
        elif ratio < 0.80:
            label = "MED"
            bg = QColor(86, 200, 255, 34)
            fg = QColor(86, 200, 255)
        else:
            label = "HIGH"
            bg = QColor(49, 208, 170, 34)
            fg = QColor(49, 208, 170)
        item.setText(f"{label} {ratio:.3f}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setBackground(bg)
        item.setForeground(fg)

    def _style_margin_item(self, item: QTableWidgetItem) -> None:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def _top3_compact_text(self, row: dict[str, Any]) -> str:
        parts: list[str] = []
        for c in row.get("top_3_candidates", []) or []:
            if not isinstance(c, dict):
                continue
            bucket = str(c.get("bucket") or "")
            score = float(c.get("score", 0.0) or 0.0)
            if not bucket:
                continue
            parts.append(f"[{bucket} {score:.0f}]")
        return " ".join(parts)

    def _style_top3_item(self, item: QTableWidgetItem, row: dict[str, Any]) -> None:
        item.setText(self._top3_compact_text(row))
        item.setToolTip(self._top3_text(row) or "No candidate scores")

    def _apply_low_conf_tint_to_item(self, item: Optional[QTableWidgetItem]) -> None:
        if item is None:
            return
        item.setBackground(QColor(245, 158, 66, 18))

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
        if len(filtered) > _REVIEW_WIDGET_THRESHOLD:
            self.review_feedback_label.setText(
                "Large review set detected. Row edit controls are temporarily disabled to keep the UI stable. "
                f"Narrow the review filters to {_REVIEW_WIDGET_THRESHOLD} rows or fewer to enable per-row overrides and hints."
            )
            self.review_feedback_label.setProperty("state", "warning")
        elif self.review_feedback_label.text().startswith("Large review set detected."):
            self.review_feedback_label.setText("")
            self.review_feedback_label.setProperty("state", None)
        repolish(self.review_feedback_label)
        self._sync_batch_review_controls()
        self._save_layout_prefs()

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
        self._save_layout_prefs()

    def _render_review_table(self, rows: list[dict[str, Any]]) -> None:
        table = self.review_table
        widget_mode = len(rows) <= _REVIEW_WIDGET_THRESHOLD
        self._review_table_widget_mode = widget_mode
        prev_updates = table.updatesEnabled()
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        table.setSortingEnabled(False)
        table.setRowCount(0)
        table.setRowCount(len(rows))

        try:
            for r, row in enumerate(rows):
                source = str(row.get("source", ""))
                effective_bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")

                items = [
                    QTableWidgetItem(str(row.get("pack", ""))),
                    QTableWidgetItem(str(row.get("file", ""))),
                    QTableWidgetItem(effective_bucket),
                    QTableWidgetItem(f"{float(row.get('confidence_ratio', 0.0) or 0.0):.4f}"),
                    QTableWidgetItem(f"{float(row.get('confidence_margin', 0.0) or 0.0):.2f}"),
                    QTableWidgetItem(""),
                ]
                self._apply_bucket_label_style(items[2], effective_bucket)
                self._style_confidence_item(
                    items[3],
                    float(row.get("confidence_ratio", 0.0) or 0.0),
                    bool(row.get("low_confidence", False)),
                )
                self._style_margin_item(items[4])
                self._style_top3_item(items[5], row)
                for col, item in enumerate(items):
                    item.setData(Qt.ItemDataRole.UserRole, source)
                    if bool(row.get("low_confidence", False)):
                        item.setData(Qt.ItemDataRole.UserRole + 1, True)
                    table.setItem(r, col, item)
                if bool(row.get("low_confidence", False)):
                    for item in (items[0], items[1], items[2], items[4], items[5]):
                        self._apply_low_conf_tint_to_item(item)

                if widget_mode:
                    override_combo = NoWheelComboBox()
                    override_combo.addItems(self._bucket_choices or [effective_bucket])
                    combo_bucket = effective_bucket
                    idx = override_combo.findText(combo_bucket)
                    override_combo.setCurrentIndex(max(0, idx))
                    override_combo.setProperty("source", source)
                    override_combo.currentTextChanged.connect(
                        lambda value, cb=override_combo: self._on_override_combo_changed(
                            str(cb.property("source") or ""), value
                        )
                    )
                    table.setCellWidget(r, 6, override_combo)

                    hint_btn = QPushButton("Hints…")
                    set_widget_role(hint_btn, "ghost")
                    hint_btn.setProperty("source", source)
                    hint_btn.clicked.connect(lambda _=False, btn=hint_btn: self._open_hint_menu(btn))
                    table.setCellWidget(r, 7, hint_btn)
                else:
                    override_item = QTableWidgetItem("Narrow filter to edit")
                    override_item.setData(Qt.ItemDataRole.UserRole, source)
                    if bool(row.get("low_confidence", False)):
                        self._apply_low_conf_tint_to_item(override_item)
                    table.setItem(r, 6, override_item)
                    hint_item = QTableWidgetItem("Narrow filter to use hints")
                    hint_item.setData(Qt.ItemDataRole.UserRole, source)
                    if bool(row.get("low_confidence", False)):
                        self._apply_low_conf_tint_to_item(hint_item)
                    table.setItem(r, 7, hint_item)
        finally:
            table.blockSignals(False)
            table.setUpdatesEnabled(prev_updates)

        if table.rowCount() > 0:
            self._suppress_autoplay_once = True
            table.selectRow(0)
        else:
            self.review_details.setPlainText("No rows match the current filter.")
            self.review_waveform.clear("No rows match the current filter.")
            self.review_audio_status_label.setText("Audio preview idle")
        self._sync_batch_review_controls()

    def _render_preview_table(self, rows: list[dict[str, Any]]) -> None:
        table = self.preview_table
        prev_updates = table.updatesEnabled()
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        table.setSortingEnabled(False)
        table.setRowCount(0)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
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
                item = QTableWidgetItem(value)
                if c == 2:
                    self._apply_bucket_label_style(item, effective_bucket)
                elif c == 5:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if low_conf and c != 5:
                    self._apply_low_conf_tint_to_item(item)
                table.setItem(r, c, item)
        table.blockSignals(False)
        table.setUpdatesEnabled(prev_updates)
        # Sorting on very large preview tables can make filter toggles feel like crashes.
        if len(rows) <= 2000:
            table.setSortingEnabled(True)
            try:
                table.sortItems(self._preview_sort_column, self._preview_sort_order)
            except Exception:
                pass

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
        self._apply_override_to_rows([row], bucket)

    def _apply_override_to_rows(self, rows: list[dict[str, Any]], bucket: str) -> None:
        bucket = str(bucket or "").strip()
        if not bucket or not rows:
            return
        changed = False
        now = datetime.datetime.now().isoformat()
        for row in rows:
            source = str(row.get("source", ""))
            if not source:
                continue
            original_bucket = str(row.get("original_bucket") or row.get("chosen_bucket") or "")
            if str(row.get("effective_bucket") or row.get("chosen_bucket") or "") == bucket:
                continue
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
                    "timestamp": now,
                }
            changed = True
        if not changed:
            return
        self._rewrite_table_bucket_cells()
        self._rebuild_pack_breakdown()
        self._preview_stale = True
        self._apply_preview_filters()
        self._update_summary_label()
        self._update_review_details()
        self._update_preview_stale_state()
        self._sync_review_detail_controls()
        self.review_feedback_label.setText(f"Applied override '{bucket}' to {len(rows)} row(s). Rerun before copy/move.")
        self.review_feedback_label.setProperty("state", "success")
        repolish(self.review_feedback_label)

    def _rewrite_table_bucket_cells(self) -> None:
        for r in range(self.review_table.rowCount()):
            item = self.review_table.item(r, 2)
            if item is None:
                continue
            source = str(item.data(Qt.ItemDataRole.UserRole) or "")
            row = self._row_index_by_source.get(source)
            if row is None:
                continue
            self._apply_bucket_label_style(item, str(row.get("effective_bucket") or row.get("chosen_bucket") or ""))

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

    def _open_selected_hint_menu(self, kind: str) -> None:
        row = self._selected_row()
        if row is None:
            return
        source = str(row.get("source", ""))
        target_bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")
        tokens = self._filename_tokens(row) if kind == "filename" else self._folder_tokens(row)
        button = self.review_detail_filename_hint_btn if kind == "filename" else self.review_detail_folder_hint_btn
        menu = QMenu(button)
        if not tokens:
            act = menu.addAction("(No tokens)")
            act.setEnabled(False)
        else:
            for token in tokens:
                act = menu.addAction(token)
                act.triggered.connect(
                    lambda checked=False, s=source, b=target_bucket, t=token, k=kind: self.hintSaveRequested.emit(
                        s, k, b, t
                    )
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
        selection_model = self.review_table.selectionModel()
        if selection_model is None:
            return None
        selected_rows = selection_model.selectedRows()
        if not selected_rows:
            return None
        source = str(selected_rows[0].data(Qt.ItemDataRole.UserRole) or "")
        return self._row_index_by_source.get(source)

    def _selected_rows(self) -> list[dict[str, Any]]:
        selection_model = self.review_table.selectionModel()
        if selection_model is None:
            return []
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for model_index in selection_model.selectedRows():
            source = str(model_index.data(Qt.ItemDataRole.UserRole) or "")
            if not source or source in seen:
                continue
            row = self._row_index_by_source.get(source)
            if row is None:
                continue
            seen.add(source)
            rows.append(row)
        return rows

    def _update_review_details(self) -> None:
        row = self._selected_row()
        if row is None:
            if self.review_table.rowCount() == 0:
                self.review_details.setPlainText("No review rows available.")
                self.review_selected_file_label.setText("No review rows available.")
                self.review_selected_meta_label.setText("")
                self.review_waveform.clear("No review rows available.")
            self._sync_review_detail_controls(None)
            self._sync_batch_review_controls()
            return

        self.review_selected_file_label.setText(f"{row.get('file', '')}")
        self.review_selected_meta_label.setText(
            " | ".join(
                [
                    f"Pack: {row.get('pack', '')}",
                    f"Bucket: {row.get('effective_bucket') or row.get('chosen_bucket') or 'UNSORTED'}",
                    f"Confidence: {float(row.get('confidence_ratio', 0.0) or 0.0):.3f}",
                    f"Margin: {float(row.get('confidence_margin', 0.0) or 0.0):.2f}",
                    "LOW" if bool(row.get("low_confidence", False)) else "Normal",
                ]
            )
        )

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
        self._sync_review_detail_controls(row)
        self._sync_batch_review_controls()
        self._load_audio_preview_for_row(row)

    def _sync_review_detail_controls(self, row: Optional[dict[str, Any]] = None) -> None:
        row = row if row is not None else self._selected_row()
        if row is None:
            with QSignalBlocker(self.review_detail_override_combo):
                self.review_detail_override_combo.clear()
            self.review_detail_override_combo.setEnabled(False)
            self.review_detail_filename_hint_btn.setEnabled(False)
            self.review_detail_folder_hint_btn.setEnabled(False)
            return
        effective_bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")
        choices = self._bucket_choices or [effective_bucket]
        with QSignalBlocker(self.review_detail_override_combo):
            self.review_detail_override_combo.clear()
            for choice in choices:
                self.review_detail_override_combo.addItem(choice)
            idx = self.review_detail_override_combo.findText(effective_bucket)
            self.review_detail_override_combo.setCurrentIndex(max(0, idx))
        self.review_detail_override_combo.setEnabled(True)
        self.review_detail_filename_hint_btn.setEnabled(True)
        self.review_detail_folder_hint_btn.setEnabled(True)

    def _on_detail_override_changed(self, bucket: str) -> None:
        row = self._selected_row()
        if row is None:
            return
        source = str(row.get("source", ""))
        if not source or not bucket:
            return
        self._on_override_combo_changed(source, bucket)

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

    # ------------------------------------------------------------------
    # Audio preview / waveform
    def _init_audio_preview_runtime(self) -> None:
        if QMediaPlayer is None or QAudioOutput is None:
            self.review_audio_play_btn.setEnabled(False)
            self.review_audio_stop_btn.setEnabled(False)
            self.review_audio_autoplay.setEnabled(False)
            self.review_audio_status_label.setText("QtMultimedia unavailable")
            self.review_waveform.clear("QtMultimedia is unavailable in this environment.")
            return
        try:
            self._audio_output = QAudioOutput(self)
            self._audio_output.setVolume(0.65)
            self._audio_player = QMediaPlayer(self)
            self._audio_player.setAudioOutput(self._audio_output)
            self._audio_player.positionChanged.connect(self._on_audio_position_changed)
            self._audio_player.durationChanged.connect(self._on_audio_duration_changed)
            self._audio_player.playbackStateChanged.connect(self._on_audio_playback_state_changed)
            try:
                self._audio_player.errorOccurred.connect(self._on_audio_error)  # type: ignore[attr-defined]
            except Exception:
                pass
        except Exception:
            self._audio_player = None
            self._audio_output = None
            self.review_audio_play_btn.setEnabled(False)
            self.review_audio_stop_btn.setEnabled(False)
            self.review_audio_autoplay.setEnabled(False)
            self.review_audio_status_label.setText("Audio preview unavailable")
            self.review_waveform.clear("Audio preview initialization failed.")

    def _on_audio_position_changed(self, position_ms: int) -> None:
        try:
            duration = max(0, int(self._audio_duration_ms or 0))
            frac = (float(position_ms) / float(duration)) if duration > 0 else 0.0
            self.review_waveform.set_progress_fraction(frac)
        except Exception:
            pass

    def _on_audio_duration_changed(self, duration_ms: int) -> None:
        self._audio_duration_ms = max(0, int(duration_ms or 0))

    def _on_audio_playback_state_changed(self, _state: Any) -> None:
        player = self._audio_player
        if player is None:
            return
        try:
            state = player.playbackState()
            if state == QMediaPlayer.PlaybackState.PlayingState:
                self.review_audio_play_btn.setText("Pause")
                self.review_audio_play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
                self.review_audio_status_label.setText("Playing")
            elif state == QMediaPlayer.PlaybackState.PausedState:
                self.review_audio_play_btn.setText("Play")
                self.review_audio_play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
                self.review_audio_status_label.setText("Paused")
            else:
                self.review_audio_play_btn.setText("Play")
                self.review_audio_play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
                if self._current_audio_source:
                    self.review_audio_status_label.setText("Stopped")
        except Exception:
            pass

    def _on_audio_error(self, *args: Any) -> None:
        msg = ""
        try:
            if self._audio_player is not None and hasattr(self._audio_player, "errorString"):
                msg = str(self._audio_player.errorString() or "")
        except Exception:
            msg = ""
        self.review_audio_status_label.setText(f"Audio error: {msg or 'playback failed'}")

    def _toggle_audio_playback(self) -> None:
        player = self._audio_player
        if player is None:
            return
        if not self._current_audio_source:
            row = self._selected_row()
            if row is None:
                return
            self._load_audio_preview_for_row(row)
        try:
            if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                player.pause()
            else:
                player.play()
        except Exception:
            self.review_audio_status_label.setText("Audio playback unavailable")

    def _stop_audio_playback(self) -> None:
        player = self._audio_player
        if player is None:
            return
        try:
            player.stop()
            self.review_waveform.set_progress_fraction(0.0)
        except Exception:
            pass

    def _load_audio_preview_for_row(self, row: dict[str, Any]) -> None:
        source = str(row.get("source", "")).strip()
        if not source:
            self.review_waveform.clear("No source path available.")
            self.review_audio_status_label.setText("No source path")
            self._current_audio_source = ""
            return

        src_path = Path(source)
        if not src_path.exists():
            self.review_waveform.clear("Source file no longer exists.")
            self.review_audio_status_label.setText("File missing")
            self._current_audio_source = ""
            return

        waveform = self._get_cached_waveform(src_path)
        if waveform is not None:
            peaks = list(waveform.get("peaks", []) or [])
            duration_sec = float(waveform.get("duration_sec", 0.0) or 0.0)
            self.review_waveform.set_waveform(peaks, f"{src_path.name} · {duration_sec:.2f}s")
        else:
            self.review_waveform.clear("Waveform preview unavailable for this file.")

        if self._audio_player is not None:
            try:
                if self._current_audio_source != str(src_path):
                    self._audio_duration_ms = 0
                    self._audio_player.stop()
                    self._audio_player.setSource(QUrl.fromLocalFile(str(src_path)))
                    self._current_audio_source = str(src_path)
                    self.review_audio_status_label.setText("Ready")
                should_autoplay = self.review_audio_autoplay.isChecked() and not self._suppress_autoplay_once
                self._suppress_autoplay_once = False
                if should_autoplay:
                    self._audio_player.play()
            except Exception:
                self.review_audio_status_label.setText("Audio playback unavailable")
        self._save_layout_prefs()

    def _get_cached_waveform(self, path: Path) -> Optional[dict[str, Any]]:
        key = str(path)
        try:
            stat = path.stat()
            sig = (int(stat.st_mtime_ns), int(stat.st_size))
        except Exception:
            sig = (0, 0)
        cached = self._waveform_cache.get(key)
        if cached and tuple(cached.get("sig", ())) == sig:
            return cached

        waveform = self._build_waveform_peaks(path)
        if waveform is None:
            return None
        waveform["sig"] = sig
        self._waveform_cache[key] = waveform
        return waveform

    def _build_waveform_peaks(self, path: Path) -> Optional[dict[str, Any]]:
        target_bins = 420
        if _sf is not None:
            try:
                data, sr = _sf.read(str(path), dtype="float32", always_2d=True)
                if data is None:
                    return None
                frame_count = int(len(data))
                if frame_count <= 0:
                    return {"peaks": [], "duration_sec": 0.0}
                # NumPy array math is intentionally avoided here so this path still
                # works if the runtime returns array-like objects.
                channels = int(getattr(data, "shape", [frame_count, 1])[1]) if hasattr(data, "shape") else 1
                step = max(1, int(frame_count / target_bins))
                peaks: list[float] = []
                for i in range(0, frame_count, step):
                    end = min(frame_count, i + step)
                    peak = 0.0
                    for j in range(i, end):
                        if channels > 1:
                            # Average absolute magnitude across channels.
                            try:
                                sample = data[j]
                                mag = 0.0
                                for ch in sample:
                                    mag += abs(float(ch))
                                mag /= max(1, channels)
                            except Exception:
                                mag = 0.0
                        else:
                            try:
                                mag = abs(float(data[j][0]))
                            except Exception:
                                mag = 0.0
                        if mag > peak:
                            peak = mag
                    peaks.append(max(0.0, min(1.0, peak)))
                    if len(peaks) >= target_bins:
                        break
                duration_sec = float(frame_count) / float(sr or 1)
                return {"peaks": peaks, "duration_sec": duration_sec}
            except Exception:
                pass

        # Lightweight fallback for simple PCM WAVs if soundfile is unavailable.
        try:
            with wave.open(str(path), "rb") as wf:
                frames = wf.getnframes()
                sr = wf.getframerate() or 1
                if frames <= 0:
                    return {"peaks": [], "duration_sec": 0.0}
                raw = wf.readframes(min(frames, sr * 30))
                if not raw:
                    return {"peaks": [], "duration_sec": 0.0}
                # Coarse byte-domain envelope fallback (not amplitude accurate, but useful visually).
                step = max(1, int(len(raw) / target_bins))
                peaks = []
                for i in range(0, len(raw), step):
                    block = raw[i : i + step]
                    if not block:
                        break
                    peak = max(block) / 255.0
                    peaks.append(max(0.0, min(1.0, float(peak))))
                    if len(peaks) >= target_bins:
                        break
                duration_sec = float(frames) / float(sr)
                return {"peaks": peaks, "duration_sec": duration_sec}
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Batch actions / context menu
    def _sync_batch_review_controls(self) -> None:
        rows = self._selected_rows()
        count = len(rows)
        self.review_selection_count_label.setText(f"{count} selected")
        enabled = count > 0 and bool(self._rows_all)
        for btn in (
            self.review_batch_override_btn,
            self.review_batch_hint_btn,
            self.review_filter_selected_pack_btn,
            self.review_filter_selected_bucket_btn,
        ):
            btn.setEnabled(enabled)

    def _clear_review_filters(self) -> None:
        with QSignalBlocker(self.review_search):
            self.review_search.clear()
        with QSignalBlocker(self.review_bucket_filter):
            self.review_bucket_filter.setCurrentIndex(0)
        with QSignalBlocker(self.review_pack_filter):
            self.review_pack_filter.setCurrentIndex(0)
        with QSignalBlocker(self.review_low_only):
            self.review_low_only.setChecked(True)
        self._apply_review_filters()

    def _filter_to_selected_pack(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        pack = str(row.get("pack", ""))
        idx = self.review_pack_filter.findText(pack)
        if idx >= 0:
            self.review_pack_filter.setCurrentIndex(idx)
        self._apply_review_filters()

    def _filter_to_selected_bucket(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")
        idx = self.review_bucket_filter.findText(bucket)
        if idx >= 0:
            self.review_bucket_filter.setCurrentIndex(idx)
        self._apply_review_filters()

    def _open_batch_override_menu(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        menu = QMenu(self.review_batch_override_btn)
        for bucket in self._bucket_choices:
            act = menu.addAction(bucket)
            act.triggered.connect(lambda _checked=False, b=bucket: self._apply_override_to_rows(self._selected_rows(), b))
        if menu.isEmpty():
            act = menu.addAction("(No buckets)")
            act.setEnabled(False)
        menu.exec(self.review_batch_override_btn.mapToGlobal(self.review_batch_override_btn.rect().bottomLeft()))

    def _selected_rows_single_bucket(self) -> Optional[str]:
        rows = self._selected_rows()
        buckets = {
            str(r.get("effective_bucket") or r.get("chosen_bucket") or "")
            for r in rows
            if str(r.get("effective_bucket") or r.get("chosen_bucket") or "")
        }
        return next(iter(buckets)) if len(buckets) == 1 else None

    def _batch_tokens(self, kind: str) -> list[str]:
        rows = self._selected_rows()
        if not rows:
            return []
        token_sets: list[set[str]] = []
        for row in rows:
            tokens = self._filename_tokens(row) if kind == "filename" else self._folder_tokens(row)
            token_sets.append(set(tokens))
        if not token_sets:
            return []
        common = set.intersection(*token_sets) if len(token_sets) > 1 else token_sets[0]
        if common:
            return sorted(common)
        union: set[str] = set().union(*token_sets)
        return sorted(union)

    def _open_batch_hint_menu_from_button(self) -> None:
        menu = QMenu(self.review_batch_hint_btn)
        self._populate_batch_hint_menu(menu)
        menu.exec(self.review_batch_hint_btn.mapToGlobal(self.review_batch_hint_btn.rect().bottomLeft()))

    def _populate_batch_hint_menu(self, menu: QMenu) -> None:
        rows = self._selected_rows()
        if not rows:
            act = menu.addAction("(No rows selected)")
            act.setEnabled(False)
            return
        bucket = self._selected_rows_single_bucket()
        if not bucket:
            act = menu.addAction("Select rows from a single effective bucket")
            act.setEnabled(False)
            return

        filename_menu = menu.addMenu(f"Apply filename hint to {len(rows)} selected")
        folder_menu = menu.addMenu(f"Apply folder hint to {len(rows)} selected")
        for kind, target_menu in (("filename", filename_menu), ("folder", folder_menu)):
            tokens = self._batch_tokens(kind)
            if not tokens:
                act = target_menu.addAction("(No tokens)")
                act.setEnabled(False)
                continue
            for token in tokens[:40]:
                act = target_menu.addAction(token)
                act.triggered.connect(
                    lambda _checked=False, k=kind, t=token: self._apply_hint_token_to_selected(k, t)
                )

    def _apply_hint_token_to_selected(self, kind: str, token: str) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        count = 0
        for row in rows:
            source = str(row.get("source", ""))
            bucket = str(row.get("effective_bucket") or row.get("chosen_bucket") or "")
            if not source or not bucket:
                continue
            self.hintSaveRequested.emit(source, kind, bucket, token)
            count += 1
        self.review_feedback_label.setText(f"Applied {kind} hint '{token}' to {count} selected row(s). Rerun to apply.")
        self.review_feedback_label.setProperty("state", "success")
        repolish(self.review_feedback_label)

    def _open_review_context_menu(self, point) -> None:
        row = self._selected_row()
        menu = QMenu(self.review_table)
        selected = self._selected_rows()
        if row is not None:
            open_dir = menu.addAction("Open file location")
            open_dir.triggered.connect(self._open_selected_file_location)
            copy_path = menu.addAction("Copy source path")
            copy_path.triggered.connect(self._copy_selected_source_path)
            menu.addSeparator()

        override_menu = menu.addMenu("Mark selected as…")
        if not selected:
            override_menu.setEnabled(False)
        else:
            for bucket in self._bucket_choices:
                act = override_menu.addAction(bucket)
                act.triggered.connect(lambda _checked=False, b=bucket: self._apply_override_to_rows(self._selected_rows(), b))

        hint_menu = menu.addMenu("Apply hint to selected…")
        self._populate_batch_hint_menu(hint_menu)

        if row is not None:
            menu.addSeparator()
            only_pack = menu.addAction("Only show this pack")
            only_pack.triggered.connect(self._filter_to_selected_pack)
            only_bucket = menu.addAction("Only show this bucket")
            only_bucket.triggered.connect(self._filter_to_selected_bucket)
        clear_filters = menu.addAction("Clear review filters")
        clear_filters.triggered.connect(self._clear_review_filters)

        menu.exec(self.review_table.viewport().mapToGlobal(point))

    def _copy_selected_source_path(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        source = str(row.get("source", ""))
        if not source:
            return
        app = self.window().windowHandle().screen() if False else None
        _ = app  # keep linter quiet for conditional path above
        try:
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(source)
            self.review_feedback_label.setText("Copied source path to clipboard.")
            self.review_feedback_label.setProperty("state", "success")
            repolish(self.review_feedback_label)
        except Exception:
            pass

    def _open_selected_file_location(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        source = str(row.get("source", ""))
        if not source:
            return
        try:
            folder = Path(source).parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Layout persistence
    def _wire_layout_pref_listeners(self) -> None:
        self.tabs.currentChanged.connect(lambda _idx: self._save_layout_prefs())
        self.review_splitter.splitterMoved.connect(lambda _pos, _index: self._save_layout_prefs())

        for widget in (
            self.review_search,
            self.preview_search,
        ):
            widget.textChanged.connect(lambda _text: self._save_layout_prefs())
        for combo in (
            self.review_bucket_filter,
            self.review_pack_filter,
            self.preview_bucket_filter,
            self.preview_pack_filter,
        ):
            combo.currentTextChanged.connect(lambda _text: self._save_layout_prefs())
        for checkbox in (
            self.review_low_only,
            self.preview_low_only,
            self.preview_changed_only,
        ):
            checkbox.toggled.connect(lambda _checked: self._save_layout_prefs())

        for table in (self.review_table, self.preview_table):
            header = table.horizontalHeader()
            header.sectionMoved.connect(lambda *_args: self._save_layout_prefs())
            header.sectionResized.connect(lambda *_args: self._save_layout_prefs())
        self.preview_table.horizontalHeader().sortIndicatorChanged.connect(self._on_preview_sort_changed)

    def _on_preview_sort_changed(self, section: int, order: Qt.SortOrder) -> None:
        self._preview_sort_column = int(section)
        self._preview_sort_order = order
        self._save_layout_prefs()

    def _save_layout_prefs(self) -> None:
        if self._restoring_layout_prefs:
            return
        try:
            p = self._prefs
            p.setValue("run_page/tab_index", int(self.tabs.currentIndex()))
            p.setValue("run_page/review_splitter_state", self.review_splitter.saveState())
            p.setValue("run_page/review_header_state", self.review_table.horizontalHeader().saveState())
            p.setValue("run_page/preview_header_state", self.preview_table.horizontalHeader().saveState())
            p.setValue("run_page/review_search", self.review_search.text())
            p.setValue("run_page/review_bucket_filter", self.review_bucket_filter.currentText())
            p.setValue("run_page/review_pack_filter", self.review_pack_filter.currentText())
            p.setValue("run_page/review_low_only", bool(self.review_low_only.isChecked()))
            p.setValue("run_page/preview_search", self.preview_search.text())
            p.setValue("run_page/preview_bucket_filter", self.preview_bucket_filter.currentText())
            p.setValue("run_page/preview_pack_filter", self.preview_pack_filter.currentText())
            p.setValue("run_page/preview_low_only", bool(self.preview_low_only.isChecked()))
            p.setValue("run_page/preview_changed_only", bool(self.preview_changed_only.isChecked()))
            p.setValue("run_page/preview_sort_col", int(self._preview_sort_column))
            p.setValue("run_page/preview_sort_order", int(self._preview_sort_order))
            p.setValue("run_page/audio_autoplay", bool(self.review_audio_autoplay.isChecked()))
        except Exception:
            pass

    def _restore_layout_prefs(self) -> None:
        self._restoring_layout_prefs = True
        try:
            p = self._prefs
            tab_idx = int(p.value("run_page/tab_index", 0) or 0)
            if 0 <= tab_idx < self.tabs.count():
                self.tabs.setCurrentIndex(tab_idx)

            review_splitter_state = p.value("run_page/review_splitter_state")
            if review_splitter_state is not None:
                try:
                    self.review_splitter.restoreState(review_splitter_state)
                except Exception:
                    pass
            for key, table in (
                ("run_page/review_header_state", self.review_table),
                ("run_page/preview_header_state", self.preview_table),
            ):
                state = p.value(key)
                if state is not None:
                    try:
                        table.horizontalHeader().restoreState(state)
                    except Exception:
                        pass

            self._pending_filter_restore = {
                "review_search": str(p.value("run_page/review_search", "") or ""),
                "review_bucket_filter": str(p.value("run_page/review_bucket_filter", "All buckets") or "All buckets"),
                "review_pack_filter": str(p.value("run_page/review_pack_filter", "All packs") or "All packs"),
                "review_low_only": bool(p.value("run_page/review_low_only", True)),
                "preview_search": str(p.value("run_page/preview_search", "") or ""),
                "preview_bucket_filter": str(p.value("run_page/preview_bucket_filter", "All buckets") or "All buckets"),
                "preview_pack_filter": str(p.value("run_page/preview_pack_filter", "All packs") or "All packs"),
                "preview_low_only": bool(p.value("run_page/preview_low_only", False)),
                "preview_changed_only": bool(p.value("run_page/preview_changed_only", False)),
            }
            self._preview_sort_column = int(p.value("run_page/preview_sort_col", 0) or 0)
            self._preview_sort_order = Qt.SortOrder(int(p.value("run_page/preview_sort_order", int(Qt.SortOrder.AscendingOrder)) or 0))
            self.review_audio_autoplay.setChecked(bool(p.value("run_page/audio_autoplay", True)))

            # Apply controls that do not depend on dynamic combo choices immediately.
            with QSignalBlocker(self.review_search):
                self.review_search.setText(self._pending_filter_restore["review_search"])
            with QSignalBlocker(self.preview_search):
                self.preview_search.setText(self._pending_filter_restore["preview_search"])
            with QSignalBlocker(self.review_low_only):
                self.review_low_only.setChecked(self._pending_filter_restore["review_low_only"])
            with QSignalBlocker(self.preview_low_only):
                self.preview_low_only.setChecked(self._pending_filter_restore["preview_low_only"])
            with QSignalBlocker(self.preview_changed_only):
                self.preview_changed_only.setChecked(self._pending_filter_restore["preview_changed_only"])
        except Exception:
            self._pending_filter_restore = {}
        finally:
            self._restoring_layout_prefs = False

    def _apply_restored_filter_values_once(self) -> None:
        if not self._pending_filter_restore:
            return
        pending = dict(self._pending_filter_restore)
        self._pending_filter_restore = {}
        with QSignalBlocker(self.review_bucket_filter):
            idx = self.review_bucket_filter.findText(str(pending.get("review_bucket_filter", "All buckets")))
            self.review_bucket_filter.setCurrentIndex(idx if idx >= 0 else 0)
        with QSignalBlocker(self.review_pack_filter):
            idx = self.review_pack_filter.findText(str(pending.get("review_pack_filter", "All packs")))
            self.review_pack_filter.setCurrentIndex(idx if idx >= 0 else 0)
        with QSignalBlocker(self.preview_bucket_filter):
            idx = self.preview_bucket_filter.findText(str(pending.get("preview_bucket_filter", "All buckets")))
            self.preview_bucket_filter.setCurrentIndex(idx if idx >= 0 else 0)
        with QSignalBlocker(self.preview_pack_filter):
            idx = self.preview_pack_filter.findText(str(pending.get("preview_pack_filter", "All packs")))
            self.preview_pack_filter.setCurrentIndex(idx if idx >= 0 else 0)
