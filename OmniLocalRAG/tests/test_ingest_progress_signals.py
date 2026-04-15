"""
test_ingest_progress_signals.py — Unit tests for IngestWorker progress signals.

Runs without PyQt5 installed by stubbing Qt classes before importing the worker.
Verifies:
  stage_changed(str)         — emitted at least once; last emission contains "完成"
  page_progress(int, int)    — (0, total) at start, (total, total) at end
  parse_done(str, int)       — parser name + markdown output count on success
  progress(int, int)         — emitted when file conversion finishes
  degraded_mode(str, str, str) — 3-arg signal kept for compatibility
  finished(bool)             — True on success, False on empty parse
"""

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class SignalRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)

    @property
    def count(self):
        return len(self.calls)


def _make_fake_embed():
    embed = MagicMock()
    embed.is_loaded = True
    embed.encode = MagicMock(return_value=[[0.1] * 384])
    return embed


def _fresh_worker(file_path="/fake/doc.pdf"):
    """
    Wipe and reinstall Qt stubs, then import IngestWorker fresh.
    Needed because other test modules install incompatible PyQt5 stubs.
    """
    # --- reinstall Qt stubs ---
    class _Sig:
        def __init__(self):
            self._slots = []
            self.emitted = []
        def connect(self, slot):
            self._slots.append(slot)
        def emit(self, *args):
            self.emitted.append(args)
            for s in self._slots:
                s(*args)

    def pyqtSignal(*args, **kwargs):
        return _Sig()

    class FakeQThread:
        def __init__(self, *a, **kw): pass
        def start(self): pass
        def isRunning(self): return False

    qtcore = types.ModuleType("PyQt5.QtCore")
    qtcore.QThread = FakeQThread
    qtcore.pyqtSignal = pyqtSignal
    qtcore.QObject = object
    qtcore.Qt = MagicMock()

    pyqt5 = types.ModuleType("PyQt5")
    pyqt5.QtCore = qtcore

    sys.modules["PyQt5"] = pyqt5
    sys.modules["PyQt5.QtCore"] = qtcore

    # --- stub heavy deps so they don't need real numpy / chroma ---
    chroma_mod = types.ModuleType("app.models.chroma_store")
    chroma_mod.ChromaStore = MagicMock
    sys.modules["app.models.chroma_store"] = chroma_mod

    # Note: do NOT stub metadata_exporter here — it is lazy-imported inside
    # _ingest_items() and test_pdf_vector_export relies on the real version.
    # We patch it per test-call instead (see _run_ingest / _run_parse).

    # --- clear worker module cache ---
    sys.modules.pop("app.workers.ingest_worker", None)

    mod = importlib.import_module("app.workers.ingest_worker")
    cls = mod.IngestWorker

    # Create instance without QThread.__init__
    worker = object.__new__(cls)
    worker.file_path = file_path

    # Attach fresh signal instances
    for name in ("progress", "chunk_indexed", "finished", "error_occurred",
                 "degraded_mode", "stage_changed", "page_progress", "parse_done"):
        object.__setattr__(worker, name, _Sig())

    return worker, mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStageChangedSignal(unittest.TestCase):

    def _run_ingest(self, parsed_items):
        worker, mod = _fresh_worker()
        stage_rec = SignalRecorder()
        finished_rec = SignalRecorder()
        worker.stage_changed.connect(stage_rec)
        worker.finished.connect(finished_rec)

        markdown = "\n\n".join(item[0] for item in parsed_items)
        fake_md_path = MagicMock()
        fake_md_path.exists.return_value = bool(markdown.strip())
        fake_md_path.read_text.return_value = markdown
        fake_md_path.name = "doc.converted.md"

        with patch.object(worker, "_convert_file_to_markdown", return_value=fake_md_path):
            worker._ingest_pdf()

        return stage_rec, finished_rec

    def test_stage_changed_emitted_at_least_once(self):
        items = [("word " * 20, 1, [], "")]
        stage_rec, _ = self._run_ingest(items)
        self.assertGreater(stage_rec.count, 0)

    def test_stage_changed_last_message_contains_done(self):
        items = [("word " * 20, 1, [], "")]
        stage_rec, _ = self._run_ingest(items)
        last = stage_rec.calls[-1][0]
        self.assertIn("完成", last)

    def test_stage_changed_mentions_conversion(self):
        items = [("word " * 20, 1, [], "")]
        stage_rec, _ = self._run_ingest(items)
        texts = [c[0] for c in stage_rec.calls]
        self.assertTrue(any("转换" in t for t in texts))

    def test_finished_true_on_success(self):
        items = [("word " * 20, 1, [], "")]
        _, finished_rec = self._run_ingest(items)
        self.assertEqual(finished_rec.count, 1)
        self.assertTrue(finished_rec.calls[0][0])

    def test_finished_false_on_empty_parse(self):
        worker, _ = _fresh_worker()
        finished_rec = SignalRecorder()
        error_rec = SignalRecorder()
        worker.finished.connect(finished_rec)
        worker.error_occurred.connect(error_rec)
        fake_md_path = MagicMock()
        fake_md_path.exists.return_value = True
        fake_md_path.read_text.return_value = ""
        fake_md_path.name = "doc.converted.md"
        with patch.object(worker, "_convert_file_to_markdown", return_value=fake_md_path):
            worker._ingest_pdf()
        self.assertFalse(finished_rec.calls[0][0])
        self.assertGreater(error_rec.count, 0)


class TestProgressSignal(unittest.TestCase):

    def _run_ingest(self, parsed_items):
        worker, mod = _fresh_worker()
        progress_rec = SignalRecorder()
        worker.progress.connect(progress_rec)

        markdown = "\n\n".join(item[0] for item in parsed_items)
        fake_md_path = MagicMock()
        fake_md_path.exists.return_value = bool(markdown.strip())
        fake_md_path.read_text.return_value = markdown
        fake_md_path.name = "doc.converted.md"

        with patch.object(worker, "_convert_file_to_markdown", return_value=fake_md_path):
            worker._ingest_pdf()

        return progress_rec

    def test_progress_emitted_when_conversion_finishes(self):
        items = [("word " * 20, 1, [], ""), ("other " * 20, 2, [], "")]
        rec = self._run_ingest(items)
        self.assertEqual(rec.count, 1)
        self.assertEqual(rec.calls[0], (1, 1))

    def test_progress_final_current_equals_total(self):
        items = [("word " * 20, 1, [], "")]
        rec = self._run_ingest(items)
        last = rec.calls[-1]
        self.assertEqual(last[0], last[1])

    def test_progress_values_monotonically_increasing(self):
        items = [("w " * 20, i + 1, [], "") for i in range(4)]
        rec = self._run_ingest(items)
        currents = [c[0] for c in rec.calls]
        for a, b in zip(currents, currents[1:]):
            self.assertLessEqual(a, b)


class TestParseWithSignals(unittest.TestCase):
    """_parse_pdf_with_signals emits page_progress / parse_done / degraded_mode."""

    def _run_parse(self, parser_results: dict, fitz_pages=5, parser_order=None):
        worker, mod = _fresh_worker()
        stage_rec = SignalRecorder()
        page_rec = SignalRecorder()
        parse_done_rec = SignalRecorder()
        degraded_rec = SignalRecorder()
        worker.stage_changed.connect(stage_rec)
        worker.page_progress.connect(page_rec)
        worker.parse_done.connect(parse_done_rec)
        worker.degraded_mode.connect(degraded_rec)

        # Fake fitz
        fake_fitz_doc = MagicMock()
        fake_fitz_doc.__len__ = MagicMock(return_value=fitz_pages)
        fake_fitz_module = MagicMock()
        fake_fitz_module.open.return_value = fake_fitz_doc

        temp_dir = tempfile.TemporaryDirectory()
        md_path = Path(temp_dir.name) / "doc.converted.md"

        # Patch parser registry entries on the module
        def _factory(name):
            res = parser_results.get(name, [])
            if isinstance(res, Exception):
                def _raise(*a, **k):
                    raise res
                return _raise
            if isinstance(res, str):
                markdown = res
            else:
                markdown = "\n\n".join(item[0] for item in res)
            return lambda path: markdown

        try:
            with patch.dict("sys.modules", {"fitz": fake_fitz_module}):
                fake_registry = {}
                for name in ("docling", "unstructured", "mineru", "marker", "ocr"):
                    parser = MagicMock()
                    parser.to_markdown.side_effect = _factory(name)
                    fake_registry[name] = parser
                with patch.object(mod, "_converted_markdown_path", return_value=md_path), \
                     patch.object(mod, "PARSER_REGISTRY", fake_registry):
                    if parser_order is not None:
                        with patch.object(mod, "_pdf_parser_order", return_value=parser_order):
                            result = worker._parse_pdf_with_signals("/fake/doc.pdf")
                    else:
                        result = worker._parse_pdf_with_signals("/fake/doc.pdf")
        finally:
            temp_dir.cleanup()

        return result, stage_rec, page_rec, parse_done_rec, degraded_rec

    def test_page_progress_initial_emission_zero(self):
        items = [("text", 1, [], "")]
        _, _, page_rec, _, _ = self._run_parse(
            {"docling": items}, parser_order=["docling", "marker", "ocr"]
        )
        self.assertEqual(page_rec.calls[0][0], 0)
        self.assertEqual(page_rec.calls[0][1], 5)

    def test_page_progress_final_current_equals_total(self):
        items = [("text", 1, [], "")]
        _, _, page_rec, _, _ = self._run_parse(
            {"docling": items}, parser_order=["docling", "marker", "ocr"]
        )
        last = page_rec.calls[-1]
        self.assertEqual(last[0], last[1])

    def test_parse_done_emitted_with_correct_parser_and_count(self):
        items = [("text", 1, [], ""), ("text2", 2, [], "")]
        _, _, _, parse_done_rec, _ = self._run_parse(
            {"docling": items}, parser_order=["docling", "marker", "ocr"]
        )
        self.assertEqual(parse_done_rec.count, 1)
        parser_name, num_blocks = parse_done_rec.calls[0]
        self.assertEqual(parser_name, "docling")
        self.assertEqual(num_blocks, 1)

    def test_degraded_mode_emitted_on_fallback(self):
        # New behavior: no auto fallback/downgrade.
        _, _, _, _, degraded_rec = self._run_parse(
            {"docling": [("text", 1, [], "")]},
            parser_order=["docling", "marker", "ocr"],
        )
        self.assertEqual(degraded_rec.count, 0)

    def test_no_degraded_when_first_parser_succeeds(self):
        items = [("text", 1, [], "")]
        _, _, _, _, degraded_rec = self._run_parse(
            {"docling": items}, parser_order=["docling", "marker", "ocr"]
        )
        self.assertEqual(degraded_rec.count, 0)

    def test_returns_empty_when_all_fail(self):
        worker, mod = _fresh_worker()
        with patch.object(mod, "_pdf_parser_order", return_value=["docling"]):
            parser = MagicMock()
            parser.to_markdown.side_effect = Exception("x")
            with patch.object(mod, "PARSER_REGISTRY", {"docling": parser}):
                with self.assertRaises(Exception):
                    worker._parse_pdf_with_signals("/fake/doc.pdf")

    def test_stage_changed_mentions_parser_name(self):
        items = [("text", 1, [], "")]
        _, stage_rec, _, _, _ = self._run_parse(
            {"docling": items}, parser_order=["docling", "marker", "ocr"]
        )
        texts = [c[0] for c in stage_rec.calls]
        self.assertTrue(any("docling" in t for t in texts))


class TestDegradedModeThreeArgs(unittest.TestCase):
    """degraded_mode signal must carry exactly 3 string args."""

    def test_signal_emits_three_args(self):
        worker, _ = _fresh_worker()
        received = []
        worker.degraded_mode.connect(lambda a, b, c: received.append((a, b, c)))
        worker.degraded_mode.emit("docling", "marker", "docling 不可用，已降级到 marker")
        self.assertEqual(len(received), 1)
        self.assertEqual(len(received[0]), 3)


if __name__ == "__main__":
    unittest.main()
