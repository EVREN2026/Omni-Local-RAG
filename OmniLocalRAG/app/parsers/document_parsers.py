"""
Document parser adapters.

Each parser is encapsulated as an independent class so it can be benchmarked
and selected manually. No auto fallback/downgrade logic is implemented here.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
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
    def to_markdown(self, file_path: str) -> str:
        raise NotImplementedError

    def parse(self, file_path: str) -> ParsedItems:
        markdown = self.to_markdown(file_path)
        # Import chunker after parser execution to avoid early GUI/DLL side
        # effects (notably on Windows when some parser backends load ONNX).
        from app.workers.ingest_worker import _markdown_to_items

        return _markdown_to_items(markdown) if markdown.strip() else []

    def get_option(self, key: str, default: Any = None) -> Any:
        return cfg.get(f"pdf.parser_options.{self.name}.{key}", default)

    def parameter_schema(self) -> Tuple[ParserParameter, ...]:
        return self.parameters

    def _get_output_dir(self, file_path: str) -> Path:
            """强化版：强制使用绝对路径，并解决可能的编码问题"""
            # 使用 .resolve().absolute() 确保路径是系统级的绝对路径
            full_path = Path(file_path).resolve().absolute()
            base_path = full_path.parent
            stem = full_path.stem
            
            # 文件夹命名：增加解析器后缀
            out_dir = base_path / f"{stem}_{self.name}_output"
            
            # 强制创建目录
            out_dir.mkdir(parents=True, exist_ok=True)
            
            # --- 关键调试代码：这一行会在你执行测试时直接在控制台打印出物理路径 ---
            print(f"\n[DEBUG] 正在创建输出目录: {out_dir}")
            if not out_dir.exists():
                print(f"[ERROR] 文件夹创建失败，请检查权限！")
            # -----------------------------------------------------------
            
            return out_dir

class DoclingParser(BaseDocumentParser):
    name = "docling"
    display_name = "Docling"

    def to_markdown(self, file_path: str) -> str:
        isolated = bool(self.get_option("isolated", True))
        timeout_sec = int(self.get_option("timeout_sec", 300))
        out_dir = self._get_output_dir(file_path)

        print(f"[INFO] 正在启动 Docling 解析...")

        try:
            if isolated:
                md_text = self._convert_docling_isolated(file_path, timeout_sec, str(out_dir))
            else:
                md_text = self._convert_docling_inline(file_path, str(out_dir))
            
            return md_text

        except Exception as e:
            raise RuntimeError(f"Docling 解析失败: {e}")

    @staticmethod
    def _convert_docling_inline(file_path: str, out_dir: str) -> str:
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(file_path)
        doc = getattr(result, "document", result)
        
        # 保存图像
        if hasattr(doc, "images") and doc.images:
            img_dir = Path(out_dir) / "images"
            img_dir.mkdir(exist_ok=True)
            for i, img_item in enumerate(doc.images):
                img_item.pil_image.save(img_dir / f"image_{i+1}.png")
        
        md_text = doc.export_to_markdown() if hasattr(doc, "export_to_markdown") else ""
        (Path(out_dir) / "document.md").write_text(md_text, encoding="utf-8")
        return md_text

    @staticmethod
    def _convert_docling_isolated(file_path: str, timeout_sec: int, out_dir: str) -> str:
        # 脚本内强制设置输出编码为 utf-8，解决 Windows 编码报错
        helper_code = r"""
import pathlib
import sys
import io

# 强制设置 IO 编码为 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

src, out_dir_str = sys.argv[1], sys.argv[2]
out_dir = pathlib.Path(out_dir_str)

try:
    from docling.document_converter import DocumentConverter
    converter = DocumentConverter()
    result = converter.convert(src)
    doc = getattr(result, "document", result)

    if hasattr(doc, "images"):
        img_dir = out_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        for i, img_item in enumerate(doc.images):
            img_item.pil_image.save(img_dir / f"image_{i+1}.png")

    markdown = doc.export_to_markdown() if hasattr(doc, "export_to_markdown") else ""
    (out_dir / "document.md").write_text(markdown, encoding="utf-8")
    sys.exit(0)
except Exception as e:
    import traceback
    print(traceback.format_exc(), file=sys.stderr)
    sys.exit(1)
"""
        # capture_output=True 但不设置 text=True，避免 Python 自动用错误的编码去 decode
        proc = subprocess.run(
            [sys.executable, "-c", helper_code, file_path, out_dir],
            capture_output=True, timeout=timeout_sec
        )
        
        if proc.returncode != 0:
            # 手动解码，并忽略无法识别的字符（如 GBK 中的中文报错字符）
            error_details = proc.stderr.decode('utf-8', errors='replace')
            if not error_details.strip():
                error_details = proc.stderr.decode('gbk', errors='replace')
            raise RuntimeError(f"子进程崩溃。详细错误:\n{error_details}")

        md_file = Path(out_dir) / "document.md"
        if md_file.exists():
            return md_file.read_text(encoding="utf-8")
        raise RuntimeError("Docling 子进程未产生 document.md")


class MarkerParser(BaseDocumentParser):
    name = "marker"
    display_name = "Marker"
    parameters = (
        ParserParameter("device", "推理设备", "select", "cpu", (("CPU", "cpu"), ("CUDA", "cuda"))),
    )

    def to_markdown(self, file_path: str) -> str:
        from marker.converters.pdf import PdfConverter  # type: ignore
        from marker.models import create_model_dict  # type: ignore
        from marker.output import save_output  # type: ignore
        
        out_dir = self._get_output_dir(file_path)
        device = self.get_option("device", cfg.get("pdf.marker_device", "cpu"))
        os.environ.setdefault("TORCH_DEVICE", str(device))
        
        converter = PdfConverter(artifact_dict=create_model_dict())
        rendered = converter(file_path)
        
        # 使用官方 API 自动保存 Markdown 和相关图片资源
        doc_stem = Path(file_path).stem
        markdown, _, _ = save_output(rendered, str(out_dir), doc_stem)
        
        return markdown


class UnstructuredParser(BaseDocumentParser):
    name = "unstructured"
    display_name = "Unstructured"
    parameters = (
        ParserParameter("strategy", "解析策略", "select", "auto", (("自动", "auto"), ("快速", "fast"), ("高精度", "hi_res"), ("仅 OCR", "ocr_only"))),
        ParserParameter("languages", "语言列表", "text", "eng,chi_sim"),
    )

    def to_markdown(self, file_path: str) -> str:
        from unstructured.partition.auto import partition  # type: ignore
        
        out_dir = self._get_output_dir(file_path)
        img_dir = out_dir / "images"
        img_dir.mkdir(exist_ok=True)

        strategy = self.get_option("strategy", "auto")
        languages = self.get_option("languages", "eng,chi_sim")
        
        kwargs: Dict[str, Any] = {
            "filename": file_path,
            "extract_image_block_types": ["Image", "Table"],  # 指示底层提取图片和表格图像
            "extract_image_block_output_dir": str(img_dir)    # 设定图像存放路径
        }
        
        if strategy: kwargs["strategy"] = strategy
        if languages: kwargs["languages"] = [part.strip() for part in str(languages).split(",") if part.strip()]
            
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
                
        md_text = "\n\n".join(lines).strip()
        Path(out_dir).joinpath("document.md").write_text(md_text, encoding="utf-8")
        return md_text


class MinerUParser(BaseDocumentParser):
    name = "mineru"
    display_name = "MinerU"
    parameters = (
        ParserParameter("command", "命令", "text", "mineru"),
        ParserParameter("extra_args", "额外参数", "text", ""),
    )

    def to_markdown(self, file_path: str) -> str:
        out_dir = self._get_output_dir(file_path)
        
        mineru_cmd = str(self.get_option("command", cfg.get("pdf.mineru_cmd", "mineru")))
        extra_args = str(self.get_option("extra_args", "") or "")
        
        base_cmd = shlex.split(mineru_cmd, posix=(os.name != "nt"))
        
        # 直接输出到持久化目录，不再使用 tempfile
        attempts = [
            base_cmd + [
                "--path", file_path,
                "--output", str(out_dir),
                # 图像提取强依赖于这两项，保持原样或明确开启
            ],
            base_cmd + ["--input", file_path, "--output", str(out_dir), "--format", "markdown"],
        ]
        
        if extra_args:
            extra = shlex.split(extra_args, posix=(os.name != "nt"))
            attempts = [cmd + extra for cmd in attempts]

        last_detail = ""
        for cmd in attempts:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            if proc.returncode == 0:
                last_detail = ""
                break
            last_detail = (proc.stderr or proc.stdout or "").strip()
            
        if last_detail:
            raise RuntimeError(f"MinerU 执行失败: {last_detail or f'exit={proc.returncode}'}")

        # 在输出目录中寻找产生的 .md 文件
        candidate = out_dir / f"{Path(file_path).stem}.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="replace")
            
        mds = sorted(out_dir.rglob("*.md"))
        if mds:
            return mds[0].read_text(encoding="utf-8", errors="replace")

        raise RuntimeError("MinerU 未产出 markdown 文件，请检查 MinerU 命令参数")


class OcrParser(BaseDocumentParser):
    name = "ocr"
    display_name = "OCR"
    parameters = (
        ParserParameter("lang", "OCR 语言", "text", "chi_sim+eng"),
        ParserParameter("scale", "渲染倍率", "int", 2, minimum=1, maximum=4, step=1),
    )

    def to_markdown(self, file_path: str) -> str:
        out_dir = self._get_output_dir(file_path)
        img_dir = out_dir / "pages"
        img_dir.mkdir(exist_ok=True)
        
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
                
                # 物理保存当前页面的渲染图
                image.save(img_dir / f"page_{page.number + 1}.png")
                
                text = pytesseract.image_to_string(image, lang=lang)
                if text.strip():
                    results.append(f"\n\n{text.strip()}")
            doc.close()
            
            md_text = "\n\n".join(results)
            Path(out_dir).joinpath("document.md").write_text(md_text, encoding="utf-8")
            return md_text
            
        except Exception as e:
            raise RuntimeError(f"OCR 解析失败: {e}") from e


PARSER_REGISTRY: Dict[str, BaseDocumentParser] = {
    "marker": MarkerParser(),
}

def available_parser_names() -> List[str]:
    return list(PARSER_REGISTRY.keys())