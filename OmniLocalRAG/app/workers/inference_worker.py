import json
import re
import time
from collections import deque
from threading import RLock
from typing import Dict, List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
from app.models.gemma_router import GemmaRouter
from app.models.llm_manager import LLMManager
from app.models.qa_memory import QAMemoryRecorder
from app.models.sqlite_store import SQLiteStore
from app.utils import config as cfg
from app.utils.category_keywords import heuristic_category as _heuristic_category
from app.utils.logger import RAG_TRACE, logger

_RAG_PROMPT_TEMPLATE = """\
{history_section}以下是从知识库中检索到的相关内容（共 {chunk_count} 条，编号 [1]-[{chunk_count}]）：

{context}

---
请严格遵守以下规则回答问题：
1. 只能使用上方检索内容作答，不得引入检索内容之外的知识补充关键事实。
2. 回答时用 [序号] 标注来源，例如"根据 [1] ..."。
3. 如果上方内容不足以回答，请直接说"当前知识库中没有找到足够的相关信息"，不要猜测。
4. 对操作步骤类问题，请输出编号分步答案（第1步、第2步...）。
5. 对参数类问题，请优先结合参数表、术语定义和图片上下文来解释。
6. 如果检索内容中包含图片引用（如 ![](_page_X_Picture_Y.jpeg)），请在回答中原样保留该图片引用，不要省略。
7. 回答文案必须忠实于检索内容原文，不得随意改写或概括原始文案的具体数值、路径、参数名称。
8. 用中文回答。
9. 如果问题包含代词（它、这个、那个等），请结合对话历史理解指代。

问题：{query}

回答："""

_DIVIDER = "=" * 72
_SECTION = "-" * 72


class _ConversationMemory:
    """运行时对话滑窗，服务 Query 改写和路由。"""

    _instance: Optional["_ConversationMemory"] = None
    _lock = RLock()

    def __new__(cls) -> "_ConversationMemory":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                size = max(1, int(cfg.get("router.memory_window", 5)))
                cls._instance._qa = deque(maxlen=size)
            return cls._instance

    def recent(self) -> List[Dict[str, str]]:
        return list(self._qa)

    def append(self, query: str, answer: str, category: str, standalone_query: str) -> None:
        self._qa.append(
            {
                "query": query.strip(),
                "answer": answer.strip(),
                "category": (category or "general").strip(),
                "standalone_query": standalone_query.strip(),
            }
        )


def _payload(result: dict) -> dict:
    payload = result.get("pdf_payload") or {}
    return payload if isinstance(payload, dict) else {}


def _result_content(result: dict) -> str:
    payload = _payload(result)
    return str(
        payload.get("display_content")
        or result.get("document")
        or result.get("content")
        or ""
    ).strip()


def _result_block_type(result: dict) -> str:
    payload = _payload(result)
    return str(payload.get("block_type") or result.get("block_type") or "text").strip() or "text"


def _result_heading_path(result: dict) -> str:
    # First check top-level heading_path (new schema), then pdf_payload
    hp = result.get("heading_path")
    if hp and str(hp).strip():
        return str(hp).strip()
    payload = _payload(result)
    return str(payload.get("heading_path") or "").strip()


def _result_semantic_description(result: dict) -> str:
    payload = _payload(result)
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("semantic_description") or "").strip()


def _result_source_file(result: dict) -> str:
    payload = _payload(result)
    source = payload.get("file") or result.get("source_file") or ""
    return str(source).strip().lower()


def _build_prompt(query: str, results: list, category: str, conversation_history: List[Dict[str, str]] = None) -> str:
    context_parts = []
    for i, result in enumerate(results, 1):
        source = result.get("anchor_id") or result.get("id", "")
        content = _result_content(result)
        heading_path = _result_heading_path(result)
        block_type = _result_block_type(result)
        semantic_description = _result_semantic_description(result)
        source_file = _result_source_file(result)

        meta_tags = [f"[Category: {result.get('route_category', category)}]"]
        if source_file:
            meta_tags.append(f"[File: {source_file}]")
        if heading_path:
            meta_tags.append(f"[Path: {heading_path}]")
        if block_type:
            meta_tags.append(f"[Type: {block_type}]")

        parts = [" ".join(meta_tags)]
        if semantic_description:
            parts.append(semantic_description)
        if content:
            parts.append(content)
        context_parts.append(f"[{i}] " + "\n".join(parts) + f"\n    （来源 chunk: {source}）")

    chunk_count = len(results)

    # Build conversation history section
    history_section = ""
    if conversation_history:
        history_lines = []
        for idx, turn in enumerate(conversation_history[-5:], 1):
            q = turn.get("query", "")
            a = turn.get("answer", "")[:200]
            history_lines.append(f"{idx}. Q: {q}\n   A: {a}")
        turn_count = len(history_lines)
        history_section = (
            f"以下是最近 {turn_count} 轮对话历史：\n"
            + "\n".join(history_lines)
            + "\n\n---\n\n"
        )

    return _RAG_PROMPT_TEMPLATE.format(
        chunk_count=chunk_count,
        context="\n\n".join(context_parts),
        query=query,
        history_section=history_section,
    )


def _build_retrieval_answer(results: list, reason: str = "") -> str:
    lines = []
    if reason:
        lines.extend([reason, ""])
    lines.append("以下是当前检索到的相关内容：")
    lines.append("")
    for i, result in enumerate(results, 1):
        source = result.get("anchor_id") or result.get("id", "")
        content = _result_content(result)
        heading_path = _result_heading_path(result)
        block_type = _result_block_type(result)
        semantic_description = _result_semantic_description(result)
        route_category = str(result.get("route_category") or "general")
        if len(content) > 900:
            content = content[:900].rstrip() + "..."

        lines.append(f"## 结果 {i}")
        lines.append(f"- 来源: {source}")
        lines.append(f"- 路由分类: {route_category}")
        if heading_path:
            lines.append(f"- Path: {heading_path}")
        if block_type:
            lines.append(f"- Type: {block_type}")
        if semantic_description:
            lines.append(f"- 描述: {semantic_description}")
        lines.extend(["", content, ""])
    return "\n".join(lines).strip()


def _build_display_results(results: list, route_category: str, query_source: str) -> list:
    display_results = []
    for row in results:
        item = dict(row)
        item["route_category"] = route_category
        item["query_source"] = query_source
        display_results.append(item)
    try:
        mappings = SQLiteStore().list_cross_modal()
    except Exception as e:
        logger.error(f"Cross-modal binding lookup failed: {e}", exc_info=True)
        return display_results

    anchor_ids = {
        str(row.get("anchor_id") or row.get("id") or "").strip()
        for row in display_results
        if str(row.get("anchor_id") or row.get("id") or "").strip()
    }
    if not anchor_ids:
        return display_results

    seen_clip_ids = set()
    added = 0
    for mapping in mappings:
        anchor_id = str(mapping.get("pdf_anchor_id") or "").strip()
        clip_id = str(mapping.get("video_clip_id") or "").strip()
        if anchor_id not in anchor_ids or not clip_id or clip_id in seen_clip_ids:
            continue
        seen_clip_ids.add(clip_id)
        summary = str(mapping.get("semantic_summary") or "").strip()
        start_sec = float(mapping.get("start_sec") or 0)
        end_sec = float(mapping.get("end_sec") or 0)
        video_file = str(mapping.get("video_file") or "").strip()
        note = str(mapping.get("note") or "").strip()
        body = summary or "已绑定视频片段"
        if note:
            body = f"{body}\n备注: {note}"
        display_results.append(
            {
                "id": clip_id,
                "anchor_id": anchor_id,
                "source_type": "video",
                "document": body,
                "content": body,
                "route_category": route_category,
                "query_source": query_source,
                "video_payload": {
                    "file": video_file,
                    "start": start_sec,
                    "end": end_sec,
                },
            }
        )
        added += 1

    if added:
        logger.info(f"Cross-modal display augmentation: added {added} bound video card(s)")
    return display_results


def _record_qa_memory(query: str, results: list, answer: str, mode: str) -> None:
    try:
        QAMemoryRecorder().record(
            query=query,
            retrieved_context=results,
            answer=answer,
            mode=mode,
        )
    except Exception as e:
        logger.error(f"QA memory record failed: {e}", exc_info=True)


def _log_rag_trace(
    query: str,
    standalone_query: str,
    route_category: str,
    query_source: str,
    results: list,
    prompt: str,
    answer: str,
    timings: dict,
) -> None:
    import datetime

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    retrieval_total = timings.get("retrieval_total_ms", 0.0)
    route_ms = timings.get("route_ms", 0.0)
    locate_ms = timings.get("locate_ms", 0.0)
    validate_ms = timings.get("validate_ms", 0.0)
    attempts = timings.get("attempts", 1)
    validated = timings.get("validated", False)
    hits = timings.get("hits", len(results))
    llm_load_ms = timings.get("llm_load_ms", 0.0)
    generation_ms = timings.get("generation_ms", 0.0)
    total_ms = timings.get("total_ms", 0.0)

    lines = [
        "",
        _DIVIDER,
        f"【RAG TRACE】{ts}",
        _SECTION,
        "§ 1. 用户检索问题",
        _SECTION,
        f"raw_query: {query}",
        f"standalone_query: {standalone_query}",
        f"route_category: {route_category}",
        f"query_source: {query_source}",
        f"attempts: {attempts}  validated: {validated}",
        "",
        f"§ 2. 检索命中内容  [route={route_ms:.1f}ms locate={locate_ms:.1f}ms validate={validate_ms:.1f}ms hits={hits} 检索耗时 {retrieval_total:.1f}ms]",
        _SECTION,
    ]

    if not results:
        lines.append("  （无命中结果）")
    else:
        for i, r in enumerate(results, 1):
            heading_path = _result_heading_path(r)
            block_type = _result_block_type(r)
            content = _result_content(r)
            lines.append(
                f"[{i}] type={block_type} category={r.get('route_category', route_category)}"
            )
            if heading_path:
                lines.append(f"  路径: {heading_path}")
            lines.append(f"  内容: {content[:240]}")
            lines.append("")

    lines += [
        f"§ 3. 输入 Gemma 的提示词  [提示词长度 {len(prompt)} 字符]",
        _SECTION,
        prompt,
        "",
        (
            f"§ 4. Gemma 输出回答 "
            f"[模型加载 {llm_load_ms:.1f}ms 生成 {generation_ms:.1f}ms 全程 {total_ms:.1f}ms]"
        ),
        _SECTION,
        answer.strip() if answer.strip() else "(空回答)",
        "",
        _DIVIDER,
        "",
    ]
    block = "\n".join(lines)
    logger.log(RAG_TRACE, block)
    logger.debug(block)


class InferenceWorker(QThread):
    token_generated = pyqtSignal(str)
    context_retrieved = pyqtSignal(list)
    timings_ready = pyqtSignal(dict)
    generation_finished = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    def __init__(self, query: str) -> None:
        super().__init__()
        self.query = query
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            overall_t0 = time.perf_counter()
            chroma = ChromaStore()
            if not chroma.connect():
                self.error_occurred.emit("数据库连接失败，请重启应用")
                self.generation_finished.emit(False)
                return
            llm = LLMManager()
            memory = _ConversationMemory()
            router = GemmaRouter()

            llm_was_loaded = llm.is_loaded

            # 1. Ensure llama-server is running
            t_load = time.perf_counter()
            llm_loaded_now = llm.load()
            llm_load_ms = (time.perf_counter() - t_load) * 1000

            if not llm_loaded_now:
                # Degraded mode: heuristic routing + SQL text search
                route_category = _heuristic_category(self.query)
                standalone_query = self.query
                query_source = "raw_query"
                router_source = "heuristic_degraded"

                t1 = time.perf_counter()
                results = chroma.search_text(self.query, limit=int(cfg.get("retrieval.top_k", 5)))
                retrieval_ms = (time.perf_counter() - t1) * 1000

                for row in results:
                    row["route_category"] = route_category
                    row["query_source"] = query_source

                base_timings = {
                    "stage": "retrieval",
                    "route_ms": 0.0,
                    "locate_ms": round(retrieval_ms, 1),
                    "validate_ms": 0.0,
                    "retrieval_total_ms": round(retrieval_ms, 1),
                    "top_k": int(cfg.get("retrieval.top_k", 5)),
                    "hits": len(results),
                    "llm_server_ready": False,
                    "category": route_category,
                    "router_source": router_source,
                    "query_source": query_source,
                    "standalone_query": standalone_query,
                    "attempts": 1,
                    "validated": False,
                    "reasoning_steps": [
                        f"[路由] 启发式降级: 分类={route_category}",
                        f"[定位] 文本搜索命中{len(results)}条",
                        "[验证] LLM不可用，跳过验证",
                    ],
                }
                self.timings_ready.emit(base_timings)
                self.context_retrieved.emit(
                    _build_display_results(results, route_category, query_source)
                )

                if not results:
                    answer = "（未检索到相关内容，请先导入知识文档）"
                    self.token_generated.emit(answer)
                    _record_qa_memory(self.query, results, answer, mode="no_results")
                    memory.append(self.query, answer, route_category, standalone_query)
                    _log_rag_trace(
                        query=self.query,
                        standalone_query=standalone_query,
                        route_category=route_category,
                        query_source=query_source,
                        results=results,
                        prompt="(降级模式，无LLM)",
                        answer=answer,
                        timings={**base_timings, "llm_load_ms": round(llm_load_ms, 1), "generation_ms": 0.0, "total_ms": (time.perf_counter() - overall_t0) * 1000},
                    )
                    self.generation_finished.emit(True)
                    return

                answer = _build_retrieval_answer(
                    results,
                    f"本地 LLM 加载失败，已降级为文本搜索。\n原因：{llm.last_error or '请检查 llama-server 配置'}",
                )
                self.token_generated.emit(answer)
                _record_qa_memory(self.query, results, answer, mode="llm_load_failed")
                memory.append(self.query, answer, route_category, standalone_query)
                _log_rag_trace(
                    query=self.query,
                    standalone_query=standalone_query,
                    route_category=route_category,
                    query_source=query_source,
                    results=results,
                    prompt="(降级模式)",
                    answer=answer,
                    timings={**base_timings, "llm_load_ms": round(llm_load_ms, 1), "generation_ms": 0.0, "total_ms": (time.perf_counter() - overall_t0) * 1000},
                )
                self.generation_finished.emit(True)
                return

            # 2. Multi-round self-verification search
            t_search = time.perf_counter()
            search_result = router.search(
                query=self.query,
                memory=memory.recent(),
                chroma=chroma,
            )
            search_ms = (time.perf_counter() - t_search) * 1000

            route_category = search_result["category"]
            standalone_query = search_result["standalone_query"]
            query_source = "standalone_query" if standalone_query != self.query else "raw_query"
            router_source = search_result["router_source"]
            results = search_result["chunks"]
            attempts = search_result["attempts"]
            validated = search_result["validated"]
            router_timings = search_result.get("timings", {})

            for row in results:
                row["route_category"] = route_category
                row["query_source"] = query_source
                row["standalone_query"] = standalone_query

            route_ms = router_timings.get("route_ms", 0.0)
            locate_ms = router_timings.get("locate_ms", 0.0)
            validate_ms = router_timings.get("validate_ms", 0.0)
            reasoning_steps = search_result.get("reasoning_steps", [])

            base_timings = {
                "stage": "retrieval",
                "route_ms": round(route_ms, 1),
                "locate_ms": round(locate_ms, 1),
                "validate_ms": round(validate_ms, 1),
                "retrieval_total_ms": round(search_ms, 1),
                "top_k": int(cfg.get("retrieval.top_k", 5)),
                "hits": len(results),
                "llm_server_ready": bool(llm_was_loaded),
                "category": route_category,
                "router_source": router_source,
                "query_source": query_source,
                "standalone_query": standalone_query,
                "attempts": attempts,
                "validated": validated,
                "reasoning_steps": reasoning_steps,
            }
            self.timings_ready.emit(base_timings)
            self.context_retrieved.emit(
                _build_display_results(results, route_category, query_source)
            )

            if not results:
                answer = "（未检索到相关内容，请先导入知识文档）"
                self.token_generated.emit(answer)
                _record_qa_memory(self.query, results, answer, mode="no_results")
                memory.append(self.query, answer, route_category, standalone_query)
                _log_rag_trace(
                    query=self.query,
                    standalone_query=standalone_query,
                    route_category=route_category,
                    query_source=query_source,
                    results=results,
                    prompt="(无检索结果，跳过提示词构建)",
                    answer=answer,
                    timings={**base_timings, "llm_load_ms": round(llm_load_ms, 1), "generation_ms": 0.0, "total_ms": (time.perf_counter() - overall_t0) * 1000},
                )
                self.generation_finished.emit(True)
                return

            # 3. Build prompt with conversation history
            prompt = _build_prompt(
                query=standalone_query,
                results=results,
                category=route_category,
                conversation_history=memory.recent(),
            )

            if not cfg.get("llm.enabled", True):
                answer = _build_retrieval_answer(
                    results,
                    "本地 LLM 当前已禁用，先展示路由检索结果。",
                )
                self.token_generated.emit(answer)
                _record_qa_memory(self.query, results, answer, mode="retrieval_only")
                memory.append(self.query, answer, route_category, standalone_query)
                _log_rag_trace(
                    query=self.query,
                    standalone_query=standalone_query,
                    route_category=route_category,
                    query_source=query_source,
                    results=results,
                    prompt=prompt,
                    answer=answer,
                    timings={**base_timings, "llm_load_ms": 0.0, "generation_ms": 0.0, "total_ms": (time.perf_counter() - overall_t0) * 1000},
                )
                self.generation_finished.emit(True)
                return

            # 4. Stream generation
            t2 = time.perf_counter()
            answer_parts: List[str] = []
            llm_generation_error = ""
            try:
                for token in llm.generate(prompt, stream=True):
                    if self._cancelled:
                        break
                    self.token_generated.emit(token)
                    answer_parts.append(token)
            except Exception as e:
                llm_generation_error = str(e)
                logger.warning(f"LLM generation failed, fallback to retrieval output: {e}")

            elapsed = time.perf_counter() - t2
            answer_text = "".join(answer_parts)
            if not self._cancelled and not answer_text.strip():
                fallback_reason = "本地 LLM 没有返回可见答案，已回退为检索结果直出。"
                if llm_generation_error:
                    fallback_reason = (
                        "本地 LLM 生成失败，已回退为检索结果直出。\n"
                        f"原因：{llm_generation_error}"
                    )
                answer_text = _build_retrieval_answer(results, fallback_reason)
                self.token_generated.emit(answer_text)

            generation_ms = elapsed * 1000
            total_ms = (time.perf_counter() - overall_t0) * 1000
            complete_timings = {
                **base_timings,
                "stage": "complete",
                "llm_load_ms": round(llm_load_ms, 1),
                "generation_ms": round(generation_ms, 1),
                "total_ms": round(total_ms, 1),
            }
            self.timings_ready.emit(complete_timings)

            if not self._cancelled:
                _log_rag_trace(
                    query=self.query,
                    standalone_query=standalone_query,
                    route_category=route_category,
                    query_source=query_source,
                    results=results,
                    prompt=prompt,
                    answer=answer_text,
                    timings=complete_timings,
                )
                _record_qa_memory(self.query, results, answer_text, mode="llm")
                memory.append(self.query, answer_text, route_category, standalone_query)
            self.generation_finished.emit(not self._cancelled)

        except MemoryError:
            logger.error("OOM during inference", exc_info=True)
            LLMManager().unload(reason="oom")
            self.error_occurred.emit("内存不足，模型已自动释放，请重试。")
            self.generation_finished.emit(False)
        except Exception as e:
            logger.error(f"InferenceWorker error: {e}", exc_info=True)
            self.error_occurred.emit(str(e))
            self.generation_finished.emit(False)
