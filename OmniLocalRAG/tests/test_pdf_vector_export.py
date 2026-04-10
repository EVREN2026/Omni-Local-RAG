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


if __name__ == "__main__":
    unittest.main()
