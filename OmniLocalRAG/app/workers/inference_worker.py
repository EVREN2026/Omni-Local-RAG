import json
import re
import time
from collections import deque
from threading import RLock
from typing import Dict, List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
from app.models.embed_manager import EmbedManager
from app.models.llm_manager import LLMManager
from app.models.qa_memory import QAMemoryRecorder
from app.models.sqlite_store import SQLiteStore
from app.utils import config as cfg
from app.utils.logger import RAG_TRACE, logger

_RAG_PROMPT_TEMPLATE = """\
以下是从知识库中检索到的相关内容（共 {chunk_count} 条，编号 [1]-[{chunk_count}]）：

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

问题：{query}

回答："""

_DIVIDER = "=" * 72
_SECTION = "-" * 72
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")

_TECH_KEYWORDS = {
    "api", "sdk", "python", "java", "cpp", "安装", "部署", "代码", "参数", "模型", "llm", "cuda",
    "故障", "调试", "配置", "接口", "版本", "依赖", "command", "linux", "windows",
}
_BUSINESS_KEYWORDS = {
    "sop", "流程", "制度", "审批", "业务", "运营", "客服", "销售", "采购", "培训", "规范", "手册",
}
_FOLLOWUP_MARKERS = {"它", "这个", "那个", "其", "这", "那", "上面", "前面", "刚才", "上一条"}


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


def _contains_any(text: str, keywords: set) -> bool:
    content = (text or "").lower()
    return any(k in content for k in keywords)


def _heuristic_category(text: str) -> str:
    if _contains_any(text, _TECH_KEYWORDS):
        return "tech_manual"
    if _contains_any(text, _BUSINESS_KEYWORDS):
        return "business_sop"
    return "general"


def _heuristic_condense(query: str, memory: List[Dict[str, str]]) -> str:
    q = (query or "").strip()
    if not q:
        return q
    if not memory:
        return q
    if any(marker in q for marker in _FOLLOWUP_MARKERS):
        last = memory[-1]
        anchor = last.get("standalone_query") or last.get("query") or ""
        anchor = anchor.strip()
        if anchor:
            return f"基于“{anchor}”：{q}"
    return q


def _parse_router_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    candidate = raw.strip()
    match = _JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(0)
    try:
        parsed = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    category = str(parsed.get("category") or "").strip()
    standalone = str(parsed.get("standalone_query") or "").strip()
    if not category or not standalone:
        return None
    if category not in {"tech_manual", "business_sop", "general"}:
        category = "general"
    return {"category": category, "standalone_query": standalone}


def _route_query(query: str, memory: List[Dict[str, str]], llm: LLMManager) -> dict:
    condensed_default = _heuristic_condense(query, memory)
    fallback = {
        "category": _heuristic_category(condensed_default),
        "standalone_query": condensed_default,
        "router_source": "heuristic",
    }

    if not cfg.get("router.enabled", True):
        return fallback

    if not cfg.get("llm.enabled", True):
        return fallback

    try:
        if not llm.is_loaded and not llm.load():
            return fallback
    except Exception:
        return fallback

    history_lines = []
    for idx, turn in enumerate(memory[-5:], 1):
        history_lines.append(
            f"{idx}. Q: {turn.get('query', '')}\n"
            f"   A: {turn.get('answer', '')[:160]}\n"
            f"   C: {turn.get('category', 'general')}"
        )
    history_text = "\n".join(history_lines) if history_lines else "（无历史）"

    prompt = (
        "你是 OmniLocalRAG 的意图路由器。"
        "请基于对话历史和当前问题输出严格 JSON，不要输出解释。\n"
        "允许的 category 只有：tech_manual, business_sop, general。\n"
        "输出格式：{\"category\":\"...\",\"standalone_query\":\"...\"}\n"
        "规则：\n"
        "- standalone_query 必须是独立可检索问题。\n"
        "- 如果问题偏安装、接口、技术排障，category=tech_manual。\n"
        "- 如果问题偏流程制度、业务SOP，category=business_sop。\n"
        "- 不确定时 category=general。\n\n"
        f"对话历史：\n{history_text}\n\n"
        f"当前问题：{query}\n"
    )
    try:
        raw = "".join(llm.generate(prompt, stream=False))
        parsed = _parse_router_json(raw)
        if parsed:
            parsed["router_source"] = "llm"
            return parsed
    except Exception as exc:
        logger.warning(f"Router LLM failed, fallback to heuristic: {exc}")

    return fallback


def _infer_result_category(result: dict) -> str:
    source_file = _result_source_file(result)
    heading = _result_heading_path(result)
    content = _result_content(result)[:300]
    blob = f"{source_file} {heading} {content}"
    return _heuristic_category(blob)


def _filter_results_by_category(results: List[dict], category: str, top_k: int) -> List[dict]:
    if category not in {"tech_manual", "business_sop"}:
        return results[:top_k]
    filtered = [row for row in results if _infer_result_category(row) == category]
    if len(filtered) >= max(1, min(top_k, 2)):
        return filtered[:top_k]
    return results[:top_k]


def _build_prompt(query: str, results: list, category: str) -> str:
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
    return _RAG_PROMPT_TEMPLATE.format(
        chunk_count=chunk_count,
        context="\n\n".join(context_parts),
        query=query,
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
    mode = timings.get("retrieval_mode", "dense")
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
        "",
        f"§ 2. 检索命中内容  [mode={mode} hits={hits} 检索耗时 {retrieval_total:.1f}ms]",
        _SECTION,
    ]

    if not results:
        lines.append("  （无命中结果）")
    else:
        for i, r in enumerate(results, 1):
            final_score = float(r.get("retrieval_score", 0.0))
            dense_score = float(r.get("dense_score", 0.0))
            sparse_score = float(r.get("sparse_score", 0.0))
            block_type = _result_block_type(r)
            heading_path = _result_heading_path(r)
            content = _result_content(r)
            lines.append(
                f"[{i}] score={final_score:.3f} dense={dense_score:.3f} "
                f"sparse={sparse_score:.3f} type={block_type} category={r.get('route_category', route_category)}"
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
            embed = EmbedManager()
            chroma = ChromaStore()
            llm = LLMManager()
            memory = _ConversationMemory()

            top_k = int(cfg.get("retrieval.top_k", 5))
            candidate_k = max(top_k, int(cfg.get("retrieval.candidate_k", top_k * 3)))
            embed_was_loaded = embed.is_loaded
            llm_was_loaded = llm.is_loaded

            t_router = time.perf_counter()
            route = _route_query(self.query, memory.recent(), llm)
            route_ms = (time.perf_counter() - t_router) * 1000
            route_category = str(route.get("category") or "general")
            standalone_query = str(route.get("standalone_query") or self.query).strip() or self.query
            query_source = "standalone_query" if standalone_query != self.query else "raw_query"
            router_source = str(route.get("router_source") or "heuristic")

            t0 = time.perf_counter()
            query_sparse = None
            if hasattr(embed, "encode_payloads"):
                [query_payload] = embed.encode_payloads([standalone_query], return_sparse=True)
                q_vec = list(query_payload.get("dense") or [])
                query_sparse = query_payload.get("sparse") or None
            else:
                [q_vec] = embed.encode([standalone_query])
            embed_ms = (time.perf_counter() - t0) * 1000

            t1 = time.perf_counter()
            raw_results = chroma.query(
                q_vec,
                n_results=candidate_k,
                query_text=standalone_query,
                query_sparse=query_sparse,
            )
            results = _filter_results_by_category(raw_results, route_category, top_k=top_k)
            for row in results:
                row["route_category"] = _infer_result_category(row)
                row["query_source"] = query_source
                row["standalone_query"] = standalone_query
            vector_ms = (time.perf_counter() - t1) * 1000
            retrieval_ms = embed_ms + vector_ms
            retrieval_mode = str(results[0].get("retrieval_mode", "dense")) if results else "dense"

            base_timings = {
                "stage": "retrieval",
                "embed_ms": round(embed_ms, 1),
                "vector_ms": round(vector_ms, 1),
                "router_ms": round(route_ms, 1),
                "retrieval_total_ms": round(retrieval_ms, 1),
                "retrieval_mode": retrieval_mode,
                "top_k": top_k,
                "hits": len(results),
                "embed_reused": bool(embed_was_loaded),
                "llm_server_ready": bool(llm_was_loaded),
                "category": route_category,
                "router_source": router_source,
                "query_source": query_source,
                "standalone_query": standalone_query,
            }
            self.timings_ready.emit(base_timings)
            self.context_retrieved.emit(
                _build_display_results(
                    results,
                    route_category=route_category,
                    query_source=query_source,
                )
            )

            if not results:
                answer = "（未检索到相关内容，请先导入知识文档）"
                self.token_generated.emit(answer)
                _record_qa_memory(self.query, results, answer, mode="no_results")
                memory.append(
                    query=self.query,
                    answer=answer,
                    category=route_category,
                    standalone_query=standalone_query,
                )
                _log_rag_trace(
                    query=self.query,
                    standalone_query=standalone_query,
                    route_category=route_category,
                    query_source=query_source,
                    results=results,
                    prompt="(无检索结果，跳过提示词构建)",
                    answer=answer,
                    timings={**base_timings, "llm_load_ms": 0.0, "generation_ms": 0.0, "total_ms": (time.perf_counter() - overall_t0) * 1000},
                )
                self.generation_finished.emit(True)
                return

            prompt = _build_prompt(
                query=standalone_query,
                results=results,
                category=route_category,
            )

            if not cfg.get("llm.enabled", True):
                answer = _build_retrieval_answer(
                    results,
                    "本地 LLM 当前已禁用，先展示路由检索结果。",
                )
                self.token_generated.emit(answer)
                _record_qa_memory(self.query, results, answer, mode="retrieval_only")
                memory.append(
                    query=self.query,
                    answer=answer,
                    category=route_category,
                    standalone_query=standalone_query,
                )
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

            t_load = time.perf_counter()
            llm_loaded_now = llm.load()
            llm_load_ms = (time.perf_counter() - t_load) * 1000

            if not llm_loaded_now:
                detail = llm.last_error or "请检查 models/ 目录与 llama-server 配置"
                answer = _build_retrieval_answer(
                    results,
                    f"本地 LLM 加载失败，已降级为检索结果直出。\n原因：{detail}",
                )
                self.token_generated.emit(answer)
                _record_qa_memory(self.query, results, answer, mode="llm_load_failed")
                memory.append(
                    query=self.query,
                    answer=answer,
                    category=route_category,
                    standalone_query=standalone_query,
                )
                _log_rag_trace(
                    query=self.query,
                    standalone_query=standalone_query,
                    route_category=route_category,
                    query_source=query_source,
                    results=results,
                    prompt=prompt,
                    answer=answer,
                    timings={**base_timings, "llm_load_ms": round(llm_load_ms, 1), "generation_ms": 0.0, "total_ms": (time.perf_counter() - overall_t0) * 1000},
                )
                self.generation_finished.emit(True)
                return

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
                memory.append(
                    query=self.query,
                    answer=answer_text,
                    category=route_category,
                    standalone_query=standalone_query,
                )
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
