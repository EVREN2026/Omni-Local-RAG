from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from app.utils import config as cfg
from app.utils.logger import logger
from app.utils.ui_loader import load_ui, require_child

_PROVIDER_DEFAULTS = {
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "claude": ("https://api.anthropic.com", "claude-3-5-sonnet-20241022"),
    "gemini": ("", "gemini-1.5-flash"),
    "custom": ("http://localhost:1234/v1", "local-model"),
}


class ApiSettingsPanel(QWidget):
    """Configuration panel for external API model settings."""

    settings_saved = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._build()
        self._load_from_config()

    def _build(self) -> None:
        load_ui(self, "api_settings_panel.ui")

        self._provider_combo = require_child(self, QComboBox, "providerCombo", "ApiSettingsPanel UI")
        self._base_url_edit = require_child(self, QLineEdit, "baseUrlEdit", "ApiSettingsPanel UI")
        self._api_key_edit = require_child(self, QLineEdit, "apiKeyEdit", "ApiSettingsPanel UI")
        self._show_key_btn = require_child(self, QPushButton, "showKeyButton", "ApiSettingsPanel UI")
        self._model_edit = require_child(self, QLineEdit, "modelEdit", "ApiSettingsPanel UI")
        self._temp_slider = require_child(self, QSlider, "tempSlider", "ApiSettingsPanel UI")
        self._temp_label = require_child(self, QLabel, "tempValueLabel", "ApiSettingsPanel UI")
        self._max_tokens_spin = require_child(self, QSpinBox, "maxTokensSpin", "ApiSettingsPanel UI")
        self._batch_size_spin = require_child(self, QSpinBox, "batchSizeSpin", "ApiSettingsPanel UI")
        self._timeout_spin = require_child(self, QSpinBox, "timeoutSpin", "ApiSettingsPanel UI")
        self._test_btn = require_child(self, QPushButton, "testButton", "ApiSettingsPanel UI")
        self._save_btn = require_child(self, QPushButton, "saveButton", "ApiSettingsPanel UI")
        self._status_edit = require_child(self, QTextEdit, "statusEdit", "ApiSettingsPanel UI")

        self._provider_combo.addItems(["openai", "claude", "gemini", "custom"])
        self._temp_slider.setRange(0, 20)
        self._temp_slider.setValue(2)
        self._max_tokens_spin.setRange(256, 16384)
        self._max_tokens_spin.setSingleStep(256)
        self._max_tokens_spin.setValue(4096)
        self._batch_size_spin.setRange(1, 50)
        self._batch_size_spin.setValue(10)
        self._timeout_spin.setRange(10, 300)
        self._timeout_spin.setSingleStep(10)
        self._timeout_spin.setValue(60)
        self._timeout_spin.setSuffix(" 秒")

        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        self._show_key_btn.toggled.connect(self._toggle_key_visibility)
        self._temp_slider.valueChanged.connect(lambda value: self._temp_label.setText(f"{value / 10:.1f}"))
        self._test_btn.clicked.connect(self._test_connection)
        self._save_btn.clicked.connect(self._save_config)

    def _on_provider_changed(self, provider: str) -> None:
        defaults = _PROVIDER_DEFAULTS.get(provider, ("", ""))
        if not self._base_url_edit.text().strip():
            self._base_url_edit.setText(defaults[0])
        if not self._model_edit.text().strip():
            self._model_edit.setText(defaults[1])

    def _toggle_key_visibility(self, checked: bool) -> None:
        self._api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)
        self._show_key_btn.setText("隐藏" if checked else "显示")

    def _test_connection(self) -> None:
        self._log("正在测试连接...")
        self._test_btn.setEnabled(False)
        provider = self._provider_combo.currentText()
        api_key = self._api_key_edit.text().strip()
        base_url = self._base_url_edit.text().strip()
        model = self._model_edit.text().strip()

        from PyQt5.QtCore import QThread

        class _PingThread(QThread):
            def __init__(self, p: str, k: str, u: str, m: str):
                super().__init__()
                self.p = p
                self.k = k
                self.u = u
                self.m = m
                self.ok = False

            def run(self) -> None:
                from app.workers.api_optimize_worker import ping_api

                self.ok = ping_api(self.p, self.k, self.u, self.m, timeout=15)

        self._ping_thread = _PingThread(provider, api_key, base_url, model)
        self._ping_thread.finished.connect(self._on_ping_done)
        self._ping_thread.start()

    def _on_ping_done(self) -> None:
        self._test_btn.setEnabled(True)
        if getattr(self._ping_thread, "ok", False):
            self._log("连接成功，API 响应正常。")
        else:
            self._log("连接失败，请检查 API Key、Base URL 和模型名称。")

    def _save_config(self) -> None:
        settings = self._collect_settings()
        try:
            import json

            config_path = cfg._CONFIG_PATH
            if config_path.exists():
                current = json.loads(config_path.read_text(encoding="utf-8"))
            else:
                current = {}
            current["api"] = settings
            config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            cfg._cache["api"] = settings
            self._log("配置已保存到 config.json")
            self.settings_saved.emit(settings)
        except Exception as exc:
            self._log(f"保存失败: {exc}")
            logger.error(f"ApiSettingsPanel save failed: {exc}", exc_info=True)

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
        provider = api_cfg.get("provider")
        if provider:
            index = self._provider_combo.findText(provider)
            if index >= 0:
                self._provider_combo.setCurrentIndex(index)
        self._base_url_edit.setText(api_cfg.get("base_url", ""))
        self._api_key_edit.setText(api_cfg.get("api_key", ""))
        self._model_edit.setText(api_cfg.get("model", ""))
        temp = float(api_cfg.get("temperature", 0.2))
        self._temp_slider.setValue(int(temp * 10))
        self._temp_label.setText(f"{temp:.1f}")
        self._max_tokens_spin.setValue(int(api_cfg.get("max_tokens", 4096)))
        self._batch_size_spin.setValue(int(api_cfg.get("batch_size", 10)))
        self._timeout_spin.setValue(int(api_cfg.get("timeout_seconds", 60)))

    def _log(self, msg: str) -> None:
        self._status_edit.append(msg)
