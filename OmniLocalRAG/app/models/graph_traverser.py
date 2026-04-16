"""
GraphTraverser — 知识图谱遍历检索器。

替代 GemmaRouter 的 route+locate+validate，纯内存操作，零 LLM 调用。
检索延迟目标: <50ms。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set, Tuple

from app.models.chroma_store import ChromaStore
from app.models.knowledge_graph import KnowledgeGraph
from app.utils import config as cfg
from app.utils.category_keywords import heuristic_category
from app.utils.logger import logger


class GraphTraverser:
    """知识图谱遍历检索器。"""

    def __init__(self) -> None:
        self._kg = KnowledgeGraph()

    def search(
        self,
        query: str,
        memory: Optional[List[Dict[str, str]]] = None,
        top_k: Optional[int] = None,
    ) -> dict:
        """完整检索流程（纯内存，零 LLM 调用）。

        1. 查询改写（启发式，复用 _heuristic_condense）
        2. 关键词匹配 → 找到起始节点
        3. BFS 遍历 → 扩展相关节点
        4. 社区感知排序 → 优先高内聚社区结果
        5. 置信度过滤 → EXTRACTED > INFERRED > AMBIGUOUS
        6. 解析 chunk 内容
        7. 返回 top_k 结果

        Returns:
            与 GemmaRouter.search() 兼容的 dict:
            {
                "category": str,
                "standalone_query": str,
                "chunks": list[dict],
                "heading_paths": list[str],
                "router_source": "graph_traversal",
                "attempts": 1,
                "validated": True,
                "timings": dict,
                "reasoning_steps": list[str],
            }
        """
        if top_k is None:
            top_k = int(cfg.get("retrieval.top_k", 5))

        timings: Dict[str, float] = {}
        reasoning_steps: List[str] = []

        # Phase 1: Heuristic query condensation
        t0 = time.perf_counter()
        standalone_query = self._condense(query, memory or [])
        condense_ms = (time.perf_counter() - t0) * 1000
        timings["condense_ms"] = condense_ms

        # Phase 2: Match nodes by keywords
        t1 = time.perf_counter()
        matched = self._kg.find_nodes(standalone_query, top_k=10)
        match_ms = (time.perf_counter() - t1) * 1000
        timings["match_ms"] = match_ms

        if not matched:
            # No matching nodes — fallback to text search
            reasoning_steps.append(f"[匹配] 无匹配节点，降级文本搜索")
            return self._fallback_text_search(query, standalone_query, top_k, timings, reasoning_steps)

        start_node_ids = [nid for _, nid in matched]
        reasoning_steps.append(
            f"[匹配] 命中{len(matched)}个起始节点: "
            f"{', '.join(self._kg.G.nodes[nid].get('label', nid)[:30] for _, nid in matched[:3])}"
        )

        # Phase 3: BFS traversal
        t2 = time.perf_counter()
        visited, edges = self._kg.traverse_bfs(
            start_nodes=start_node_ids,
            max_depth=int(cfg.get("graph.traversal_depth", 3)),
            max_nodes=int(cfg.get("graph.traversal_max_nodes", 50)),
        )
        traverse_ms = (time.perf_counter() - t2) * 1000
        timings["traverse_ms"] = traverse_ms
        reasoning_steps.append(
            f"[遍历] BFS 扩展到{len(visited)}个节点, {len(edges)}条边"
        )

        # Phase 4: Community-aware ranking
        t3 = time.perf_counter()
        ranked_nodes = self._rank_by_community(visited, standalone_query)
        rank_ms = (time.perf_counter() - t3) * 1000
        timings["rank_ms"] = rank_ms

        # Phase 5: Resolve chunks from ChromaStore
        t4 = time.perf_counter()
        chunks, heading_paths, category = self._resolve_chunks(
            ranked_nodes[:top_k]
        )
        resolve_ms = (time.perf_counter() - t4) * 1000
        timings["resolve_ms"] = resolve_ms
        reasoning_steps.append(
            f"[解析] 获取{len(chunks)}条chunk, 分类={category}"
        )

        # Total
        total_ms = (time.perf_counter() - t0) * 1000
        timings["retrieval_total_ms"] = total_ms

        reasoning_steps.append(
            f"[结果] 命中{len(chunks)}条, 验证通过, 图遍历耗时{total_ms:.1f}ms"
        )

        return {
            "category": category,
            "standalone_query": standalone_query,
            "chunks": chunks,
            "heading_paths": heading_paths,
            "router_source": "graph_traversal",
            "attempts": 1,
            "validated": True,
            "timings": timings,
            "reasoning_steps": reasoning_steps,
        }

    # ------------------------------------------------------------------
    # Internal: query condensation
    # ------------------------------------------------------------------

    @staticmethod
    def _condense(query: str, memory: List[Dict[str, str]]) -> str:
        """启发式查询改写，复用现有逻辑。"""
        from app.utils.category_keywords import _FOLLOWUP_MARKERS

        q = (query or "").strip()
        if not q or not memory:
            return q
        if any(marker in q for marker in _FOLLOWUP_MARKERS):
            last = memory[-1]
            anchor = last.get("standalone_query") or last.get("query") or ""
            if anchor.strip():
                return f'基于"{anchor}"：{q}'
        return q

    # ------------------------------------------------------------------
    # Internal: community-aware ranking
    # ------------------------------------------------------------------

    def _rank_by_community(
        self, node_ids: Set[str], query: str
    ) -> List[str]:
        """按社区内聚度 + 查询相关性排序节点（三层模糊匹配）。"""
        from app.models.knowledge_graph import _KEYWORD_RE

        kg = self._kg
        # 提取关键词：空格分词 + 正则提取中英文 token
        terms = [t.lower() for t in query.split() if len(t) > 1]
        extra_terms = [
            m.group().lower()
            for m in _KEYWORD_RE.finditer(query)
            if len(m.group()) >= 2
        ]
        all_terms = list(dict.fromkeys(terms + extra_terms))  # 去重保序
        query_ngrams = kg._build_ngrams(all_terms)

        scored: List[Tuple[float, str]] = []
        for nid in node_ids:
            if not kg.G.has_node(nid):
                continue
            ndata = kg.G.nodes[nid]
            label = (ndata.get("label") or "").lower()
            heading = (ndata.get("heading_path") or "").lower()
            category = (ndata.get("category") or "").lower()
            combined = f"{label} {heading} {category}"

            # Layer 1: Exact substring match (highest score)
            relevance = 0.0
            for t in all_terms:
                if t in combined:
                    if t in heading:
                        relevance += 3.0
                    elif t in label:
                        relevance += 2.5
                    else:
                        relevance += 1.5

            # Layer 2: N-gram partial match (medium score)
            if relevance == 0.0:
                ngram_hits = sum(1 for ng in query_ngrams if ng in combined)
                if ngram_hits > 0:
                    ratio = ngram_hits / max(len(query_ngrams), 1)
                    relevance += ratio * 1.5

            # Layer 3: Edit distance fuzzy match (low score)
            if relevance == 0.0:
                node_tokens = set(_KEYWORD_RE.findall(combined))
                best_fuzzy = 0.0
                for t in all_terms:
                    if len(t) < 2:
                        continue
                    for ntoken in node_tokens:
                        len_ratio = min(len(t), len(ntoken)) / max(len(t), len(ntoken))
                        if len_ratio < 0.6:
                            continue
                        dist = kg._edit_distance(t, ntoken)
                        similarity = 1.0 - (dist / max(len(t), len(ntoken)))
                        if similarity >= 0.7:
                            best_fuzzy = max(best_fuzzy, similarity)
                if best_fuzzy > 0:
                    relevance += best_fuzzy * 1.0

            # Community cohesion bonus
            community_id = kg.get_community(nid)
            cohesion = kg.community_cohesion(community_id) if community_id >= 0 else 0.0

            # Degree bonus (more connected = more central)
            degree = kg.G.degree(nid)
            degree_bonus = min(degree / 10.0, 1.0)

            # Combined score
            score = relevance * 2.0 + cohesion * 1.0 + degree_bonus * 0.5
            scored.append((score, nid))

        scored.sort(key=lambda x: -x[0])
        return [nid for _, nid in scored]

    # ------------------------------------------------------------------
    # Internal: resolve chunks
    # ------------------------------------------------------------------

    def _resolve_chunks(
        self, node_ids: List[str]
    ) -> Tuple[List[dict], List[str], str]:
        """从 ChromaStore 获取节点对应的 chunk 内容。"""
        chroma = ChromaStore()
        if not chroma._ensure_connected():
            return [], [], "general"

        chunks: List[dict] = []
        heading_paths: List[str] = []
        categories: Dict[str, int] = {}
        seen_ids: Set[str] = set()

        for nid in node_ids:
            if nid in seen_ids:
                continue
            seen_ids.add(nid)

            ndata = self._kg.G.nodes.get(nid, {})
            chunk_id = ndata.get("chunk_id", nid)

            # Try to fetch from ChromaStore by ID
            try:
                with chroma._conn() as conn:
                    row = conn.execute(
                        "SELECT * FROM vectors WHERE id=?", (chunk_id,)
                    ).fetchone()
                if row is not None:
                    chunk = ChromaStore._row_to_dict(row)
                    chunks.append(chunk)
                    hp = chunk.get("heading_path", "")
                    if hp and hp not in heading_paths:
                        heading_paths.append(hp)
                    cat = chunk.get("category", "general")
                    categories[cat] = categories.get(cat, 0) + 1
            except Exception as e:
                logger.debug(f"GraphTraverser: chunk {chunk_id} fetch failed: {e}")

        # Determine dominant category
        category = "general"
        if categories:
            category = max(categories, key=categories.get)  # type: ignore

        return chunks, heading_paths, category

    # ------------------------------------------------------------------
    # Internal: fallback
    # ------------------------------------------------------------------

    def _fallback_text_search(
        self,
        query: str,
        standalone_query: str,
        top_k: int,
        timings: Dict[str, float],
        reasoning_steps: List[str],
    ) -> dict:
        """降级到 ChromaStore.search_text()。"""
        t0 = time.perf_counter()
        chroma = ChromaStore()
        results = chroma.search_text(query, limit=top_k)
        search_ms = (time.perf_counter() - t0) * 1000
        timings["search_text_ms"] = search_ms
        timings["retrieval_total_ms"] = (
            timings.get("condense_ms", 0) + search_ms
        )

        heading_paths = list(
            {r.get("heading_path", "") for r in results if r.get("heading_path")}
        )
        category = heuristic_category(query)

        reasoning_steps.append(
            f"[降级] 文本搜索命中{len(results)}条, 耗时{search_ms:.1f}ms"
        )

        return {
            "category": category,
            "standalone_query": standalone_query,
            "chunks": results,
            "heading_paths": heading_paths,
            "router_source": "text_search_fallback",
            "attempts": 1,
            "validated": False,
            "timings": timings,
            "reasoning_steps": reasoning_steps,
        }
