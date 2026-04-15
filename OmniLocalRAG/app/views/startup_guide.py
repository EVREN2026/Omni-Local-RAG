import webbrowser
from typing import List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.utils.startup_check import CheckItem
from app.utils.ui_loader import load_ui, require_child, resolve_layout


class StartupGuideDialog(QDialog):
    def __init__(self, missing: List[CheckItem], parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setMinimumWidth(520)
        self._build(missing)

    def _build(self, missing: List[CheckItem]) -> None:
        load_ui(self, "startup_guide.ui")
        self._intro_label = require_child(self, QLabel, "introLabel", "StartupGuide UI")
        self._hint_label = require_child(self, QLabel, "hintLabel", "StartupGuide UI")
        self._close_btn = require_child(self, QPushButton, "closeButton", "StartupGuide UI")
        items_layout = resolve_layout(
            self,
            host_widget_name="itemsHostWidget",
            layout_name="itemsLayout",
            fallback_widget_name="itemsScrollArea",
            ui_name="StartupGuide UI",
        )
        self._close_btn.clicked.connect(self.reject)

        for item in missing:
            row_widget = QWidget(self)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)

            icon = QLabel("!", row_widget)
            icon.setStyleSheet("color: #f38ba8; font-weight: bold; min-width: 16px;")
            row.addWidget(icon)

            info_layout = QVBoxLayout()
            title = QLabel(f"<b>{item.label}</b>", row_widget)
            desc = QLabel(f"路径: {item.path}", row_widget)
            desc.setStyleSheet("color: #6c7086; font-size: 11px;")
            info_layout.addWidget(title)
            info_layout.addWidget(desc)
            row.addLayout(info_layout)

            if item.download_url:
                btn = QPushButton("前往下载 →", row_widget)
                btn.setFixedWidth(110)
                btn.clicked.connect(lambda _, url=item.download_url: webbrowser.open(url))
                row.addWidget(btn)

            row.addStretch()
            items_layout.addWidget(row_widget)

        items_layout.addStretch()
