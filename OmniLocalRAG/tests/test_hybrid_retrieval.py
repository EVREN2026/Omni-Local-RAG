import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class HybridRetrievalTest(unittest.TestCase):
    def setUp(self):
        from app.utils import config as cfg
        from app.models.chroma_store import ChromaStore

        self.cfg = cfg
        self.ChromaStore = ChromaStore
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.old_instance = ChromaStore._instance
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {
            "retrieval": {
                "top_k": 5,
                "distance_threshold": 0.7,
                "mode": "hybrid",
                "hybrid_dense_weight": 0.65,
                "hybrid_sparse_weight": 0.35,
            }
        }
        ChromaStore._instance = None
        self.store = ChromaStore()
        self.assertTrue(self.store.connect())

    def tearDown(self):
        self.ChromaStore._instance = self.old_instance
        self.cfg._BASE = self.old_base
        self.cfg._CONFIG_PATH = self.old_config_path
        self.cfg._cache = self.old_cache
        self.temp_dir.cleanup()

    def test_hybrid_query_prefers_exact_ip_match(self):
        self.store.add(
            content="左侧相机 IP 192.168.20.10，连接网口 1。",
            embedding=[1.0, 0.0],
            source_type="markdown",
            anchor_id="chunk-a",
            doc_id="chunk-a",
        )
        self.store.add(
            content="左侧相机 IP 192.168.20.11，连接网口 2。",
            embedding=[1.0, 0.0],
            source_type="markdown",
            anchor_id="chunk-b",
            doc_id="chunk-b",
        )

        results = self.store.query([1.0, 0.0], n_results=2, query_text="192.168.20.10")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], "chunk-a")
        self.assertEqual(results[0]["retrieval_mode"], "hybrid")
        self.assertGreater(results[0]["sparse_score"], results[1]["sparse_score"])
        self.assertGreater(results[0]["retrieval_score"], results[1]["retrieval_score"])

    def test_empty_query_text_falls_back_to_dense_only(self):
        self.store.add(
            content="曝光参数调整",
            embedding=[1.0, 0.0],
            source_type="markdown",
            anchor_id="chunk-a",
            doc_id="chunk-a",
        )
        results = self.store.query([1.0, 0.0], n_results=1, query_text="")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["retrieval_mode"], "dense")
        self.assertEqual(results[0]["sparse_score"], 0.0)

    def test_query_sparse_uses_stored_sparse_payload(self):
        self.store.add(
            content="网口 1 参数说明",
            embedding=[1.0, 0.0],
            source_type="markdown",
            anchor_id="chunk-a",
            doc_id="chunk-a",
            sparse_payload={"10": 0.9},
            embed_backend="flagembedding",
        )
        self.store.add(
            content="网口 2 参数说明",
            embedding=[1.0, 0.0],
            source_type="markdown",
            anchor_id="chunk-b",
            doc_id="chunk-b",
            sparse_payload={"20": 0.9},
            embed_backend="flagembedding",
        )

        results = self.store.query(
            [1.0, 0.0],
            n_results=2,
            query_text="网口参数",
            query_sparse={"10": 1.0},
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], "chunk-a")
        self.assertGreater(results[0]["sparse_score"], results[1]["sparse_score"])


if __name__ == "__main__":
    unittest.main()
