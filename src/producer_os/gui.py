"""
Producer OS GUI Wizard

This module implements a full wizard interface for Producer OS
using PySide6.  The wizard guides the user through four steps:

1. **Inbox** – Select the source folder containing packs to be
   sorted.  A summary of the number of packs and loose files is
   shown, and the user can choose whether to perform a dry run.
2. **Hub** – Select the destination hub folder.  The user can
   choose whether files should be copied or moved.  A warning is
   displayed if the hub is the same as or inside the inbox.
3. **Options** – Configure optional behaviours such as file type
   handling, preserving vendor structure, loop safety, FL Studio
   styling and theme.  A developer tools toggle reveals advanced
   diagnostics.
4. **Run** – Execute the sort or analysis.  The page shows a
   progress indicator while running and displays a summary of
   results upon completion.  The user can save the run report
   from this page and open the logs directory.

Configuration is persisted via :class:`producer_os.config_service.ConfigService`.
Changes are saved immediately when the user modifies any field.
The engine is invoked asynchronously on a background thread to
prevent UI blocking.  Only one engine run may be active at a
time.

The GUI intentionally avoids altering the engine’s behaviour.
All routing logic, classification heuristics and file I/O remain
inside :class:`producer_os.engine.ProducerOSEngine`.  The GUI merely
collects options, passes them to the engine, and displays results.

To launch the GUI run:

.. code-block:: bash

    python -m producer_os.gui
"""

from __future__ import annotations

import sys
import threading
import json
from pathlib import Path
from typing import Any, Dict, Optional
SortConfig = Dict[str, Any]

try:
    from PySide6.QtCore import Qt, QUrl, Signal, QObject
    from PySide6.QtGui import QColor, QPalette, QDesktopServices
    from PySide6.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QStackedWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QFileDialog,
        QCheckBox,
        QComboBox,
        QTextEdit,
        QProgressBar,
        QFormLayout,
        QMessageBox,
    )
    import PySide6
except ImportError:
    # PySide6 is optional; if not installed the GUI cannot be used.
    PySide6 = None  # type: ignore

from producer_os.engine import ProducerOSEngine
from producer_os.styles_service import StyleService
from producer_os.config_service import load_config, save_config

print("Producer OS GUI starting...")

class EngineRunner(QObject):
    """Helper to run the engine on a background thread.

    Emits a ``finished`` signal when complete with the report
    dictionary and the absolute path to the generated run report JSON.
    """

    finished = Signal(dict, str)

    def __init__(self, engine: ProducerOSEngine, mode: str) -> None:
        super().__init__()
        self.engine = engine
        self.mode = mode

    def start(self) -> None:
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self) -> None:
        report = self.engine.run(mode=self.mode)

        run_id = report.get("run_id")
        hub_dir = self.engine.hub_dir

        report_path = ""
        try:
            if run_id:
                candidate = hub_dir / "logs" / str(run_id) / "run_report.json"
                if candidate.exists():
                    report_path = str(candidate)
        except Exception:
            report_path = ""

        self.finished.emit(report, report_path)


class WizardPage(QWidget):
    """Base class for wizard pages providing convenience methods."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(6)


class ProducerOSWizard(QMainWindow):
    """Main window implementing the Producer OS wizard."""

    def __init__(self) -> None:
        super().__init__()
        if PySide6 is None:
            # Show an error if PySide6 is not installed
            raise ImportError(
                "PySide6 is not installed. Please install PySide6 to use the GUI."
            )
        self.setWindowTitle("Producer OS Wizard")
        self.resize(800, 600)
        # Configuration
        self.app_dir = Path(__file__).resolve().parents[1]
        self.config_service = ConfigService(app_dir=self.app_dir)
        self.config: Dict[str, Any] = self.config_service.load_config()
        # Load styles for display names
        self.styles_data = self.config_service.load_styles()
        self.style_service = StyleService(self.styles_data)
        # Load bucket mapping
        buckets_data = self.config_service.load_buckets()
        self.bucket_service = BucketService(buckets_data)
        # GUI state
        self.state: Dict[str, Any] = {
            "inbox_path": self.config.get("inbox_path", ""),
            "hub_path": self.config.get("hub_path", ""),
            "action": self.config.get("action", "move"),  # move or copy
            "dry_run": self.config.get("dry_run", False),
            "preserve_vendor": self.config.get("preserve_vendor", True),
            "file_types": self.config.get("file_types", {"wav": True, "mp3": False, "flac": False}),
            "loop_safety": self.config.get("loop_safety", True),
            "theme": self.config.get("theme", "system"),
            "developer_tools": self.config.get("developer_tools", False),
        }
        # Set up central stacked widget
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        # Create pages
        self.page1 = self._create_inbox_page()
        self.page2 = self._create_hub_page()
        self.page3 = self._create_options_page()
        self.page4 = self._create_run_page()
        for page in [self.page1, self.page2, self.page3, self.page4]:
            self.stack.addWidget(page)
        # Navigation buttons
        nav_layout = QHBoxLayout()
        self.prev_btn = QPushButton("← Back")
        self.next_btn = QPushButton("Next →")
        self.prev_btn.clicked.connect(self.go_previous)
        self.next_btn.clicked.connect(self.go_next)
        nav_layout.addWidget(self.prev_btn)
        nav_layout.addStretch()
        nav_layout.addWidget(self.next_btn)
        # Main layout includes stacked pages and navigation
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.addWidget(self.stack)
        container_layout.addLayout(nav_layout)
        self.setCentralWidget(container)
        self.update_nav_buttons()
        # Apply initial theme
        self.apply_theme(self.state["theme"])

    # ------------------------------------------------------------------
    # Page 1: Inbox selection
    def _create_inbox_page(self) -> QWidget:
        page = WizardPage()
        title = QLabel("Step 1 – Choose Inbox (Source)")
        title.setStyleSheet("font-weight: bold; font-size: 18px;")
        page.layout.addWidget(title)
        form_layout = QFormLayout()
        # Inbox path
        self.inbox_edit = QLineEdit(self.state.get("inbox_path", ""))
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self.browse_inbox)
        hbox = QHBoxLayout()
        hbox.addWidget(self.inbox_edit)
        hbox.addWidget(browse_btn)
        form_layout.addRow("Inbox folder:", hbox)
        # Preview labels
        self.inbox_preview_label = QLabel("Preview: 0 packs, 0 loose files")
        form_layout.addRow("Preview:", self.inbox_preview_label)
        # Dry run checkbox
        self.dry_run_checkbox = QCheckBox("Dry run (no file operations)")
        self.dry_run_checkbox.setChecked(self.state.get("dry_run", False))
        self.dry_run_checkbox.toggled.connect(self.on_dry_run_changed)
        form_layout.addRow(self.dry_run_checkbox)
        page.layout.addLayout(form_layout)
        # Update preview when path changes
        self.inbox_edit.textChanged.connect(self.update_inbox_preview)
        # Initial preview
        self.update_inbox_preview()
        return page

    def browse_inbox(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Inbox Folder", self.inbox_edit.text() or str(Path.home()))
        if directory:
            self.inbox_edit.setText(directory)
            self.state["inbox_path"] = directory
            self.save_setting("inbox_path", directory)
            self.update_inbox_preview()

    def update_inbox_preview(self) -> None:
        path_str = self.inbox_edit.text()
        count_packs = 0
        count_loose = 0
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
        self.inbox_preview_label.setText(f"Preview: {count_packs} pack(s), {count_loose} loose file(s)")
        # Save state
        self.state["inbox_path"] = path_str
        self.save_setting("inbox_path", path_str)

    def on_dry_run_changed(self, checked: bool) -> None:
        self.state["dry_run"] = checked
        self.save_setting("dry_run", checked)

    # ------------------------------------------------------------------
    # Page 2: Hub selection and action
    def _create_hub_page(self) -> QWidget:
        page = WizardPage()
        title = QLabel("Step 2 – Choose Hub (Destination)")
        title.setStyleSheet("font-weight: bold; font-size: 18px;")
        page.layout.addWidget(title)
        form_layout = QFormLayout()
        # Hub path
        self.hub_edit = QLineEdit(self.state.get("hub_path", ""))
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self.browse_hub)
        hbox = QHBoxLayout()
        hbox.addWidget(self.hub_edit)
        hbox.addWidget(browse_btn)
        form_layout.addRow("Hub folder:", hbox)
        # Action dropdown (move or copy)
        self.action_combo = QComboBox()
        self.action_combo.addItems(["move", "copy"])
        current_action = self.state.get("action", "move")
        idx = self.action_combo.findText(current_action)
        if idx >= 0:
            self.action_combo.setCurrentIndex(idx)
        self.action_combo.currentTextChanged.connect(self.on_action_changed)
        form_layout.addRow("Action:", self.action_combo)
        # Warning label
        self.hub_warning_label = QLabel("")
        self.hub_warning_label.setStyleSheet("color: red;")
        form_layout.addRow("", self.hub_warning_label)
        page.layout.addLayout(form_layout)
        # Update warning when path or inbox changes
        self.hub_edit.textChanged.connect(self.update_hub_warning)
        self.inbox_edit.textChanged.connect(self.update_hub_warning)
        # Initial warning
        self.update_hub_warning()
        return page

    def browse_hub(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Hub Folder", self.hub_edit.text() or str(Path.home()))
        if directory:
            self.hub_edit.setText(directory)
            self.state["hub_path"] = directory
            self.save_setting("hub_path", directory)
            self.update_hub_warning()

    def update_hub_warning(self) -> None:
        inbox_str = self.inbox_edit.text()
        hub_str = self.hub_edit.text()
        warning = ""
        if inbox_str and hub_str:
            try:
                inbox_path = Path(inbox_str).resolve()
                hub_path = Path(hub_str).resolve()
                if inbox_path == hub_path:
                    warning = "Hub folder must be different from inbox."
                elif inbox_path in hub_path.parents:
                    warning = "Hub folder cannot be inside the inbox."
            except Exception:
                pass
        self.hub_warning_label.setText(warning)
        # Save state
        self.state["hub_path"] = hub_str
        self.save_setting("hub_path", hub_str)

    def on_action_changed(self, action: str) -> None:
        self.state["action"] = action
        self.save_setting("action", action)

    # ------------------------------------------------------------------
    # Page 3: Options
    def _create_options_page(self) -> QWidget:
        page = WizardPage()
        title = QLabel("Step 3 – Options")
        title.setStyleSheet("font-weight: bold; font-size: 18px;")
        page.layout.addWidget(title)
        form_layout = QFormLayout()
        # File types
        self.wav_checkbox = QCheckBox("WAV")
        self.mp3_checkbox = QCheckBox("MP3")
        self.flac_checkbox = QCheckBox("FLAC")
        self.wav_checkbox.setChecked(self.state.get("file_types", {}).get("wav", True))
        self.mp3_checkbox.setChecked(self.state.get("file_types", {}).get("mp3", False))
        self.flac_checkbox.setChecked(self.state.get("file_types", {}).get("flac", False))
        self.wav_checkbox.toggled.connect(lambda c: self.on_file_type_changed("wav", c))
        self.mp3_checkbox.toggled.connect(lambda c: self.on_file_type_changed("mp3", c))
        self.flac_checkbox.toggled.connect(lambda c: self.on_file_type_changed("flac", c))
        hbox_ft = QHBoxLayout()
        hbox_ft.addWidget(self.wav_checkbox)
        hbox_ft.addWidget(self.mp3_checkbox)
        hbox_ft.addWidget(self.flac_checkbox)
        form_layout.addRow("File types:", hbox_ft)
        # Preserve vendor structure
        self.vendor_checkbox = QCheckBox("Preserve vendor structure (keep original folder layout)")
        self.vendor_checkbox.setChecked(self.state.get("preserve_vendor", True))
        self.vendor_checkbox.toggled.connect(self.on_preserve_vendor_changed)
        form_layout.addRow(self.vendor_checkbox)
        # Loop safety
        self.loop_checkbox = QCheckBox("Avoid reprocessing duplicate loops (prevent '(2)' spam)")
        self.loop_checkbox.setChecked(self.state.get("loop_safety", True))
        self.loop_checkbox.toggled.connect(self.on_loop_safety_changed)
        form_layout.addRow(self.loop_checkbox)
        # FL Studio styling note
        info_label = QLabel(
            "FL Studio styling uses IconIndex only. Sidecar .nfo files are written next to folders (not inside)."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-style: italic; color: gray;")
        form_layout.addRow(info_label)
        # Theme selection
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["system", "dark", "light"])
        idx = self.theme_combo.findText(self.state.get("theme", "system"))
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)
        form_layout.addRow("Theme:", self.theme_combo)
        # Developer tools toggle
        self.dev_checkbox = QCheckBox("Developer tools")
        self.dev_checkbox.setChecked(self.state.get("developer_tools", False))
        self.dev_checkbox.toggled.connect(self.on_dev_tools_changed)
        form_layout.addRow(self.dev_checkbox)
        # Developer tools buttons (initially hidden)
        self.dev_buttons_container = QWidget()
        dev_layout = QVBoxLayout(self.dev_buttons_container)
        self.open_config_btn = QPushButton("Open config folder")
        self.open_config_btn.clicked.connect(self.open_config_folder)
        self.open_last_report_btn = QPushButton("Open last report")
        self.open_last_report_btn.clicked.connect(self.open_last_report)
        self.validate_schema_btn = QPushButton("Validate schemas")
        self.validate_schema_btn.clicked.connect(self.validate_schemas)
        dev_layout.addWidget(self.open_config_btn)
        dev_layout.addWidget(self.open_last_report_btn)
        dev_layout.addWidget(self.validate_schema_btn)
        form_layout.addRow(self.dev_buttons_container)
        # Hide dev buttons if tools off
        self.dev_buttons_container.setVisible(self.dev_checkbox.isChecked())
        page.layout.addLayout(form_layout)
        return page

    def on_file_type_changed(self, ext: str, checked: bool) -> None:
        file_types = self.state.get("file_types", {})
        file_types[ext] = checked
        self.state["file_types"] = file_types
        self.save_setting("file_types", file_types)

    def on_preserve_vendor_changed(self, checked: bool) -> None:
        self.state["preserve_vendor"] = checked
        self.save_setting("preserve_vendor", checked)

    def on_loop_safety_changed(self, checked: bool) -> None:
        self.state["loop_safety"] = checked
        self.save_setting("loop_safety", checked)

    def on_theme_changed(self, theme: str) -> None:
        self.state["theme"] = theme
        self.apply_theme(theme)
        self.save_setting("theme", theme)

    def on_dev_tools_changed(self, checked: bool) -> None:
        self.state["developer_tools"] = checked
        self.save_setting("developer_tools", checked)
        self.dev_buttons_container.setVisible(checked)

    def open_config_folder(self) -> None:
    # Open config directory in file explorer
         cfg_dir = self.config_service.get_config_dir()
         QDesktopServices.openUrl(QUrl.fromLocalFile(str(cfg_dir)))

    def open_last_report(self) -> None:
        # Find most recent run_report.json in hub logs
        hub_path_str = self.state.get("hub_path")
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
        # Sort by modification time descending
        reports.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        report_path = reports[0]
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(report_path)))

    def validate_schemas(self) -> None:
        # Validate loaded config and styles against schemas and show result
        try:
            self.config_service.load_config()
            self.config_service.load_styles()
            self.config_service.load_buckets()
            QMessageBox.information(
                self,
                "Schema validation",
                "All configuration files are valid."
            )
        except Exception as exc:
            QMessageBox.warning(self, "Schema validation", f"Validation failed: {exc}")

    # ------------------------------------------------------------------
    # Page 4: Run and results
    def _create_run_page(self) -> QWidget:
        page = WizardPage()
        title = QLabel("Step 4 – Run")
        title.setStyleSheet("font-weight: bold; font-size: 18px;")
        page.layout.addWidget(title)
        # Buttons for analyze and run
        btn_layout = QHBoxLayout()
        self.analyze_btn = QPushButton("Analyze")
        self.run_btn = QPushButton("Run")
        btn_layout.addWidget(self.analyze_btn)
        btn_layout.addWidget(self.run_btn)
        page.layout.addLayout(btn_layout)
        # Connect signals
        self.analyze_btn.clicked.connect(lambda: self.start_engine_run("analyze"))
        self.run_btn.clicked.connect(lambda: self.start_engine_run(self.state.get("action", "move")))
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate by default
        self.progress_bar.setVisible(False)
        page.layout.addWidget(self.progress_bar)
        # Log / output area
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        page.layout.addWidget(self.log_edit)
        # Summary label
        self.summary_label = QLabel("")
        page.layout.addWidget(self.summary_label)
        # Save report button
        self.save_report_btn = QPushButton("Save run report…")
        self.save_report_btn.setVisible(False)
        self.save_report_btn.clicked.connect(self.save_run_report)
        page.layout.addWidget(self.save_report_btn)
        return page

    def start_engine_run(self, mode: str) -> None:
        # Validate inputs
        inbox_path = self.state.get("inbox_path")
        hub_path = self.state.get("hub_path")
        if not inbox_path or not Path(inbox_path).exists():
            QMessageBox.warning(self, "Run", "Please select a valid inbox folder.")
            return
        if not hub_path or not Path(hub_path).exists():
            QMessageBox.warning(self, "Run", "Please select a valid hub folder.")
            return
        if inbox_path == hub_path or Path(hub_path).resolve().is_relative_to(Path(inbox_path).resolve()):
            QMessageBox.warning(self, "Run", "Hub folder must be different from and not inside the inbox.")
            return
        # Disable buttons and show progress bar
        self.analyze_btn.setEnabled(False)
        self.run_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.log_edit.clear()
        self.summary_label.setText("")
        self.save_report_btn.setVisible(False)
        # Create engine instance
        engine = ProducerOSEngine(
            inbox_dir=Path(inbox_path),
            hub_dir=Path(hub_path),
            style_service=self.style_service,
            config=self.config,
            bucket_service=self.bucket_service,
        )
        # Run on background thread
        self.engine_runner = EngineRunner(engine, mode)
        self.engine_runner.finished.connect(self.on_engine_finished)
        self.engine_runner.start()

    def on_engine_finished(self, report: dict, report_path: str) -> None:
        # Re-enable buttons
        self.analyze_btn.setEnabled(True)
        self.run_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.current_report = report
        self.current_report_path = report_path
        self.save_report_btn.setVisible(True)
        # Display summary
        summary_parts = []
        if report:
            summary_parts.append(f"Processed {report.get('files_processed', 0)} file(s)")
            summary_parts.append(f"Moved {report.get('files_moved', 0)}")
            summary_parts.append(f"Copied {report.get('files_copied', 0)}")
            summary_parts.append(f"Unsorted {report.get('unsorted', 0)}")
        self.summary_label.setText(
            "<b>Summary:</b> " + "; ".join(summary_parts)
        )
        # Show log: list packs and counts
        log_lines = []
        for pack in report.get("packs", []):
            files = pack.get("files", [])
            counts = {}
            for f in files:
                bucket = f.get("bucket")
                counts[bucket] = counts.get(bucket, 0) + 1
            counts_str = ", ".join(f"{b}: {c}" for b, c in counts.items())
            log_lines.append(f"{pack['pack']}: {counts_str}")
        self.log_edit.setPlainText("\n".join(log_lines))
        # Save report path for exporting
        self.current_report_path = report_path
        self.save_report_btn.setVisible(True)

    def save_run_report(self) -> None:
        # Ask where to save
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save run report",
            str(Path.home() / "run_report.json"),
            "JSON files (*.json)"
        )
        if not dest:
            return

        try:
            # If we have a real report file from hub logs, copy it
            if getattr(self, "current_report_path", "") and Path(self.current_report_path).exists():
                data = Path(self.current_report_path).read_text(encoding="utf-8")
                Path(dest).write_text(data, encoding="utf-8")
            else:
                # Otherwise (Analyze mode), save the in-memory report
                report_obj = getattr(self, "current_report", {})
                Path(dest).write_text(
                    __import__("json").dumps(report_obj, indent=2),
                    encoding="utf-8"
                )

            QMessageBox.information(self, "Save report", "Report saved successfully.")
        except Exception as exc:
            QMessageBox.warning(self, "Save report", f"Failed to save report: {exc}")

    # ------------------------------------------------------------------
    # Navigation between pages
    def go_next(self) -> None:
        index = self.stack.currentIndex()
        if index < self.stack.count() - 1:
            self.stack.setCurrentIndex(index + 1)
            self.update_nav_buttons()

    def go_previous(self) -> None:
        index = self.stack.currentIndex()
        if index > 0:
            self.stack.setCurrentIndex(index - 1)
            self.update_nav_buttons()

    def update_nav_buttons(self) -> None:
        idx = self.stack.currentIndex()
        self.prev_btn.setEnabled(idx > 0)
        if idx == self.stack.count() - 1:
            self.next_btn.setEnabled(False)
        else:
            self.next_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Configuration persistence
    def save_setting(self, key: str, value: Any) -> None:
        self.config[key] = value
        try:
            self.config_service.save_config(self.config)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Theme handling
    def apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if not app:
            return
        if theme == "system":
            # Reset palette to system
            app.setStyle("Fusion")
            app.setPalette(app.style().standardPalette())
            return
        # Use Fusion style for custom palettes
        app.setStyle("Fusion")
        palette = QPalette()
        if theme == "dark":
            # Based on Fusion dark palette
            palette.setColor(QPalette.Window, QColor(53, 53, 53))
            palette.setColor(QPalette.WindowText, Qt.white)
            palette.setColor(QPalette.Base, QColor(25, 25, 25))
            palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
            palette.setColor(QPalette.ToolTipBase, Qt.white)
            palette.setColor(QPalette.ToolTipText, Qt.white)
            palette.setColor(QPalette.Text, Qt.white)
            palette.setColor(QPalette.Button, QColor(53, 53, 53))
            palette.setColor(QPalette.ButtonText, Qt.white)
            palette.setColor(QPalette.BrightText, Qt.red)
            palette.setColor(QPalette.Link, QColor(42, 130, 218))
            palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
            palette.setColor(QPalette.HighlightedText, Qt.black)
        else:  # light
            palette.setColor(QPalette.Window, Qt.white)
            palette.setColor(QPalette.WindowText, Qt.black)
            palette.setColor(QPalette.Base, Qt.white)
            palette.setColor(QPalette.AlternateBase, QColor(240, 240, 240))
            palette.setColor(QPalette.ToolTipBase, Qt.black)
            palette.setColor(QPalette.ToolTipText, Qt.white)
            palette.setColor(QPalette.Text, Qt.black)
            palette.setColor(QPalette.Button, QColor(240, 240, 240))
            palette.setColor(QPalette.ButtonText, Qt.black)
            palette.setColor(QPalette.BrightText, Qt.red)
            palette.setColor(QPalette.Link, QColor(0, 122, 204))
            palette.setColor(QPalette.Highlight, QColor(0, 122, 204))
            palette.setColor(QPalette.HighlightedText, Qt.white)
        app.setPalette(palette)


def main() -> int:
    """Entry point for launching the GUI from the command line."""
    if PySide6 is None:
        print("Error: PySide6 is not installed. Please install PySide6 to use the GUI.")
        return 1
    app = QApplication(sys.argv)
    wizard = ProducerOSWizard()
    wizard.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
