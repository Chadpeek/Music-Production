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
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from producer_os.bucket_service import BucketService
from producer_os.config_service import ConfigService
from producer_os.engine import ProducerOSEngine
from producer_os.styles_service import DEFAULT_STYLE, StyleService
from producer_os.ui.animations import slide_fade_in
from producer_os.ui.engine_runner import EngineRunner
from producer_os.ui.pages import HubPage, InboxPage, OptionsPage, RunPage
from producer_os.ui.state import WizardState
from producer_os.ui.theme import (
    THEME_PRESET_CHOICES,
    THEME_PRESET_LABELS,
    apply_app_theme,
    normalize_accent_color,
    normalize_accent_mode,
    normalize_accent_preset,
    normalize_theme_name,
    normalize_ui_density,
)
from producer_os.ui.widgets import NoWheelComboBox, StatusBadge, StepSidebar, ToastHost, set_widget_role


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
        self.toast_host = ToastHost(self.content_surface)
        self._create_pages()
        self._wire_signals()
        self._initialize_state()
        self._sync_theme_controls(self.state.theme)
        self._sync_appearance_controls()
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
        self.header_open_config_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        top_layout.addWidget(self.header_open_config_btn)

        self.header_open_report_btn = QPushButton("Last Report")
        set_widget_role(self.header_open_report_btn, "ghost")
        self.header_open_report_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
        top_layout.addWidget(self.header_open_report_btn)

        self.header_theme_combo = NoWheelComboBox()
        for theme_value in THEME_PRESET_CHOICES:
            self.header_theme_combo.addItem(THEME_PRESET_LABELS.get(theme_value, theme_value), theme_value)
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
        self.prev_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.next_btn = QPushButton("Next")
        set_widget_role(self.next_btn, "primary")
        self.next_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        footer_layout.addWidget(self.prev_btn)
        footer_layout.addWidget(self.next_btn)

        root_layout.addWidget(self.footer_bar)

    def _create_pages(self) -> None:
        self.inbox_page = InboxPage(self.state.inbox_path, self.state.dry_run)
        self.hub_page = HubPage(self.state.hub_path, self.state.output_folder_name, self.state.action)
        self.options_page = OptionsPage(
            file_types=self.state.file_types,
            preserve_vendor=self.state.preserve_vendor,
            loop_safety=self.state.loop_safety,
            theme=self.state.theme,
            ui_density=self.state.ui_density,
            ui_accent_mode=self.state.ui_accent_mode,
            ui_accent_preset=self.state.ui_accent_preset,
            ui_accent_color=self.state.ui_accent_color,
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
        self.header_theme_combo.currentIndexChanged.connect(
            lambda _idx: self.on_theme_changed(str(self.header_theme_combo.currentData() or "system"))
        )

        self.inbox_page.browseRequested.connect(self.browse_inbox)
        self.inbox_page.inboxPathChanged.connect(self.on_inbox_path_changed)
        self.inbox_page.dryRunChanged.connect(self.on_dry_run_changed)

        self.hub_page.browseRequested.connect(self.browse_hub)
        self.hub_page.hubPathChanged.connect(self.on_hub_path_changed)
        self.hub_page.outputFolderNameChanged.connect(self.on_output_folder_name_changed)
        self.hub_page.actionChanged.connect(self.on_action_changed)

        self.options_page.fileTypeChanged.connect(self.on_file_type_changed)
        self.options_page.preserveVendorChanged.connect(self.on_preserve_vendor_changed)
        self.options_page.loopSafetyChanged.connect(self.on_loop_safety_changed)
        self.options_page.themeChanged.connect(self.on_theme_changed)
        self.options_page.uiDensityChanged.connect(self.on_ui_density_changed)
        self.options_page.accentModeChanged.connect(self.on_accent_mode_changed)
        self.options_page.accentPresetChanged.connect(self.on_accent_preset_changed)
        self.options_page.accentColorChanged.connect(self.on_accent_color_changed)
        self.options_page.developerToolsChanged.connect(self.on_dev_tools_changed)
        self.options_page.openConfigRequested.connect(self.open_config_folder)
        self.options_page.openLogsRequested.connect(self.open_logs_folder)
        self.options_page.openLastReportRequested.connect(self.open_last_report)
        self.options_page.validateSchemasRequested.connect(self.validate_schemas)
        self.options_page.verifyAudioDependenciesRequested.connect(self.verify_audio_dependencies)
        self.options_page.qtPluginCheckRequested.connect(self.qt_plugin_check)
        self.options_page.bucketCustomizationReloadRequested.connect(self.reload_bucket_customizations)
        self.options_page.bucketCustomizationSaveRequested.connect(self.save_bucket_customizations)

        self.run_page.analyzeRequested.connect(lambda: self.start_engine_run("analyze"))
        self.run_page.runRequested.connect(lambda: self.start_engine_run(self.state.action))
        self.run_page.saveReportRequested.connect(self.save_run_report)
        self.run_page.hintSaveRequested.connect(self.save_bucket_hint_from_review)

    def _initialize_state(self) -> None:
        self.update_inbox_preview()
        self.update_hub_warning()
        self.run_page.set_action(self.state.action)
        self.options_page.set_developer_tools_visible(self.state.developer_tools, animate=False)
        self._apply_density_to_pages(self.state.ui_density)
        self._sync_appearance_controls()
        self._refresh_troubleshooting_status()
        self._refresh_bucket_customization_editor()

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
        is_run_step = idx == self.stack.count() - 1
        self.prev_btn.setEnabled(idx > 0)
        self.next_btn.setVisible(not is_run_step)
        self.next_btn.setText("Next")
        self.next_btn.setEnabled(not is_run_step)

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
        theme = normalize_theme_name(theme)
        for combo in (self.header_theme_combo, self.options_page.theme_combo):
            idx = combo.findData(theme)
            if idx >= 0:
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(idx)
        try:
            self.options_page.refresh_theme_previews()
        except Exception:
            pass

    def _sync_appearance_controls(self) -> None:
        self.state.ui_density = normalize_ui_density(self.state.ui_density)
        self.state.ui_accent_mode = normalize_accent_mode(self.state.ui_accent_mode)
        self.state.ui_accent_preset = normalize_accent_preset(self.state.ui_accent_preset)
        self.state.ui_accent_color = normalize_accent_color(self.state.ui_accent_color)
        try:
            self.options_page.set_ui_density_value(self.state.ui_density)
            self.options_page.set_accent_settings(
                self.state.ui_accent_mode,
                self.state.ui_accent_preset,
                self.state.ui_accent_color,
            )
        except Exception:
            pass

    def _apply_density_to_pages(self, density: str) -> None:
        density = normalize_ui_density(density)
        compact = density == "compact"
        try:
            root = self.centralWidget()
            if isinstance(root, QWidget):
                root_layout = root.layout()
                if root_layout is not None:
                    root_layout.setContentsMargins(
                        12 if compact else 16,
                        12 if compact else 16,
                        12 if compact else 16,
                        12 if compact else 16,
                    )
                    root_layout.setSpacing(10 if compact else 12)
        except Exception:
            pass
        try:
            self.step_sidebar.setFixedWidth(270 if compact else 290)
        except Exception:
            pass
        try:
            content_layout = self.content_surface.layout()
            if content_layout is not None:
                content_layout.setContentsMargins(
                    10 if compact else 14,
                    10 if compact else 14,
                    10 if compact else 14,
                    10 if compact else 14,
                )
        except Exception:
            pass
        for page in getattr(self, "pages", []):
            if hasattr(page, "apply_density"):
                try:
                    page.apply_density(density)
                except Exception:
                    pass

    def _apply_theme_only(self, theme: str) -> None:
        app_instance = QApplication.instance()
        if app_instance is None:
            return
        app = cast(QApplication, app_instance)
        apply_app_theme(
            app,
            theme,
            density=self.state.ui_density,
            accent_mode=self.state.ui_accent_mode,
            accent_preset=self.state.ui_accent_preset,
            accent_color=self.state.ui_accent_color,
        )
        try:
            self.options_page.refresh_theme_previews()
        except Exception:
            pass

    def on_theme_changed(self, theme: str) -> None:
        theme = normalize_theme_name(theme)
        self.state.theme = theme
        self._sync_theme_controls(theme)
        self._apply_theme_only(theme)
        self.save_setting("theme", theme)

    def on_ui_density_changed(self, density: str) -> None:
        density = normalize_ui_density(density)
        self.state.ui_density = density
        self._apply_density_to_pages(density)
        self._apply_theme_only(self.state.theme)
        self.save_setting("ui_density", density)

    def on_accent_mode_changed(self, mode: str) -> None:
        mode = normalize_accent_mode(mode)
        self.state.ui_accent_mode = mode
        self._sync_appearance_controls()
        self._apply_theme_only(self.state.theme)
        self.save_setting("ui_accent_mode", mode)

    def on_accent_preset_changed(self, preset: str) -> None:
        preset = normalize_accent_preset(preset)
        self.state.ui_accent_preset = preset
        self._sync_appearance_controls()
        self._apply_theme_only(self.state.theme)
        self.save_setting("ui_accent_preset", preset)

    def on_accent_color_changed(self, color: str) -> None:
        color = normalize_accent_color(color)
        self.state.ui_accent_color = color
        self._sync_appearance_controls()
        self._apply_theme_only(self.state.theme)
        self.save_setting("ui_accent_color", color)

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
            self, "Select Destination Folder", self.state.hub_path or str(Path.home())
        )
        if directory:
            self.hub_page.set_hub_path(directory)
            self.on_hub_path_changed(directory)

    def on_hub_path_changed(self, path_str: str) -> None:
        self.state.hub_path = path_str
        self.save_setting("hub_path", path_str)
        self.update_hub_warning()
        self._refresh_troubleshooting_status()

    def on_output_folder_name_changed(self, name: str) -> None:
        self.state.output_folder_name = str(name)
        self.save_setting("output_folder_name", self.state.output_folder_name)
        self.update_hub_warning()

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
        output_name_warning = self._validate_output_folder_name(self.state.output_folder_name)
        if output_name_warning:
            return output_name_warning
        if not inbox_str or not hub_str:
            return ""
        try:
            inbox_path = Path(inbox_str).resolve()
            hub_path = Path(hub_str).resolve()
        except Exception:
            return ""
        if inbox_path == hub_path:
            return "Destination folder must be different from the inbox."
        if inbox_path in hub_path.parents:
            return "Destination folder cannot be inside the inbox."
        return ""

    def _validate_output_folder_name(self, name: str) -> str:
        text = (name or "").strip()
        if not text:
            return "Organized folder name cannot be blank."
        if text in {".", ".."}:
            return "Organized folder name cannot be '.' or '..'."
        if any(ch in text for ch in ("/", "\\")):
            return "Organized folder name must be a single folder name (no slashes)."
        if text.lower() == "logs":
            return "Organized folder name cannot be 'logs' because logs is reserved."
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
        cfg["output_folder_name"] = str(self.state.output_folder_name or "").strip()
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
            self._toast("All configuration files are valid.", kind="success")
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
        self._toast("Audio dependency check completed.", kind="info")

    def qt_plugin_check(self) -> None:
        try:
            if not getattr(sys, "frozen", False):
                msg = "Source run detected. Qt platform plugin checks are primarily for packaged builds."
                self.options_page.set_qt_plugin_status(msg)
                self._toast("Qt plugin check completed (source run).", kind="info")
                return
            exe_dir = Path(sys.executable).resolve().parent
            matches = list(exe_dir.rglob("qwindows.dll"))
            if matches:
                msg = f"Found qwindows.dll at {matches[0]}"
                self.options_page.set_qt_plugin_status("qwindows.dll found")
                self._toast("Qt plugin found in packaged app directory.", kind="success")
            else:
                msg = "qwindows.dll not found under the application directory."
                self.options_page.set_qt_plugin_status("qwindows.dll not found")
                QMessageBox.warning(self, "Qt plugin check", msg)
        except Exception as exc:
            self.options_page.set_qt_plugin_status("check failed")
            QMessageBox.warning(self, "Qt plugin check", f"Check failed: {exc}")

    def _bucket_ids_for_customization(self) -> list[str]:
        ids: list[str] = []
        try:
            field_info = getattr(ProducerOSEngine, "__dataclass_fields__", {}).get("BUCKET_RULES")
            default_factory = getattr(field_info, "default_factory", None)
            if callable(default_factory):
                default_rules = default_factory()
                if isinstance(default_rules, dict):
                    ids = [str(bucket_id) for bucket_id in default_rules.keys()]
        except Exception:
            ids = []

        # If engine defaults are unavailable for any reason, fall back to any buckets
        # already observed from a previous run.
        for bucket_id in self._last_engine_bucket_ids:
            if bucket_id not in ids:
                ids.append(str(bucket_id))
        extras = list((self.bucket_service.mapping or {}).keys())
        extras += list((self.styles_data.get("buckets", {}) or {}).keys())
        for bucket_id in extras:
            if bucket_id not in ids:
                ids.append(str(bucket_id))
        return ids

    def _refresh_bucket_customization_editor(self) -> None:
        try:
            bucket_ids = self._bucket_ids_for_customization()
            bucket_names = dict(self.bucket_service.mapping or {})
            bucket_styles = dict((self.styles_data.get("buckets", {}) or {}))
            self.options_page.set_bucket_customizations(bucket_ids, bucket_names, bucket_styles)
        except Exception as exc:
            self.options_page.set_bucket_customization_status(f"Load failed: {exc}", success=False)

    def reload_bucket_customizations(self) -> None:
        try:
            self.styles_data = self.config_service.load_styles()
            self.style_service = StyleService(self.styles_data)
            buckets_data = self.config_service.load_buckets()
            self.bucket_service = BucketService(buckets_data)
            self._refresh_bucket_customization_editor()
            self.options_page.set_bucket_customization_status("Reloaded bucket names/colors from disk.", success=True)
        except Exception as exc:
            self.options_page.set_bucket_customization_status(f"Reload failed: {exc}", success=False)
            QMessageBox.warning(self, "Bucket customization", f"Failed to reload bucket customizations: {exc}")

    def _normalize_bucket_color(self, value: str) -> str:
        text = (value or "").strip().upper()
        if text.startswith("$") or text.startswith("#"):
            text = text[1:]
        if len(text) != 6 or any(ch not in "0123456789ABCDEF" for ch in text):
            raise ValueError(f"Invalid color '{value}'. Use $RRGGBB (example: $CC0000).")
        return f"${text}"

    def _normalize_icon_index(self, value: object) -> int:
        raw = str(value if value is not None else "").strip()
        if not raw:
            raise ValueError("IconIndex cannot be blank.")
        text = raw
        base = 10
        if text.lower().startswith("0x"):
            text = text[2:]
            base = 16
        elif text.startswith("$"):
            text = text[1:]
            base = 16
        elif any(ch in "ABCDEFabcdef" for ch in text):
            base = 16
        elif len(text) == 4 and all(ch in "0123456789ABCDEFabcdef" for ch in text):
            # FL Studio icon charts are often shown as 4-char hex codes (e.g. 0074, f129).
            base = 16
        if not text or any(ch not in "0123456789ABCDEFabcdef" for ch in text):
            raise ValueError(
                f"Invalid IconIndex '{raw}'. Use decimal (10) or hex (f129, 0074, 0xF129)."
            )
        icon_index = int(text, base)
        if icon_index < 0:
            raise ValueError(f"Invalid IconIndex '{raw}'. Value must be >= 0.")
        return icon_index

    def _default_category_styles(self) -> dict[str, dict[str, int | str]]:
        # Fallbacks used only when no styles file exists yet.
        return {
            "Samples": {"Color": "$4863A0", "IconIndex": 10, "SortGroup": 0},
            "Loops": {"Color": "$A849A8", "IconIndex": 20, "SortGroup": 1},
            "MIDI": {"Color": "$3A8E3A", "IconIndex": 30, "SortGroup": 2},
            "UNSORTED": {"Color": "$7F7F7F", "IconIndex": 0, "SortGroup": 3},
        }

    def save_bucket_customizations(self, names_obj: object, colors_obj: object, icons_obj: object) -> None:
        try:
            raw_names = dict(cast(dict[str, Any], names_obj or {}))
            raw_colors = dict(cast(dict[str, Any], colors_obj or {}))
            raw_icons = dict(cast(dict[str, Any], icons_obj or {}))
            bucket_ids = self._bucket_ids_for_customization()

            buckets_payload: dict[str, str] = {}
            seen_display_names: dict[str, str] = {}
            for bucket_id in bucket_ids:
                display_name = str(raw_names.get(bucket_id, bucket_id) or "").strip()
                if not display_name:
                    raise ValueError(f"Display name cannot be blank for bucket '{bucket_id}'.")
                folded = display_name.casefold()
                prev = seen_display_names.get(folded)
                if prev and prev != bucket_id:
                    raise ValueError(
                        f"Duplicate display name '{display_name}' for buckets '{prev}' and '{bucket_id}'."
                    )
                seen_display_names[folded] = bucket_id
                buckets_payload[bucket_id] = display_name

            styles_payload = self.config_service.load_styles()
            if not isinstance(styles_payload, dict):
                styles_payload = {}
            categories = dict(styles_payload.get("categories") or self.styles_data.get("categories") or {})
            if not categories:
                categories = self._default_category_styles()
            style_buckets = dict(styles_payload.get("buckets") or self.styles_data.get("buckets") or {})

            for bucket_id in bucket_ids:
                normalized_color = self._normalize_bucket_color(str(raw_colors.get(bucket_id, "") or ""))
                normalized_icon = self._normalize_icon_index(raw_icons.get(bucket_id, ""))
                existing_style = dict(style_buckets.get(bucket_id) or {})
                icon_index = normalized_icon
                sort_group = int(existing_style.get("SortGroup", DEFAULT_STYLE.get("SortGroup", 0)) or 0)
                style_buckets[bucket_id] = {
                    "Color": normalized_color,
                    "IconIndex": icon_index,
                    "SortGroup": sort_group,
                }

            styles_payload = {"categories": categories, "buckets": style_buckets}

            self.config_service.save_buckets(buckets_payload)
            self.config_service.save_styles(styles_payload)

            self.styles_data = styles_payload
            self.style_service = StyleService(self.styles_data)
            self.bucket_service = BucketService(buckets_payload)
            self._refresh_bucket_customization_editor()
            self.options_page.set_bucket_customization_status(
                "Saved bucket names/colors. Changes apply to future runs and style writes.",
                success=True,
            )
            self._toast("Bucket customizations saved. Rerun analyze/copy/move to apply.", kind="success", timeout_ms=4200)
        except Exception as exc:
            self.options_page.set_bucket_customization_status(f"Save failed: {exc}", success=False)
            QMessageBox.warning(self, "Bucket customization", f"Failed to save bucket customizations:\n{exc}")

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
                self._toast(f"Hint already exists: {token} -> {bucket}", kind="info")
                return
            bucket_values.append(token)
            bucket_values[:] = sorted({str(v).strip().lower() for v in bucket_values if str(v).strip()})
            hints["version"] = 1
            self.config_service.save_bucket_hints(hints)
            self.run_page.record_saved_hint(source, kind, bucket, token)
            self.run_page.set_review_feedback(f"Saved {kind} hint '{token}' -> {bucket}. Rerun to apply.", success=True)
            self._toast(f"Saved {kind} hint '{token}' -> {bucket}. Rerun to apply.", kind="success", timeout_ms=4200)
        except Exception as exc:
            self.run_page.set_review_feedback(f"Failed to save hint: {exc}", success=False)
            self._toast("Failed to save hint.", kind="warning")

    # ------------------------------------------------------------------
    # Run page / engine execution
    def _set_status(self, text: str, kind: str, pulsing: bool) -> None:
        self.header_status_badge.set_status(text, kind=kind, pulsing=pulsing)

    def _toast(self, text: str, kind: str = "info", timeout_ms: int = 3600) -> None:
        try:
            self.toast_host.show_toast(text, kind=kind, timeout_ms=timeout_ms)
        except Exception:
            pass

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
            QMessageBox.warning(self, "Run", "Please select a valid destination folder.")
            return
        output_name_warning = self._validate_output_folder_name(self.state.output_folder_name)
        if output_name_warning:
            QMessageBox.warning(self, "Run", output_name_warning)
            return
        try:
            inbox_resolved = Path(inbox_path).resolve()
            hub_resolved = Path(hub_path).resolve()
            if inbox_resolved == hub_resolved or hub_resolved.is_relative_to(inbox_resolved):
                QMessageBox.warning(
                    self,
                    "Run",
                    "Destination folder must be different from and not inside the inbox.",
                )
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
        self.engine_runner.logLine.connect(self.on_engine_log_line)
        self.engine_runner.progressEvent.connect(self.on_engine_progress_event)
        self.engine_runner.finished.connect(self.on_engine_finished)
        self.engine_runner.start()

    def on_engine_log_line(self, line: str) -> None:
        try:
            self.run_page.append_log_line(line)
        except Exception:
            pass

    def on_engine_progress_event(self, payload: dict) -> None:
        try:
            self.run_page.update_progress_event(dict(payload or {}))
        except Exception:
            pass

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

        bucket_colors = {
            str(bucket_id): str((style or {}).get("Color", ""))
            for bucket_id, style in dict((self.styles_data.get("buckets", {}) or {})).items()
            if isinstance(style, dict)
        }
        self.run_page.set_results(
            report,
            log_lines,
            bucket_choices=self._last_engine_bucket_ids,
            bucket_colors=bucket_colors,
        )
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
            self._toast("Run report saved successfully.", kind="success")
        except Exception as exc:
            QMessageBox.warning(self, "Save report", f"Failed to save report: {exc}")
