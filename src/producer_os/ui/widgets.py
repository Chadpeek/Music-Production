from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QEvent, QObject, QRect, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from producer_os.ui.animations import animate_reveal, pulse_opacity, stop_pulse


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_widget_role(button: QPushButton, role: str) -> None:
    button.setProperty("role", role)
    repolish(button)


class CardFrame(QFrame):
    """Reusable elevated card container."""

    def __init__(self, title: str | None = None, subtitle: str | None = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardFrame")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        self.layout_root = layout

        self._title_label: Optional[QLabel] = None
        self._subtitle_label: Optional[QLabel] = None
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("SectionTitle")
            self._title_label = title_label
            layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("FieldHint")
            subtitle_label.setWordWrap(True)
            self._subtitle_label = subtitle_label
            layout.addWidget(subtitle_label)

        self.body_layout = QVBoxLayout()
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        layout.addLayout(self.body_layout)

    def apply_density(self, density: str) -> None:
        compact = str(density).strip().lower() == "compact"
        self.layout_root.setContentsMargins(14 if compact else 18, 12 if compact else 16, 14 if compact else 18, 12 if compact else 16)
        self.layout_root.setSpacing(8 if compact else 12)
        self.body_layout.setSpacing(8 if compact else 12)


class HeaderBlock(QWidget):
    def __init__(self, title: str, subtitle: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        layout.addWidget(self.title_label)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("PageSubtitle")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)
        self._layout = layout

    def apply_density(self, density: str) -> None:
        compact = str(density).strip().lower() == "compact"
        self._layout.setSpacing(4 if compact else 6)


class StatChip(QFrame):
    def __init__(self, label: str, value: str = "0", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatChip")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(3)
        self.value_label = QLabel(value)
        self.value_label.setObjectName("StatValue")
        self.label_label = QLabel(label)
        self.label_label.setObjectName("StatLabel")
        layout.addWidget(self.value_label)
        layout.addWidget(self.label_label)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)

    def apply_density(self, density: str) -> None:
        compact = str(density).strip().lower() == "compact"
        layout = self.layout()
        if layout is None:
            return
        layout.setContentsMargins(10 if compact else 14, 8 if compact else 12, 10 if compact else 14, 7 if compact else 10)
        layout.setSpacing(2 if compact else 3)


class StatusBadge(QLabel):
    def __init__(self, text: str = "Ready", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("StatusBadge")
        self.set_status(text, kind="neutral", pulsing=False)

    def set_status(self, text: str, kind: str = "neutral", pulsing: bool = False) -> None:
        self.setText(text)
        self.setProperty("badgeKind", kind)
        repolish(self)
        if pulsing:
            pulse_opacity(self)
        else:
            stop_pulse(self)


class SegmentedControl(QFrame):
    valueChanged = Signal(str)

    def __init__(self, values: Iterable[str], current: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentedGroup")
        self._buttons: dict[str, QPushButton] = {}
        self._value = current
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        for value in values:
            button = QPushButton(value.capitalize())
            button.setCheckable(True)
            button.setProperty("segmented", "true")
            button.clicked.connect(lambda checked=False, v=value: self.set_value(v, emit=True))
            self._buttons[value] = button
            layout.addWidget(button)
        self.set_value(current, emit=False)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str, emit: bool = False) -> None:
        if value not in self._buttons:
            return
        self._value = value
        for key, button in self._buttons.items():
            with QSignalBlocker(button):
                button.setChecked(key == value)
            button.setProperty("checked", "true" if key == value else "false")
            repolish(button)
        if emit:
            self.valueChanged.emit(value)


class NoWheelComboBox(QComboBox):
    """Ignore wheel events unless the dropdown is explicitly open."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        try:
            view = self.view()
        except Exception:
            view = None
        if view is not None and view.isVisible():
            super().wheelEvent(event)
            return
        event.ignore()


class ThemePreviewCard(QFrame):
    clicked = Signal(str)

    def __init__(self, theme_id: str, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.theme_id = theme_id
        self.setObjectName("ThemePreviewCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", False)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        self._root_layout = root

        self.strip = QFrame()
        self.strip.setObjectName("ThemePreviewStrip")
        strip_layout = QVBoxLayout(self.strip)
        strip_layout.setContentsMargins(8, 8, 8, 8)
        strip_layout.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        self.accent_dot = QFrame()
        self.accent_dot.setObjectName("ThemePreviewAccent")
        self.accent_dot.setFixedSize(10, 10)
        header_row.addWidget(self.accent_dot)
        header_row.addStretch(1)
        strip_layout.addLayout(header_row)

        chip_row = QHBoxLayout()
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(6)
        for txt in ("Cards", "Tables"):
            chip = QFrame()
            chip.setObjectName("ThemePreviewChip")
            chip_layout = QHBoxLayout(chip)
            chip_layout.setContentsMargins(6, 2, 6, 2)
            chip_layout.setSpacing(0)
            lbl = QLabel(txt)
            lbl.setObjectName("ThemePreviewChipText")
            chip_layout.addWidget(lbl)
            chip_row.addWidget(chip)
        chip_row.addStretch(1)
        strip_layout.addLayout(chip_row)
        root.addWidget(self.strip)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("ThemePreviewTitle")
        root.addWidget(self.title_label)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("ThemePreviewMeta")
        root.addWidget(self.meta_label)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", bool(selected))
        repolish(self)

    def set_density_text(self, density_label: str) -> None:
        self.meta_label.setText(density_label)

    def apply_density(self, density: str) -> None:
        compact = str(density).strip().lower() == "compact"
        self._root_layout.setContentsMargins(8 if compact else 10, 8 if compact else 10, 8 if compact else 10, 8 if compact else 10)
        self._root_layout.setSpacing(6 if compact else 8)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.theme_id)
        super().mousePressEvent(event)


class ToastMessage(QFrame):
    closed = Signal(object)

    def __init__(self, title: str, kind: str = "info", timeout_ms: int = 3800, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToastMessage")
        self.setProperty("toastKind", kind)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        icon = QLabel(self._icon_for_kind(kind))
        icon.setObjectName("ToastIcon")
        layout.addWidget(icon)
        label = QLabel(title)
        label.setObjectName("ToastText")
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(max(800, int(timeout_ms)))
        repolish(self)

    def _icon_for_kind(self, kind: str) -> str:
        return {
            "success": "✓",
            "warning": "!",
            "error": "×",
        }.get(kind, "i")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.close()
        super().mousePressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.closed.emit(self)
        super().closeEvent(event)


class ToastHost(QWidget):
    def __init__(self, anchor: QWidget) -> None:
        super().__init__(anchor)
        self._anchor = anchor
        self.setObjectName("ToastHost")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setVisible(False)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 10, 10, 0)
        self._layout.setSpacing(8)
        self._layout.addStretch(1)
        self._anchor.installEventFilter(self)
        self._sync_geometry()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self._anchor and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Move,
            QEvent.Type.Show,
        }:
            self._sync_geometry()
        return super().eventFilter(watched, event)

    def _sync_geometry(self) -> None:
        self.setGeometry(self._anchor.rect())
        self.raise_()

    def show_toast(self, text: str, *, kind: str = "info", timeout_ms: int = 3800) -> None:
        self._sync_geometry()
        toast = ToastMessage(text, kind=kind, timeout_ms=timeout_ms, parent=self)
        toast.closed.connect(self._on_toast_closed)
        toast.setMaximumWidth(420)
        row = QWidget(self)
        row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(0)
        row_layout.addStretch(1)
        row_layout.addWidget(toast)
        self._layout.insertWidget(0, row)
        self.setVisible(True)
        row.show()
        toast.show()

    def _on_toast_closed(self, toast_obj: object) -> None:
        toast = toast_obj if isinstance(toast_obj, ToastMessage) else None
        if toast is None:
            return
        row = toast.parentWidget()
        if row is not None:
            row.deleteLater()
        if self._layout.count() <= 1:
            self.setVisible(False)


class StepItem(QFrame):
    clicked = Signal()

    def __init__(self, index: int, title: str, description: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.index = index
        self.setObjectName("StepItem")
        self.setProperty("stepState", "idle")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(66)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        idx_label = QLabel(f"{index + 1:02d}")
        idx_label.setObjectName("StepIndex")
        idx_label.setFixedWidth(24)
        root.addWidget(idx_label)

        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(18)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = self._icon_for_step_title(title)
        if not icon.isNull():
            self.icon_label.setPixmap(icon.pixmap(16, 16))
        root.addWidget(self.icon_label)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("StepItemTitle")
        text_col.addWidget(self.title_label)

        self.desc_label = QLabel(description)
        self.desc_label.setObjectName("StepItemDesc")
        self.desc_label.setWordWrap(True)
        text_col.addWidget(self.desc_label)

        root.addLayout(text_col, 1)
        repolish(self)

    def _icon_for_step_title(self, title: str):
        style = self.style()
        title_key = (title or "").strip().lower()
        pix = None
        if "inbox" in title_key:
            pix = getattr(QStyle.StandardPixmap, "SP_DirOpenIcon", QStyle.StandardPixmap.SP_DirIcon)
        elif "hub" in title_key or "destination" in title_key:
            pix = getattr(QStyle.StandardPixmap, "SP_DriveHDIcon", QStyle.StandardPixmap.SP_DirIcon)
        elif "option" in title_key:
            pix = getattr(QStyle.StandardPixmap, "SP_FileDialogDetailedView", QStyle.StandardPixmap.SP_FileDialogInfoView)
        elif "run" in title_key:
            pix = getattr(QStyle.StandardPixmap, "SP_MediaPlay", QStyle.StandardPixmap.SP_ArrowForward)
        else:
            pix = QStyle.StandardPixmap.SP_FileDialogInfoView
        return style.standardIcon(pix)

    def set_step_state(self, state: str) -> None:
        self.setProperty("stepState", state)
        repolish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StepSidebar(QFrame):
    stepSelected = Signal(int)

    def __init__(self, steps: list[tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarCard")
        self._items: list[StepItem] = []
        self._current_index = 0
        self._max_clickable = 0
        self._invalid_indices: set[int] = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("Workflow")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        hint = QLabel("Guided setup and run orchestration")
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._stack = QWidget()
        self._stack_layout = QVBoxLayout(self._stack)
        self._stack_layout.setContentsMargins(0, 6, 0, 6)
        self._stack_layout.setSpacing(8)
        layout.addWidget(self._stack, 1)

        self._highlight = QFrame(self._stack)
        self._highlight.setObjectName("StepHighlight")
        self._highlight.setFixedWidth(4)
        self._highlight.raise_()
        self._highlight.hide()

        for idx, (step_title, desc) in enumerate(steps):
            item = StepItem(idx, step_title, desc, self._stack)
            item.clicked.connect(lambda i=idx: self._on_item_clicked(i))
            self._items.append(item)
            self._stack_layout.addWidget(item)
        self._stack_layout.addSpacerItem(
            QSpacerItem(20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

        self.set_max_clickable(0)
        self.set_current_index(0, animate=False)

    def _on_item_clicked(self, index: int) -> None:
        if index <= self._max_clickable:
            self.stepSelected.emit(index)

    def set_max_clickable(self, index: int) -> None:
        self._max_clickable = max(0, index)
        for item in self._items:
            item.setEnabled(item.index <= self._max_clickable)
            cursor = Qt.CursorShape.PointingHandCursor if item.isEnabled() else Qt.CursorShape.ArrowCursor
            item.setCursor(cursor)

    def set_invalid_indices(self, indices: set[int]) -> None:
        self._invalid_indices = set(indices)
        self._apply_states()

    def set_current_index(self, index: int, animate: bool = True) -> None:
        if not self._items:
            return
        self._current_index = max(0, min(index, len(self._items) - 1))
        self._apply_states()
        self._move_highlight(animate=animate)

    def _apply_states(self) -> None:
        for item in self._items:
            if item.index == self._current_index:
                state = "current"
            elif item.index in self._invalid_indices:
                state = "invalid"
            elif item.index < self._current_index:
                state = "done"
            else:
                state = "idle"
            item.set_step_state(state)

    def _highlight_rect_for(self, item: StepItem) -> QRect:
        g = item.geometry()
        return QRect(0, g.y() + 10, 4, max(14, g.height() - 20))

    def _move_highlight(self, animate: bool = True) -> None:
        if not self._items:
            return
        item = self._items[self._current_index]
        target = self._highlight_rect_for(item)
        self._highlight.show()
        if not animate or not self._highlight.isVisible():
            self._highlight.setGeometry(target)
            return
        from PySide6.QtCore import QEasingCurve, QPropertyAnimation

        anim = QPropertyAnimation(self._highlight, b"geometry", self._highlight)
        anim.setDuration(220)
        anim.setStartValue(self._highlight.geometry())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        from producer_os.ui.animations import _keep_animation  # local import to avoid cycle

        _keep_animation(self._highlight, anim)
        anim.start()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._move_highlight(animate=False)


class AnimatedPanel(QWidget):
    def __init__(self, child: QWidget, expanded: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.child = child
        layout.addWidget(child)
        if not expanded:
            self.setMaximumHeight(0)
            self.setVisible(False)

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        if animate:
            animate_reveal(self, expanded)
        else:
            self.setVisible(expanded)
            self.setMaximumHeight(16777215 if expanded else 0)
