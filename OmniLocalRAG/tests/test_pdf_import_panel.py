"""
test_pdf_import_panel.py — Unit tests for PdfImportPanel config read/write.

Runs without a display and without PyQt5 installed by stubbing all Qt classes.
Tests focus on the non-GUI logic:
  - _load_from_config reads values from a mocked config
  - _save_to_config writes the correct JSON keys to disk
  - Parser order list is populated in the configured order
  - move_up / move_down reorder the list correctly
"""

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Qt stubs (no display / no PyQt5 required)
# ---------------------------------------------------------------------------

def _install_qt_stubs():
    """Install minimal Qt stubs so pdf_import_panel can be imported."""

    class _Signal:
        def __init__(self):
            self._slots = []
        def connect(self, slot):
            self._slots.append(slot)
        def emit(self, *args):
            for slot in self._slots:
                slot(*args)

    def _fake_signal(*args, **kwargs):
        s = MagicMock()
        s.connect = MagicMock()
        s.emit = MagicMock()
        return s

    # --- PyQt5.QtCore ---
    qtcore = types.ModuleType("PyQt5.QtCore")
    qtcore.Qt = MagicMock()
    qtcore.Qt.Horizontal = 1
    qtcore.pyqtSignal = _fake_signal

    class FakeQObject:
        def __init__(self, *a, **kw): pass

    qtcore.QObject = FakeQObject

    # --- PyQt5.QtWidgets ---
    qtwidgets = types.ModuleType("PyQt5.QtWidgets")

    class _FakeWidget:
        def __init__(self, *a, **kw):
            self._visible = True
        def setMinimumWidth(self, *a): pass
        def setWindowTitle(self, *a): pass
        def addWidget(self, *a): pass
        def addLayout(self, *a): pass
        def addRow(self, *a): pass
        def addStretch(self, *a): pass
        def setContentsMargins(self, *a): pass
        def setSpacing(self, *a): pass
        def setFixedWidth(self, *a): pass
        def setFixedHeight(self, *a): pass
        def setReadOnly(self, *a): pass
        def setPlaceholderText(self, *a): pass
        def clicked(self): pass
        def valueChanged(self): pass
        def connect(self, *a): pass
        def setVisible(self, v):
            self._visible = bool(v)
        def isVisible(self):
            return self._visible

    class FakeListWidgetItem:
        def __init__(self, text=""):
            self._text = text
        def text(self):
            return self._text
        def setText(self, t):
            self._text = t

    class FakeListWidget(_FakeWidget):
        InternalMove = 1
        def __init__(self, *a, **kw):
            self._items = []
            self.currentRowChanged = _Signal()
        def setDragDropMode(self, *a): pass
        def setFixedHeight(self, *a): pass
        def clear(self):
            self._items = []
        def addItem(self, item):
            if isinstance(item, str):
                item = FakeListWidgetItem(item)
            self._items.append(item)
        def count(self):
            return len(self._items)
        def item(self, i):
            return self._items[i] if 0 <= i < len(self._items) else None
        def currentRow(self):
            return self._cur_row if hasattr(self, "_cur_row") else -1
        def setCurrentRow(self, r):
            self._cur_row = r
            self.currentRowChanged.emit(r)
        def takeItem(self, row):
            item = self._items.pop(row)
            return item
        def insertItem(self, row, item):
            if isinstance(item, str):
                item = FakeListWidgetItem(item)
            self._items.insert(row, item)

    class FakeComboBox(_FakeWidget):
        def __init__(self, *a, **kw):
            self._items = []  # (label, data)
            self._idx = 0
        def addItem(self, label, data=None):
            self._items.append((label, data if data is not None else label))
        def count(self):
            return len(self._items)
        def itemData(self, i):
            return self._items[i][1] if 0 <= i < len(self._items) else None
        def setCurrentIndex(self, i):
            self._idx = i
        def currentIndex(self):
            return self._idx
        def currentData(self):
            return self._items[self._idx][1] if self._items else None
        def findData(self, data):
            for i, (_, d) in enumerate(self._items):
                if d == data:
                    return i
            return -1

    class FakeSpinBox(_FakeWidget):
        def __init__(self, *a, **kw):
            self._val = 0
            self._min = 0
            self._max = 9999
        def setRange(self, lo, hi):
            self._min, self._max = lo, hi
        def setSpecialValueText(self, *a): pass
        def setSingleStep(self, *a): pass
        def setValue(self, v):
            self._val = max(self._min, min(self._max, v))
        def value(self):
            return self._val
        def setFixedWidth(self, *a): pass
        @property
        def valueChanged(self):
            m = MagicMock()
            m.connect = MagicMock()
            return m

    class FakeSlider(_FakeWidget):
        def __init__(self, *a, **kw):
            self._val = 0
        def setRange(self, *a): pass
        def setSingleStep(self, *a): pass
        def setPageStep(self, *a): pass
        def setFixedWidth(self, *a): pass
        def setValue(self, v):
            self._val = v
        def value(self):
            return self._val
        @property
        def valueChanged(self):
            m = MagicMock()
            m.connect = MagicMock()
            return m

    class FakeCheckBox(_FakeWidget):
        def __init__(self, *a, **kw):
            self._checked = False
        def setChecked(self, v):
            self._checked = bool(v)
        def isChecked(self):
            return self._checked

    class FakeLineEdit(_FakeWidget):
        def __init__(self, *a, **kw):
            self._text = ""
        def setText(self, t):
            self._text = t
        def text(self):
            return self._text
        def setReadOnly(self, *a): pass
        def setPlaceholderText(self, *a): pass

    class FakePushButton(_FakeWidget):
        def __init__(self, *a, **kw):
            self._enabled = True
        def setEnabled(self, v):
            self._enabled = bool(v)
        def isEnabled(self):
            return self._enabled
        def setFixedWidth(self, *a): pass
        def setToolTip(self, *a): pass
        @property
        def clicked(self):
            m = MagicMock()
            m.connect = MagicMock()
            return m

    class FakeLabel(_FakeWidget):
        def __init__(self, *a, **kw): pass

    class FakeGroupBox(_FakeWidget):
        def __init__(self, *a, **kw): pass

    class FakeDialogButtonBox(_FakeWidget):
        AcceptRole = 0
        RejectRole = 1
        def __init__(self, *a, **kw):
            self._btns = {}
        def addButton(self, label, role):
            btn = FakePushButton(label)
            self._btns[label] = btn
            return btn
        @property
        def accepted(self):
            m = MagicMock(); m.connect = MagicMock(); return m
        @property
        def rejected(self):
            m = MagicMock(); m.connect = MagicMock(); return m

    class FakeLayout(_FakeWidget):
        pass

    class FakeDialog(_FakeWidget):
        Accepted = 1
        Rejected = 0
        def __init__(self, *a, **kw): pass
        def exec_(self): return FakeDialog.Accepted
        def accept(self): pass
        def reject(self): pass
        def setMinimumWidth(self, *a): pass
        def setWindowTitle(self, *a): pass

    # Wire up
    for name in [
        "QWidget", "QDialog", "QGroupBox", "QFormLayout", "QHBoxLayout",
        "QVBoxLayout",
    ]:
        setattr(qtwidgets, name, _FakeWidget)

    qtwidgets.QListWidget = FakeListWidget
    qtwidgets.QListWidgetItem = FakeListWidgetItem
    qtwidgets.QComboBox = FakeComboBox
    qtwidgets.QSpinBox = FakeSpinBox
    qtwidgets.QSlider = FakeSlider
    qtwidgets.QCheckBox = FakeCheckBox
    qtwidgets.QPushButton = FakePushButton
    qtwidgets.QLabel = FakeLabel
    qtwidgets.QGroupBox = FakeGroupBox
    qtwidgets.QDialogButtonBox = FakeDialogButtonBox
    qtwidgets.QDialog = FakeDialog
    qtwidgets.QLineEdit = FakeLineEdit
    qtwidgets.QFileDialog = MagicMock()

    pyqt5 = types.ModuleType("PyQt5")
    pyqt5.QtCore = qtcore
    pyqt5.QtWidgets = qtwidgets

    sys.modules["PyQt5"] = pyqt5
    sys.modules["PyQt5.QtCore"] = qtcore
    sys.modules["PyQt5.QtWidgets"] = qtwidgets

    # Stub app.utils.config so we control it per-test
    return FakeListWidget, FakeComboBox, FakeSpinBox, FakeCheckBox


_FakeListWidget, _FakeComboBox, _FakeSpinBox, _FakeCheckBox = _install_qt_stubs()


# ---------------------------------------------------------------------------
# Now import the module under test
# ---------------------------------------------------------------------------
from app.utils import config as cfg
# Force reload so it picks up stubs
sys.modules.pop("app.views.pdf_import_panel", None)
import app.views.pdf_import_panel as _panel_mod
from app.views.pdf_import_panel import PdfImportPanel, _slider_spin_row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPdfImportPanelConfigRoundtrip(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._orig_base = cfg._BASE
        self._orig_config_path = cfg._CONFIG_PATH
        self._orig_cache = dict(cfg._cache)

        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {}

        cfg._CONFIG_PATH.write_text(
            json.dumps(
                {
                    "pdf": {
                        "parser_order": ["docling", "unstructured", "mineru", "marker", "ocr"],
                        "parser_options": {
                            "marker": {"device": "cuda"},
                            "ocr": {"lang": "eng", "scale": 3},
                            "mineru": {"command": "mineru-test", "extra_args": "--lang zh"},
                            "unstructured": {"strategy": "fast", "languages": "eng"},
                        },
                        "marker_device": "cuda",
                        "ocr_lang": "eng",
                        "start_page": 2,
                        "end_page": 10,
                    },
                    "chunking": {
                        "max_chars": 1200,
                        "overlap_chars": 100,
                        "keep_heading_path": False,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        cfg._BASE = self._orig_base
        cfg._CONFIG_PATH = self._orig_config_path
        cfg._cache = self._orig_cache
        self.temp_dir.cleanup()

    def _make_panel(self):
        _install_qt_stubs()   # reinstall stubs so Qt.Horizontal etc. are present
        sys.modules.pop("app.views.pdf_import_panel", None)
        mod = importlib.import_module("app.views.pdf_import_panel")
        return mod.PdfImportPanel()

    def test_load_from_config_parser_order(self):
        panel = self._make_panel()
        items = [panel._parser_list.item(i).text() for i in range(panel._parser_list.count())]
        self.assertEqual(items[:5], ["docling", "unstructured", "mineru", "marker", "ocr"])

    def test_load_from_config_chunking_max_chars(self):
        panel = self._make_panel()
        self.assertEqual(panel._max_chars_spin.value(), 1200)

    def test_load_from_config_chunking_overlap(self):
        panel = self._make_panel()
        self.assertEqual(panel._overlap_chars_spin.value(), 100)

    def test_load_from_config_keep_heading_false(self):
        panel = self._make_panel()
        self.assertFalse(panel._keep_heading_cb.isChecked())

    def test_load_from_config_page_range(self):
        panel = self._make_panel()
        self.assertEqual(panel._start_page_spin.value(), 2)
        self.assertEqual(panel._end_page_spin.value(), 10)

    def test_load_from_config_marker_device(self):
        panel = self._make_panel()
        widget = panel._parser_param_widgets["marker"]["device"]
        self.assertEqual(widget.currentData(), "cuda")

    def test_load_from_config_ocr_lang(self):
        panel = self._make_panel()
        widget = panel._parser_param_widgets["ocr"]["lang"]
        self.assertEqual(widget.text(), "eng")

    def test_load_from_config_unstructured_strategy(self):
        panel = self._make_panel()
        widget = panel._parser_param_widgets["unstructured"]["strategy"]
        self.assertEqual(widget.currentData(), "fast")

    def test_load_from_config_mineru_command(self):
        panel = self._make_panel()
        widget = panel._parser_param_widgets["mineru"]["command"]
        self.assertEqual(widget.text(), "mineru-test")

    def test_save_to_config_writes_parser_order(self):
        panel = self._make_panel()
        # Swap first two items: result should be [unstructured, docling, ...]
        panel._parser_list.setCurrentRow(0)
        panel._move_parser_down()
        panel._save_to_config()

        saved = json.loads(cfg._CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["pdf"]["parser_order"][0], "unstructured")
        self.assertEqual(saved["pdf"]["parser_order"][1], "docling")

    def test_save_to_config_writes_max_chars(self):
        panel = self._make_panel()
        panel._max_chars_spin.setValue(2400)
        panel._save_to_config()
        saved = json.loads(cfg._CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["chunking"]["max_chars"], 2400)

    def test_save_to_config_writes_overlap(self):
        panel = self._make_panel()
        panel._overlap_chars_spin.setValue(300)
        panel._save_to_config()
        saved = json.loads(cfg._CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["chunking"]["overlap_chars"], 300)

    def test_save_to_config_writes_keep_heading(self):
        panel = self._make_panel()
        panel._keep_heading_cb.setChecked(True)
        panel._save_to_config()
        saved = json.loads(cfg._CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertTrue(saved["chunking"]["keep_heading_path"])

    def test_save_to_config_invalidates_cache(self):
        panel = self._make_panel()
        cfg._cache["pdf.parser_options.marker.device"] = "old_value"
        panel._save_to_config()
        self.assertEqual(len(cfg._cache), 0)

    def test_save_to_config_preserves_unrelated_keys(self):
        raw = json.loads(cfg._CONFIG_PATH.read_text(encoding="utf-8"))
        raw["llm"] = {"model": "test-model"}
        cfg._CONFIG_PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        cfg._cache.clear()

        panel = self._make_panel()
        panel._save_to_config()

        saved = json.loads(cfg._CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertIn("llm", saved)
        self.assertEqual(saved["llm"]["model"], "test-model")

    def test_move_parser_up(self):
        panel = self._make_panel()
        panel._parser_list.setCurrentRow(1)
        panel._move_parser_up()
        items = [panel._parser_list.item(i).text() for i in range(panel._parser_list.count())]
        self.assertEqual(items[0], "unstructured")
        self.assertEqual(items[1], "docling")

    def test_move_parser_down(self):
        panel = self._make_panel()
        panel._parser_list.setCurrentRow(1)
        panel._move_parser_down()
        items = [panel._parser_list.item(i).text() for i in range(panel._parser_list.count())]
        self.assertEqual(items[1], "mineru")
        self.assertEqual(items[2], "unstructured")

    def test_switch_parser_updates_visible_parameter_group(self):
        panel = self._make_panel()
        panel._parser_list.setCurrentRow(2)
        self.assertFalse(panel._parser_param_groups["docling"].isVisible())
        self.assertTrue(panel._parser_param_groups["mineru"].isVisible())

    def test_save_to_config_writes_parser_options(self):
        panel = self._make_panel()
        panel._parser_param_widgets["ocr"]["lang"].setText("chi_sim+eng")
        panel._parser_param_widgets["ocr"]["scale"].setValue(4)
        panel._save_to_config()

        saved = json.loads(cfg._CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["pdf"]["parser_options"]["ocr"]["lang"], "chi_sim+eng")
        self.assertEqual(saved["pdf"]["parser_options"]["ocr"]["scale"], 4)

    def test_import_btn_disabled_on_init(self):
        panel = self._make_panel()
        self.assertFalse(panel._import_btn.isEnabled())

    def test_selected_file_none_on_init(self):
        panel = self._make_panel()
        self.assertIsNone(panel.selected_file())

    def test_selected_file_after_browse(self):
        panel = self._make_panel()
        panel._file_path = "/tmp/fake.pdf"
        panel._file_edit.setText("/tmp/fake.pdf")
        panel._import_btn.setEnabled(True)
        self.assertEqual(panel.selected_file(), "/tmp/fake.pdf")
        self.assertTrue(panel._import_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
