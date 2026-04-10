import importlib
import tempfile
import unittest
from pathlib import Path


class SQLiteMetadataStoreTest(unittest.TestCase):
    def setUp(self):
        from app.utils import config as cfg

        self.cfg = cfg
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {}

        self.sqlite_store = importlib.import_module("app.models.sqlite_store")
        self.old_db_path = self.sqlite_store._DB_PATH
        self.old_instance = self.sqlite_store.SQLiteStore._instance
        self.sqlite_store._DB_PATH = cfg.abs_path("data/omni.db")
        self.sqlite_store.SQLiteStore._instance = None

    def tearDown(self):
        self.sqlite_store._DB_PATH = self.old_db_path
        self.sqlite_store.SQLiteStore._instance = self.old_instance
        self.cfg._BASE = self.old_base
        self.cfg._CONFIG_PATH = self.old_config_path
        self.cfg._cache = self.old_cache
        self.temp_dir.cleanup()

    def test_sqlite_store_remains_primary_metadata_source(self):
        store = self.sqlite_store.SQLiteStore()
        store.init()

        db_path = Path(self.temp_dir.name) / "data" / "omni.db"
        self.assertTrue(db_path.exists())

        transcript_id = store.insert_transcript(
            video_file="demo.mp4",
            start_sec=1.0,
            end_sec=2.5,
            text="hello transcript",
            confidence=0.98,
        )
        transcripts = store.get_transcripts("demo.mp4")
        self.assertEqual(len(transcripts), 1)
        self.assertEqual(transcripts[0]["id"], transcript_id)
        self.assertEqual(transcripts[0]["text"], "hello transcript")

        clip_id = store.insert_clip(
            video_file="demo.mp4",
            start_sec=1.0,
            end_sec=2.5,
            semantic_summary="first summary",
            chroma_id="vec-1",
        )
        clip = store.get_clip(clip_id)
        self.assertIsNotNone(clip)
        self.assertEqual(clip["semantic_summary"], "first summary")
        self.assertEqual(clip["chroma_id"], "vec-1")

        store.update_clip(clip_id, semantic_summary="updated summary", chroma_id="vec-2")
        updated_clip = store.get_clip(clip_id)
        self.assertEqual(updated_clip["semantic_summary"], "updated summary")
        self.assertEqual(updated_clip["chroma_id"], "vec-2")

        map_id = store.insert_cross_modal(
            video_clip_id=clip_id,
            pdf_anchor_id="pdf-anchor-1",
            note="related evidence",
        )
        mappings = store.get_cross_modal_by_clip(clip_id)
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["id"], map_id)
        self.assertEqual(mappings[0]["pdf_anchor_id"], "pdf-anchor-1")

        store.set_config("runtime.primary_store", "sqlite")
        self.assertEqual(store.get_config("runtime.primary_store"), "sqlite")
        self.assertEqual(store.get_config("missing", "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
