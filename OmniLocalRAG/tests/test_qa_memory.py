import json
import tempfile
import unittest
from pathlib import Path

from app.models.qa_memory import QAMemoryRecorder
from app.utils import config as cfg


class QAMemoryRecorderTest(unittest.TestCase):
    def setUp(self):
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {
            "qa_memory": {
                "enabled": True,
                "path": "data/eval/qa_dialogue_memory.md",
                "jsonl_path": "data/eval/qa_dialogue_memory.jsonl",
                "max_context_chars": 16,
            }
        }

    def tearDown(self):
        cfg._BASE = self.old_base
        cfg._CONFIG_PATH = self.old_config_path
        cfg._cache = self.old_cache
        self.temp_dir.cleanup()

    def test_record_writes_markdown_and_jsonl(self):
        hit = {
            "id": "chunk-1",
            "anchor_id": "anchor-1",
            "source_type": "markdown",
            "distance": 0.12,
            "document": "这是一段很长的检索内容，需要截断后写入问答记忆文件。",
        }

        entry = QAMemoryRecorder().record(
            query="如何验证切片质量？",
            retrieved_context=[hit],
            answer="检查 chunk_count、vector_dim 和 stored。",
            mode="llm",
        )

        md_path = Path(self.temp_dir.name) / "data" / "eval" / "qa_dialogue_memory.md"
        jsonl_path = Path(self.temp_dir.name) / "data" / "eval" / "qa_dialogue_memory.jsonl"
        self.assertTrue(md_path.exists())
        self.assertTrue(jsonl_path.exists())
        self.assertIn("如何验证切片质量？", md_path.read_text(encoding="utf-8"))
        self.assertIn("检查 chunk_count", md_path.read_text(encoding="utf-8"))

        rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["id"], entry["id"])
        self.assertEqual(rows[0]["mode"], "llm")
        self.assertEqual(rows[0]["retrieved_context"][0]["id"], "chunk-1")
        self.assertTrue(rows[0]["retrieved_context"][0]["document"].endswith("..."))

    def test_disabled_recorder_does_not_write_files(self):
        cfg._cache["qa_memory"]["enabled"] = False

        entry = QAMemoryRecorder().record(
            query="问题",
            retrieved_context=[],
            answer="回答",
            mode="llm",
        )

        self.assertEqual(entry, {})
        self.assertFalse((Path(self.temp_dir.name) / "data" / "eval" / "qa_dialogue_memory.md").exists())


if __name__ == "__main__":
    unittest.main()
