import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.utils import config as cfg


class MetadataExporter:
    """Write human-reviewable PDF ingest metadata without affecting runtime stores."""

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
        source = Path(source_file)
        export_dir = self.export_root / "pdf"
        export_dir.mkdir(parents=True, exist_ok=True)

        normalized_chunks = [self._normalize_chunk(chunk) for chunk in chunks]
        payload = {
            "source_file": source.name,
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
            "content": str(chunk.get("content", "")),
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
        lines: List[str] = [
            "# PDF Chunk Export",
            "",
            f"- Source file: {payload['source_file']}",
            f"- Chunk count: {payload['chunk_count']}",
            f"- Exported at: {payload['exported_at']}",
            "",
        ]

        for index, chunk in enumerate(payload["chunks"], start=1):
            content = str(chunk.get("content", ""))
            fence = "````" if "```" in content else "```"
            lines.extend(
                [
                    f"## Chunk {index}",
                    "",
                    f"- chunk_id: {chunk.get('chunk_id', '')}",
                    f"- anchor_id: {chunk.get('anchor_id', '')}",
                    f"- page: {chunk.get('page', 0)}",
                    f"- vector_dim: {chunk.get('vector_dim')}",
                    f"- stored: {str(chunk.get('stored', False)).lower()}",
                    "",
                    f"{fence}text",
                    content,
                    fence,
                    "",
                ]
            )

        return "\n".join(lines)
