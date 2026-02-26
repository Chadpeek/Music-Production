from __future__ import annotations

import platform
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSequentialAnimationGroup,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


def _supports_opacity_effects() -> bool:
    # Qt/PySide on Windows can spam QPainter warnings with QGraphicsOpacityEffect
    # on frequently-updated widgets (e.g. pulsing status labels).
    return platform.system().lower() != "windows"


def _anim_store(owner: Any) -> list[Any]:
    store = getattr(owner, "_ui_animations", None)
    if store is None:
        store = []
        setattr(owner, "_ui_animations", store)
    return store


def _keep_animation(owner: Any, animation: Any) -> None:
    store = _anim_store(owner)
    store.append(animation)

    def _cleanup() -> None:
        try:
            store.remove(animation)
        except ValueError:
            pass

    if hasattr(animation, "finished"):
        animation.finished.connect(_cleanup)


def _opacity_effect(widget: QWidget) -> QGraphicsOpacityEffect:
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        return effect
    new_effect = QGraphicsOpacityEffect(widget)
    new_effect.setOpacity(1.0)
    widget.setGraphicsEffect(new_effect)
    return new_effect


def fade_in(widget: QWidget, duration_ms: int = 220, start: float = 0.0, end: float = 1.0) -> None:
    if not _supports_opacity_effects():
        return
    effect = _opacity_effect(widget)
    effect.setOpacity(start)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration_ms)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    _keep_animation(widget, anim)
    anim.start()


def slide_fade_in(widget: QWidget, dx: int = 18, duration_ms: int = 240) -> None:
    end_pos = widget.pos()
    start_pos = QPoint(end_pos.x() + dx, end_pos.y())
    widget.move(start_pos)

    pos_anim = QPropertyAnimation(widget, b"pos", widget)
    pos_anim.setDuration(duration_ms)
    pos_anim.setStartValue(start_pos)
    pos_anim.setEndValue(end_pos)
    pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    group = QParallelAnimationGroup(widget)
    group.addAnimation(pos_anim)
    if _supports_opacity_effects():
        effect = _opacity_effect(widget)
        effect.setOpacity(0.0)
        opacity_anim = QPropertyAnimation(effect, b"opacity", widget)
        opacity_anim.setDuration(duration_ms)
        opacity_anim.setStartValue(0.0)
        opacity_anim.setEndValue(1.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(opacity_anim)
    _keep_animation(widget, group)
    group.start()


def pulse_opacity(widget: QWidget, low: float = 0.55, high: float = 1.0, duration_ms: int = 950) -> None:
    stop_pulse(widget)
    if not _supports_opacity_effects():
        return
    effect = _opacity_effect(widget)
    effect.setOpacity(high)

    down = QPropertyAnimation(effect, b"opacity", widget)
    down.setDuration(duration_ms // 2)
    down.setStartValue(high)
    down.setEndValue(low)
    down.setEasingCurve(QEasingCurve.Type.InOutSine)

    up = QPropertyAnimation(effect, b"opacity", widget)
    up.setDuration(duration_ms // 2)
    up.setStartValue(low)
    up.setEndValue(high)
    up.setEasingCurve(QEasingCurve.Type.InOutSine)

    seq = QSequentialAnimationGroup(widget)
    seq.addAnimation(down)
    seq.addAnimation(up)
    seq.setLoopCount(-1)
    setattr(widget, "_pulse_animation", seq)
    _keep_animation(widget, seq)
    seq.start()


def stop_pulse(widget: QWidget) -> None:
    seq = getattr(widget, "_pulse_animation", None)
    if seq is not None:
        try:
            seq.stop()
        finally:
            setattr(widget, "_pulse_animation", None)
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        if _supports_opacity_effects():
            effect.setOpacity(1.0)
        else:
            widget.setGraphicsEffect(None)  # type: ignore[arg-type]


def animate_reveal(widget: QWidget, expanded: bool, duration_ms: int = 220) -> None:
    widget.setVisible(True)
    start_height = widget.maximumHeight()
    if start_height < 0 or start_height > 10000:
        start_height = widget.sizeHint().height() if expanded else widget.height()
    end_height = widget.sizeHint().height() if expanded else 0

    height_anim = QPropertyAnimation(widget, b"maximumHeight", widget)
    height_anim.setDuration(duration_ms)
    height_anim.setStartValue(start_height)
    height_anim.setEndValue(end_height)
    height_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    group = QParallelAnimationGroup(widget)
    group.addAnimation(height_anim)
    effect: QGraphicsOpacityEffect | None = None
    if _supports_opacity_effects():
        effect = _opacity_effect(widget)
        effect.setOpacity(0.0 if (expanded and start_height == 0) else effect.opacity())
        opacity_anim = QPropertyAnimation(effect, b"opacity", widget)
        opacity_anim.setDuration(duration_ms)
        opacity_anim.setStartValue(effect.opacity())
        opacity_anim.setEndValue(1.0 if expanded else 0.0)
        opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(opacity_anim)

    def _finish() -> None:
        if expanded:
            widget.setMaximumHeight(16777215)
            if effect is not None:
                effect.setOpacity(1.0)
        else:
            widget.setVisible(False)
            widget.setMaximumHeight(0)
            if effect is not None:
                effect.setOpacity(1.0)

    group.finished.connect(_finish)
    _keep_animation(widget, group)
    group.start()
