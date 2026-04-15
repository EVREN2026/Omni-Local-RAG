from __future__ import annotations

from pathlib import Path


def ui_root() -> Path:
    return Path(__file__).resolve().parents[2] / "ui"


def ui_path(filename: str) -> Path:
    path = ui_root() / filename
    if not path.exists():
        raise FileNotFoundError(f"UI file not found: {path}")
    return path


def load_ui(instance, filename: str):
    try:
        from PyQt5 import uic  # type: ignore
    except Exception:
        return None
    return uic.loadUi(str(ui_path(filename)), instance)


def load_ui_widget(filename: str):
    try:
        from PyQt5 import uic  # type: ignore
    except Exception:
        return None
    return uic.loadUi(str(ui_path(filename)))


def require_child(owner, widget_type, name: str, ui_name: str = "UI"):
    widget = None
    if hasattr(owner, "findChild"):
        widget = owner.findChild(widget_type, name)
    else:
        widget = getattr(owner, name, None)
    if widget is None:
        try:
            widget = widget_type()
            setattr(owner, name, widget)
        except Exception:
            raise RuntimeError(
                f"{ui_name} is missing required widget '{name}'. "
                "Please keep this objectName in the .ui file or update the corresponding Python view."
            )
    return widget


def optional_child(owner, widget_type, name: str):
    if hasattr(owner, "findChild"):
        return owner.findChild(widget_type, name)
    return getattr(owner, name, None)


def resolve_layout(
    owner,
    *,
    host_widget_name: str | None = None,
    layout_name: str | None = None,
    fallback_widget_name: str | None = None,
    ui_name: str = "UI",
) -> object:
    if hasattr(owner, "findChild"):
        if host_widget_name:
            try:
                from PyQt5.QtWidgets import QWidget  # type: ignore
            except Exception:
                QWidget = object  # type: ignore
            host = owner.findChild(QWidget, host_widget_name)
            if host is not None and getattr(host, "layout", lambda: None)() is not None:
                return host.layout()

        if layout_name:
            try:
                from PyQt5.QtWidgets import QLayout  # type: ignore
            except Exception:
                QLayout = object  # type: ignore
            layout = owner.findChild(QLayout, layout_name)
            if layout is not None:
                return layout

        if fallback_widget_name:
            try:
                from PyQt5.QtWidgets import QWidget  # type: ignore
            except Exception:
                QWidget = object  # type: ignore
            fallback = owner.findChild(QWidget, fallback_widget_name)
            if fallback is not None and getattr(fallback, "layout", lambda: None)() is not None:
                return fallback.layout()
    else:
        try:
            from PyQt5.QtWidgets import QVBoxLayout  # type: ignore
        except Exception:
            QVBoxLayout = None  # type: ignore
        if QVBoxLayout is not None:
            return QVBoxLayout()

    wanted = [name for name in (host_widget_name, layout_name, fallback_widget_name) if name]
    raise RuntimeError(
        f"{ui_name} is missing a required layout container. "
        f"Expected one of: {', '.join(wanted)}"
    )
