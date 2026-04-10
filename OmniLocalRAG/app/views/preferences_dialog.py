"""
preferences_dialog.py — User preferences dialog accessible from the tray icon.

Covers the most commonly changed settings without exposing every config key:
  • LLM Server : llama-server 路径、端口、GPU 层数、Flash Attention 等
  • LLM 推理   : model path, GPU layers, context size, temperature
  • Embedding  : device (cpu/cuda)
  • Retrieval  : top_k, distance threshold
  • UI         : hotkey, window width, opacity, animation speed
  • Chunking   : max_chars, overlap_chars, min_section_chars
"""

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
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.utils import config as cfg
from app.utils.logger import logger


class PreferencesDialog(QDialog):
    """Tabbed preferences dialog.  Writes directly to config.json on Accept."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("偏好设置")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)
        self._build()
        self._load_values()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 10)
        root.setSpacing(10)

        tabs = QTabWidget()
        tabs.addTab(self._build_server_tab(),    "llama-server")
        tabs.addTab(self._build_llm_tab(),       "LLM 推理")
        tabs.addTab(self._build_embed_tab(),      "嵌入模型")
        tabs.addTab(self._build_retrieval_tab(),  "检索")
        tabs.addTab(self._build_ui_tab(),         "界面 / 热键")
        tabs.addTab(self._build_chunking_tab(),   "分块参数")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── Tab: llama-server ─────────────────────────────────────────────

    def _build_server_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        # server_dir
        self._server_dir = QLineEdit()
        self._server_dir.setPlaceholderText("models/llama-b8747-bin-win-cuda-12.4-x64")
        browse_dir_btn = QPushButton("浏览…")
        browse_dir_btn.setFixedWidth(64)
        browse_dir_btn.clicked.connect(self._browse_server_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self._server_dir)
        dir_row.addWidget(browse_dir_btn)
        form.addRow("llama-server 目录:", dir_row)

        # model_path (override)
        self._server_model_path = QLineEdit()
        self._server_model_path.setPlaceholderText("留空则使用 LLM 推理中的模型路径")
        browse_gguf_btn = QPushButton("浏览…")
        browse_gguf_btn.setFixedWidth(64)
        browse_gguf_btn.clicked.connect(self._browse_server_gguf)
        gguf_row = QHBoxLayout()
        gguf_row.addWidget(self._server_model_path)
        gguf_row.addWidget(browse_gguf_btn)
        form.addRow("GGUF 模型文件 (可选):", gguf_row)

        # host + port
        self._server_host = QLineEdit()
        self._server_host.setPlaceholderText("127.0.0.1")
        self._server_host.setMaximumWidth(160)
        form.addRow("监听地址:", self._server_host)

        self._server_port = QSpinBox()
        self._server_port.setRange(1024, 65535)
        self._server_port.setMaximumWidth(100)
        form.addRow("监听端口:", self._server_port)

        # n_gpu_layers
        self._server_gpu_layers = QSpinBox()
        self._server_gpu_layers.setRange(-1, 9999)
        self._server_gpu_layers.setToolTip("999 = 全部卸载到 GPU；0 = 纯 CPU；-1 = 跟随 LLM 推理设置")
        form.addRow("GPU 层数 (--n-gpu-layers):", self._server_gpu_layers)

        # ctx_size
        self._server_ctx_size = QSpinBox()
        self._server_ctx_size.setRange(512, 131072)
        self._server_ctx_size.setSingleStep(1024)
        form.addRow("上下文长度 (--ctx-size):", self._server_ctx_size)

        # flash_attn
        self._server_flash_attn = QCheckBox("启用 Flash Attention (--flash-attn)")
        form.addRow("", self._server_flash_attn)

        # no_mmap
        self._server_no_mmap = QCheckBox("禁用内存映射 (--no-mmap)")
        form.addRow("", self._server_no_mmap)

        # numa
        self._server_numa = QComboBox()
        self._server_numa.addItems(["", "distribute", "isolate", "numactl"])
        self._server_numa.setToolTip("NUMA 内存分配策略，多路服务器推荐 distribute")
        form.addRow("NUMA 策略 (--numa):", self._server_numa)

        # extra_args
        self._server_extra_args = QLineEdit()
        self._server_extra_args.setPlaceholderText("如 --threads 8 --batch-size 512")
        form.addRow("额外参数:", self._server_extra_args)

        # auto_download
        self._server_auto_download = QCheckBox("找不到二进制时自动从 GitHub 下载")
        form.addRow("", self._server_auto_download)

        hint = QLabel(
            "llama-server 在首次推理时自动启动，切换模型时自动重启。\n"
            "修改后下次呼出搜索框时生效。"
        )
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return w

    # ── Tab: LLM ──────────────────────────────────────────────────────

    def _build_llm_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        self._llm_enabled = QCheckBox("启用 LLM 推理")
        form.addRow("", self._llm_enabled)

        self._llm_model_path = QLineEdit()
        self._llm_model_path.setPlaceholderText("models/gemma-4-2b-it-q4_k_m.gguf")
        browse_btn = QPushButton("浏览…")
        browse_btn.setFixedWidth(64)
        browse_btn.clicked.connect(self._browse_gguf)
        path_row = QHBoxLayout()
        path_row.addWidget(self._llm_model_path)
        path_row.addWidget(browse_btn)
        form.addRow("GGUF 模型文件:", path_row)

        self._llm_gpu_layers = QSpinBox()
        self._llm_gpu_layers.setRange(0, 200)
        self._llm_gpu_layers.setToolTip("0 = 纯 CPU；正值 = 卸载到 GPU 的层数")
        form.addRow("GPU 层数 (n_gpu_layers):", self._llm_gpu_layers)

        self._llm_n_ctx = QSpinBox()
        self._llm_n_ctx.setRange(512, 32768)
        self._llm_n_ctx.setSingleStep(512)
        form.addRow("上下文长度 (n_ctx):", self._llm_n_ctx)

        self._llm_max_tokens = QSpinBox()
        self._llm_max_tokens.setRange(64, 4096)
        self._llm_max_tokens.setSingleStep(64)
        form.addRow("最大生成 token:", self._llm_max_tokens)

        self._llm_temperature = QDoubleSpinBox()
        self._llm_temperature.setRange(0.0, 2.0)
        self._llm_temperature.setSingleStep(0.05)
        self._llm_temperature.setDecimals(2)
        form.addRow("Temperature:", self._llm_temperature)

        self._llm_repeat_penalty = QDoubleSpinBox()
        self._llm_repeat_penalty.setRange(1.0, 2.0)
        self._llm_repeat_penalty.setSingleStep(0.05)
        self._llm_repeat_penalty.setDecimals(2)
        form.addRow("Repeat penalty:", self._llm_repeat_penalty)

        self._idle_timeout = QSpinBox()
        self._idle_timeout.setRange(1, 120)
        self._idle_timeout.setSuffix(" 分钟")
        self._idle_timeout.setToolTip("闲置超时后自动卸载 LLM")
        form.addRow("闲置卸载超时:", self._idle_timeout)

        return w

    # ── Tab: Embedding ────────────────────────────────────────────────

    def _build_embed_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        self._embed_model_path = QLineEdit()
        self._embed_model_path.setPlaceholderText("models/bge-m3")
        form.addRow("模型目录:", self._embed_model_path)

        self._embed_device = QComboBox()
        self._embed_device.addItems(["cpu", "cuda"])
        form.addRow("运行设备:", self._embed_device)

        hint = QLabel("BGE-M3 在应用运行期间常驻内存，修改后重启生效。")
        hint.setObjectName("HintLabel")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return w

    # ── Tab: Retrieval ────────────────────────────────────────────────

    def _build_retrieval_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        self._top_k = QSpinBox()
        self._top_k.setRange(1, 50)
        self._top_k.setToolTip("每次检索返回的最大文档数")
        form.addRow("Top-K:", self._top_k)

        self._distance_threshold = QDoubleSpinBox()
        self._distance_threshold.setRange(0.0, 1.0)
        self._distance_threshold.setSingleStep(0.05)
        self._distance_threshold.setDecimals(2)
        self._distance_threshold.setToolTip("余弦距离阈值，超过该值的结果被丢弃（越小越严格）")
        form.addRow("距离阈值:", self._distance_threshold)
        return w

    # ── Tab: UI ───────────────────────────────────────────────────────

    def _build_ui_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        self._hotkey = QLineEdit()
        self._hotkey.setPlaceholderText("alt+space")
        self._hotkey.setToolTip("格式遵循 keyboard 库，如 ctrl+shift+space")
        form.addRow("全局热键:", self._hotkey)

        self._window_width = QSpinBox()
        self._window_width.setRange(500, 1600)
        self._window_width.setSingleStep(20)
        self._window_width.setSuffix(" px")
        form.addRow("搜索窗口宽度:", self._window_width)

        self._window_opacity = QDoubleSpinBox()
        self._window_opacity.setRange(0.5, 1.0)
        self._window_opacity.setSingleStep(0.05)
        self._window_opacity.setDecimals(2)
        form.addRow("窗口透明度:", self._window_opacity)

        self._animation_ms = QSpinBox()
        self._animation_ms.setRange(0, 500)
        self._animation_ms.setSuffix(" ms")
        form.addRow("呼出动画时长:", self._animation_ms)
        return w

    # ── Tab: Chunking ─────────────────────────────────────────────────

    def _build_chunking_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(10)

        self._max_chars = QSpinBox()
        self._max_chars.setRange(200, 8000)
        self._max_chars.setSingleStep(100)
        self._max_chars.setSuffix(" 字符")
        form.addRow("最大 chunk 长度:", self._max_chars)

        self._overlap_chars = QSpinBox()
        self._overlap_chars.setRange(0, 2000)
        self._overlap_chars.setSingleStep(20)
        self._overlap_chars.setSuffix(" 字符")
        form.addRow("重叠长度:", self._overlap_chars)

        self._min_section_chars = QSpinBox()
        self._min_section_chars.setRange(10, 500)
        self._min_section_chars.setSuffix(" 字符")
        form.addRow("最小段落长度:", self._min_section_chars)

        self._keep_heading_path = QCheckBox("在 chunk 前缀中保留 heading 路径")
        form.addRow("", self._keep_heading_path)
        return w

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def _load_values(self) -> None:
        # llama-server
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
        # LLM
        self._llm_enabled.setChecked(bool(cfg.get("llm.enabled", True)))
        self._llm_model_path.setText(str(cfg.get("llm.model_path", "")))
        self._llm_gpu_layers.setValue(int(cfg.get("llm.n_gpu_layers", 999)))
        self._llm_n_ctx.setValue(int(cfg.get("llm.n_ctx", 8192)))
        self._llm_max_tokens.setValue(int(cfg.get("llm.max_tokens", 512)))
        self._llm_temperature.setValue(float(cfg.get("llm.temperature", 0.7)))
        self._llm_repeat_penalty.setValue(float(cfg.get("llm.repeat_penalty", 1.1)))
        self._idle_timeout.setValue(int(cfg.get("idle_timeout_minutes", 10)))
        # Embedding
        self._embed_model_path.setText(str(cfg.get("embed.model_path", "")))
        self._embed_device.setCurrentText(str(cfg.get("embed.device", "cpu")))
        # Retrieval
        self._top_k.setValue(int(cfg.get("retrieval.top_k", 5)))
        self._distance_threshold.setValue(float(cfg.get("retrieval.distance_threshold", 0.7)))
        # UI
        self._hotkey.setText(str(cfg.get("ui.hotkey", "alt+space")))
        self._window_width.setValue(int(cfg.get("ui.window_width", 800)))
        self._window_opacity.setValue(float(cfg.get("ui.window_opacity", 0.95)))
        self._animation_ms.setValue(int(cfg.get("ui.animation_ms", 80)))
        # Chunking
        self._max_chars.setValue(int(cfg.get("chunking.max_chars", 1800)))
        self._overlap_chars.setValue(int(cfg.get("chunking.overlap_chars", 240)))
        self._min_section_chars.setValue(int(cfg.get("chunking.min_section_chars", 80)))
        self._keep_heading_path.setChecked(bool(cfg.get("chunking.keep_heading_path", True)))

    def _on_save(self) -> None:
        """Merge changed values into config.json and invalidate cache."""
        try:
            config_path = cfg._CONFIG_PATH
            if config_path.exists():
                existing: Dict[str, Any] = json.loads(
                    config_path.read_text(encoding="utf-8")
                )
            else:
                existing = {}

            # Deep-set helpers
            def _set(d: dict, *keys, value) -> None:
                for k in keys[:-1]:
                    d = d.setdefault(k, {})
                d[keys[-1]] = value

            # llama-server
            _set(existing, "llama_server", "server_dir",    value=self._server_dir.text().strip())
            _set(existing, "llama_server", "model_path",    value=self._server_model_path.text().strip())
            _set(existing, "llama_server", "host",          value=self._server_host.text().strip() or "127.0.0.1")
            _set(existing, "llama_server", "port",          value=self._server_port.value())
            _set(existing, "llama_server", "n_gpu_layers",  value=self._server_gpu_layers.value())
            _set(existing, "llama_server", "ctx_size",      value=self._server_ctx_size.value())
            _set(existing, "llama_server", "flash_attn",    value=self._server_flash_attn.isChecked())
            _set(existing, "llama_server", "no_mmap",       value=self._server_no_mmap.isChecked())
            _set(existing, "llama_server", "numa",          value=self._server_numa.currentText())
            _set(existing, "llama_server", "extra_args",    value=self._server_extra_args.text().strip())
            _set(existing, "llama_server", "auto_download", value=self._server_auto_download.isChecked())

            _set(existing, "llm", "enabled",       value=self._llm_enabled.isChecked())
            _set(existing, "llm", "model_path",    value=self._llm_model_path.text().strip())
            _set(existing, "llm", "n_gpu_layers",  value=self._llm_gpu_layers.value())
            _set(existing, "llm", "n_ctx",         value=self._llm_n_ctx.value())
            _set(existing, "llm", "max_tokens",    value=self._llm_max_tokens.value())
            _set(existing, "llm", "temperature",   value=self._llm_temperature.value())
            _set(existing, "llm", "repeat_penalty",value=self._llm_repeat_penalty.value())
            _set(existing, "idle_timeout_minutes", value=self._idle_timeout.value())

            _set(existing, "embed", "model_path", value=self._embed_model_path.text().strip())
            _set(existing, "embed", "device",     value=self._embed_device.currentText())

            _set(existing, "retrieval", "top_k",              value=self._top_k.value())
            _set(existing, "retrieval", "distance_threshold", value=self._distance_threshold.value())

            _set(existing, "ui", "hotkey",         value=self._hotkey.text().strip() or "alt+space")
            _set(existing, "ui", "window_width",   value=self._window_width.value())
            _set(existing, "ui", "window_opacity", value=self._window_opacity.value())
            _set(existing, "ui", "animation_ms",   value=self._animation_ms.value())

            _set(existing, "chunking", "max_chars",         value=self._max_chars.value())
            _set(existing, "chunking", "overlap_chars",     value=self._overlap_chars.value())
            _set(existing, "chunking", "min_section_chars", value=self._min_section_chars.value())
            _set(existing, "chunking", "keep_heading_path", value=self._keep_heading_path.isChecked())

            config_path.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Invalidate config cache so next cfg.get() call re-reads the file
            cfg._cache.clear()
            logger.info("Preferences saved to config.json")
            self.accept()
        except Exception as e:
            logger.error(f"Preferences save failed: {e}", exc_info=True)
            QMessageBox.critical(self, "保存失败", str(e))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _browse_server_dir(self) -> None:
        from PyQt5.QtWidgets import QFileDialog
        directory = QFileDialog.getExistingDirectory(
            self, "选择 llama-server 目录",
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
            self, "选择 GGUF 模型文件（llama-server 专用）",
            str(cfg.abs_path("models")),
            "GGUF Files (*.gguf *.bin);;All Files (*)"
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
            self, "选择 GGUF 模型文件",
            str(cfg.abs_path("models")),
            "GGUF Files (*.gguf *.bin);;All Files (*)"
        )
        if path:
            # Store relative to project root when possible
            try:
                rel = Path(path).relative_to(cfg._BASE)
                self._llm_model_path.setText(str(rel))
            except ValueError:
                self._llm_model_path.setText(path)
