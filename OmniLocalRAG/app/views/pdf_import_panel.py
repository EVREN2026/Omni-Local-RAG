"""
PdfImportPanel — PDF 导入配置面板（对话框形式）。

功能：
  - 解析器优先级排序（拖拽 / 上下移动按钮）
  - OCR 语言下拉（chi_sim+eng / eng / jpn+eng）
  - Marker 推理设备（cpu / cuda）
  - 分块参数：max_chars 滑块 + 数字框、overlap_chars 滑块 + 数字框
  - keep_heading_path 复选框
  - 页面范围输入（可选，start_page / end_page）
  - "保存并开始导入" / "取消" 按钮
  - 所有配置即时写回 config.json

用法（在 KnowledgeEditor 中）：
    panel = PdfImportPanel(parent=self)
    if panel.exec_() == QDialog.Accepted:
        path = panel.selected_file()
        self._ingest.ingest_file(path)
"""

import json
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.utils import config as cfg
from app.utils.logger import logger
from app.parsers.document_parsers import PARSER_REGISTRY, ParserParameter, available_parser_names

_ALL_PARSERS = available_parser_names()


class PdfImportPanel(QDialog):
    """Modal dialog for configuring and launching a PDF import."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("文件导入配置")
        self.setMinimumWidth(480)
        self._file_path: Optional[str] = None
        self._parser_param_groups = {}
        self._parser_param_widgets = {}
        self._build()
        self._load_from_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def selected_file(self) -> Optional[str]:
        return self._file_path

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # File chooser
        file_group = QGroupBox("目标文件（PDF/Office/文本）")
        file_layout = QHBoxLayout(file_group)
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("点击右侧按钮选择文件…")
        self._file_edit.setReadOnly(True)
        browse_btn = QPushButton("浏览…")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._browse_file)
        file_layout.addWidget(self._file_edit)
        file_layout.addWidget(browse_btn)
        root.addWidget(file_group)

        # Parser order
        parser_group = QGroupBox("解析器优先级（从上到下依次尝试）")
        parser_layout = QHBoxLayout(parser_group)
        self._parser_list = QListWidget()
        self._parser_list.setDragDropMode(QListWidget.InternalMove)
        self._parser_list.setFixedHeight(110)
        if hasattr(self._parser_list, "currentRowChanged"):
            self._parser_list.currentRowChanged.connect(lambda _: self._update_parser_params_panel())
        parser_layout.addWidget(self._parser_list)

        btn_col = QVBoxLayout()
        up_btn = QPushButton("▲")
        up_btn.setFixedWidth(32)
        up_btn.clicked.connect(self._move_parser_up)
        down_btn = QPushButton("▼")
        down_btn.setFixedWidth(32)
        down_btn.clicked.connect(self._move_parser_down)
        btn_col.addWidget(up_btn)
        btn_col.addWidget(down_btn)
        btn_col.addStretch()
        parser_layout.addLayout(btn_col)
        root.addWidget(parser_group)

        self._build_parser_param_groups(root)

        # Chunking params
        chunk_group = QGroupBox("分块参数")
        chunk_form = QFormLayout(chunk_group)

        self._max_chars_spin, max_chars_row = _slider_spin_row(400, 4000, 100)
        chunk_form.addRow("最大字符数 (max_chars):", max_chars_row)

        self._overlap_chars_spin, overlap_row = _slider_spin_row(0, 800, 50)
        chunk_form.addRow("重叠字符数 (overlap_chars):", overlap_row)

        self._keep_heading_cb = QCheckBox("保留标题路径前缀 (keep_heading_path)")
        chunk_form.addRow("", self._keep_heading_cb)
        root.addWidget(chunk_group)

        # Page range (optional)
        page_group = QGroupBox("页面范围（留空表示全部）")
        page_layout = QHBoxLayout(page_group)
        page_layout.addWidget(QLabel("起始页:"))
        self._start_page_spin = QSpinBox()
        self._start_page_spin.setRange(0, 9999)
        self._start_page_spin.setSpecialValueText("全部")
        page_layout.addWidget(self._start_page_spin)
        page_layout.addWidget(QLabel("结束页:"))
        self._end_page_spin = QSpinBox()
        self._end_page_spin.setRange(0, 9999)
        self._end_page_spin.setSpecialValueText("全部")
        page_layout.addWidget(self._end_page_spin)
        page_layout.addStretch()
        root.addWidget(page_group)

        # Buttons
        btn_box = QDialogButtonBox()
        self._import_btn = btn_box.addButton("保存并开始导入", QDialogButtonBox.AcceptRole)
        self._import_btn.setEnabled(False)
        btn_box.addButton("取消", QDialogButtonBox.RejectRole)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Load / save config
    # ------------------------------------------------------------------

    def _load_from_config(self) -> None:
        """Populate widgets from the current config.json values."""
        # Parser order
        order = cfg.get("pdf.parser_order", _ALL_PARSERS)
        if not isinstance(order, list):
            order = list(_ALL_PARSERS)
        # Show configured parsers first, then any missing ones at the end
        shown = [p for p in order if p in _ALL_PARSERS]
        rest = [p for p in _ALL_PARSERS if p not in shown]
        self._parser_list.clear()
        for name in shown + rest:
            self._parser_list.addItem(QListWidgetItem(name))
        if self._parser_list.count() > 0:
            self._parser_list.setCurrentRow(0)

        self._load_parser_options_from_config()

        # Chunking
        self._max_chars_spin.setValue(int(cfg.get("chunking.max_chars", 1800)))
        self._overlap_chars_spin.setValue(int(cfg.get("chunking.overlap_chars", 240)))
        self._keep_heading_cb.setChecked(bool(cfg.get("chunking.keep_heading_path", True)))

        # Page range
        self._start_page_spin.setValue(int(cfg.get("pdf.start_page", 0)))
        self._end_page_spin.setValue(int(cfg.get("pdf.end_page", 0)))

    def _save_to_config(self) -> None:
        """Write current widget values back to config.json."""
        parser_order = [
            self._parser_list.item(i).text()
            for i in range(self._parser_list.count())
        ]
        max_chars = self._max_chars_spin.value()
        overlap_chars = self._overlap_chars_spin.value()
        keep_heading = self._keep_heading_cb.isChecked()
        start_page = self._start_page_spin.value()
        end_page = self._end_page_spin.value()

        config_path = cfg._CONFIG_PATH
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        except Exception:
            raw = {}

        raw.setdefault("pdf", {})
        raw["pdf"]["parser_order"] = parser_order
        raw["pdf"]["parser_options"] = self._collect_parser_options()
        # Keep legacy keys populated for older code paths and existing configs.
        raw["pdf"]["marker_device"] = raw["pdf"]["parser_options"].get("marker", {}).get("device", "cpu")
        raw["pdf"]["ocr_lang"] = raw["pdf"]["parser_options"].get("ocr", {}).get("lang", "chi_sim+eng")
        raw["pdf"]["mineru_cmd"] = raw["pdf"]["parser_options"].get("mineru", {}).get("command", "mineru")
        raw["pdf"]["start_page"] = start_page
        raw["pdf"]["end_page"] = end_page

        raw.setdefault("chunking", {})
        raw["chunking"]["max_chars"] = max_chars
        raw["chunking"]["overlap_chars"] = overlap_chars
        raw["chunking"]["keep_heading_path"] = keep_heading

        config_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Invalidate in-memory cache so workers pick up new values immediately
        cfg._cache.clear()
        logger.info("PdfImportPanel: config saved")

    def _build_parser_param_groups(self, root: QVBoxLayout) -> None:
        for parser_name in _ALL_PARSERS:
            parser = PARSER_REGISTRY[parser_name]
            group = QGroupBox(f"{parser.display_name} 参数")
            form = QFormLayout(group)
            widgets = {}
            schema = parser.parameter_schema()
            if not schema:
                form.addRow("", QLabel("此解析器无需额外参数"))
            for param in schema:
                widget = self._create_param_widget(param)
                form.addRow(f"{param.label}:", widget)
                widgets[param.name] = widget
            self._parser_param_groups[parser_name] = group
            self._parser_param_widgets[parser_name] = widgets
            root.addWidget(group)

    def _update_parser_params_panel(self) -> None:
        current = self._current_parser_name()
        for name, group in self._parser_param_groups.items():
            group.setVisible(name == current)

    def _current_parser_name(self) -> str:
        row = self._parser_list.currentRow()
        item = self._parser_list.item(row)
        return item.text() if item else (_ALL_PARSERS[0] if _ALL_PARSERS else "")

    def _load_parser_options_from_config(self) -> None:
        for parser_name in _ALL_PARSERS:
            parser = PARSER_REGISTRY[parser_name]
            for param in parser.parameter_schema():
                widget = self._parser_param_widgets[parser_name].get(param.name)
                if widget is None:
                    continue
                value = self._config_parser_option(parser_name, param)
                self._set_param_widget_value(widget, param, value)
        self._update_parser_params_panel()

    def _config_parser_option(self, parser_name: str, param: ParserParameter):
        value = cfg.get(f"pdf.parser_options.{parser_name}.{param.name}", None)
        if value is not None:
            return value
        if parser_name == "marker" and param.name == "device":
            return cfg.get("pdf.marker_device", param.default)
        if parser_name == "ocr" and param.name == "lang":
            return cfg.get("pdf.ocr_lang", param.default)
        if parser_name == "mineru" and param.name == "command":
            return cfg.get("pdf.mineru_cmd", param.default)
        return param.default

    def _collect_parser_options(self) -> dict:
        options = {}
        for parser_name, widgets in self._parser_param_widgets.items():
            parser = PARSER_REGISTRY[parser_name]
            options[parser_name] = {}
            for param in parser.parameter_schema():
                widget = widgets.get(param.name)
                if widget is not None:
                    options[parser_name][param.name] = self._param_widget_value(widget, param)
        return options

    def _create_param_widget(self, param: ParserParameter):
        if param.kind == "select":
            combo = QComboBox()
            for label, value in param.options:
                combo.addItem(label, value)
            return combo
        if param.kind == "int":
            spin = QSpinBox()
            spin.setRange(param.minimum, param.maximum)
            spin.setSingleStep(param.step)
            return spin
        if param.kind == "bool":
            return QCheckBox()
        edit = QLineEdit()
        return edit

    def _set_param_widget_value(self, widget, param: ParserParameter, value) -> None:
        if param.kind == "select":
            idx = widget.findData(value)
            if idx >= 0:
                widget.setCurrentIndex(idx)
            return
        if param.kind == "int":
            widget.setValue(int(value))
            return
        if param.kind == "bool":
            widget.setChecked(bool(value))
            return
        widget.setText(str(value))

    def _param_widget_value(self, widget, param: ParserParameter):
        if param.kind == "select":
            return widget.currentData()
        if param.kind == "int":
            return widget.value()
        if param.kind == "bool":
            return widget.isChecked()
        return widget.text()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择文件",
            "",
            "Documents (*.pdf *.doc *.docx *.ppt *.pptx *.xls *.xlsx *.md *.txt);;All Files (*)",
        )
        if path:
            self._file_path = path
            self._file_edit.setText(path)
            self._import_btn.setEnabled(True)

    def _move_parser_up(self) -> None:
        row = self._parser_list.currentRow()
        if row > 0:
            item = self._parser_list.takeItem(row)
            self._parser_list.insertItem(row - 1, item)
            self._parser_list.setCurrentRow(row - 1)

    def _move_parser_down(self) -> None:
        row = self._parser_list.currentRow()
        if row < self._parser_list.count() - 1:
            item = self._parser_list.takeItem(row)
            self._parser_list.insertItem(row + 1, item)
            self._parser_list.setCurrentRow(row + 1)

    def _on_accept(self) -> None:
        self._save_to_config()
        self.accept()


# ---------------------------------------------------------------------------
# Helper: linked slider + spinbox row
# ---------------------------------------------------------------------------

def _slider_spin_row(min_val: int, max_val: int, step: int):
    """Return (QSpinBox, QWidget) with a slider and spinbox that stay in sync."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)

    slider = QSlider(Qt.Horizontal)
    slider.setRange(min_val, max_val)
    slider.setSingleStep(step)
    slider.setPageStep(step * 5)
    slider.setFixedWidth(200)

    spin = QSpinBox()
    spin.setRange(min_val, max_val)
    spin.setSingleStep(step)
    spin.setFixedWidth(70)

    slider.valueChanged.connect(spin.setValue)
    spin.valueChanged.connect(slider.setValue)

    layout.addWidget(slider)
    layout.addWidget(spin)
    return spin, container
