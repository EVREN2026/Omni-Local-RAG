from __future__ import annotations

from pathlib import Path

from PyQt5 import uic
from PyQt5.QtWidgets import QLayout, QWidget


def ui_root() -> Path:
    return Path(__file__).resolve().parents[2] / "ui"


def ui_path(filename: str) -> Path:
    path = ui_root() / filename
    if not path.exists():
        raise FileNotFoundError(f"UI file not found: {path}")
    return path


def load_ui(instance, filename: str):
    return uic.loadUi(str(ui_path(filename)), instance)


def load_ui_widget(filename: str):
    return uic.loadUi(str(ui_path(filename)))


def require_child(owner, widget_type, name: str, ui_name: str = "UI"):
    widget = owner.findChild(widget_type, name)
    if widget is None:
        raise RuntimeError(
            f"{ui_name} is missing required widget '{name}'. "
            "Please keep this objectName in the .ui file or update the corresponding Python view."
        )
    return widget


def optional_child(owner, widget_type, name: str):
    return owner.findChild(widget_type, name)


def resolve_layout(
    owner,
    *,
    host_widget_name: str | None = None,
    layout_name: str | None = None,
    fallback_widget_name: str | None = None,
    ui_name: str = "UI",
) -> QLayout:
    if host_widget_name:
        host = owner.findChild(QWidget, host_widget_name)
        if host is not None and host.layout() is not None:
            return host.layout()

    if layout_name:
        layout = owner.findChild(QLayout, layout_name)
        if layout is not None:
            return layout

    if fallback_widget_name:
        fallback = owner.findChild(QWidget, fallback_widget_name)
        if fallback is not None and fallback.layout() is not None:
            return fallback.layout()

    wanted = [name for name in (host_widget_name, layout_name, fallback_widget_name) if name]
    raise RuntimeError(
        f"{ui_name} is missing a required layout container. "
        f"Expected one of: {', '.join(wanted)}"
    )
