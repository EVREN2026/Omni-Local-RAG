"""
GemmaRouter — Gemma-4-2b 驱动的多轮自证路由引擎。

搜索流程：
  1. route()   — 意图路由：判定分类 + 改写查询
  2. locate()  — 文档定位：从 heading_path 列表中选择目标文档段
  3. validate()— 验证定位：检查定位结果是否合理
  4. search()  — 编排完整流程，支持最多 N 轮重试
  5. auto_tag()— 导入时 LLM 自动标注文档分类
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from app.models.chroma_store import ChromaStore
from app.models.llm_http_client import LLMHttpClient
from app.utils import config as cfg
from app.utils.category_keywords import heuristic_category
from app.utils.logger import logger

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
_VALID_CATEGORIES = {
    "tech_manual", "business_sop", "company_intro",
    "parameter", "process", "project_code", "general",
}


def _parse_json(raw: str) -> Optional[dict]:
    """Extract first JSON object from LLM output."""
    if not raw:
        return None
    candidate = raw.strip()
    match = _JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(0)
    try:
        return json.loads(candidate)
    except Exception:
        return None


class GemmaRouter:
    """Gemma-4-2b 驱动的多轮自证路由引擎。"""

    def __init__(self, client: Optional[LLMHttpClient] = None) -> None:
        self._client = client

    def _get_client(self) -> LLMHttpClient:
        if self._client is None:
            self._client = LLMHttpClient()
        return self._client

    def _llm_chat(self, messages: list[dict]) -> str:
        """Single-shot LLM call, returns raw text."""
        try:
            return self._get_client().chat_once(messages)
        except Exception as e:
            logger.warning(f"GemmaRouter LLM call failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Phase 1: Route — 意图路由
    # ------------------------------------------------------------------

    def route(self, query: str, memory: List[Dict[str, str]]) -> dict:
        """意图路由：判定分类 + 改写查询。

        Returns:
            {"category": str, "standalone_query": str, "router_source": str, "reasoning": str}
        """
        condensed = self._heuristic_condense(query, memory)
        fallback = {
            "category": heuristic_category(condensed),
            "standalone_query": condensed,
            "router_source": "heuristic",
            "reasoning": f"启发式路由: 分类={heuristic_category(condensed)}, 改写=\"{condensed}\"",
        }

        if not cfg.get("router.enabled", True) or not cfg.get("llm.enabled", True):
            return fallback

        history_text = self._format_history(memory)

        few_shot = cfg.get("router.few_shot_examples", True)
        prompt = self._build_route_prompt(query, history_text, few_shot)

        raw = self._llm_chat([{"role": "user", "content": prompt}])
        parsed = self._parse_route_output(raw)
        if parsed:
            parsed["router_source"] = "llm"
            parsed["reasoning"] = f"LLM路由: 分类={parsed['category']}, 改写=\"{parsed['standalone_query']}\""
            return parsed

        return fallback

    def _build_route_prompt(self, query: str, history_text: str, few_shot: bool) -> str:
        base = (
            "你是 OmniLocalRAG 的意图路由器。请基于对话历史和当前问题输出严格 JSON，不要输出解释。\n"
            "允许的 category 只有：tech_manual, business_sop, company_intro, parameter, process, project_code, general。\n"
            "输出格式：{\"category\":\"...\",\"standalone_query\":\"...\"}\n"
            "规则：\n"
            "- standalone_query 必须是独立可检索问题，将代词替换为实际指代内容。\n"
            "- 如果问题偏安装、接口、技术排障、参数配置，category=tech_manual。\n"
            "- 如果问题偏流程制度、业务SOP，category=business_sop。\n"
            "- 如果问题偏公司介绍、概况，category=company_intro。\n"
            "- 如果问题偏参数表、规格数据，category=parameter。\n"
            "- 如果问题偏操作流程、步骤，category=process。\n"
            "- 如果问题涉及项目代号，category=project_code。\n"
            "- 不确定时 category=general。\n"
        )

        if few_shot:
            base += (
                "\n示例：\n"
                "对话历史：\n"
                "1. Q: OpenClaw 系统怎么安装？\n"
                "   A: OpenClaw 系统安装步骤如下...\n"
                "   C: tech_manual\n\n"
                "当前问题：它怎么跑起来？\n"
                "输出：{\"category\":\"tech_manual\",\"standalone_query\":\"OpenClaw 系统的启动步骤\"}\n\n"
                "---\n\n"
                "对话历史：\n"
                "1. Q: 员工请假流程是什么？\n"
                "   A: 员工请假需按以下流程...\n"
                "   C: business_sop\n\n"
                "当前问题：需要审批吗？\n"
                "输出：{\"category\":\"business_sop\",\"standalone_query\":\"员工请假流程是否需要审批\"}\n\n"
                "---\n\n"
                "对话历史：（无历史）\n"
                "当前问题：公司有哪些福利？\n"
                "输出：{\"category\":\"company_intro\",\"standalone_query\":\"公司有哪些福利\"}\n\n"
                "---\n\n"
            )

        base += (
            f"对话历史：\n{history_text}\n\n"
            f"当前问题：{query}\n"
            "输出："
        )
        return base

    def _parse_route_output(self, raw: str) -> Optional[dict]:
        parsed = _parse_json(raw)
        if not parsed or not isinstance(parsed, dict):
            return None
        category = str(parsed.get("category") or "").strip()
        standalone = str(parsed.get("standalone_query") or "").strip()
        if not category or not standalone:
            return None
        if category not in _VALID_CATEGORIES:
            category = "general"
        return {"category": category, "standalone_query": standalone}

    # ------------------------------------------------------------------
    # Phase 2: Locate — 文档定位
    # ------------------------------------------------------------------

    def locate(self, query: str, category: str, chroma: ChromaStore) -> dict:
        """文档定位：从 heading_path 列表中选择目标文档段。

        Returns:
            {"heading_paths": list[str], "chunks": list[dict], "confidence": float, "reasoning": str}
        """
        headings = chroma.list_headings(category=category)
        if not headings:
            # Fallback 1: try headings without category filter
            headings = chroma.list_headings()
            if headings:
                # Filter to those likely relevant (keep top N by text match)
                query_lower = query.lower()
                scored = []
                for h in headings:
                    hp = h.get("heading_path", "")
                    score = sum(1 for t in query_lower.split() if len(t) > 1 and t in hp.lower())
                    scored.append((h, score))
                scored.sort(key=lambda x: -x[1])
                # Keep headings with any keyword match, or top 20 if none match
                matched = [h for h, s in scored if s > 0]
                headings = matched[:30] if matched else [h for h, _ in scored[:20]]
            if not headings:
                # Fallback 2: text search without category filter
                chunks = chroma.search_text(query, limit=10)
                return {
                    "heading_paths": [],
                    "chunks": chunks,
                    "confidence": 0.3 if chunks else 0.0,
                    "reasoning": f"无heading索引，降级文本搜索，命中{len(chunks)}条",
                }

        # Build indexed heading list for LLM
        heading_list = []
        for idx, h in enumerate(headings, 1):
            hp = h.get("heading_path", "")
            cc = h.get("chunk_count", 1)
            heading_list.append(f"{idx}. {hp} ({cc} chunks)")

        heading_text = "\n".join(heading_list)

        prompt = (
            f"以下是 [{category}] 分类下的文档标题索引：\n"
            f"{heading_text}\n\n"
            f"用户问题：{query}\n\n"
            "请选择最相关的标题编号（可多选），输出 JSON：\n"
            '{"selected_indices": [1, 3], "reason": "..."}\n'
            "只输出 JSON，不要输出解释。"
        )

        raw = self._llm_chat([{"role": "user", "content": prompt}])
        selected = self._parse_locate_output(raw, len(headings))

        if not selected:
            # LLM failed to locate, fallback to text search (no category filter)
            chunks = chroma.search_text(query, limit=10)
            return {
                "heading_paths": [],
                "chunks": chunks,
                "confidence": 0.3 if chunks else 0.0,
                "reasoning": f"LLM定位失败，降级文本搜索，命中{len(chunks)}条",
            }

        # Fetch chunks for selected headings
        selected_headings = [headings[i - 1] for i in selected if 0 < i <= len(headings)]
        all_chunks: List[dict] = []
        selected_paths: List[str] = []
        for h in selected_headings:
            hp = h.get("heading_path", "")
            if hp:
                selected_paths.append(hp)
                chunks = chroma.get_by_heading(hp, category=category)
                all_chunks.extend(chunks)

        confidence = min(1.0, len(selected) * 0.4) if selected_paths else 0.0
        selected_desc = ", ".join(selected_paths[:3])
        if len(selected_paths) > 3:
            selected_desc += f" 等{len(selected_paths)}个"
        return {
            "heading_paths": selected_paths,
            "chunks": all_chunks,
            "confidence": confidence,
            "reasoning": f"选中{len(selected)}个标题: {selected_desc or '(空)'}",
        }

    def _parse_locate_output(self, raw: str, max_index: int) -> List[int]:
        parsed = _parse_json(raw)
        if not parsed or not isinstance(parsed, dict):
            return []
        indices = parsed.get("selected_indices") or parsed.get("indices") or []
        if not isinstance(indices, list):
            return []
        valid = []
        for idx in indices:
            try:
                i = int(idx)
                if 1 <= i <= max_index:
                    valid.append(i)
            except (ValueError, TypeError):
                continue
        return valid

    # ------------------------------------------------------------------
    # Phase 3: Validate — 验证定位
    # ------------------------------------------------------------------

    def validate(self, query: str, located_headings: list, chunks: list) -> dict:
        """验证定位：检查定位结果是否合理。

        Returns:
            {"valid": bool, "reason": str, "missing": str, "reasoning": str}
        """
        if not chunks:
            return {"valid": False, "reason": "未定位到任何内容", "missing": query, "reasoning": "验证失败: 未定位到任何内容"}

        # Build content summary for validation (truncate to fit context)
        content_parts = []
        for i, chunk in enumerate(chunks[:8], 1):
            hp = chunk.get("heading_path", "")
            content = chunk.get("content", "")[:300]
            content_parts.append(f"[{i}] {hp}\n{content}")

        content_text = "\n\n".join(content_parts)

        prompt = (
            f"用户问题：{query}\n\n"
            f"定位到的内容：\n{content_text}\n\n"
            "这些内容是否足以回答用户问题？\n"
            '输出 JSON：{"valid": true/false, "reason": "...", "missing": "..."}\n'
            "只输出 JSON，不要输出解释。"
        )

        raw = self._llm_chat([{"role": "user", "content": prompt}])
        return self._parse_validate_output(raw)

    def _parse_validate_output(self, raw: str) -> dict:
        parsed = _parse_json(raw)
        if not parsed or not isinstance(parsed, dict):
            # Default to valid if LLM fails
            return {"valid": True, "reason": "LLM 验证失败，默认通过", "missing": "", "reasoning": "LLM验证失败，默认通过"}
        valid = bool(parsed.get("valid", True))
        reason = str(parsed.get("reason", ""))
        missing = str(parsed.get("missing", ""))
        reasoning = f"验证{'通过' if valid else '失败'}: {reason}" if reason else f"验证{'通过' if valid else '失败'}"
        return {
            "valid": valid,
            "reason": reason,
            "missing": missing,
            "reasoning": reasoning,
        }

    # ------------------------------------------------------------------
    # Full search: route → locate → validate → (retry)
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        memory: List[Dict[str, str]],
        chroma: ChromaStore,
    ) -> dict:
        """完整多轮自证搜索流程。

        Returns:
            {
                "category": str,
                "standalone_query": str,
                "chunks": list[dict],
                "heading_paths": list[str],
                "router_source": str,
                "attempts": int,
                "validated": bool,
                "timings": dict,
                "reasoning_steps": list[str],
            }
        """
        max_retries = int(cfg.get("router.max_validate_retries", 3))
        timings: Dict[str, float] = {}
        reasoning_steps: List[str] = []

        # Phase 1: Route
        t0 = time.perf_counter()
        route_result = self.route(query, memory)
        timings["route_ms"] = (time.perf_counter() - t0) * 1000

        category = route_result["category"]
        standalone_query = route_result["standalone_query"]
        router_source = route_result["router_source"]
        reasoning_steps.append(f"[路由] {route_result.get('reasoning', f'分类={category}, 改写=\"{standalone_query}\"')}")

        # Phase 2+3: Locate + Validate with retries
        attempts = 0
        validated = False
        all_chunks: List[dict] = []
        all_heading_paths: List[str] = []

        for attempt in range(1, max_retries + 1):
            attempts = attempt

            # Locate
            t1 = time.perf_counter()
            locate_result = self.locate(standalone_query, category, chroma)
            timings["locate_ms"] = (time.perf_counter() - t1) * 1000

            located_chunks = locate_result["chunks"]
            located_headings = locate_result["heading_paths"]
            reasoning_steps.append(f"[定位·第{attempt}轮] {locate_result.get('reasoning', f'命中{len(located_chunks)}条')}")

            if not located_chunks:
                # No results at all — try broader search
                if category != "general":
                    reasoning_steps.append(f"[扩展] 分类{category}无结果，切换到general")
                    category = "general"
                    continue
                break

            # Validate
            t2 = time.perf_counter()
            validate_result = self.validate(standalone_query, located_headings, located_chunks)
            timings["validate_ms"] = (time.perf_counter() - t2) * 1000
            reasoning_steps.append(f"[验证·第{attempt}轮] {validate_result.get('reasoning', '通过' if validate_result['valid'] else '失败')}")

            if validate_result["valid"]:
                validated = True
                all_chunks = located_chunks
                all_heading_paths = located_headings
                break

            # Validation failed — retry with broader scope
            logger.info(
                f"GemmaRouter validate failed (attempt {attempt}): "
                f"{validate_result.get('reason', '')}"
            )
            if attempt < max_retries:
                # Expand: drop category filter or use text search
                if category != "general":
                    reasoning_steps.append(f"[重试] 验证失败，扩展到general分类")
                    category = "general"
                else:
                    # Already general, try text search as supplement
                    extra = chroma.search_text(
                        validate_result.get("missing") or standalone_query,
                        limit=10,
                    )
                    located_chunks.extend(extra)
                    all_chunks = located_chunks
                    all_heading_paths = located_headings
                    validated = False
                    reasoning_steps.append(f"[补充] 文本搜索补充{len(extra)}条")
                    break

        # If all retries exhausted, use last locate result
        if not all_chunks and located_chunks:
            all_chunks = located_chunks
            all_heading_paths = located_headings

        # Limit to top_k
        top_k = int(cfg.get("retrieval.top_k", 5))
        all_chunks = all_chunks[:top_k]

        reasoning_steps.append(f"[结果] 命中{len(all_chunks)}条, 验证{'通过' if validated else '未通过'}, 共{attempts}轮")

        return {
            "category": category,
            "standalone_query": standalone_query,
            "chunks": all_chunks,
            "heading_paths": all_heading_paths,
            "router_source": router_source,
            "attempts": attempts,
            "validated": validated,
            "timings": timings,
            "reasoning_steps": reasoning_steps,
        }

    # ------------------------------------------------------------------
    # Auto-tag: LLM 自动标注文档分类
    # ------------------------------------------------------------------

    def auto_tag(
        self,
        content_sample: str,
        heading_paths: List[str],
    ) -> dict:
        """用 Gemma 分析文档内容，自动生成属性标签。

        Returns:
            {"category": str, "tags": list[str], "description": str}
        """
        fallback = {
            "category": heuristic_category(content_sample),
            "tags": [],
            "description": "",
        }

        if not cfg.get("llm.enabled", True):
            return fallback

        headings_text = "\n".join(f"- {h}" for h in heading_paths[:30]) or "（无标题）"
        sample = content_sample[:1500] if content_sample else "（无内容）"

        prompt = (
            "分析以下文档的标题结构和内容样本，输出分类标签。\n"
            "允许的 category：tech_manual, business_sop, company_intro, parameter, process, project_code, general\n\n"
            f"文档标题：\n{headings_text}\n\n"
            f"内容样本：\n{sample}\n\n"
            '输出 JSON：{"category":"...","tags":["..."],"description":"..."}\n'
            "tags 是关键词标签列表（最多5个），description 是一句话描述。\n"
            "只输出 JSON。"
        )

        raw = self._llm_chat([{"role": "user", "content": prompt}])
        parsed = _parse_json(raw)
        if not parsed or not isinstance(parsed, dict):
            return fallback

        category = str(parsed.get("category") or "").strip()
        if category not in _VALID_CATEGORIES:
            category = fallback["category"]

        tags = parsed.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip() for t in tags if str(t).strip()][:5]

        description = str(parsed.get("description") or "").strip()

        return {
            "category": category,
            "tags": tags,
            "description": description,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _heuristic_condense(query: str, memory: List[Dict[str, str]]) -> str:
        """Condense follow-up questions using conversation memory."""
        from app.utils.category_keywords import _FOLLOWUP_MARKERS

        q = (query or "").strip()
        if not q or not memory:
            return q
        if any(marker in q for marker in _FOLLOWUP_MARKERS):
            last = memory[-1]
            anchor = last.get("standalone_query") or last.get("query") or ""
            if anchor.strip():
                return f'基于\u201c{anchor}\u201d：{q}'
        return q

    @staticmethod
    def _format_history(memory: List[Dict[str, str]]) -> str:
        if not memory:
            return "（无历史）"
        lines = []
        for idx, turn in enumerate(memory[-5:], 1):
            lines.append(
                f"{idx}. Q: {turn.get('query', '')}\n"
                f"   A: {turn.get('answer', '')[:160]}\n"
                f"   C: {turn.get('category', 'general')}"
            )
        return "\n".join(lines)
