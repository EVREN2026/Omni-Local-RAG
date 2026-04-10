import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


class DummySignal:
    def __init__(self, *args, **kwargs):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class DummyQThread:
    def __init__(self, *args, **kwargs):
        pass


class FakeEmbedManager:
    def encode(self, texts):
        return [[0.1, 0.2]]


class FakeChromaStore:
    def query(self, embedding, n_results=5):
        return [
            {
                "id": "vec-1",
                "anchor_id": "anchor-1",
                "document": "Markdown chunk content",
            }
        ]


class FakeLLMManager:
    load_called = False
    last_error = "should not load"

    def load(self):
        self.__class__.load_called = True
        return False


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

    llm_module = types.ModuleType("app.models.llm_manager")
    llm_module.LLMManager = FakeLLMManager
    sys.modules["app.models.llm_manager"] = llm_module


class InferenceRetrievalFallbackTest(unittest.TestCase):
    def setUp(self):
        self.stubbed_modules = (
            "PyQt5",
            "PyQt5.QtCore",
            "app.models.embed_manager",
            "app.models.chroma_store",
            "app.models.llm_manager",
            "app.workers.inference_worker",
        )
        self.old_modules = {name: sys.modules.get(name) for name in self.stubbed_modules}
        install_import_stubs()
        sys.modules.pop("app.workers.inference_worker", None)
        self.inference_worker = importlib.import_module("app.workers.inference_worker")

        from app.utils import config as cfg

        self.cfg = cfg
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {
            "llm": {"enabled": False},
            "retrieval": {"top_k": 5},
            "qa_memory": {
                "enabled": True,
                "path": "data/eval/qa_dialogue_memory.md",
                "jsonl_path": "data/eval/qa_dialogue_memory.jsonl",
            },
        }
        FakeLLMManager.load_called = False
        for signal_name in ("token_generated", "context_retrieved", "generation_finished", "error_occurred"):
            getattr(self.inference_worker.InferenceWorker, signal_name).emitted.clear()

    def tearDown(self):
        self.cfg._BASE = self.old_base
        self.cfg._CONFIG_PATH = self.old_config_path
        self.cfg._cache = self.old_cache
        self.temp_dir.cleanup()
        for name, module in self.old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_llm_disabled_emits_retrieval_answer_without_loading_llm(self):
        worker = self.inference_worker.InferenceWorker("query")

        worker.run()

        self.assertFalse(FakeLLMManager.load_called)
        answer = self.inference_worker.InferenceWorker.token_generated.emitted[-1][0]
        self.assertIn("本地 LLM 当前已禁用", answer)
        self.assertIn("Markdown chunk content", answer)
        self.assertEqual(self.inference_worker.InferenceWorker.generation_finished.emitted[-1], (True,))
        self.assertEqual(self.inference_worker.InferenceWorker.error_occurred.emitted, [])
        memory_path = Path(self.temp_dir.name) / "data" / "eval" / "qa_dialogue_memory.md"
        self.assertIn("Markdown chunk content", memory_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
