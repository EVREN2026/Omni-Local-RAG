"""
StartupGuide — shown when required files are missing at launch.
Lists missing items and provides download links.
"""
import webbrowser
from typing import List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.utils.startup_check import CheckItem


class StartupGuideDialog(QDialog):
    def __init__(self, missing: List[CheckItem], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Omni-Local RAG — 首次启动引导")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setMinimumWidth(520)
        self._build(missing)

    def _build(self, missing: List[CheckItem]) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(QLabel(
            "<b>检测到以下必需文件缺失，请按提示操作后重启程序：</b>"
        ))

        for item in missing:
            row = QHBoxLayout()
            icon = QLabel("✗")
            icon.setStyleSheet("color: #f38ba8; font-weight: bold; min-width: 16px;")
            row.addWidget(icon)

            info = QVBoxLayout()
            lbl = QLabel(f"<b>{item.label}</b>")
            desc = QLabel(f"路径: {item.path}")
            desc.setStyleSheet("color: #6c7086; font-size: 11px;")
            info.addWidget(lbl)
            info.addWidget(desc)
            row.addLayout(info)

            if item.download_url:
                btn = QPushButton("前往下载 ↗")
                btn.setFixedWidth(110)
                btn.clicked.connect(lambda _, url=item.download_url: webbrowser.open(url))
                row.addWidget(btn)

            row.addStretch()
            layout.addLayout(row)

        layout.addSpacing(8)
        hint = QLabel(
            "将模型文件放入 <code>models/</code> 目录，"
            "数据目录将自动创建。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #a6adc8;")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭并退出")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
