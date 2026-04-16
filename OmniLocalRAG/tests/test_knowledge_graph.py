"""Tests for KnowledgeGraph and GraphBuilder."""

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class KnowledgeGraphTest(unittest.TestCase):
    """Test KnowledgeGraph singleton, CRUD, traversal, and community detection."""

    def setUp(self):
        from app.models.knowledge_graph import KnowledgeGraph
        KnowledgeGraph._reset()
        self.KnowledgeGraph = KnowledgeGraph
        self.kg = KnowledgeGraph()

    def tearDown(self):
        self.KnowledgeGraph._reset()

    def test_singleton(self):
        kg2 = self.KnowledgeGraph()
        self.assertIs(kg2, self.kg)

    def test_add_node_and_find(self):
        self.kg.add_node(
            node_id="chunk-1",
            label="工控机IP配置教程 [tech_manual]",
            category="tech_manual",
            heading_path="快速入门 > 工控机IP配置教程",
        )
        self.assertEqual(self.kg.node_count(), 1)

        matched = self.kg.find_nodes("工控机IP配置", top_k=5)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][1], "chunk-1")

    def test_add_edge(self):
        self.kg.add_node(node_id="a", label="Node A")
        self.kg.add_node(node_id="b", label="Node B")
        self.kg.add_edge(
            source="a", target="b",
            relation="references",
            confidence="EXTRACTED",
            confidence_score=1.0,
        )
        self.assertEqual(self.kg.edge_count(), 1)

    def test_bfs_traversal(self):
        # Build a small graph: a -> b -> c -> d
        for nid, label in [("a", "root"), ("b", "child1"), ("c", "child2"), ("d", "leaf")]:
            self.kg.add_node(node_id=nid, label=label)
        for src, tgt in [("a", "b"), ("b", "c"), ("c", "d")]:
            self.kg.add_edge(source=src, target=tgt, relation="references")

        visited, edges = self.kg.traverse_bfs(start_nodes=["a"], max_depth=3, max_nodes=50)
        self.assertEqual(len(visited), 4)
        self.assertEqual(len(edges), 3)

    def test_dfs_traversal(self):
        for nid, label in [("a", "root"), ("b", "child1"), ("c", "child2")]:
            self.kg.add_node(node_id=nid, label=label)
        for src, tgt in [("a", "b"), ("b", "c")]:
            self.kg.add_edge(source=src, target=tgt, relation="references")

        visited, edges = self.kg.traverse_dfs(start_nodes=["a"], max_depth=6, max_nodes=50)
        self.assertEqual(len(visited), 3)

    def test_community_detection(self):
        # Two disconnected clusters
        for nid in ["a1", "a2", "a3"]:
            self.kg.add_node(node_id=nid, label=f"cluster_a_{nid}")
        for nid in ["b1", "b2", "b3"]:
            self.kg.add_node(node_id=nid, label=f"cluster_b_{nid}")

        # Connect within clusters
        for src, tgt in [("a1", "a2"), ("a2", "a3"), ("b1", "b2"), ("b2", "b3")]:
            self.kg.add_edge(source=src, target=tgt, relation="references")

        communities = self.kg.detect_communities()
        self.assertGreaterEqual(len(communities), 2)

    def test_remove_nodes_by_source(self):
        self.kg.add_node(node_id="x1", label="X1", source_file="doc1.pdf")
        self.kg.add_node(node_id="x2", label="X2", source_file="doc1.pdf")
        self.kg.add_node(node_id="y1", label="Y1", source_file="doc2.pdf")

        removed = self.kg.remove_nodes_by_source("doc1.pdf")
        self.assertEqual(removed, 2)
        self.assertEqual(self.kg.node_count(), 1)

    def test_save_and_load(self):
        self.kg.add_node(node_id="n1", label="Test Node", category="general")
        self.kg.detect_communities()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "graph.json"
            self.kg.save(path)

            self.KnowledgeGraph._reset()
            kg2 = self.KnowledgeGraph()
            loaded = kg2.load(path)
            self.assertTrue(loaded)
            self.assertEqual(kg2.node_count(), 1)

    def test_is_loaded(self):
        self.assertFalse(self.kg.is_loaded())
        self.kg.add_node(node_id="n1", label="Test")
        self.kg._loaded = True
        self.assertTrue(self.kg.is_loaded())

    def test_build_ngrams(self):
        ngrams = self.KnowledgeGraph._build_ngrams(["工控机", "IP"])
        self.assertIn("工控", ngrams)
        self.assertIn("控机", ngrams)
        # "IP" is only 2 chars → one n-gram "IP"
        self.assertIn("IP", ngrams)

    def test_edit_distance(self):
        ed = self.KnowledgeGraph._edit_distance
        self.assertEqual(ed("", ""), 0)
        self.assertEqual(ed("abc", "abc"), 0)
        self.assertEqual(ed("abc", ""), 3)
        self.assertEqual(ed("", "abc"), 3)
        self.assertEqual(ed("kitten", "sitting"), 3)
        self.assertEqual(ed("config", "cnfig"), 1)

    def test_fuzzy_match_ngram(self):
        """Layer 2: N-gram partial match should find nodes when exact match fails."""
        self.kg.add_node(
            node_id="chunk-1",
            label="网络参数设置教程",
            category="tech_manual",
        )
        # "网络参" is a partial match — N-gram should catch it
        matched = self.kg.find_nodes("网络参", top_k=5)
        self.assertGreater(len(matched), 0)
        self.assertEqual(matched[0][1], "chunk-1")

    def test_fuzzy_match_edit_distance(self):
        """Layer 3: Edit distance fuzzy match should find spelling variants."""
        self.kg.add_node(
            node_id="chunk-1",
            label="configuration settings",
            category="tech_manual",
        )
        # "configration" is a misspelling of "configuration"
        matched = self.kg.find_nodes("configration", top_k=5)
        self.assertGreater(len(matched), 0)
        self.assertEqual(matched[0][1], "chunk-1")


class GraphBuilderTest(unittest.TestCase):
    """Test GraphBuilder from ChromaStore data."""

    def setUp(self):
        from app.utils import config as cfg
        from app.models.chroma_store import ChromaStore
        from app.models.knowledge_graph import KnowledgeGraph

        KnowledgeGraph._reset()
        self.cfg = cfg
        self.ChromaStore = ChromaStore
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.old_instance = ChromaStore._instance
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {}
        ChromaStore._instance = None
        self.store = ChromaStore()
        self.assertTrue(self.store.connect())

    def tearDown(self):
        self.ChromaStore._instance = self.old_instance
        self.cfg._BASE = self.old_base
        self.cfg._CONFIG_PATH = self.old_config_path
        self.cfg._cache = self.old_cache
        self.temp_dir.cleanup()
        from app.models.knowledge_graph import KnowledgeGraph
        KnowledgeGraph._reset()

    def test_build_from_empty_store(self):
        from app.models.graph_builder import GraphBuilder
        builder = GraphBuilder()
        G = builder.build(self.store)
        self.assertEqual(G.number_of_nodes(), 0)

    def test_build_from_chunks(self):
        # Add some chunks to the store
        self.store.add(
            content="工控机IP配置步骤",
            source_type="markdown",
            anchor_id="chunk-a",
            doc_id="chunk-a",
            category="tech_manual",
            heading_path="快速入门 > 工控机IP配置",
        )
        self.store.add(
            content="网络参数设置",
            source_type="markdown",
            anchor_id="chunk-b",
            doc_id="chunk-b",
            category="tech_manual",
            heading_path="快速入门 > 工控机IP配置 > 网络参数",
        )

        from app.models.graph_builder import GraphBuilder
        builder = GraphBuilder()
        G = builder.build(self.store)
        self.assertEqual(G.number_of_nodes(), 2)
        # Should have heading hierarchy edge
        self.assertGreaterEqual(G.number_of_edges(), 1)


class GraphTraverserTest(unittest.TestCase):
    """Test GraphTraverser search logic."""

    def setUp(self):
        from app.utils import config as cfg
        from app.models.chroma_store import ChromaStore
        from app.models.knowledge_graph import KnowledgeGraph

        KnowledgeGraph._reset()
        self.cfg = cfg
        self.ChromaStore = ChromaStore
        self.old_base = cfg._BASE
        self.old_config_path = cfg._CONFIG_PATH
        self.old_cache = dict(cfg._cache)
        self.old_instance = ChromaStore._instance
        self.temp_dir = tempfile.TemporaryDirectory()
        cfg._BASE = Path(self.temp_dir.name)
        cfg._CONFIG_PATH = cfg._BASE / "config.json"
        cfg._cache = {"retrieval": {"top_k": 5}}
        ChromaStore._instance = None
        self.store = ChromaStore()
        self.assertTrue(self.store.connect())

        # Add test data
        self.store.add(
            content="工控机IP配置步骤：1. 打开网络连接 2. 设置IP地址",
            source_type="markdown",
            anchor_id="chunk-ip",
            doc_id="chunk-ip",
            category="tech_manual",
            heading_path="快速入门 > 工控机IP配置",
            pdf_payload={"file": "guide.pdf"},
        )
        self.store.add(
            content="网络参数：速度和双工模式配置",
            source_type="markdown",
            anchor_id="chunk-net",
            doc_id="chunk-net",
            category="tech_manual",
            heading_path="快速入门 > 工控机IP配置 > 网络参数",
            pdf_payload={"file": "guide.pdf"},
        )

        # Build graph
        from app.models.graph_builder import GraphBuilder
        builder = GraphBuilder()
        G = builder.build(self.store)
        kg = KnowledgeGraph()
        kg._G = G
        kg.detect_communities()
        kg._loaded = True

    def tearDown(self):
        self.ChromaStore._instance = self.old_instance
        self.cfg._BASE = self.old_base
        self.cfg._CONFIG_PATH = self.old_config_path
        self.cfg._cache = self.old_cache
        self.temp_dir.cleanup()
        from app.models.knowledge_graph import KnowledgeGraph
        KnowledgeGraph._reset()

    def test_search_returns_results(self):
        from app.models.graph_traverser import GraphTraverser
        traverser = GraphTraverser()
        result = traverser.search(query="工控机IP配置", top_k=5)
        self.assertEqual(result["router_source"], "graph_traversal")
        self.assertGreater(len(result["chunks"]), 0)
        self.assertTrue(result["validated"])

    def test_search_fallback_when_no_graph(self):
        from app.models.knowledge_graph import KnowledgeGraph
        from app.models.graph_traverser import GraphTraverser
        KnowledgeGraph._reset()
        kg = KnowledgeGraph()  # empty graph
        traverser = GraphTraverser()
        result = traverser.search(query="工控机IP配置", top_k=5)
        self.assertEqual(result["router_source"], "text_search_fallback")

    def test_search_result_format_compatible(self):
        from app.models.graph_traverser import GraphTraverser
        traverser = GraphTraverser()
        result = traverser.search(query="网络参数", top_k=5)
        # Check all required keys from GemmaRouter.search() format
        required_keys = [
            "category", "standalone_query", "chunks", "heading_paths",
            "router_source", "attempts", "validated", "timings", "reasoning_steps",
        ]
        for key in required_keys:
            self.assertIn(key, result, f"Missing key: {key}")


if __name__ == "__main__":
    unittest.main()
