import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class DummySignal:
    def __init__(self, *args, **kwargs):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class DummyQThread:
    def __init__(self, *args, **kwargs):
        pass


class FakeEmbedManager:
    encode_calls = []

    def encode(self, texts):
        self.__class__.encode_calls.extend(texts)
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


class FakeChromaStore:
    add_calls = []

    def add(self, **kwargs):
        self.__class__.add_calls.append(kwargs)
        return kwargs.get("doc_id")


class FakePdfPage:
    number = 0

    def get_text(self, mode="text"):
        return "hello from pymupdf"


class FakeFitz:
    @staticmethod
    def open(file_path):
        return [FakePdfPage()]


def install_import_stubs():
    pyqt5 = types.ModuleType("PyQt5")
    qtcore = types.ModuleType("PyQt5.QtCore")
    qtcore.QThread = DummyQThread
    qtcore.pyqtSignal = lambda *args, **kwargs: DummySignal()
    pyqt5.QtCore = qtcore
    sys.modules["PyQt5"] = pyqt5
    sys.modules["PyQt5.QtCore"] = qtcore

    embed_module = types.ModuleType("app.models.embed_manager")
    embed_module.EmbedManager = FakeEmbedManager
    sys.modules["app.models.embed_manager"] = embed_module

    chroma_module = types.ModuleType("app.models.chroma_store")
    chroma_module.ChromaStore = FakeChromaStore
    sys.modules["app.models.chroma_store"] = chroma_module


class PdfVectorExportTest(unittest.TestCase):
    def setUp(self):
        install_import_stubs()
        sys.modules.pop("app.workers.ingest_worker", None)
        self.ingest_worker = importlib.import_module("app.workers.ingest_worker")
        FakeEmbedManager.encode_calls = []
        FakeChromaStore.add_calls = []

        from app.utils import config as cfg

        self.cfg = cfg
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {}

    def tearDown(self):
        self.cfg._BASE = self.old_base
        self.cfg._CONFIG_PATH = self.old_config_path
        self.cfg._cache = self.old_cache
        self.temp_dir.cleanup()

    def test_pdf_chunks_are_embedded_stored_and_exported(self):
        source_file = str(Path(self.temp_dir.name) / "sample.pdf")
        long_text = " ".join(f"word{i}" for i in range(700))
        parsed_items = [(long_text, 1, [[0, 0, 10, 10]], "")]

        with patch.object(self.ingest_worker, "_parse_pdf", return_value=parsed_items) as parse_pdf:
            worker = self.ingest_worker.IngestWorker(source_file)
            worker._ingest_pdf()

        parse_pdf.assert_called_once_with(source_file)
        self.assertGreater(len(FakeChromaStore.add_calls), 1)
        self.assertEqual(len(FakeEmbedManager.encode_calls), len(FakeChromaStore.add_calls))

        for add_call in FakeChromaStore.add_calls:
            self.assertEqual(add_call["source_type"], "pdf")
            self.assertTrue(add_call["anchor_id"])
            self.assertEqual(add_call["anchor_id"], add_call["doc_id"])

        json_path = Path(self.temp_dir.name) / "data" / "exports" / "pdf" / "sample.chunks.json"
        md_path = Path(self.temp_dir.name) / "data" / "exports" / "pdf" / "sample.chunks.md"
        self.assertTrue(json_path.exists())
        self.assertTrue(md_path.exists())

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_file"], "sample.pdf")
        self.assertEqual(payload["chunk_count"], len(FakeChromaStore.add_calls))
        for chunk in payload["chunks"]:
            self.assertTrue(chunk["chunk_id"])
            self.assertTrue(chunk["anchor_id"])
            self.assertIn("content", chunk)
            self.assertEqual(chunk["vector_dim"], 4)
            self.assertTrue(chunk["stored"])

        markdown = md_path.read_text(encoding="utf-8")
        self.assertIn("# PDF Chunk Export", markdown)
        self.assertIn("- Source file: sample.pdf", markdown)
        self.assertIn("- vector_dim: 4", markdown)
        self.assertIn("```text", markdown)

    def test_markdown_file_is_embedded_stored_and_exported(self):
        source_path = Path(self.temp_dir.name) / "notes.md"
        source_path.write_text(
            "# Title\n\n"
            + " ".join(f"alpha{i}" for i in range(700))
            + "\n\n## Next\n\nshort paragraph",
            encoding="utf-8",
        )

        worker = self.ingest_worker.IngestWorker(str(source_path))
        worker._ingest_markdown()

        self.assertGreater(len(FakeChromaStore.add_calls), 1)
        self.assertEqual(len(FakeEmbedManager.encode_calls), len(FakeChromaStore.add_calls))
        for add_call in FakeChromaStore.add_calls:
            self.assertEqual(add_call["source_type"], "markdown")
            self.assertTrue(add_call["anchor_id"])
            self.assertEqual(add_call["pdf_payload"], {})

        json_path = Path(self.temp_dir.name) / "data" / "exports" / "markdown" / "notes.chunks.json"
        md_path = Path(self.temp_dir.name) / "data" / "exports" / "markdown" / "notes.chunks.md"
        self.assertTrue(json_path.exists())
        self.assertTrue(md_path.exists())

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_file"], "notes.md")
        self.assertEqual(payload["source_type"], "markdown")
        self.assertEqual(payload["chunk_count"], len(FakeChromaStore.add_calls))
        self.assertTrue(all(chunk["source_type"] == "markdown" for chunk in payload["chunks"]))
        contents = [chunk["content"] for chunk in payload["chunks"]]
        self.assertTrue(any(content.startswith("Heading path: Title") for content in contents))
        self.assertFalse(any(content.strip() == "# Title" for content in contents))

    def test_markdown_chunking_merges_heading_metadata_with_body(self):
        markdown = (
            "# Title\n\n"
            "Author: Team\n\n"
            "Date: 2026-04-10\n\n"
            "## Empty Section\n\n"
            "## Usage\n\n"
            "Step 1: import markdown.\n\n"
            "Step 2: ask questions."
        )

        items = self.ingest_worker._markdown_to_items(markdown)
        content = "\n\n".join(item[0] for item in items)

        self.assertIn("Heading path: Title", content)
        self.assertIn("Author: Team", content)
        self.assertIn("Date: 2026-04-10", content)
        self.assertIn("Heading path: Title > Usage", content)
        self.assertNotIn("# Title", content)
        self.assertNotIn("Empty Section", content)

    def test_parse_pdf_uses_docling_first(self):
        docling_rows = [("docling markdown", 0, [], "")]

        with patch.object(self.ingest_worker, "_parse_pdf_with_docling", return_value=docling_rows) as docling:
            with patch.object(self.ingest_worker, "_parse_pdf_with_marker") as marker:
                with patch.object(self.ingest_worker, "_parse_pdf_with_pymupdf") as pymupdf:
                    results = self.ingest_worker._parse_pdf("sample.pdf")

        self.assertEqual(results, docling_rows)
        docling.assert_called_once_with("sample.pdf")
        marker.assert_not_called()
        pymupdf.assert_not_called()

    def test_parse_pdf_uses_marker_when_docling_fails(self):
        marker_rows = [("marker markdown", 0, [], "")]

        with patch.object(self.ingest_worker, "_parse_pdf_with_docling", side_effect=RuntimeError("missing toolkit")):
            with patch.object(self.ingest_worker, "_parse_pdf_with_marker", return_value=marker_rows) as marker:
                results = self.ingest_worker._parse_pdf("sample.pdf")

        self.assertEqual(results, marker_rows)
        marker.assert_called_once_with("sample.pdf")

    def test_parse_pdf_order_can_prefer_pymupdf(self):
        self.cfg._cache = {"pdf": {"parser_order": ["pymupdf", "docling"]}}
        old_fitz = sys.modules.get("fitz")
        sys.modules["fitz"] = FakeFitz
        try:
            with patch.object(
                self.ingest_worker,
                "_parse_pdf_with_docling",
                side_effect=AssertionError("Docling should not be called when PyMuPDF is configured first"),
            ):
                results = self.ingest_worker._parse_pdf("sample.pdf")
        finally:
            if old_fitz is None:
                sys.modules.pop("fitz", None)
            else:
                sys.modules["fitz"] = old_fitz

        self.assertEqual(results, [("hello from pymupdf", 1, [], "")])


if __name__ == "__main__":
    unittest.main()
