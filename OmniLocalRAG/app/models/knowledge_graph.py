"""
KnowledgeGraph — 内存知识图谱，基于 NetworkX。

支持 BFS/DFS 遍历和 Louvain 社区检测，替代 LLM 驱动的 route/locate/validate。
检索阶段零 LLM 调用，纯内存操作。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx
from networkx.readwrite import json_graph

from app.utils import config as cfg
from app.utils.logger import logger

try:
    import community as community_louvain
    _HAS_LOUVAIN = True
except ImportError:
    _HAS_LOUVAIN = False

_GRAPH_DIR = "data/knowledge_graph"
_GRAPH_FILE = "graph.json"

# Chinese + English keyword regex
_KEYWORD_RE = re.compile(r"[A-Za-z0-9_.:/\\-]{2,}|[\u4e00-\u9fff]{2,}")


class KnowledgeGraph:
    """内存知识图谱，单例模式。"""

    _instance: Optional["KnowledgeGraph"] = None
    _lock = RLock()

    def __new__(cls) -> "KnowledgeGraph":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._G = nx.Graph()
                cls._instance._communities: Dict[int, List[str]] = {}
                cls._instance._cohesion: Dict[int, float] = {}
                cls._instance._god_nodes: List[str] = []
                cls._instance._node_index: Dict[str, str] = {}
                cls._instance._dirty = False
                cls._instance._loaded = False
            return cls._instance

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load(self, path: Optional[Path] = None) -> bool:
        """从 graph.json 加载图到内存。"""
        if path is None:
            path = cfg.abs_path(f"{_GRAPH_DIR}/{_GRAPH_FILE}")
        if not path.exists():
            logger.info(f"KnowledgeGraph: no graph file at {path}")
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            G = json_graph.node_link_graph(data, edges="links")
            self._G = G
            # Load communities from graph-level attributes
            graph_data = data.get("graph", {})
            self._communities = {}
            for k, v in graph_data.get("communities", {}).items():
                self._communities[int(k)] = v
            self._cohesion = {}
            for k, v in graph_data.get("cohesion", {}).items():
                self._cohesion[int(k)] = v
            self._god_nodes = graph_data.get("god_nodes", [])
            self._rebuild_index()
            self._loaded = True
            self._dirty = False
            logger.info(
                f"KnowledgeGraph loaded: {G.number_of_nodes()} nodes, "
                f"{G.number_of_edges()} edges, {len(self._communities)} communities"
            )
            return True
        except Exception as e:
            logger.error(f"KnowledgeGraph load failed: {e}", exc_info=True)
            return False

    def save(self, path: Optional[Path] = None) -> None:
        """保存图到 graph.json。"""
        if path is None:
            path = cfg.abs_path(f"{_GRAPH_DIR}/{_GRAPH_FILE}")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json_graph.node_link_data(self._G, edges="links")
            # Attach communities/cohesion/god_nodes to graph-level dict
            data.setdefault("graph", {})
            data["graph"]["communities"] = {
                str(k): v for k, v in self._communities.items()
            }
            data["graph"]["cohesion"] = {
                str(k): v for k, v in self._cohesion.items()
            }
            data["graph"]["god_nodes"] = self._god_nodes
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info(f"KnowledgeGraph saved to {path}")
        except Exception as e:
            logger.error(f"KnowledgeGraph save failed: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Node / Edge CRUD
    # ------------------------------------------------------------------

    def add_node(
        self,
        node_id: str,
        label: str,
        file_type: str = "document",
        source_file: str = "",
        source_location: str = "",
        chunk_id: str = "",
        category: str = "general",
        heading_path: str = "",
    ) -> None:
        """添加节点到图。"""
        self._G.add_node(
            node_id,
            label=label,
            file_type=file_type,
            source_file=source_file,
            source_location=source_location,
            chunk_id=chunk_id,
            category=category,
            heading_path=heading_path,
        )
        # Update index
        for token in _KEYWORD_RE.finditer(label.lower()):
            self._node_index[token.group()] = node_id
        self._dirty = True

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        confidence: str = "EXTRACTED",
        confidence_score: float = 1.0,
        source_file: str = "",
        weight: float = 1.0,
    ) -> None:
        """添加边到图。"""
        if not self._G.has_node(source) or not self._G.has_node(target):
            return
        self._G.add_edge(
            source,
            target,
            relation=relation,
            confidence=confidence,
            confidence_score=confidence_score,
            source_file=source_file,
            weight=weight,
        )
        self._dirty = True

    def remove_nodes_by_source(self, source_file: str) -> int:
        """删除某源文件的所有节点，返回删除数量。"""
        to_remove = [
            nid
            for nid, ndata in self._G.nodes(data=True)
            if ndata.get("source_file") == source_file
        ]
        if to_remove:
            self._G.remove_nodes_from(to_remove)
            self._rebuild_index()
            self._dirty = True
        return len(to_remove)

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def find_nodes(self, query: str, top_k: int = 5) -> List[Tuple[float, str]]:
        """模糊匹配节点，返回 [(score, node_id), ...] 按分数降序。

        三层匹配策略：
        1. 精确子串匹配（最高分）
        2. N-gram 部分匹配（中分）— 拆分查询为 2-gram 片段
        3. 编辑距离模糊匹配（低分）— 处理拼写变体
        """
        terms = self._extract_terms(query)
        if not terms:
            return []

        # Build n-grams from query terms for partial matching
        query_ngrams = self._build_ngrams(terms)

        scored: Dict[str, float] = {}
        for nid, ndata in self._G.nodes(data=True):
            label = (ndata.get("label") or "").lower()
            heading = (ndata.get("heading_path") or "").lower()
            category = (ndata.get("category") or "").lower()
            combined = f"{label} {heading} {category}"

            score = 0.0

            # Layer 1: Exact substring match (highest score)
            for term in terms:
                if term in combined:
                    if term in heading:
                        score += 3.0
                    elif term in label:
                        score += 2.5
                    else:
                        score += 1.5

            # Layer 2: N-gram partial match (medium score)
            if score == 0.0:
                ngram_hits = 0
                for ng in query_ngrams:
                    if ng in combined:
                        ngram_hits += 1
                if ngram_hits > 0:
                    # Score proportional to how many n-grams matched
                    ratio = ngram_hits / max(len(query_ngrams), 1)
                    score += ratio * 1.5

            # Layer 3: Edit distance fuzzy match (low score)
            if score == 0.0:
                # Extract tokens from node text for comparison
                node_tokens = set(_KEYWORD_RE.findall(combined))
                best_fuzzy = 0.0
                for term in terms:
                    if len(term) < 2:
                        continue
                    for ntoken in node_tokens:
                        # Skip if too different in length
                        len_ratio = min(len(term), len(ntoken)) / max(len(term), len(ntoken))
                        if len_ratio < 0.6:
                            continue
                        # Compute normalized edit distance
                        dist = self._edit_distance(term, ntoken)
                        max_len = max(len(term), len(ntoken))
                        similarity = 1.0 - (dist / max_len)
                        # Threshold: at least 70% similar
                        if similarity >= 0.7:
                            best_fuzzy = max(best_fuzzy, similarity)
                if best_fuzzy > 0:
                    score += best_fuzzy * 1.0

            if score > 0:
                scored[nid] = score

        result = sorted(scored.items(), key=lambda x: -x[1])
        return [(s, nid) for nid, s in result[:top_k]]

    def traverse_bfs(
        self,
        start_nodes: List[str],
        max_depth: int = 3,
        max_nodes: int = 50,
    ) -> Tuple[Set[str], List[Tuple[str, str]]]:
        """BFS 遍历，返回 (visited_nodes, traversed_edges)。"""
        if not start_nodes:
            return set(), []

        visited: Set[str] = set()
        edges: List[Tuple[str, str]] = []
        frontier = set(n for n in start_nodes if self._G.has_node(n))
        visited.update(frontier)

        for _ in range(max_depth):
            if len(visited) >= max_nodes:
                break
            next_frontier: Set[str] = set()
            for n in frontier:
                for neighbor in self._G.neighbors(n):
                    if neighbor not in visited and len(visited) < max_nodes:
                        next_frontier.add(neighbor)
                        edges.append((n, neighbor))
            visited.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break

        return visited, edges

    def traverse_dfs(
        self,
        start_nodes: List[str],
        max_depth: int = 6,
        max_nodes: int = 50,
    ) -> Tuple[Set[str], List[Tuple[str, str]]]:
        """DFS 遍历，返回 (visited_nodes, traversed_edges)。"""
        if not start_nodes:
            return set(), []

        visited: Set[str] = set()
        edges: List[Tuple[str, str]] = []
        stack = [(n, 0) for n in reversed(start_nodes) if self._G.has_node(n)]

        while stack:
            node, depth = stack.pop()
            if node in visited or depth > max_depth or len(visited) >= max_nodes:
                continue
            visited.add(node)
            for neighbor in self._G.neighbors(node):
                if neighbor not in visited:
                    stack.append((neighbor, depth + 1))
                    edges.append((node, neighbor))

        return visited, edges

    # ------------------------------------------------------------------
    # Community detection
    # ------------------------------------------------------------------

    def detect_communities(self) -> Dict[int, List[str]]:
        """Louvain 社区检测。"""
        if self._G.number_of_nodes() == 0:
            self._communities = {}
            self._cohesion = {}
            self._god_nodes = []
            return self._communities

        t0 = time.perf_counter()

        if _HAS_LOUVAIN:
            partition = community_louvain.best_partition(self._G)
        else:
            # Fallback: connected components as communities
            partition = {}
            for cid, component in enumerate(nx.connected_components(self._G)):
                for node in component:
                    partition[node] = cid

        # Build communities dict
        self._communities = {}
        for node, cid in partition.items():
            self._communities.setdefault(cid, []).append(node)

        # Compute cohesion for each community
        self._cohesion = {}
        for cid, nodes in self._communities.items():
            subgraph = self._G.subgraph(nodes)
            n_nodes = subgraph.number_of_nodes()
            n_edges = subgraph.number_of_edges()
            if n_nodes > 1:
                max_edges = n_nodes * (n_nodes - 1) / 2
                self._cohesion[cid] = n_edges / max_edges
            else:
                self._cohesion[cid] = 0.0

        # Compute god nodes (top by degree)
        degree_sorted = sorted(
            self._G.degree(), key=lambda x: -x[1]
        )
        self._god_nodes = [nid for nid, _ in degree_sorted[:10]]

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info(
            f"Community detection: {len(self._communities)} communities, "
            f"{len(self._god_nodes)} god nodes in {elapsed:.1f}ms"
        )
        self._dirty = False
        return self._communities

    def get_community(self, node_id: str) -> int:
        """获取节点所属社区 ID。"""
        for cid, nodes in self._communities.items():
            if node_id in nodes:
                return cid
        return -1

    def get_god_nodes(self) -> List[str]:
        """获取高度连接节点列表。"""
        return self._god_nodes

    def community_cohesion(self, community_id: int) -> float:
        """获取社区内聚度。"""
        return self._cohesion.get(community_id, 0.0)

    # ------------------------------------------------------------------
    # Index
    # ------------------------------------------------------------------

    def _rebuild_index(self) -> None:
        """重建节点关键词索引。"""
        self._node_index = {}
        for nid, ndata in self._G.nodes(data=True):
            label = (ndata.get("label") or "").lower()
            for token in _KEYWORD_RE.finditer(label):
                self._node_index[token.group()] = nid

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_loaded(self) -> bool:
        return self._loaded and self._G.number_of_nodes() > 0

    def node_count(self) -> int:
        return self._G.number_of_nodes()

    def edge_count(self) -> int:
        return self._G.number_of_edges()

    @property
    def G(self) -> nx.Graph:
        return self._G

    @property
    def communities(self) -> Dict[int, List[str]]:
        return self._communities

    @property
    def dirty(self) -> bool:
        return self._dirty

    @dirty.setter
    def dirty(self, value: bool) -> None:
        self._dirty = value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_terms(query: str) -> List[str]:
        """从查询中提取关键词。"""
        seen: Set[str] = set()
        terms: List[str] = []
        # Full phrase
        phrase = " ".join(str(query or "").lower().split())
        if phrase and len(phrase) >= 2:
            seen.add(phrase)
            terms.append(phrase)
        # Individual tokens
        for match in _KEYWORD_RE.finditer(str(query or "")):
            token = match.group().lower()
            if len(token) >= 2 and token not in seen:
                seen.add(token)
                terms.append(token)
        return terms

    @staticmethod
    def _build_ngrams(terms: List[str], n: int = 2) -> List[str]:
        """从查询关键词生成 n-gram 片段。"""
        ngrams: List[str] = []
        for term in terms:
            if len(term) < n:
                continue
            for i in range(len(term) - n + 1):
                ngrams.append(term[i:i + n])
        return ngrams

    @staticmethod
    def _edit_distance(s1: str, s2: str) -> int:
        """计算 Levenshtein 编辑距离。"""
        if len(s1) < len(s2):
            return KnowledgeGraph._edit_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(
                    prev[j + 1] + 1,      # delete
                    curr[j] + 1,          # insert
                    prev[j] + (c1 != c2)  # replace
                ))
            prev = curr
        return prev[-1]

    # ------------------------------------------------------------------
    # Reset (for testing)
    # ------------------------------------------------------------------

    @classmethod
    def _reset(cls) -> None:
        """Reset singleton for testing."""
        cls._instance = None
