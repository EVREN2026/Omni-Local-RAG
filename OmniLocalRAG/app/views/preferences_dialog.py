import json
from pathlib import Path
from typing import Any, Dict

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
)

from app.utils import config as cfg
from app.utils.logger import logger
from app.utils.ui_loader import load_ui, require_child


class PreferencesDialog(QDialog):
    """Tabbed preferences dialog that writes directly to config.json on save."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)
        self._build()
        self._load_values()

    def _build(self) -> None:
        load_ui(self, "preferences_dialog.ui")

        self._server_dir = require_child(self, QLineEdit, "serverDirEdit", "PreferencesDialog UI")
        self._server_model_path = require_child(self, QLineEdit, "serverModelPathEdit", "PreferencesDialog UI")
        self._server_host = require_child(self, QLineEdit, "serverHostEdit", "PreferencesDialog UI")
        self._server_port = require_child(self, QSpinBox, "serverPortSpin", "PreferencesDialog UI")
        self._server_gpu_layers = require_child(self, QSpinBox, "serverGpuLayersSpin", "PreferencesDialog UI")
        self._server_ctx_size = require_child(self, QSpinBox, "serverCtxSizeSpin", "PreferencesDialog UI")
        self._server_flash_attn = require_child(self, QCheckBox, "serverFlashAttnCheck", "PreferencesDialog UI")
        self._server_no_mmap = require_child(self, QCheckBox, "serverNoMmapCheck", "PreferencesDialog UI")
        self._server_numa = require_child(self, QComboBox, "serverNumaCombo", "PreferencesDialog UI")
        self._server_extra_args = require_child(self, QLineEdit, "serverExtraArgsEdit", "PreferencesDialog UI")
        self._server_auto_download = require_child(self, QCheckBox, "serverAutoDownloadCheck", "PreferencesDialog UI")

        self._llm_enabled = require_child(self, QCheckBox, "llmEnabledCheck", "PreferencesDialog UI")
        self._llm_model_path = require_child(self, QLineEdit, "llmModelPathEdit", "PreferencesDialog UI")
        self._llm_gpu_layers = require_child(self, QSpinBox, "llmGpuLayersSpin", "PreferencesDialog UI")
        self._llm_n_ctx = require_child(self, QSpinBox, "llmNCtxSpin", "PreferencesDialog UI")
        self._llm_max_tokens = require_child(self, QSpinBox, "llmMaxTokensSpin", "PreferencesDialog UI")
        self._llm_temperature = require_child(self, QDoubleSpinBox, "llmTemperatureSpin", "PreferencesDialog UI")
        self._llm_repeat_penalty = require_child(self, QDoubleSpinBox, "llmRepeatPenaltySpin", "PreferencesDialog UI")
        self._idle_timeout = require_child(self, QSpinBox, "idleTimeoutSpin", "PreferencesDialog UI")

        self._embed_model_path = require_child(self, QLineEdit, "embedModelPathEdit", "PreferencesDialog UI")
        self._embed_device = require_child(self, QComboBox, "embedDeviceCombo", "PreferencesDialog UI")

        self._top_k = require_child(self, QSpinBox, "topKSpin", "PreferencesDialog UI")
        self._distance_threshold = require_child(self, QDoubleSpinBox, "distanceThresholdSpin", "PreferencesDialog UI")

        self._hotkey = require_child(self, QLineEdit, "hotkeyEdit", "PreferencesDialog UI")
        self._window_width = require_child(self, QSpinBox, "windowWidthSpin", "PreferencesDialog UI")
        self._window_opacity = require_child(self, QDoubleSpinBox, "windowOpacitySpin", "PreferencesDialog UI")
        self._animation_ms = require_child(self, QSpinBox, "animationMsSpin", "PreferencesDialog UI")

        self._max_chars = require_child(self, QSpinBox, "maxCharsSpin", "PreferencesDialog UI")
        self._overlap_chars = require_child(self, QSpinBox, "overlapCharsSpin", "PreferencesDialog UI")
        self._min_section_chars = require_child(self, QSpinBox, "minSectionCharsSpin", "PreferencesDialog UI")
        self._keep_heading_path = require_child(self, QCheckBox, "keepHeadingPathCheck", "PreferencesDialog UI")

        self._button_box = require_child(self, QDialogButtonBox, "buttonBox", "PreferencesDialog UI")
        browse_server_dir_btn = require_child(self, QPushButton, "browseServerDirButton", "PreferencesDialog UI")
        browse_server_model_btn = require_child(self, QPushButton, "browseServerModelButton", "PreferencesDialog UI")
        browse_llm_model_btn = require_child(self, QPushButton, "browseLlmModelButton", "PreferencesDialog UI")

        self._server_port.setRange(1024, 65535)
        self._server_gpu_layers.setRange(-1, 9999)
        self._server_ctx_size.setRange(512, 131072)
        self._server_ctx_size.setSingleStep(1024)
        self._server_numa.addItems(["", "distribute", "isolate", "numactl"])

        self._llm_gpu_layers.setRange(0, 200)
        self._llm_n_ctx.setRange(512, 32768)
        self._llm_n_ctx.setSingleStep(512)
        self._llm_max_tokens.setRange(64, 4096)
        self._llm_max_tokens.setSingleStep(64)
        self._llm_temperature.setRange(0.0, 2.0)
        self._llm_temperature.setSingleStep(0.05)
        self._llm_temperature.setDecimals(2)
        self._llm_repeat_penalty.setRange(1.0, 2.0)
        self._llm_repeat_penalty.setSingleStep(0.05)
        self._llm_repeat_penalty.setDecimals(2)
        self._idle_timeout.setRange(1, 120)
        self._idle_timeout.setSuffix(" 分钟")

        self._embed_device.addItems(["cpu", "cuda"])

        self._top_k.setRange(1, 50)
        self._distance_threshold.setRange(0.0, 1.0)
        self._distance_threshold.setSingleStep(0.05)
        self._distance_threshold.setDecimals(2)

        self._window_width.setRange(500, 1600)
        self._window_width.setSingleStep(20)
        self._window_width.setSuffix(" px")
        self._window_opacity.setRange(0.5, 1.0)
        self._window_opacity.setSingleStep(0.05)
        self._window_opacity.setDecimals(2)
        self._animation_ms.setRange(0, 500)
        self._animation_ms.setSuffix(" ms")

        self._max_chars.setRange(200, 8000)
        self._max_chars.setSingleStep(100)
        self._max_chars.setSuffix(" 字符")
        self._overlap_chars.setRange(0, 2000)
        self._overlap_chars.setSingleStep(20)
        self._overlap_chars.setSuffix(" 字符")
        self._min_section_chars.setRange(10, 500)
        self._min_section_chars.setSuffix(" 字符")

        self._button_box.button(QDialogButtonBox.Save).setText("保存")
        self._button_box.button(QDialogButtonBox.Cancel).setText("取消")
        self._button_box.accepted.connect(self._on_save)
        self._button_box.rejected.connect(self.reject)
        browse_server_dir_btn.clicked.connect(self._browse_server_dir)
        browse_server_model_btn.clicked.connect(self._browse_server_gguf)
        browse_llm_model_btn.clicked.connect(self._browse_gguf)

    def _load_values(self) -> None:
        self._server_dir.setText(str(cfg.get("llama_server.server_dir", "")))
        self._server_model_path.setText(str(cfg.get("llama_server.model_path", "")))
        self._server_host.setText(str(cfg.get("llama_server.host", "127.0.0.1")))
        self._server_port.setValue(int(cfg.get("llama_server.port", 8000)))
        self._server_gpu_layers.setValue(int(cfg.get("llama_server.n_gpu_layers", 999)))
        self._server_ctx_size.setValue(int(cfg.get("llama_server.ctx_size", 8192)))
        self._server_flash_attn.setChecked(bool(cfg.get("llama_server.flash_attn", True)))
        self._server_no_mmap.setChecked(bool(cfg.get("llama_server.no_mmap", True)))
        self._server_numa.setCurrentText(str(cfg.get("llama_server.numa", "distribute")))
        self._server_extra_args.setText(str(cfg.get("llama_server.extra_args", "")))
        self._server_auto_download.setChecked(bool(cfg.get("llama_server.auto_download", True)))

        self._llm_enabled.setChecked(bool(cfg.get("llm.enabled", True)))
        self._llm_model_path.setText(str(cfg.get("llm.model_path", "")))
        self._llm_gpu_layers.setValue(int(cfg.get("llm.n_gpu_layers", 999)))
        self._llm_n_ctx.setValue(int(cfg.get("llm.n_ctx", 8192)))
        self._llm_max_tokens.setValue(int(cfg.get("llm.max_tokens", 512)))
        self._llm_temperature.setValue(float(cfg.get("llm.temperature", 0.7)))
        self._llm_repeat_penalty.setValue(float(cfg.get("llm.repeat_penalty", 1.1)))
        self._idle_timeout.setValue(int(cfg.get("idle_timeout_minutes", 10)))

        self._embed_model_path.setText(str(cfg.get("embed.model_path", "")))
        self._embed_device.setCurrentText(str(cfg.get("embed.device", "cpu")))

        self._top_k.setValue(int(cfg.get("retrieval.top_k", 5)))
        self._distance_threshold.setValue(float(cfg.get("retrieval.distance_threshold", 0.7)))

        self._hotkey.setText(str(cfg.get("ui.hotkey", "alt+space")))
        self._window_width.setValue(int(cfg.get("ui.window_width", 800)))
        self._window_opacity.setValue(float(cfg.get("ui.window_opacity", 0.95)))
        self._animation_ms.setValue(int(cfg.get("ui.animation_ms", 80)))

        self._max_chars.setValue(int(cfg.get("chunking.max_chars", 1800)))
        self._overlap_chars.setValue(int(cfg.get("chunking.overlap_chars", 240)))
        self._min_section_chars.setValue(int(cfg.get("chunking.min_section_chars", 80)))
        self._keep_heading_path.setChecked(bool(cfg.get("chunking.keep_heading_path", True)))

    def _on_save(self) -> None:
        try:
            config_path = cfg._CONFIG_PATH
            if config_path.exists():
                existing: Dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
            else:
                existing = {}

            def _set(container: dict, *keys, value) -> None:
                for key in keys[:-1]:
                    container = container.setdefault(key, {})
                container[keys[-1]] = value

            _set(existing, "llama_server", "server_dir", value=self._server_dir.text().strip())
            _set(existing, "llama_server", "model_path", value=self._server_model_path.text().strip())
            _set(existing, "llama_server", "host", value=self._server_host.text().strip() or "127.0.0.1")
            _set(existing, "llama_server", "port", value=self._server_port.value())
            _set(existing, "llama_server", "n_gpu_layers", value=self._server_gpu_layers.value())
            _set(existing, "llama_server", "ctx_size", value=self._server_ctx_size.value())
            _set(existing, "llama_server", "flash_attn", value=self._server_flash_attn.isChecked())
            _set(existing, "llama_server", "no_mmap", value=self._server_no_mmap.isChecked())
            _set(existing, "llama_server", "numa", value=self._server_numa.currentText())
            _set(existing, "llama_server", "extra_args", value=self._server_extra_args.text().strip())
            _set(existing, "llama_server", "auto_download", value=self._server_auto_download.isChecked())

            _set(existing, "llm", "enabled", value=self._llm_enabled.isChecked())
            _set(existing, "llm", "model_path", value=self._llm_model_path.text().strip())
            _set(existing, "llm", "n_gpu_layers", value=self._llm_gpu_layers.value())
            _set(existing, "llm", "n_ctx", value=self._llm_n_ctx.value())
            _set(existing, "llm", "max_tokens", value=self._llm_max_tokens.value())
            _set(existing, "llm", "temperature", value=self._llm_temperature.value())
            _set(existing, "llm", "repeat_penalty", value=self._llm_repeat_penalty.value())
            _set(existing, "idle_timeout_minutes", value=self._idle_timeout.value())

            _set(existing, "embed", "model_path", value=self._embed_model_path.text().strip())
            _set(existing, "embed", "device", value=self._embed_device.currentText())

            _set(existing, "retrieval", "top_k", value=self._top_k.value())
            _set(existing, "retrieval", "distance_threshold", value=self._distance_threshold.value())

            _set(existing, "ui", "hotkey", value=self._hotkey.text().strip() or "alt+space")
            _set(existing, "ui", "window_width", value=self._window_width.value())
            _set(existing, "ui", "window_opacity", value=self._window_opacity.value())
            _set(existing, "ui", "animation_ms", value=self._animation_ms.value())

            _set(existing, "chunking", "max_chars", value=self._max_chars.value())
            _set(existing, "chunking", "overlap_chars", value=self._overlap_chars.value())
            _set(existing, "chunking", "min_section_chars", value=self._min_section_chars.value())
            _set(existing, "chunking", "keep_heading_path", value=self._keep_heading_path.isChecked())

            config_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            cfg._cache.clear()
            logger.info("Preferences saved to config.json")
            self.accept()
        except Exception as exc:
            logger.error(f"Preferences save failed: {exc}", exc_info=True)
            QMessageBox.critical(self, "保存失败", str(exc))

    def _browse_server_dir(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        directory = QFileDialog.getExistingDirectory(
            self,
            "选择 llama-server 目录",
            str(cfg.abs_path(self._server_dir.text() or "models")),
        )
        if directory:
            try:
                rel = Path(directory).relative_to(cfg._BASE)
                self._server_dir.setText(str(rel))
            except ValueError:
                self._server_dir.setText(directory)

    def _browse_server_gguf(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 GGUF 模型文件（llama-server 专用）",
            str(cfg.abs_path("models")),
            "GGUF Files (*.gguf *.bin);;All Files (*)",
        )
        if path:
            try:
                rel = Path(path).relative_to(cfg._BASE)
                self._server_model_path.setText(str(rel))
            except ValueError:
                self._server_model_path.setText(path)

    def _browse_gguf(self) -> None:
        from PyQt5.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 GGUF 模型文件",
            str(cfg.abs_path("models")),
            "GGUF Files (*.gguf *.bin);;All Files (*)",
        )
        if path:
            try:
                rel = Path(path).relative_to(cfg._BASE)
                self._llm_model_path.setText(str(rel))
            except ValueError:
                self._llm_model_path.setText(path)
