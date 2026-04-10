import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from app.utils import config as cfg


class QAMemoryRecorder:
    """Append-only QA evaluation memory for human accuracy review."""

    def __init__(self) -> None:
        self.enabled = bool(cfg.get("qa_memory.enabled", True))
        self.md_path = cfg.abs_path(cfg.get("qa_memory.path", "data/eval/qa_dialogue_memory.md"))
        self.jsonl_path = cfg.abs_path(
            cfg.get("qa_memory.jsonl_path", "data/eval/qa_dialogue_memory.jsonl")
        )
        self.max_context_chars = int(cfg.get("qa_memory.max_context_chars", 1200))

    def record(
        self,
        query: str,
        retrieved_context: Iterable[Dict[str, Any]],
        answer: str,
        mode: str,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {}

        entry = {
            "id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "query": query,
            "answer": answer,
            "retrieved_context": [self._normalize_hit(hit, index) for index, hit in enumerate(retrieved_context, 1)],
            "human_review": {
                "expected_answer": "",
                "verdict": "unreviewed",
                "accuracy_score": None,
                "retrieval_score": None,
                "notes": "",
                "chunking_action": "",
            },
        }

        self._append_jsonl(entry)
        self._append_markdown(entry)
        return entry

    def ensure_markdown_header(self) -> None:
        self.md_path.parent.mkdir(parents=True, exist_ok=True)
        if self.md_path.exists() and self.md_path.stat().st_size > 0:
            return
        self.md_path.write_text(
            "\n".join(
                [
                    "# QA Dialogue Memory",
                    "",
                    "用于记录本地 RAG 问答准确性验证样本。",
                    "",
                    "评审时重点填写：Expected answer、Verdict、Accuracy score、Retrieval score、Notes、Chunking action。",
                    "",
                    "Verdict 建议值：correct / partial / wrong / unsupported / retrieval_miss。",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def _append_jsonl(self, entry: Dict[str, Any]) -> None:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _append_markdown(self, entry: Dict[str, Any]) -> None:
        self.ensure_markdown_header()
        with self.md_path.open("a", encoding="utf-8") as f:
            f.write(self._format_markdown_entry(entry))

    def _normalize_hit(self, hit: Dict[str, Any], rank: int) -> Dict[str, Any]:
        document = str(hit.get("document") or hit.get("content") or "").strip()
        if len(document) > self.max_context_chars:
            document = document[: self.max_context_chars].rstrip() + "..."
        return {
            "rank": rank,
            "id": str(hit.get("id", "")),
            "anchor_id": str(hit.get("anchor_id", "")),
            "source_type": str(hit.get("source_type", "")),
            "distance": hit.get("distance"),
            "document": document,
            "pdf_payload": self._json_safe(hit.get("pdf_payload", {})),
            "video_payload": self._json_safe(hit.get("video_payload", {})),
        }

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            json.dumps(value, ensure_ascii=False)
            return value
        except TypeError:
            return str(value)

    def _format_markdown_entry(self, entry: Dict[str, Any]) -> str:
        lines: List[str] = [
            "",
            f"## QA {entry['created_at']}  `{entry['id']}`",
            "",
            f"- Mode: {entry['mode']}",
            f"- Verdict: {entry['human_review']['verdict']}",
            "- Accuracy score: ",
            "- Retrieval score: ",
            "- Chunking action: ",
            "",
            "### Question",
            "",
            entry["query"],
            "",
            "### Answer",
            "",
            entry["answer"],
            "",
            "### Retrieved Chunks",
            "",
        ]
        for hit in entry["retrieved_context"]:
            lines.extend(
                [
                    f"#### Hit {hit['rank']}",
                    "",
                    f"- id: {hit['id']}",
                    f"- anchor_id: {hit['anchor_id']}",
                    f"- source_type: {hit['source_type']}",
                    f"- distance: {hit['distance']}",
                    "",
                    "```text",
                    hit["document"],
                    "```",
                    "",
                ]
            )
        lines.extend(
            [
                "### Human Review",
                "",
                "- Expected answer: ",
                "- Notes: ",
                "",
            ]
        )
        return "\n".join(lines)
