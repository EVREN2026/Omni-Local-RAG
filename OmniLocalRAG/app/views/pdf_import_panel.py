"""
PdfImportPanel - file import configuration dialog.

Main features:
- File chooser
- Two-column conversion tech area
  - Left: parser type selection and ordering
  - Right: tech intro, image-data capability, and parser parameters
- Optional page range
- Save config and start import
"""

from __future__ import annotations

import json
from typing import Dict, Optional

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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.parsers.document_parsers import PARSER_REGISTRY, ParserParameter, available_parser_names
from app.utils import config as cfg
from app.utils.logger import logger

_ALL_PARSERS = available_parser_names()

_PARSER_TECH_INFO: Dict[str, Dict[str, str]] = {
    "docling": {
        "intro": "结构化文档转换，优先识别标题和段落层级。",
        "image_data": "否（当前环境常回退为纯文本提取）",
        "basic": "依赖 docling + ONNX 运行环境，失败时会走文本兜底。",
    },
    "marker": {
        "intro": "面向版面复杂 PDF 的解析器，适合图文混排文档。",
        "image_data": "是（可生成 Markdown 图片引用）",
        "basic": "支持 CPU/CUDA，图像文件是否可显示取决于后续资产落盘策略。",
    },
    "unstructured": {
        "intro": "通用文档解析方案，偏文本鲁棒性。",
        "image_data": "否（当前集成仅输出文本块）",
        "basic": "支持策略与语言配置，兼容性强。",
    },
    "mineru": {
        "intro": "通过 MinerU 命令行进行版面转换。",
        "image_data": "是（可生成 images 路径引用）",
        "basic": "当前集成使用临时目录，若需编辑器显示图片需持久化图片目录。",
    },
    "ocr": {
        "intro": "按页渲染后执行 OCR，适合扫描件。",
        "image_data": "否（仅输出识别文本）",
        "basic": "依赖 Tesseract 与语言包。",
    },
}


class PdfImportPanel(QDialog):
    """Modal dialog for configuring and launching a file import."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("文件导入配置")
        self.setMinimumWidth(900)

        self._file_path: Optional[str] = None
        self._parser_param_groups: Dict[str, QGroupBox] = {}
        self._parser_param_widgets: Dict[str, Dict[str, QWidget]] = {}

        self._parser_intro_title: Optional[QLabel] = None
        self._parser_intro_text: Optional[QLabel] = None
        self._parser_intro_image: Optional[QLabel] = None
        self._parser_intro_basic: Optional[QLabel] = None

        self._build()
        self._load_from_config()

    def selected_file(self) -> Optional[str]:
        return self._file_path

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

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

        parser_row = QHBoxLayout()
        parser_row.setSpacing(10)

        parser_group = QGroupBox("转换技术类型（第一项为当前使用，不自动降级）")
        parser_layout = QHBoxLayout(parser_group)
        self._parser_list = QListWidget()
        self._parser_list.setDragDropMode(QListWidget.InternalMove)
        self._parser_list.setMinimumHeight(180)
        self._parser_list.currentRowChanged.connect(lambda _: self._update_parser_params_panel())
        parser_layout.addWidget(self._parser_list)
        parser_row.addWidget(parser_group, 1)

        intro_group = QGroupBox("技术介绍 / 图像能力 / 参数")
        intro_layout = QVBoxLayout(intro_group)
        self._parser_intro_title = QLabel("")
        self._parser_intro_title.setStyleSheet("font-weight: 600;")
        self._parser_intro_text = QLabel("")
        self._parser_intro_text.setWordWrap(True)
        self._parser_intro_image = QLabel("")
        self._parser_intro_image.setWordWrap(True)
        self._parser_intro_basic = QLabel("")
        self._parser_intro_basic.setWordWrap(True)

        intro_layout.addWidget(self._parser_intro_title)
        intro_layout.addWidget(self._parser_intro_text)
        intro_layout.addWidget(self._parser_intro_image)
        intro_layout.addWidget(self._parser_intro_basic)

        self._build_parser_param_groups(intro_layout)
        parser_row.addWidget(intro_group, 2)
        root.addLayout(parser_row)

        page_group = QGroupBox("页面范围（留空表示全部）")
        page_layout = QHBoxLayout(page_group)
        page_layout.addWidget(QLabel("起始页"))
        self._start_page_spin = QSpinBox()
        self._start_page_spin.setRange(0, 9999)
        self._start_page_spin.setSpecialValueText("全部")
        page_layout.addWidget(self._start_page_spin)
        page_layout.addWidget(QLabel("结束页"))
        self._end_page_spin = QSpinBox()
        self._end_page_spin.setRange(0, 9999)
        self._end_page_spin.setSpecialValueText("全部")
        page_layout.addWidget(self._end_page_spin)
        page_layout.addStretch()
        root.addWidget(page_group)

        btn_box = QDialogButtonBox()
        self._import_btn = btn_box.addButton("保存并开始导入", QDialogButtonBox.AcceptRole)
        self._import_btn.setEnabled(False)
        btn_box.addButton("取消", QDialogButtonBox.RejectRole)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def _load_from_config(self) -> None:
        order = cfg.get("pdf.parser_order", _ALL_PARSERS)
        if not isinstance(order, list):
            order = list(_ALL_PARSERS)
        shown = [p for p in order if p in _ALL_PARSERS]
        rest = [p for p in _ALL_PARSERS if p not in shown]

        self._parser_list.clear()
        for name in shown + rest:
            self._parser_list.addItem(QListWidgetItem(name))
        if self._parser_list.count() > 0:
            self._parser_list.setCurrentRow(0)

        self._load_parser_options_from_config()
        self._start_page_spin.setValue(int(cfg.get("pdf.start_page", 0)))
        self._end_page_spin.setValue(int(cfg.get("pdf.end_page", 0)))

    def _save_to_config(self) -> None:
        parser_order = [self._parser_list.item(i).text() for i in range(self._parser_list.count())]
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

        raw["pdf"]["marker_device"] = raw["pdf"]["parser_options"].get("marker", {}).get("device", "cpu")
        raw["pdf"]["ocr_lang"] = raw["pdf"]["parser_options"].get("ocr", {}).get("lang", "chi_sim+eng")
        raw["pdf"]["mineru_cmd"] = raw["pdf"]["parser_options"].get("mineru", {}).get("command", "mineru")
        raw["pdf"]["start_page"] = start_page
        raw["pdf"]["end_page"] = end_page

        config_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        cfg._cache.clear()
        logger.info("PdfImportPanel: config saved")

    def _build_parser_param_groups(self, root_layout: QVBoxLayout) -> None:
        for parser_name in _ALL_PARSERS:
            parser = PARSER_REGISTRY[parser_name]
            group = QGroupBox(f"{parser.display_name} 参数")
            form = QFormLayout(group)
            widgets: Dict[str, QWidget] = {}
            schema = parser.parameter_schema()
            if not schema:
                form.addRow("", QLabel("此解析器无需额外参数"))
            for param in schema:
                widget = self._create_param_widget(param)
                form.addRow(f"{param.label}:", widget)
                widgets[param.name] = widget
            self._parser_param_groups[parser_name] = group
            self._parser_param_widgets[parser_name] = widgets
            root_layout.addWidget(group)

    def _update_parser_params_panel(self) -> None:
        current = self._current_parser_name()
        for name, group in self._parser_param_groups.items():
            group.setVisible(name == current)
        self._update_parser_intro_panel(current)

    def _update_parser_intro_panel(self, parser_name: str) -> None:
        parser = PARSER_REGISTRY.get(parser_name)
        if parser is None:
            if self._parser_intro_title:
                self._parser_intro_title.setText("未找到解析器")
            return

        info = _PARSER_TECH_INFO.get(parser_name, {})
        intro = info.get("intro", "暂无技术说明。")
        image_data = info.get("image_data", "未知")
        basic = info.get("basic", "暂无基础信息。")
        schema = parser.parameter_schema()
        param_labels = "、".join([p.label for p in schema]) if schema else "无"

        if self._parser_intro_title:
            self._parser_intro_title.setText(f"{parser.display_name} ({parser_name})")
        if self._parser_intro_text:
            self._parser_intro_text.setText(f"技术说明: {intro}")
        if self._parser_intro_image:
            self._parser_intro_image.setText(f"是否生成图像数据: {image_data}")
        if self._parser_intro_basic:
            self._parser_intro_basic.setText(f"基础信息: {basic}\n可配置参数: {param_labels}")

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
        return QLineEdit()

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

    def _on_accept(self) -> None:
        self._save_to_config()
        self.accept()
