"""
GraphUpdater — 增量更新知识图谱。

文档入库/变更时调用，增量更新图中的节点和边。
更新后标记 dirty，由 maybe_recluster() 触发重聚类。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.models.knowledge_graph import KnowledgeGraph
from app.utils.logger import logger


class GraphUpdater:
    """增量更新知识图谱。"""

    def __init__(self) -> None:
        self._kg = KnowledgeGraph()

    def on_chunks_added(
        self,
        chunks: List[Dict[str, Any]],
        source_file: str,
    ) -> None:
        """新增 chunks 时更新图。

        1. 添加新节点
        2. 添加新边（heading/category/source 关系）
        3. 标记 dirty
        """
        if not chunks:
            return

        # Add nodes
        for chunk in chunks:
            chunk_id = chunk.get("id", chunk.get("chunk_id", ""))
            if not chunk_id:
                continue
            label = self._build_label(chunk, source_file)
            self._kg.add_node(
                node_id=chunk_id,
                label=label,
                file_type="document",
                source_file=source_file,
                source_location=chunk.get("heading_path", ""),
                chunk_id=chunk_id,
                category=chunk.get("category", "general"),
                heading_path=chunk.get("heading_path", ""),
            )

        # Add edges
        self._add_heading_edges(chunks)
        self._add_category_edges(chunks, source_file)
        self._add_source_edges(chunks, source_file)

        logger.info(
            f"GraphUpdater: added {len(chunks)} nodes for {source_file}"
        )

    def on_chunks_deleted(self, source_file: str) -> None:
        """删除文件的所有 chunks 时更新图。"""
        removed = self._kg.remove_nodes_by_source(source_file)
        if removed:
            logger.info(f"GraphUpdater: removed {removed} nodes for {source_file}")

    def on_chunks_updated(
        self,
        chunks: List[Dict[str, Any]],
        source_file: str,
    ) -> None:
        """更新 chunks 时重建该文件的节点和边。"""
        self.on_chunks_deleted(source_file)
        self.on_chunks_added(chunks, source_file)

    def maybe_recluster(self) -> None:
        """如果图 dirty，重跑社区检测并保存。"""
        if self._kg.dirty:
            self._kg.detect_communities()
            self._kg.save()
            logger.info("GraphUpdater: re-clustered and saved graph")

    # ------------------------------------------------------------------
    # Internal: edge builders (lightweight versions for incremental)
    # ------------------------------------------------------------------

    def _add_heading_edges(self, chunks: List[Dict]) -> None:
        """heading_path 层级关系 → references 边。"""
        heading_map: Dict[str, str] = {}
        for chunk in chunks:
            hp = chunk.get("heading_path", "")
            nid = chunk.get("id", chunk.get("chunk_id", ""))
            if hp and nid and hp not in heading_map:
                heading_map[hp] = nid

        for hp, nid in heading_map.items():
            parts = [p.strip() for p in hp.split(">") if p.strip()]
            if len(parts) < 2:
                continue
            for i in range(1, len(parts)):
                parent_hp = " > ".join(parts[:i])
                child_hp = " > ".join(parts[: i + 1])
                parent_nid = heading_map.get(parent_hp)
                child_nid = heading_map.get(child_hp)
                if parent_nid and child_nid and parent_nid != child_nid:
                    self._kg.add_edge(
                        source=parent_nid,
                        target=child_nid,
                        relation="references",
                        confidence="EXTRACTED",
                        confidence_score=1.0,
                        source_file="",
                        weight=1.0,
                    )

    def _add_category_edges(self, chunks: List[Dict], source_file: str) -> None:
        """同 category + 同 source_file → conceptually_related_to 边。"""
        for i, chunk in enumerate(chunks):
            nid = chunk.get("id", chunk.get("chunk_id", ""))
            if not nid:
                continue
            count = 0
            for j, other in enumerate(chunks):
                if i == j:
                    continue
                other_id = other.get("id", other.get("chunk_id", ""))
                if not other_id:
                    continue
                if chunk.get("category") == other.get("category"):
                    self._kg.add_edge(
                        source=nid,
                        target=other_id,
                        relation="conceptually_related_to",
                        confidence="EXTRACTED",
                        confidence_score=1.0,
                        source_file=source_file,
                        weight=0.5,
                    )
                    count += 1
                    if count >= 5:
                        break

    def _add_source_edges(self, chunks: List[Dict], source_file: str) -> None:
        """同 source_file 相邻 chunk → references 边。"""
        items = []
        for chunk in chunks:
            nid = chunk.get("id", chunk.get("chunk_id", ""))
            hp = chunk.get("heading_path", "")
            if nid:
                items.append((nid, hp))

        items.sort(key=lambda x: x[1])
        for i in range(len(items) - 1):
            self._kg.add_edge(
                source=items[i][0],
                target=items[i + 1][0],
                relation="references",
                confidence="EXTRACTED",
                confidence_score=1.0,
                source_file=source_file,
                weight=0.3,
            )

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_label(chunk: Dict, source_file: str) -> str:
        """构建节点标签。"""
        hp = chunk.get("heading_path", "")
        content = chunk.get("content", chunk.get("display_content", ""))
        category = chunk.get("category", "general")
        if hp:
            return f"{hp} [{category}]"
        snippet = (content or "")[:60].strip().replace("\n", " ")
        if snippet:
            return f"{snippet}... [{category}]"
        return f"{source_file} [{category}]"
