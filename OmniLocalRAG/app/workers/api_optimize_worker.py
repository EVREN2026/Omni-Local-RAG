"""
api_optimize_worker.py — API-agnostic chunk optimisation pipeline.

Task flow
---------
1. Load data/templates/api_optimize_task.md  (system prompt / task template)
2. Load input chunks JSON  (L2 data)
3. Load qa_dialogue_memory.jsonl  (conversation history with verdict labels)
4. Split into batches (configurable batch_size)
5. For each batch: build prompt → call API → parse JSON response
6. Merge all batch results into a single optimised JSON file
7. Optionally trigger json_db_sync to push changes into VectorStore (L3)

Supported providers (selected via config.json api.provider):
  openai   — OpenAI Chat Completions (GPT-4o, GPT-4-Turbo, …)
  claude   — Anthropic Messages API (claude-3-5-sonnet-…)
  gemini   — Google Generative Language API (gemini-1.5-pro, …)
  custom   — Any OpenAI-compatible endpoint (LM Studio, Ollama, vLLM, …)

All providers share the same request/response contract defined in
data/templates/api_optimize_task.md.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.utils import config as cfg
from app.utils.logger import logger


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------

def _call_openai(
    system: str,
    user: str,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> str:
    """Call OpenAI (or compatible) chat completions endpoint."""
    import urllib.request

    url = (base_url.rstrip("/") + "/chat/completions") if base_url else "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _call_claude(
    system: str,
    user: str,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> str:
    """Call Anthropic Messages API."""
    import urllib.request

    url = (base_url.rstrip("/") + "/v1/messages") if base_url else "https://api.anthropic.com/v1/messages"
    payload = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"]


def _call_gemini(
    system: str,
    user: str,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> str:
    """Call Google Generative Language API."""
    import urllib.request

    model_id = model or "gemini-1.5-pro"
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
        f"?key={api_key}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


_PROVIDER_DISPATCH = {
    "openai": _call_openai,
    "claude": _call_claude,
    "gemini": _call_gemini,
    "custom": _call_openai,   # same protocol as OpenAI
}


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_user_prompt(
    task_template: str,
    source_file: str,
    chunks: List[Dict[str, Any]],
    qa_samples: List[Dict[str, Any]],
) -> str:
    payload = {
        "task_version": "1.0",
        "source_file": source_file,
        "qa_samples": qa_samples,
        "chunks": [
            {
                "chunk_id": c.get("chunk_id", ""),
                "heading_path": c.get("heading_path", ""),
                "block_type": c.get("block_type", "text"),
                "content": str(c.get("content", ""))[:1500],  # guard against huge chunks
                "is_manual": bool(c.get("is_manual", False)),
            }
            for c in chunks
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _load_qa_samples(
    jsonl_path: Path,
    max_samples: int = 20,
) -> List[Dict[str, Any]]:
    """Load recent QA dialogue samples for context."""
    samples = []
    if not jsonl_path.exists():
        return samples
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines[-max_samples * 2:]):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            hr = entry.get("human_review", {})
            verdict = hr.get("verdict", "unreviewed")
            if verdict == "unreviewed":
                continue
            retrieved = [
                hit.get("document", "")[:300]
                for hit in entry.get("retrieved_context", [])[:3]
            ]
            samples.append(
                {
                    "question": entry.get("query", ""),
                    "retrieved_chunks": retrieved,
                    "answer": str(entry.get("answer", ""))[:400],
                    "verdict": verdict,
                    "notes": hr.get("notes", ""),
                }
            )
        except Exception:
            continue
        if len(samples) >= max_samples:
            break
    return samples


def _parse_api_response(raw: str) -> Optional[Dict[str, Any]]:
    """Try to extract JSON from raw API response."""
    raw = raw.strip()
    # Strip markdown code fences if present
    for fence in ("```json", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):]
    if raw.endswith("```"):
        raw = raw[:-3]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        logger.warning(f"api_optimize: response not valid JSON: {e}")
        return None


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class ApiOptimizeWorker(QThread):
    """Background thread that runs the API optimisation pipeline."""

    batch_started = pyqtSignal(int, int)      # (batch_index, total_batches)
    batch_finished = pyqtSignal(dict)         # batch result summary
    optimize_done = pyqtSignal(str)           # output JSON path
    error_occurred = pyqtSignal(str)
    log_message = pyqtSignal(str)             # human-readable progress line

    def __init__(
        self,
        chunks_json_path: str,
        output_json_path: Optional[str] = None,
        auto_sync_db: bool = False,
    ) -> None:
        super().__init__()
        self.chunks_json_path = Path(chunks_json_path)
        if output_json_path:
            self.output_path = Path(output_json_path)
        else:
            stem = self.chunks_json_path.stem.replace(".chunks", "")
            self.output_path = self.chunks_json_path.parent / f"{stem}.optimized.json"
        self.auto_sync_db = auto_sync_db
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    # ------------------------------------------------------------------
    # Thread entry
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            self._run_pipeline()
        except Exception as e:
            logger.error(f"ApiOptimizeWorker error: {e}", exc_info=True)
            self.error_occurred.emit(str(e))

    def _run_pipeline(self) -> None:
        # 1. Load task template
        template_path = cfg.abs_path(
            cfg.get("api.task_template", "data/templates/api_optimize_task.md")
        )
        if not template_path.exists():
            self.error_occurred.emit(f"任务模板文件不存在: {template_path}")
            return
        task_template = template_path.read_text(encoding="utf-8")
        self.log_message.emit(f"任务模板已加载: {template_path.name}")

        # 2. Load chunks JSON
        if not self.chunks_json_path.exists():
            self.error_occurred.emit(f"输入 JSON 不存在: {self.chunks_json_path}")
            return
        data = json.loads(self.chunks_json_path.read_text(encoding="utf-8"))
        chunks = data.get("chunks", [])
        source_file = data.get("source_file", self.chunks_json_path.stem)
        self.log_message.emit(f"已加载 {len(chunks)} 条 chunk，来源: {source_file}")

        # 3. Load QA samples
        qa_jsonl = cfg.abs_path(cfg.get("qa_memory.jsonl_path", "data/eval/qa_dialogue_memory.jsonl"))
        qa_samples = _load_qa_samples(qa_jsonl)
        self.log_message.emit(f"已加载 {len(qa_samples)} 条对话样本（已标注verdict）")

        # 4. API config
        api_cfg = cfg.get("api", {}) or {}
        provider = str(api_cfg.get("provider", "openai")).lower()
        api_key = str(api_cfg.get("api_key", ""))
        base_url = str(api_cfg.get("base_url", ""))
        model = str(api_cfg.get("model", "gpt-4o-mini"))
        temperature = float(api_cfg.get("temperature", 0.2))
        max_tokens = int(api_cfg.get("max_tokens", 4096))
        batch_size = int(api_cfg.get("batch_size", 10))
        timeout = int(api_cfg.get("timeout_seconds", 60))

        call_fn = _PROVIDER_DISPATCH.get(provider)
        if call_fn is None:
            self.error_occurred.emit(f"不支持的 API 提供商: {provider}（支持: openai/claude/gemini/custom）")
            return

        if not api_key and provider != "custom":
            self.error_occurred.emit(f"未配置 API Key（config.json → api.api_key）")
            return

        # 5. Batch processing
        batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
        total_batches = len(batches)
        all_optimized: List[Dict[str, Any]] = []
        global_suggestions: List[str] = []

        self.log_message.emit(f"开始批次处理：共 {total_batches} 批，每批 {batch_size} 条")

        for batch_idx, batch in enumerate(batches):
            if self._cancelled:
                self.log_message.emit("任务已取消")
                return

            self.batch_started.emit(batch_idx + 1, total_batches)
            self.log_message.emit(f"[{batch_idx + 1}/{total_batches}] 发送 API 请求...")

            user_prompt = _build_user_prompt(task_template, source_file, batch, qa_samples)

            try:
                t0 = time.perf_counter()
                raw = call_fn(
                    system=task_template,
                    user=user_prompt,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                elapsed = time.perf_counter() - t0
                self.log_message.emit(f"[{batch_idx + 1}/{total_batches}] 响应收到 ({elapsed:.1f}s)")
            except Exception as e:
                self.log_message.emit(f"[{batch_idx + 1}/{total_batches}] API 调用失败: {e}")
                self.error_occurred.emit(f"批次 {batch_idx + 1} API 错误: {e}")
                continue

            result = _parse_api_response(raw)
            if result is None:
                self.log_message.emit(f"[{batch_idx + 1}/{total_batches}] 响应解析失败，跳过本批")
                continue

            optimized = result.get("optimized_chunks", [])
            all_optimized.extend(optimized)
            global_suggestions.extend(result.get("global_suggestions", []))

            summary = {
                "batch": batch_idx + 1,
                "total": total_batches,
                "chunks_in_batch": len(batch),
                "suggestions": len(optimized),
            }
            self.batch_finished.emit(summary)

        # 6. Apply optimizations to original chunks
        updated_chunks = self._apply_optimizations(chunks, all_optimized)

        # 7. Write output JSON
        output_payload = {
            "source_file": source_file,
            "source_type": data.get("source_type", "markdown"),
            "exported_at": data.get("exported_at", ""),
            "optimized_at": datetime.now(timezone.utc).isoformat(),
            "optimizer_model": f"{provider}/{model}",
            "chunk_count": len(updated_chunks),
            "global_suggestions": list(dict.fromkeys(global_suggestions))[:10],
            "chunks": updated_chunks,
        }
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.log_message.emit(f"优化结果已写入: {self.output_path}")

        # 8. Optional DB sync
        if self.auto_sync_db and not self._cancelled:
            self.log_message.emit("正在同步到数据库...")
            try:
                from app.models.json_db_sync import sync_json_to_db
                counts = sync_json_to_db(self.output_path)
                self.log_message.emit(
                    f"数据库同步完成：插入 {counts['inserted']} 更新 {counts['updated']} "
                    f"跳过 {counts['skipped']} 错误 {counts['errors']}"
                )
            except Exception as e:
                self.log_message.emit(f"数据库同步失败: {e}")

        self.optimize_done.emit(str(self.output_path))

    @staticmethod
    def _apply_optimizations(
        original_chunks: List[Dict[str, Any]],
        optimized_list: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merge API optimisation results back into the original chunk list."""
        opt_map: Dict[str, Dict[str, Any]] = {
            o["chunk_id"]: o for o in optimized_list if o.get("chunk_id")
        }

        result = []
        skip_ids: set = set()

        for chunk in original_chunks:
            cid = chunk.get("chunk_id", "")
            if cid in skip_ids:
                continue

            opt = opt_map.get(cid)
            if opt is None:
                result.append(chunk)
                continue

            action = opt.get("action", "keep")

            if action == "keep":
                result.append(chunk)

            elif action == "update":
                new_content = opt.get("optimized_content", "").strip()
                if new_content:
                    updated = dict(chunk)
                    updated["content"] = new_content
                    updated["is_manual"] = True
                    updated["optimization_reason"] = opt.get("reason", "")
                    result.append(updated)
                else:
                    result.append(chunk)

            elif action == "delete":
                logger.info(f"api_optimize: deleting chunk {cid}: {opt.get('reason', '')}")

            elif action == "merge_with_next":
                # Find the merge target
                merge_id = opt.get("merge_target_id", "")
                target = opt_map.get(merge_id, {})
                merged = dict(chunk)
                if merge_id:
                    skip_ids.add(merge_id)
                    # Find target chunk content
                    for c in original_chunks:
                        if c.get("chunk_id") == merge_id:
                            merged_content = (
                                str(chunk.get("content", "")).strip()
                                + "\n\n"
                                + str(c.get("content", "")).strip()
                            )
                            merged["content"] = merged_content
                            merged["is_manual"] = True
                            merged["optimization_reason"] = opt.get("reason", "")
                            break
                result.append(merged)

            else:
                result.append(chunk)

        return result


# ---------------------------------------------------------------------------
# Standalone test helper (no Qt required)
# ---------------------------------------------------------------------------

def ping_api(provider: str, api_key: str, base_url: str, model: str, timeout: int = 10) -> bool:
    """Send a minimal test message and return True if the API responds."""
    call_fn = _PROVIDER_DISPATCH.get(provider.lower())
    if call_fn is None:
        return False
    try:
        resp = call_fn(
            system="You are a test assistant.",
            user='Reply with exactly: {"ok": true}',
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=0.0,
            max_tokens=20,
            timeout=timeout,
        )
        return '"ok"' in resp or "ok" in resp
    except Exception as e:
        logger.warning(f"ping_api({provider}): {e}")
        return False
