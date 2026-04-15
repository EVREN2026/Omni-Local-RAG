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
        return "hello from marker"


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

    def test_pdf_file_is_converted_to_markdown_without_chunking_or_vectorization(self):
        source_file = str(Path(self.temp_dir.name) / "sample.pdf")
        markdown = "# Sample\n\nConverted markdown body."
        fake_parser = types.SimpleNamespace(to_markdown=lambda path: markdown)

        with patch.object(self.ingest_worker, "PARSER_REGISTRY", {"docling": fake_parser}), \
             patch.object(self.ingest_worker, "_pdf_parser_order", return_value=["docling"]):
            worker = self.ingest_worker.IngestWorker(source_file)
            worker._ingest_pdf()

        self.assertEqual(len(FakeChromaStore.add_calls), 0)
        self.assertEqual(len(FakeEmbedManager.encode_calls), 0)

        converted_path = Path(self.temp_dir.name) / "data" / "exports" / "pdf" / "sample.converted.md"
        json_path = Path(self.temp_dir.name) / "data" / "exports" / "pdf" / "sample.chunks.json"
        md_path = Path(self.temp_dir.name) / "data" / "exports" / "pdf" / "sample.chunks.md"
        self.assertTrue(converted_path.exists())
        self.assertEqual(converted_path.read_text(encoding="utf-8"), markdown)
        self.assertFalse(json_path.exists())
        self.assertFalse(md_path.exists())

    def test_chunk_exporter_still_writes_review_files_for_manual_chunking(self):
        source_file = str(Path(self.temp_dir.name) / "sample.pdf")
        chunk_id = "chunk-1"
        from app.models.metadata_exporter import MetadataExporter

        MetadataExporter().export_chunks(
            source_file=source_file,
            source_type="pdf",
            chunks=[
                {
                    "chunk_id": chunk_id,
                    "anchor_id": chunk_id,
                    "source_type": "pdf",
                    "page": 1,
                    "coords": [],
                    "heading_path": "Sample",
                    "block_type": "text",
                    "content": "chunk content",
                    "display_content": "chunk content",
                    "embedding_text": "[Path: Sample]\n\nchunk content",
                    "metadata": {"path": "Sample", "type": "text"},
                    "vector_dim": None,
                    "stored": False,
                    "is_manual": False,
                }
            ],
        )

        json_path = Path(self.temp_dir.name) / "data" / "exports" / "pdf" / "sample.chunks.json"
        md_path = Path(self.temp_dir.name) / "data" / "exports" / "pdf" / "sample.chunks.md"
        self.assertTrue(md_path.exists())

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_file"], "sample.pdf")
        self.assertEqual(payload["chunk_count"], 1)
        for chunk in payload["chunks"]:
            self.assertEqual(chunk["chunk_id"], chunk_id)
            self.assertEqual(chunk["anchor_id"], chunk_id)
            self.assertIn("content", chunk)
            self.assertIsNone(chunk["vector_dim"])
            self.assertFalse(chunk["stored"])

        markdown = md_path.read_text(encoding="utf-8")
        self.assertIn("# PDF Chunk Export", markdown)
        self.assertIn("- Source file: sample.pdf", markdown)
        self.assertIn("- vector_dim: None", markdown)
        self.assertIn("### Display Content", markdown)
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
            self.assertEqual(add_call["pdf_payload"]["file"], "notes.md")
            self.assertIn("display_content", add_call["pdf_payload"])
            self.assertIn("embedding_text", add_call["pdf_payload"])
            self.assertIn("metadata", add_call["pdf_payload"])

        json_path = Path(self.temp_dir.name) / "data" / "exports" / "markdown" / "notes.chunks.json"
        md_path = Path(self.temp_dir.name) / "data" / "exports" / "markdown" / "notes.chunks.md"
        self.assertTrue(json_path.exists())
        self.assertTrue(md_path.exists())

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_file"], "notes.md")
        self.assertEqual(payload["source_type"], "markdown")
        self.assertEqual(payload["chunk_count"], len(FakeChromaStore.add_calls))
        self.assertTrue(all(chunk["source_type"] == "markdown" for chunk in payload["chunks"]))
        self.assertTrue(any(chunk["display_content"].startswith("alpha0") for chunk in payload["chunks"]))
        self.assertTrue(any(chunk["embedding_text"].startswith("[Path: Title]") for chunk in payload["chunks"]))
        self.assertTrue(any(chunk["block_type"] in {"term", "text", "tutorial"} for chunk in payload["chunks"]))
        for add_call, chunk in zip(FakeChromaStore.add_calls, payload["chunks"]):
            self.assertEqual(add_call["content"], chunk["display_content"])

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
        display = "\n\n".join(getattr(item, "display_content", item[0]) for item in items)
        embedding = "\n\n".join(getattr(item, "embedding_text", item[0]) for item in items)

        self.assertIn("Author: Team", display)
        self.assertIn("Date: 2026-04-10", display)
        self.assertIn("[Path: Title]", embedding)
        self.assertIn("[Path: Title > Usage]", embedding)
        self.assertNotIn("# Title", display)
        self.assertNotIn("Empty Section", display)

    def test_markdown_chunking_extracts_table_and_image_semantics(self):
        markdown = (
            "# 硬件连接\n\n"
            "## 相机连接\n\n"
            "左侧相机 IP 为 192.168.1.10。\n"
            "![相机图](images/cam.jpg)\n"
            "连接完成后检查取流状态。\n\n"
            "| 参数 | 含义 | 默认值 |\n"
            "| --- | --- | --- |\n"
            "| n_gpu_layers | GPU层数 | 999 |\n"
        )

        items = self.ingest_worker._markdown_to_items(markdown)
        block_types = [getattr(item, "block_type", "") for item in items]
        self.assertIn("image", block_types)
        self.assertTrue(any(bt in {"table", "parameter_table"} for bt in block_types))

        image_item = next(item for item in items if getattr(item, "block_type", "") == "image")
        image_meta = getattr(image_item, "metadata", {})
        self.assertEqual(image_meta.get("image_path"), "images/cam.jpg")
        self.assertTrue(image_meta.get("has_image"))
        self.assertIn("[Path: 硬件连接 > 相机连接]", getattr(image_item, "embedding_text", ""))

        table_item = next(item for item in items if getattr(item, "block_type", "") in {"table", "parameter_table"})
        table_meta = getattr(table_item, "metadata", {})
        self.assertTrue(table_meta.get("has_table"))
        self.assertIn("n_gpu_layers", getattr(table_item, "embedding_text", ""))

    def test_parse_pdf_uses_docling_first(self):
        docling_rows = [("docling markdown", 0, [], "")]
        docling = types.SimpleNamespace(parse=lambda p: docling_rows)
        marker_called = {"v": False}
        marker = types.SimpleNamespace(parse=lambda p: marker_called.__setitem__("v", True))
        with patch.object(self.ingest_worker, "PARSER_REGISTRY", {"docling": docling, "marker": marker}):
            with patch.object(self.ingest_worker, "_pdf_parser_order", return_value=["docling", "marker"]):
                results = self.ingest_worker._parse_pdf("sample.pdf")

        self.assertEqual(results, docling_rows)
        self.assertFalse(marker_called["v"])

    def test_parse_pdf_uses_marker_when_docling_fails(self):
        docling = types.SimpleNamespace(parse=lambda p: (_ for _ in ()).throw(RuntimeError("missing toolkit")))
        with patch.object(self.ingest_worker, "PARSER_REGISTRY", {"docling": docling}):
            with patch.object(self.ingest_worker, "_pdf_parser_order", return_value=["docling"]):
                with self.assertRaises(RuntimeError):
                    self.ingest_worker._parse_pdf("sample.pdf")

    def test_parse_pdf_order_can_prefer_marker(self):
        marker_rows = [("hello from marker", 1, [], "")]
        docling = types.SimpleNamespace(parse=lambda p: (_ for _ in ()).throw(AssertionError("should not call docling")))
        marker = types.SimpleNamespace(parse=lambda p: marker_rows)
        with patch.object(self.ingest_worker, "PARSER_REGISTRY", {"docling": docling, "marker": marker}):
            with patch.object(self.ingest_worker, "_pdf_parser_order", return_value=["marker", "docling"]):
                results = self.ingest_worker._parse_pdf("sample.pdf")
        self.assertEqual(results, marker_rows)


if __name__ == "__main__":
    unittest.main()
