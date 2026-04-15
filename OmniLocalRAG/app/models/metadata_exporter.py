import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.utils import config as cfg


class MetadataExporter:
    """Write human-reviewable ingest metadata without affecting runtime stores."""

    def __init__(
        self,
        export_root: Optional[str] = None,
        write_json: Optional[bool] = None,
        write_markdown: Optional[bool] = None,
    ) -> None:
        configured_root = export_root or cfg.get("exports.path", "data/exports")
        root = Path(configured_root)
        self.export_root = root if root.is_absolute() else cfg.abs_path(str(root))
        self.write_json = cfg.get("exports.write_json", True) if write_json is None else write_json
        self.write_markdown = cfg.get("exports.write_markdown", True) if write_markdown is None else write_markdown

    def export_pdf_chunks(self, source_file: str, chunks: Iterable[Dict[str, Any]]) -> Dict[str, Path]:
        return self.export_chunks(source_file=source_file, chunks=chunks, source_type="pdf")

    def export_chunks(
        self,
        source_file: str,
        chunks: Iterable[Dict[str, Any]],
        source_type: str,
    ) -> Dict[str, Path]:
        source = Path(source_file)
        source_type = self._safe_source_type(source_type)
        export_dir = self.export_root / source_type
        export_dir.mkdir(parents=True, exist_ok=True)

        normalized_chunks = [self._normalize_chunk(chunk) for chunk in chunks]
        payload = {
            "source_file": source.name,
            "source_type": source_type,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "chunk_count": len(normalized_chunks),
            "chunks": normalized_chunks,
        }

        paths: Dict[str, Path] = {}
        stem = self._safe_stem(source)
        if self.write_json:
            json_path = export_dir / f"{stem}.chunks.json"
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            paths["json"] = json_path

        if self.write_markdown:
            md_path = export_dir / f"{stem}.chunks.md"
            md_path.write_text(self._render_markdown(payload), encoding="utf-8")
            paths["markdown"] = md_path

        return paths

    @staticmethod
    def _safe_source_type(source_type: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", source_type or "").strip("._")
        return cleaned or "document"

    @staticmethod
    def _safe_stem(source: Path) -> str:
        stem = source.stem or "pdf"
        return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "pdf"

    @staticmethod
    def _normalize_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "chunk_id": str(chunk.get("chunk_id", "")),
            "anchor_id": str(chunk.get("anchor_id", "")),
            "source_type": chunk.get("source_type", "pdf"),
            "page": chunk.get("page", 0),
            "coords": MetadataExporter._json_safe(chunk.get("coords", [])),
            "heading_path": str(chunk.get("heading_path", "")),
            "block_type": str(chunk.get("block_type", "text")),
            "content": str(chunk.get("content", "")),
            "display_content": str(chunk.get("display_content", chunk.get("content", ""))),
            "embedding_text": str(chunk.get("embedding_text", chunk.get("content", ""))),
            "metadata": MetadataExporter._json_safe(chunk.get("metadata", {})),
            "vector_dim": chunk.get("vector_dim"),
            "stored": bool(chunk.get("stored", False)),
            "is_manual": bool(chunk.get("is_manual", False)),
        }

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            json.dumps(value)
            return value
        except TypeError:
            return str(value)

    @staticmethod
    def _render_markdown(payload: Dict[str, Any]) -> str:
        source_type = str(payload.get("source_type", "document"))
        label = "PDF" if source_type.lower() == "pdf" else source_type.title()
        lines: List[str] = [
            f"# {label} Chunk Export",
            "",
            f"- Source file: {payload['source_file']}",
            f"- Source type: {source_type}",
            f"- Chunk count: {payload['chunk_count']}",
            f"- Exported at: {payload['exported_at']}",
            "",
        ]

        for index, chunk in enumerate(payload["chunks"], start=1):
            display_content = str(chunk.get("display_content", chunk.get("content", "")))
            embedding_text = str(chunk.get("embedding_text", chunk.get("content", "")))
            semantic_description = str((chunk.get("metadata", {}) or {}).get("semantic_description", ""))
            fence = "````" if "```" in display_content else "```"
            heading_path = chunk.get("heading_path", "")
            block_type = chunk.get("block_type", "text")
            meta_lines = [
                f"- chunk_id: {chunk.get('chunk_id', '')}",
                f"- anchor_id: {chunk.get('anchor_id', '')}",
                f"- page: {chunk.get('page', 0)}",
                f"- vector_dim: {chunk.get('vector_dim')}",
                f"- stored: {str(chunk.get('stored', False)).lower()}",
                f"- block_type: {block_type}",
            ]
            if heading_path:
                meta_lines.append(f"- heading_path: {heading_path}")
            if semantic_description:
                meta_lines.append(f"- semantic_description: {semantic_description}")
            lines.extend(
                [
                    f"## Chunk {index}",
                    "",
                    *meta_lines,
                    "",
                    "### Display Content",
                    "",
                    f"{fence}text",
                    display_content,
                    fence,
                    "",
                ]
            )
            if embedding_text and embedding_text != display_content:
                embedding_fence = "````" if "```" in embedding_text else "```"
                lines.extend(
                    [
                        "### Embedding Text",
                        "",
                        f"{embedding_fence}text",
                        embedding_text,
                        embedding_fence,
                        "",
                    ]
                )

        return "\n".join(lines)
