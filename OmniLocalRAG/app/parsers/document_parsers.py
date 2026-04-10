"""
Document parser adapters.

Each parser is encapsulated as an independent class so it can be benchmarked
and selected manually. No auto fallback/downgrade logic is implemented here.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.utils import config as cfg

ParsedItems = List[Tuple[str, int, list, str]]


@dataclass(frozen=True)
class ParserParameter:
    name: str
    label: str
    kind: str = "text"  # text | int | bool | select
    default: Any = ""
    options: Tuple[Tuple[str, Any], ...] = ()
    minimum: int = 0
    maximum: int = 9999
    step: int = 1


class BaseDocumentParser(ABC):
    name = "base"
    display_name = "Base"
    parameters: Tuple[ParserParameter, ...] = ()

    @abstractmethod
    def parse(self, file_path: str) -> ParsedItems:
        raise NotImplementedError

    def get_option(self, key: str, default: Any = None) -> Any:
        return cfg.get(f"pdf.parser_options.{self.name}.{key}", default)

    def parameter_schema(self) -> Tuple[ParserParameter, ...]:
        return self.parameters


class DoclingParser(BaseDocumentParser):
    name = "docling"
    display_name = "Docling"

    def parse(self, file_path: str) -> ParsedItems:
        from docling.document_converter import DocumentConverter  # type: ignore
        from app.workers.ingest_worker import _docling_items_to_chunks, _markdown_to_items

        converter = DocumentConverter()
        result = converter.convert(file_path)
        doc = getattr(result, "document", result)
        if hasattr(doc, "export_to_markdown"):
            markdown = doc.export_to_markdown()
            return _markdown_to_items(markdown)
        return _docling_items_to_chunks(doc)


class MarkerParser(BaseDocumentParser):
    name = "marker"
    display_name = "Marker"
    parameters = (
        ParserParameter(
            "device",
            "推理设备",
            "select",
            "cpu",
            (("CPU", "cpu"), ("CUDA", "cuda")),
        ),
    )

    def parse(self, file_path: str) -> ParsedItems:
        from marker.converters.pdf import PdfConverter  # type: ignore
        from marker.models import create_model_dict  # type: ignore
        from marker.output import text_from_rendered  # type: ignore
        from app.workers.ingest_worker import _markdown_to_items

        device = self.get_option("device", cfg.get("pdf.marker_device", "cpu"))
        os.environ.setdefault("TORCH_DEVICE", str(device))
        converter = PdfConverter(artifact_dict=create_model_dict())
        rendered = converter(file_path)
        markdown, _, _ = text_from_rendered(rendered)
        return _markdown_to_items(markdown)


class UnstructuredParser(BaseDocumentParser):
    name = "unstructured"
    display_name = "Unstructured"
    parameters = (
        ParserParameter(
            "strategy",
            "解析策略",
            "select",
            "auto",
            (("自动", "auto"), ("快速", "fast"), ("高精度", "hi_res"), ("仅 OCR", "ocr_only")),
        ),
        ParserParameter("languages", "语言列表", "text", "eng,chi_sim"),
    )

    def parse(self, file_path: str) -> ParsedItems:
        from unstructured.partition.auto import partition  # type: ignore
        from app.workers.ingest_worker import _markdown_to_items

        strategy = self.get_option("strategy", "auto")
        languages = self.get_option("languages", "eng,chi_sim")
        kwargs: Dict[str, Any] = {"filename": file_path}
        if strategy:
            kwargs["strategy"] = strategy
        if languages:
            kwargs["languages"] = [part.strip() for part in str(languages).split(",") if part.strip()]
        elements = partition(**kwargs)
        lines = []
        for elem in elements:
            text = str(elem).strip()
            if not text:
                continue
            category = getattr(elem, "category", "") or ""
            if str(category).lower() in {"title", "header"}:
                lines.append(f"# {text}")
            else:
                lines.append(text)
        markdown = "\n\n".join(lines).strip()
        return _markdown_to_items(markdown) if markdown else []


class MinerUParser(BaseDocumentParser):
    name = "mineru"
    display_name = "MinerU"
    parameters = (
        ParserParameter("command", "命令", "text", "mineru"),
        ParserParameter("extra_args", "额外参数", "text", ""),
    )

    def parse(self, file_path: str) -> ParsedItems:
        """
        MinerU adapter via external command.
        Configure in config.json:
          pdf.parser_options.mineru.command: "mineru"
        The command should output a markdown file path on stdout OR write
        <input_stem>.md under the provided temp output directory.
        """
        from app.workers.ingest_worker import _markdown_to_items

        mineru_cmd = str(self.get_option("command", cfg.get("pdf.mineru_cmd", "mineru")))
        extra_args = str(self.get_option("extra_args", "") or "")
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            cmd = shlex.split(mineru_cmd) + [
                "--input",
                file_path,
                "--output",
                str(out_dir),
                "--format",
                "markdown",
            ]
            if extra_args:
                cmd.extend(shlex.split(extra_args))
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(f"MinerU 执行失败: {detail or f'exit={proc.returncode}'}")

            md_path: Path | None = None
            stdout = (proc.stdout or "").strip()
            if stdout and Path(stdout).exists() and stdout.lower().endswith(".md"):
                md_path = Path(stdout)
            else:
                candidate = out_dir / f"{Path(file_path).stem}.md"
                if candidate.exists():
                    md_path = candidate
                else:
                    mds = sorted(out_dir.rglob("*.md"))
                    if mds:
                        md_path = mds[0]

            if md_path is None or not md_path.exists():
                raise RuntimeError("MinerU 未产出 markdown 文件，请检查 MinerU 命令参数")

            markdown = md_path.read_text(encoding="utf-8", errors="replace")
            return _markdown_to_items(markdown) if markdown.strip() else []


class OcrParser(BaseDocumentParser):
    name = "ocr"
    display_name = "OCR"
    parameters = (
        ParserParameter("lang", "OCR 语言", "text", "chi_sim+eng"),
        ParserParameter("scale", "渲染倍率", "int", 2, minimum=1, maximum=4, step=1),
    )

    def parse(self, file_path: str) -> ParsedItems:
        results = []
        try:
            import fitz  # type: ignore
            from PIL import Image  # type: ignore
            import pytesseract  # type: ignore

            lang = str(self.get_option("lang", cfg.get("pdf.ocr_lang", "chi_sim+eng")))
            scale = int(self.get_option("scale", 2))
            doc = fitz.open(file_path)
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                image = Image.open(BytesIO(pix.tobytes("png")))
                text = pytesseract.image_to_string(image, lang=lang)
                if text.strip():
                    results.append((text.strip(), page.number + 1, [], ""))
        except Exception as e:
            raise RuntimeError(f"OCR 解析失败: {e}") from e
        return results


PARSER_REGISTRY: Dict[str, BaseDocumentParser] = {
    "docling": DoclingParser(),
    "unstructured": UnstructuredParser(),
    "mineru": MinerUParser(),
    "marker": MarkerParser(),
    "ocr": OcrParser(),
}


def available_parser_names() -> List[str]:
    return list(PARSER_REGISTRY.keys())
