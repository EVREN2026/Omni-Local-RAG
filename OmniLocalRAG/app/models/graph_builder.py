"""
GraphBuilder — 从现有数据库构建知识图谱。

从 ChromaStore + SQLiteStore 提取节点和边，构建 NetworkX 图。
所有边均为 EXTRACTED（确定性关系），confidence_score = 1.0。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import networkx as nx

from app.models.chroma_store import ChromaStore
from app.models.sqlite_store import SQLiteStore
from app.utils.logger import logger


class GraphBuilder:
    """从现有数据库构建知识图谱。"""

    def build(
        self,
        chroma: Optional[ChromaStore] = None,
        sqlite: Optional[SQLiteStore] = None,
    ) -> nx.Graph:
        """完整构建流程。

        1. 从 vectors 表提取所有 chunk 为节点
        2. 从 heading_path 层级生成 references 边
        3. 从 cross_modal_map 生成 shares_data_with 边
        4. 同 category + 同 source_file 生成 conceptually_related_to 边
        5. 同 source_file 相邻 chunk 生成 references 边
        """
        G = nx.Graph()

        if chroma is None:
            chroma = ChromaStore()
        if not chroma.connect():
            logger.warning("GraphBuilder: ChromaStore not connected, returning empty graph")
            return G

        # Step 1: Extract all chunks as nodes
        chunks = self._fetch_all_chunks(chroma)
        if not chunks:
            logger.info("GraphBuilder: no chunks found, returning empty graph")
            return G

        for chunk in chunks:
            node_id = chunk.get("id", "")
            if not node_id:
                continue
            label = self._build_label(chunk)
            G.add_node(
                node_id,
                label=label,
                file_type="document",
                source_file=self._get_source_file(chunk),
                source_location=chunk.get("heading_path", ""),
                chunk_id=node_id,
                category=chunk.get("category", "general"),
                heading_path=chunk.get("heading_path", ""),
            )

        logger.info(f"GraphBuilder: {G.number_of_nodes()} nodes from ChromaStore")

        # Step 2: heading_path hierarchy edges
        self._add_heading_edges(G, chunks)

        # Step 3: cross-modal edges
        if sqlite is None:
            try:
                sqlite = SQLiteStore()
            except Exception:
                sqlite = None
        if sqlite is not None:
            self._add_cross_modal_edges(G, sqlite)

        # Step 4: same-category edges
        self._add_category_edges(G, chunks)

        # Step 5: same-source sequential edges
        self._add_source_edges(G, chunks)

        logger.info(
            f"GraphBuilder: built graph with {G.number_of_nodes()} nodes, "
            f"{G.number_of_edges()} edges"
        )
        return G

    def build_from_chunks(self, chunks: List[Dict[str, Any]]) -> nx.Graph:
        """增量构建：仅处理新增/变更的 chunks。"""
        G = nx.Graph()
        for chunk in chunks:
            node_id = chunk.get("id", chunk.get("chunk_id", ""))
            if not node_id:
                continue
            label = self._build_label(chunk)
            G.add_node(
                node_id,
                label=label,
                file_type="document",
                source_file=self._get_source_file(chunk),
                source_location=chunk.get("heading_path", ""),
                chunk_id=node_id,
                category=chunk.get("category", "general"),
                heading_path=chunk.get("heading_path", ""),
            )

        self._add_heading_edges(G, chunks)
        self._add_category_edges(G, chunks)
        self._add_source_edges(G, chunks)
        return G

    # ------------------------------------------------------------------
    # Internal: fetch chunks
    # ------------------------------------------------------------------

    def _fetch_all_chunks(self, chroma: ChromaStore) -> List[Dict[str, Any]]:
        """从 ChromaStore 获取所有 chunks。"""
        try:
            # Use search_text with a broad query to get all, or list_by_category
            categories = [
                "tech_manual", "business_sop", "company_intro",
                "parameter", "process", "project_code", "general",
            ]
            all_chunks: List[Dict[str, Any]] = []
            seen_ids: set = set()
            for cat in categories:
                rows = chroma.list_by_category(cat)
                for row in rows:
                    rid = row.get("id", "")
                    if rid and rid not in seen_ids:
                        seen_ids.add(rid)
                        all_chunks.append(row)
            return all_chunks
        except Exception as e:
            logger.error(f"GraphBuilder._fetch_all_chunks failed: {e}", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Internal: edge builders
    # ------------------------------------------------------------------

    def _add_heading_edges(self, G: nx.Graph, chunks: List[Dict]) -> None:
        """heading_path 层级关系 → references 边。

        "A > B > C" 生成: A references B, B references C
        """
        heading_map: Dict[str, str] = {}  # heading_path -> node_id (first chunk)
        for chunk in chunks:
            hp = chunk.get("heading_path", "")
            nid = chunk.get("id", chunk.get("chunk_id", ""))
            if hp and nid and hp not in heading_map:
                heading_map[hp] = nid

        for hp, nid in heading_map.items():
            parts = [p.strip() for p in hp.split(">") if p.strip()]
            if len(parts) < 2:
                continue
            # Link each level to its parent
            for i in range(1, len(parts)):
                parent_hp = " > ".join(parts[:i])
                child_hp = " > ".join(parts[: i + 1])
                parent_nid = heading_map.get(parent_hp)
                child_nid = heading_map.get(child_hp)
                if (
                    parent_nid
                    and child_nid
                    and parent_nid != child_nid
                    and G.has_node(parent_nid)
                    and G.has_node(child_nid)
                    and not G.has_edge(parent_nid, child_nid)
                ):
                    G.add_edge(
                        parent_nid,
                        child_nid,
                        relation="references",
                        confidence="EXTRACTED",
                        confidence_score=1.0,
                        source_file="",
                        weight=1.0,
                    )

    def _add_cross_modal_edges(self, G: nx.Graph, sqlite: SQLiteStore) -> None:
        """cross_modal_map → shares_data_with 边。"""
        try:
            mappings = sqlite.list_cross_modal()
        except Exception as e:
            logger.warning(f"GraphBuilder: cross_modal query failed: {e}")
            return

        for mapping in mappings:
            pdf_anchor = str(mapping.get("pdf_anchor_id") or "").strip()
            clip_id = str(mapping.get("video_clip_id") or "").strip()
            if (
                pdf_anchor
                and clip_id
                and G.has_node(pdf_anchor)
                and G.has_node(clip_id)
                and not G.has_edge(pdf_anchor, clip_id)
            ):
                G.add_edge(
                    pdf_anchor,
                    clip_id,
                    relation="shares_data_with",
                    confidence="EXTRACTED",
                    confidence_score=1.0,
                    source_file="",
                    weight=1.0,
                )

    def _add_category_edges(self, G: nx.Graph, chunks: List[Dict]) -> None:
        """同 category + 同 source_file → conceptually_related_to 边。

        为避免边爆炸，每个 chunk 最多连 5 个同 category 同源节点。
        """
        from collections import defaultdict

        cat_source_map: Dict[tuple, List[str]] = defaultdict(list)
        for chunk in chunks:
            cat = chunk.get("category", "general")
            src = self._get_source_file(chunk)
            nid = chunk.get("id", chunk.get("chunk_id", ""))
            if nid:
                cat_source_map[(cat, src)].append(nid)

        for (cat, src), node_ids in cat_source_map.items():
            # Link each node to up to 5 neighbors in same category+source
            for i, nid in enumerate(node_ids):
                if not G.has_node(nid):
                    continue
                count = 0
                for j, other_id in enumerate(node_ids):
                    if i == j or not G.has_node(other_id):
                        continue
                    if G.has_edge(nid, other_id):
                        continue
                    G.add_edge(
                        nid,
                        other_id,
                        relation="conceptually_related_to",
                        confidence="EXTRACTED",
                        confidence_score=1.0,
                        source_file=src,
                        weight=0.5,
                    )
                    count += 1
                    if count >= 5:
                        break

    def _add_source_edges(self, G: nx.Graph, chunks: List[Dict]) -> None:
        """同 source_file 相邻 chunk → references 边（顺序关系）。"""
        from collections import defaultdict

        source_chunks: Dict[str, List[tuple]] = defaultdict(list)
        for chunk in chunks:
            src = self._get_source_file(chunk)
            nid = chunk.get("id", chunk.get("chunk_id", ""))
            hp = chunk.get("heading_path", "")
            if nid and src:
                source_chunks[src].append((nid, hp))

        for src, items in source_chunks.items():
            # Sort by heading_path for sequential ordering
            items.sort(key=lambda x: x[1])
            for i in range(len(items) - 1):
                nid1 = items[i][0]
                nid2 = items[i + 1][0]
                if (
                    G.has_node(nid1)
                    and G.has_node(nid2)
                    and not G.has_edge(nid1, nid2)
                ):
                    G.add_edge(
                        nid1,
                        nid2,
                        relation="references",
                        confidence="EXTRACTED",
                        confidence_score=1.0,
                        source_file=src,
                        weight=0.3,
                    )

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_label(chunk: Dict) -> str:
        """构建节点的人类可读标签。"""
        hp = chunk.get("heading_path", "")
        content = chunk.get("content", "")
        category = chunk.get("category", "general")
        if hp:
            return f"{hp} [{category}]"
        # Fallback: first 60 chars of content
        snippet = content[:60].strip().replace("\n", " ")
        if snippet:
            return f"{snippet}... [{category}]"
        return f"chunk [{category}]"

    @staticmethod
    def _get_source_file(chunk: Dict) -> str:
        """从 chunk 提取源文件名。"""
        payload = chunk.get("pdf_payload", {})
        if isinstance(payload, dict):
            return str(payload.get("file", "")).strip()
        return ""
