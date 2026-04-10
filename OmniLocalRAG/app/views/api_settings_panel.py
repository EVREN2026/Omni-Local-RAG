"""
api_settings_panel.py — API model configuration control panel.

Allows the user to configure any API provider (OpenAI, Claude, Gemini,
or a custom OpenAI-compatible endpoint) and persist the settings to
config.json under the "api" key.

Integrated into KnowledgeEditor as the "API 设置" tab.
"""

from typing import Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.utils import config as cfg
from app.utils.logger import logger

# Provider → (default_base_url, default_model)
_PROVIDER_DEFAULTS = {
    "openai":  ("https://api.openai.com/v1", "gpt-4o-mini"),
    "claude":  ("https://api.anthropic.com", "claude-3-5-sonnet-20241022"),
    "gemini":  ("",                          "gemini-1.5-flash"),
    "custom":  ("http://localhost:1234/v1",  "local-model"),
}


class ApiSettingsPanel(QWidget):
    """Configuration panel for external API model settings."""

    settings_saved = pyqtSignal(dict)   # emitted when user saves config

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build()
        self._load_from_config()

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # Title
        title = QLabel("API 模型配置")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        root.addWidget(title)

        desc = QLabel(
            "配置用于切片优化的外部 API 模型。支持 OpenAI / Claude / Gemini / 兼容 OpenAI 格式的本地 API。"
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888; font-size: 12px;")
        root.addWidget(desc)

        # Provider group
        provider_group = QGroupBox("提供商选择")
        form = QFormLayout(provider_group)

        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["openai", "claude", "gemini", "custom"])
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        form.addRow("API 提供商:", self._provider_combo)

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("留空使用默认地址，自定义填入完整 Base URL")
        form.addRow("Base URL:", self._base_url_edit)

        # API Key row with show/hide toggle
        key_row = QHBoxLayout()
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.Password)
        self._api_key_edit.setPlaceholderText("sk-... 或 API Key")
        self._show_key_btn = QPushButton("显示")
        self._show_key_btn.setFixedWidth(50)
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self._api_key_edit)
        key_row.addWidget(self._show_key_btn)
        key_widget = QWidget()
        key_widget.setLayout(key_row)
        form.addRow("API Key:", key_widget)

        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("如 gpt-4o-mini / claude-3-5-sonnet-20241022")
        form.addRow("模型名称:", self._model_edit)

        root.addWidget(provider_group)

        # Generation params group
        gen_group = QGroupBox("生成参数")
        gen_form = QFormLayout(gen_group)

        # Temperature slider
        temp_row = QHBoxLayout()
        self._temp_slider = QSlider(Qt.Horizontal)
        self._temp_slider.setRange(0, 20)   # 0.0 – 2.0 in steps of 0.1
        self._temp_slider.setValue(2)        # default 0.2
        self._temp_label = QLabel("0.2")
        self._temp_label.setFixedWidth(35)
        self._temp_slider.valueChanged.connect(
            lambda v: self._temp_label.setText(f"{v / 10:.1f}")
        )
        temp_row.addWidget(self._temp_slider)
        temp_row.addWidget(self._temp_label)
        temp_widget = QWidget()
        temp_widget.setLayout(temp_row)
        gen_form.addRow("Temperature:", temp_widget)

        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(256, 16384)
        self._max_tokens_spin.setSingleStep(256)
        self._max_tokens_spin.setValue(4096)
        gen_form.addRow("Max tokens:", self._max_tokens_spin)

        self._batch_size_spin = QSpinBox()
        self._batch_size_spin.setRange(1, 50)
        self._batch_size_spin.setValue(10)
        self._batch_size_spin.setToolTip("每次 API 调用传入的 chunk 数量")
        gen_form.addRow("Batch size:", self._batch_size_spin)

        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(10, 300)
        self._timeout_spin.setSingleStep(10)
        self._timeout_spin.setValue(60)
        self._timeout_spin.setSuffix(" 秒")
        gen_form.addRow("超时:", self._timeout_spin)

        root.addWidget(gen_group)

        # Action buttons
        btn_row = QHBoxLayout()
        self._test_btn = QPushButton("测试连接")
        self._test_btn.clicked.connect(self._test_connection)
        self._save_btn = QPushButton("保存配置")
        self._save_btn.setStyleSheet("font-weight: bold;")
        self._save_btn.clicked.connect(self._save_config)
        btn_row.addWidget(self._test_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

        # Status / test result log
        self._status_edit = QTextEdit()
        self._status_edit.setReadOnly(True)
        self._status_edit.setFixedHeight(100)
        self._status_edit.setPlaceholderText("测试结果和日志将显示在这里...")
        root.addWidget(self._status_edit)

        root.addStretch()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_provider_changed(self, provider: str) -> None:
        defaults = _PROVIDER_DEFAULTS.get(provider, ("", ""))
        if not self._base_url_edit.text():
            self._base_url_edit.setText(defaults[0])
        if not self._model_edit.text():
            self._model_edit.setText(defaults[1])

    def _toggle_key_visibility(self, checked: bool) -> None:
        self._api_key_edit.setEchoMode(
            QLineEdit.Normal if checked else QLineEdit.Password
        )
        self._show_key_btn.setText("隐藏" if checked else "显示")

    def _test_connection(self) -> None:
        self._log("正在测试连接...")
        self._test_btn.setEnabled(False)
        provider = self._provider_combo.currentText()
        api_key = self._api_key_edit.text().strip()
        base_url = self._base_url_edit.text().strip()
        model = self._model_edit.text().strip()

        # Run in a one-shot thread to avoid blocking UI
        from PyQt5.QtCore import QThread

        class _PingThread(QThread):
            def __init__(self, p, k, u, m):
                super().__init__()
                self.p, self.k, self.u, self.m = p, k, u, m
                self.ok = False

            def run(self):
                from app.workers.api_optimize_worker import ping_api
                self.ok = ping_api(self.p, self.k, self.u, self.m, timeout=15)

        self._ping_thread = _PingThread(provider, api_key, base_url, model)
        self._ping_thread.finished.connect(self._on_ping_done)
        self._ping_thread.start()

    def _on_ping_done(self) -> None:
        self._test_btn.setEnabled(True)
        if self._ping_thread.ok:
            self._log("连接成功！API 响应正常。")
        else:
            self._log("连接失败。请检查 API Key、Base URL 和模型名称。")

    def _save_config(self) -> None:
        settings = self._collect_settings()
        # Write to config.json
        try:
            import json
            config_path = cfg._CONFIG_PATH
            if config_path.exists():
                current = json.loads(config_path.read_text(encoding="utf-8"))
            else:
                current = {}
            current["api"] = settings
            config_path.write_text(
                json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Also update live cache
            cfg._cache["api"] = settings
            self._log("配置已保存到 config.json")
            self.settings_saved.emit(settings)
        except Exception as e:
            self._log(f"保存失败: {e}")
            logger.error(f"ApiSettingsPanel save failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _collect_settings(self) -> dict:
        return {
            "provider": self._provider_combo.currentText(),
            "base_url": self._base_url_edit.text().strip(),
            "api_key": self._api_key_edit.text().strip(),
            "model": self._model_edit.text().strip(),
            "temperature": self._temp_slider.value() / 10.0,
            "max_tokens": self._max_tokens_spin.value(),
            "batch_size": self._batch_size_spin.value(),
            "timeout_seconds": self._timeout_spin.value(),
            "task_template": "data/templates/api_optimize_task.md",
        }

    def _load_from_config(self) -> None:
        api_cfg = cfg.get("api", {}) or {}
        if api_cfg.get("provider"):
            idx = self._provider_combo.findText(api_cfg["provider"])
            if idx >= 0:
                self._provider_combo.setCurrentIndex(idx)
        self._base_url_edit.setText(api_cfg.get("base_url", ""))
        self._api_key_edit.setText(api_cfg.get("api_key", ""))
        self._model_edit.setText(api_cfg.get("model", ""))
        temp = float(api_cfg.get("temperature", 0.2))
        self._temp_slider.setValue(int(temp * 10))
        self._max_tokens_spin.setValue(int(api_cfg.get("max_tokens", 4096)))
        self._batch_size_spin.setValue(int(api_cfg.get("batch_size", 10)))
        self._timeout_spin.setValue(int(api_cfg.get("timeout_seconds", 60)))

    def _log(self, msg: str) -> None:
        self._status_edit.append(msg)
