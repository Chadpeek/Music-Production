from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Optional, cast

from PySide6.QtCore import QSignalBlocker, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from producer_os.bucket_service import BucketService
from producer_os.config_service import ConfigService
from producer_os.engine import ProducerOSEngine
from producer_os.styles_service import StyleService
from producer_os.ui.animations import slide_fade_in
from producer_os.ui.engine_runner import EngineRunner
from producer_os.ui.pages import HubPage, InboxPage, OptionsPage, RunPage
from producer_os.ui.state import WizardState
from producer_os.ui.theme import apply_app_theme
from producer_os.ui.widgets import StatusBadge, StepSidebar, set_widget_role


class ProducerOSWindow(QMainWindow):
    STEP_DEFS: list[tuple[str, str]] = [
        ("Inbox", "Choose the source folder"),
        ("Hub", "Choose destination and transfer mode"),
        ("Options", "Configure safety and appearance"),
        ("Run", "Analyze, execute, and review"),
    ]

    def __init__(self, app_icon: Optional[QIcon] = None) -> None:
        super().__init__()
        self.setObjectName("AppWindow")
        self.setWindowTitle("Producer OS Wizard")
        self.resize(1180, 820)
        self.setMinimumSize(1040, 720)
        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self.app_dir = Path(__file__).resolve().parents[2]
        self.config_service = ConfigService(app_dir=self.app_dir)
        self.config: dict[str, Any] = self.config_service.load_config()
        self.styles_data = self.config_service.load_styles()
        self.style_service = StyleService(self.styles_data)
        buckets_data = self.config_service.load_buckets()
        self.bucket_service = BucketService(buckets_data)
        self.state = WizardState.from_config(self.config)

        self.engine_runner: Optional[EngineRunner] = None
        self.current_report: dict[str, Any] = {}
        self.current_report_path = ""
        self._last_engine_bucket_ids: list[str] = []
        self._visited_max_index = 0
        self._current_step_index = 0

        self._build_shell(app_icon)
        self._create_pages()
        self._wire_signals()
        self._initialize_state()
        self._sync_theme_controls(self.state.theme)
        self._apply_theme_only(self.state.theme)
        self._set_status("Ready", kind="neutral", pulsing=False)
        self._set_current_step(0, animate=False)
        self._refresh_troubleshooting_status()

    # ------------------------------------------------------------------
    # UI shell
    def _build_shell(self, app_icon: Optional[QIcon]) -> None:
        root = QWidget()
        root.setObjectName("RootShell")
        self.setCentralWidget(root)

        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        self.top_bar = QFrame()
        self.top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(14, 12, 14, 12)
        top_layout.setSpacing(12)

        title_cluster = QWidget()
        title_layout = QHBoxLayout(title_cluster)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(10)

        if app_icon is not None and not app_icon.isNull():
            icon_label = QLabel()
            icon_label.setPixmap(app_icon.pixmap(24, 24))
            title_layout.addWidget(icon_label)

        title_text = QWidget()
        title_text_layout = QVBoxLayout(title_text)
        title_text_layout.setContentsMargins(0, 0, 0, 0)
        title_text_layout.setSpacing(0)
        app_title = QLabel("Producer-OS")
        app_title.setObjectName("AppTitle")
        app_subtitle = QLabel("Premium Wizard · Rule-based sample organization")
        app_subtitle.setObjectName("AppSubtitle")
        title_text_layout.addWidget(app_title)
        title_text_layout.addWidget(app_subtitle)
        title_layout.addWidget(title_text)
        top_layout.addWidget(title_cluster)
        top_layout.addStretch(1)

        self.header_open_config_btn = QPushButton("Config")
        set_widget_role(self.header_open_config_btn, "ghost")
        top_layout.addWidget(self.header_open_config_btn)

        self.header_open_report_btn = QPushButton("Last Report")
        set_widget_role(self.header_open_report_btn, "ghost")
        top_layout.addWidget(self.header_open_report_btn)

        self.header_theme_combo = QComboBox()
        self.header_theme_combo.addItems(["system", "dark", "light"])
        top_layout.addWidget(self.header_theme_combo)

        self.header_status_badge = StatusBadge("Ready")
        top_layout.addWidget(self.header_status_badge)

        root_layout.addWidget(self.top_bar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        root_layout.addWidget(body, 1)

        self.step_sidebar = StepSidebar(self.STEP_DEFS)
        self.step_sidebar.setFixedWidth(290)
        body_layout.addWidget(self.step_sidebar)

        self.content_surface = QFrame()
        self.content_surface.setObjectName("ContentSurface")
        content_layout = QVBoxLayout(self.content_surface)
        content_layout.setContentsMargins(14, 14, 14, 14)
        content_layout.setSpacing(0)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack)
        body_layout.addWidget(self.content_surface, 1)

        self.footer_bar = QFrame()
        self.footer_bar.setObjectName("FooterBar")
        footer_layout = QHBoxLayout(self.footer_bar)
        footer_layout.setContentsMargins(14, 10, 14, 10)
        footer_layout.setSpacing(10)

        self.footer_hint_label = QLabel("")
        self.footer_hint_label.setObjectName("MutedLabel")
        footer_layout.addWidget(self.footer_hint_label, 1)

        self.prev_btn = QPushButton("Back")
        set_widget_role(self.prev_btn, "ghost")
        self.next_btn = QPushButton("Next")
        set_widget_role(self.next_btn, "primary")
        footer_layout.addWidget(self.prev_btn)
        footer_layout.addWidget(self.next_btn)

        root_layout.addWidget(self.footer_bar)

    def _create_pages(self) -> None:
        self.inbox_page = InboxPage(self.state.inbox_path, self.state.dry_run)
        self.hub_page = HubPage(self.state.hub_path, self.state.action)
        self.options_page = OptionsPage(
            file_types=self.state.file_types,
            preserve_vendor=self.state.preserve_vendor,
            loop_safety=self.state.loop_safety,
            theme=self.state.theme,
            developer_tools=self.state.developer_tools,
        )
        self.run_page = RunPage(action=self.state.action)

        self.pages: list[QWidget] = [self.inbox_page, self.hub_page, self.options_page, self.run_page]
        for page in self.pages:
            self.stack.addWidget(page)

    def _wire_signals(self) -> None:
        self.prev_btn.clicked.connect(self.go_previous)
        self.next_btn.clicked.connect(self.go_next)
        self.step_sidebar.stepSelected.connect(self._on_step_sidebar_selected)

        self.header_open_config_btn.clicked.connect(self.open_config_folder)
        self.header_open_report_btn.clicked.connect(self.open_last_report)
        self.header_theme_combo.currentTextChanged.connect(self.on_theme_changed)

        self.inbox_page.browseRequested.connect(self.browse_inbox)
        self.inbox_page.inboxPathChanged.connect(self.on_inbox_path_changed)
        self.inbox_page.dryRunChanged.connect(self.on_dry_run_changed)

        self.hub_page.browseRequested.connect(self.browse_hub)
        self.hub_page.hubPathChanged.connect(self.on_hub_path_changed)
        self.hub_page.actionChanged.connect(self.on_action_changed)

        self.options_page.fileTypeChanged.connect(self.on_file_type_changed)
        self.options_page.preserveVendorChanged.connect(self.on_preserve_vendor_changed)
        self.options_page.loopSafetyChanged.connect(self.on_loop_safety_changed)
        self.options_page.themeChanged.connect(self.on_theme_changed)
        self.options_page.developerToolsChanged.connect(self.on_dev_tools_changed)
        self.options_page.openConfigRequested.connect(self.open_config_folder)
        self.options_page.openLogsRequested.connect(self.open_logs_folder)
        self.options_page.openLastReportRequested.connect(self.open_last_report)
        self.options_page.validateSchemasRequested.connect(self.validate_schemas)
        self.options_page.verifyAudioDependenciesRequested.connect(self.verify_audio_dependencies)
        self.options_page.qtPluginCheckRequested.connect(self.qt_plugin_check)

        self.run_page.analyzeRequested.connect(lambda: self.start_engine_run("analyze"))
        self.run_page.runRequested.connect(lambda: self.start_engine_run(self.state.action))
        self.run_page.saveReportRequested.connect(self.save_run_report)
        self.run_page.hintSaveRequested.connect(self.save_bucket_hint_from_review)

    def _initialize_state(self) -> None:
        self.update_inbox_preview()
        self.update_hub_warning()
        self.run_page.set_action(self.state.action)
        self.options_page.set_developer_tools_visible(self.state.developer_tools, animate=False)
        self._refresh_troubleshooting_status()

    # ------------------------------------------------------------------
    # Navigation
    def go_next(self) -> None:
        if self._current_step_index < self.stack.count() - 1:
            self._set_current_step(self._current_step_index + 1)

    def go_previous(self) -> None:
        if self._current_step_index > 0:
            self._set_current_step(self._current_step_index - 1)

    def _on_step_sidebar_selected(self, index: int) -> None:
        if index <= self._visited_max_index:
            self._set_current_step(index)

    def _set_current_step(self, index: int, animate: bool = True) -> None:
        index = max(0, min(index, self.stack.count() - 1))
        previous = self._current_step_index
        self._current_step_index = index
        self._visited_max_index = max(self._visited_max_index, index)

        self.stack.setCurrentIndex(index)
        if animate and previous != index:
            direction = 18 if index > previous else -18
            slide_fade_in(self.stack.currentWidget(), dx=direction)

        self.step_sidebar.set_max_clickable(self._visited_max_index)
        self.step_sidebar.set_current_index(index, animate=animate)
        self.update_nav_buttons()
        self._update_footer_hint()
        self._update_step_validation_states()

    def update_nav_buttons(self) -> None:
        idx = self._current_step_index
        self.prev_btn.setEnabled(idx > 0)
        self.next_btn.setEnabled(idx < self.stack.count() - 1)
        if idx == self.stack.count() - 1:
            self.next_btn.setText("Run Page")
            self.next_btn.setEnabled(False)
        else:
            self.next_btn.setText("Next")

    def _update_footer_hint(self) -> None:
        hints = [
            "Select a source inbox and verify the preview counts.",
            "Choose a hub destination and confirm transfer mode.",
            "Review safety options, theme, and developer utilities.",
            "Analyze or run, then inspect results and export the report.",
        ]
        self.footer_hint_label.setText(hints[self._current_step_index])

    # ------------------------------------------------------------------
    # Config persistence
    def save_setting(self, key: str, value: Any) -> None:
        self.config[key] = value
        try:
            self.config_service.save_config(self.config)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Theme handling
    def _sync_theme_controls(self, theme: str) -> None:
        for combo in (self.header_theme_combo, self.options_page.theme_combo):
            idx = combo.findText(theme)
            if idx >= 0:
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(idx)

    def _apply_theme_only(self, theme: str) -> None:
        app_instance = QApplication.instance()
        if app_instance is None:
            return
        app = cast(QApplication, app_instance)
        apply_app_theme(app, theme)

    def on_theme_changed(self, theme: str) -> None:
        if theme not in {"system", "dark", "light"}:
            theme = "system"
        self.state.theme = theme
        self._sync_theme_controls(theme)
        self._apply_theme_only(theme)
        self.save_setting("theme", theme)

    # ------------------------------------------------------------------
    # Inbox page
    def browse_inbox(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select Inbox Folder", self.state.inbox_path or str(Path.home())
        )
        if directory:
            self.inbox_page.set_inbox_path(directory)
            self.on_inbox_path_changed(directory)

    def on_inbox_path_changed(self, path_str: str) -> None:
        self.state.inbox_path = path_str
        self.save_setting("inbox_path", path_str)
        self.update_inbox_preview()
        self.update_hub_warning()

    def on_dry_run_changed(self, checked: bool) -> None:
        self.state.dry_run = checked
        self.save_setting("dry_run", checked)

    def update_inbox_preview(self) -> None:
        count_packs = 0
        count_loose = 0
        path_str = self.state.inbox_path
        if path_str:
            path = Path(path_str)
            if path.exists() and path.is_dir():
                try:
                    for entry in path.iterdir():
                        if entry.is_dir():
                            count_packs += 1
                        elif entry.is_file():
                            count_loose += 1
                except Exception:
                    pass
        self.inbox_page.set_preview_counts(count_packs, count_loose)
        self._update_step_validation_states()

    # ------------------------------------------------------------------
    # Hub page
    def browse_hub(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select Hub Folder", self.state.hub_path or str(Path.home())
        )
        if directory:
            self.hub_page.set_hub_path(directory)
            self.on_hub_path_changed(directory)

    def on_hub_path_changed(self, path_str: str) -> None:
        self.state.hub_path = path_str
        self.save_setting("hub_path", path_str)
        self.update_hub_warning()
        self._refresh_troubleshooting_status()

    def on_action_changed(self, action: str) -> None:
        if action not in {"move", "copy"}:
            return
        self.state.action = action
        self.hub_page.set_action(action)
        self.run_page.set_action(action)
        self.save_setting("action", action)

    def _compute_hub_warning(self) -> str:
        inbox_str = self.state.inbox_path
        hub_str = self.state.hub_path
        if not inbox_str or not hub_str:
            return ""
        try:
            inbox_path = Path(inbox_str).resolve()
            hub_path = Path(hub_str).resolve()
        except Exception:
            return ""
        if inbox_path == hub_path:
            return "Hub folder must be different from inbox."
        if inbox_path in hub_path.parents:
            return "Hub folder cannot be inside the inbox."
        return ""

    def update_hub_warning(self) -> None:
        self.hub_page.set_warning(self._compute_hub_warning())
        self._update_step_validation_states()

    # ------------------------------------------------------------------
    # Options page
    def on_file_type_changed(self, ext: str, checked: bool) -> None:
        if ext not in self.state.file_types:
            return
        self.state.file_types[ext] = checked
        self.save_setting("file_types", dict(self.state.file_types))

    def on_preserve_vendor_changed(self, checked: bool) -> None:
        self.state.preserve_vendor = checked
        self.save_setting("preserve_vendor", checked)

    def on_loop_safety_changed(self, checked: bool) -> None:
        self.state.loop_safety = checked
        self.save_setting("loop_safety", checked)

    def on_dev_tools_changed(self, checked: bool) -> None:
        self.state.developer_tools = checked
        self.save_setting("developer_tools", checked)

    def _refresh_troubleshooting_status(self) -> None:
        try:
            self.options_page.set_portable_mode_status(self.config_service.is_portable_mode())
        except Exception:
            pass
        # Provide a lightweight passive Qt plugin status hint.
        try:
            if getattr(sys, "frozen", False):
                exe_dir = Path(sys.executable).resolve().parent
                has_qwindows = any((exe_dir).rglob("qwindows.dll"))
                self.options_page.set_qt_plugin_status("qwindows.dll found" if has_qwindows else "qwindows.dll not found")
            else:
                self.options_page.set_qt_plugin_status("source run (packaged plugin check applies to frozen builds)")
        except Exception:
            self.options_page.set_qt_plugin_status("check failed")

    def _build_engine_config(self) -> dict[str, Any]:
        cfg = dict(self.config or {})
        try:
            cfg["config_dir"] = str(self.config_service.get_config_dir())
            cfg["config_path"] = str(self.config_service.get_config_path())
            cfg["bucket_hints_path"] = str(self.config_service.get_bucket_hints_path())
            cfg["bucket_hints"] = self.config_service.load_bucket_hints()
        except Exception:
            pass
        return cfg

    def open_config_folder(self) -> None:
        cfg_dir = self.config_service.get_config_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(cfg_dir)))

    def open_logs_folder(self) -> None:
        hub_path_str = self.state.hub_path
        if not hub_path_str:
            QMessageBox.information(self, "Open logs", "Please select a hub folder first.")
            return
        logs_dir = Path(hub_path_str) / "logs"
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir)))

    def open_last_report(self) -> None:
        hub_path_str = self.state.hub_path
        if not hub_path_str:
            QMessageBox.information(self, "Open report", "Please select a hub folder first.")
            return
        hub_path = Path(hub_path_str)
        logs_dir = hub_path / "logs"
        if not logs_dir.exists():
            QMessageBox.information(self, "Open report", "No logs directory found in the hub.")
            return
        reports = list(logs_dir.rglob("run_report.json"))
        if not reports:
            QMessageBox.information(self, "Open report", "No reports found yet.")
            return
        reports.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(reports[0])))

    def validate_schemas(self) -> None:
        try:
            self.config_service.load_config()
            self.config_service.load_styles()
            self.config_service.load_buckets()
            self.config_service.load_bucket_hints()
            QMessageBox.information(self, "Schema validation", "All configuration files are valid.")
        except Exception as exc:
            QMessageBox.warning(self, "Schema validation", f"Validation failed: {exc}")

    def verify_audio_dependencies(self) -> None:
        modules = ["soundfile", "librosa", "scipy", "numba", "llvmlite"]
        status_parts: list[str] = []
        for name in modules:
            try:
                importlib.import_module(name)
                status_parts.append(f"{name}:ok")
            except Exception as exc:
                status_parts.append(f"{name}:missing ({exc.__class__.__name__})")
        summary = ", ".join(status_parts)
        self.options_page.set_audio_dependencies_status(summary)
        QMessageBox.information(self, "Audio dependencies", summary)

    def qt_plugin_check(self) -> None:
        try:
            if not getattr(sys, "frozen", False):
                msg = "Source run detected. Qt platform plugin checks are primarily for packaged builds."
                self.options_page.set_qt_plugin_status(msg)
                QMessageBox.information(self, "Qt plugin check", msg)
                return
            exe_dir = Path(sys.executable).resolve().parent
            matches = list(exe_dir.rglob("qwindows.dll"))
            if matches:
                msg = f"Found qwindows.dll at {matches[0]}"
                self.options_page.set_qt_plugin_status("qwindows.dll found")
                QMessageBox.information(self, "Qt plugin check", msg)
            else:
                msg = "qwindows.dll not found under the application directory."
                self.options_page.set_qt_plugin_status("qwindows.dll not found")
                QMessageBox.warning(self, "Qt plugin check", msg)
        except Exception as exc:
            self.options_page.set_qt_plugin_status("check failed")
            QMessageBox.warning(self, "Qt plugin check", f"Check failed: {exc}")

    def save_bucket_hint_from_review(self, source: str, kind: str, bucket: str, token: str) -> None:
        token = (token or "").strip().lower()
        bucket = (bucket or "").strip()
        if not token or not bucket:
            self.run_page.set_review_feedback("No token selected for hint save.", success=False)
            return
        key = "filename_keywords" if kind == "filename" else "folder_keywords"
        try:
            hints = self.config_service.load_bucket_hints()
            target = hints.setdefault(key, {})
            if not isinstance(target, dict):
                target = {}
                hints[key] = target

            existing_bucket: Optional[str] = None
            for bkt, values in target.items():
                if not isinstance(values, list):
                    continue
                if token in {str(v).strip().lower() for v in values}:
                    existing_bucket = str(bkt)
                    break

            if existing_bucket and existing_bucket != bucket:
                reply = QMessageBox.question(
                    self,
                    "Conflicting hint token",
                    (
                        f"The {kind} token '{token}' already exists for bucket '{existing_bucket}'.\n\n"
                        f"Add it to '{bucket}' as well?"
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    self.run_page.set_review_feedback("Hint save cancelled.", success=False)
                    return

            bucket_values = target.setdefault(bucket, [])
            if not isinstance(bucket_values, list):
                bucket_values = []
                target[bucket] = bucket_values
            if token in {str(v).strip().lower() for v in bucket_values}:
                self.run_page.set_review_feedback(f"Hint already exists: {kind} '{token}' -> {bucket}", success=True)
                self.run_page.record_saved_hint(source, kind, bucket, token)
                return
            bucket_values.append(token)
            bucket_values[:] = sorted({str(v).strip().lower() for v in bucket_values if str(v).strip()})
            hints["version"] = 1
            self.config_service.save_bucket_hints(hints)
            self.run_page.record_saved_hint(source, kind, bucket, token)
            self.run_page.set_review_feedback(f"Saved {kind} hint '{token}' -> {bucket}. Rerun to apply.", success=True)
        except Exception as exc:
            self.run_page.set_review_feedback(f"Failed to save hint: {exc}", success=False)

    # ------------------------------------------------------------------
    # Run page / engine execution
    def _set_status(self, text: str, kind: str, pulsing: bool) -> None:
        self.header_status_badge.set_status(text, kind=kind, pulsing=pulsing)

    def _valid_inbox_path(self) -> bool:
        path_str = self.state.inbox_path
        return bool(path_str and Path(path_str).exists())

    def _valid_hub_path(self) -> bool:
        path_str = self.state.hub_path
        return bool(path_str and Path(path_str).exists())

    def _update_step_validation_states(self) -> None:
        invalid: set[int] = set()
        if not self._valid_inbox_path():
            invalid.add(0)
        if not self._valid_hub_path() or bool(self._compute_hub_warning()):
            invalid.add(1)
        self.step_sidebar.set_invalid_indices(invalid)

    def start_engine_run(self, mode: str) -> None:
        inbox_path = self.state.inbox_path
        hub_path = self.state.hub_path
        if not inbox_path or not Path(inbox_path).exists():
            QMessageBox.warning(self, "Run", "Please select a valid inbox folder.")
            return
        if not hub_path or not Path(hub_path).exists():
            QMessageBox.warning(self, "Run", "Please select a valid hub folder.")
            return
        try:
            inbox_resolved = Path(inbox_path).resolve()
            hub_resolved = Path(hub_path).resolve()
            if inbox_resolved == hub_resolved or hub_resolved.is_relative_to(inbox_resolved):
                QMessageBox.warning(self, "Run", "Hub folder must be different from and not inside the inbox.")
                return
        except Exception:
            pass

        self._set_current_step(3)
        self.run_page.clear_results()
        self.run_page.set_busy(True, mode=mode)
        badge_kind = "analyzing" if mode == "analyze" else "running"
        self._set_status("Analyzing" if mode == "analyze" else "Running", kind=badge_kind, pulsing=True)

        engine = ProducerOSEngine(
            inbox_dir=Path(inbox_path),
            hub_dir=Path(hub_path),
            style_service=self.style_service,
            config=self._build_engine_config(),
            bucket_service=self.bucket_service,
        )
        self._last_engine_bucket_ids = list(engine.BUCKET_RULES.keys())
        self.engine_runner = EngineRunner(engine, mode)
        self.engine_runner.finished.connect(self.on_engine_finished)
        self.engine_runner.start()

    def on_engine_finished(self, report: dict, report_path: str) -> None:
        self.current_report = report
        self.current_report_path = report_path
        self.run_page.set_busy(False)

        log_lines: list[str] = []
        for pack in report.get("packs", []):
            files = pack.get("files", [])
            counts: dict[str, int] = {}
            for file_entry in files:
                bucket = str(file_entry.get("bucket"))
                counts[bucket] = counts.get(bucket, 0) + 1
            counts_str = ", ".join(f"{bucket}: {count}" for bucket, count in counts.items())
            log_lines.append(f"{pack['pack']}: {counts_str}")

        self.run_page.set_results(report, log_lines, bucket_choices=self._last_engine_bucket_ids)
        if report.get("failed", 0):
            self._set_status("Completed with warnings", kind="warning", pulsing=False)
        else:
            self._set_status("Completed", kind="success", pulsing=False)

    def save_run_report(self) -> None:
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save run report", str(Path.home() / "run_report.json"), "JSON files (*.json)"
        )
        if not dest:
            return
        try:
            export_payload = dict(self.current_report or {})
            manual_review = self.run_page.get_manual_review_overlay()
            if manual_review:
                export_payload["manual_review"] = manual_review
            if self.current_report_path and Path(self.current_report_path).exists():
                data = json.loads(Path(self.current_report_path).read_text(encoding="utf-8"))
                if manual_review:
                    data["manual_review"] = manual_review
                Path(dest).write_text(json.dumps(data, indent=2), encoding="utf-8")
            else:
                Path(dest).write_text(json.dumps(export_payload, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Save report", "Report saved successfully.")
        except Exception as exc:
            QMessageBox.warning(self, "Save report", f"Failed to save report: {exc}")
