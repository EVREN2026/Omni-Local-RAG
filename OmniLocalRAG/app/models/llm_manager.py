"""
llm_manager.py — LLM 推理入口（llama-server HTTP 模式）。

架构变化（v2）：
  • 对话/生成模型：由 LlamaServerManager 启动本地 llama-server.exe HTTP 服务，
    通过 LLMHttpClient 调用 OpenAI-compatible /v1/chat/completions API。
    完全不再使用 llama-cpp-python 加载对话模型。

公共接口与旧版保持兼容：
  • LLMManager().load()          → 启动 llama-server（如未运行）
  • LLMManager().unload()        → 停止 llama-server
  • LLMManager().generate(prompt, stream=True) → Iterator[str]
  • LLMManager().is_loaded       → bool
  • LLMManager().last_error      → str
"""

from __future__ import annotations

import time
from typing import Iterator, Optional

from app.utils import config as cfg
from app.utils.logger import logger


class LLMManager:
    """单例：对话 LLM 的统一入口，后端为 llama-server HTTP。"""

    _instance: Optional["LLMManager"] = None
    _last_error: str = ""
    _server_ready: bool = False

    def __new__(cls) -> "LLMManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── 公共属性 ─────────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        """True 表示 llama-server 正在运行且健康。"""
        from app.models.llama_server_manager import LlamaServerManager
        return LlamaServerManager().is_running

    @property
    def last_error(self) -> str:
        return self._last_error

    # ── load / unload ─────────────────────────────────────────────────────

    def load(self) -> bool:
        """
        确保 llama-server 已启动并就绪。
        返回 True 表示就绪，False 表示失败（last_error 有详情）。
        """
        if not cfg.get("llm.enabled", True):
            # 用户禁用了 LLM，不需要启动服务
            return False

        from app.models.llama_server_manager import LlamaServerManager

        mgr = LlamaServerManager()

        # 已就绪
        if mgr.is_running:
            return True

        # 取模型路径
        model_path = self._resolve_model_path()
        if not model_path:
            return False

        t0 = time.perf_counter()
        logger.info(f"正在启动 llama-server (模型: {model_path})...")
        ok = mgr.ensure_started(model_path)
        elapsed = (time.perf_counter() - t0) * 1000

        if ok:
            self._last_error = ""
            logger.info(f"llama-server 就绪 ({elapsed:.0f}ms): {mgr.base_url}")
            return True
        else:
            self._last_error = mgr.last_error
            logger.error(f"llama-server 启动失败: {self._last_error}")
            return False

    def unload(self, reason: str = "manual") -> None:
        """停止 llama-server 子进程。"""
        from app.models.llama_server_manager import LlamaServerManager
        LlamaServerManager().stop()
        logger.info(f"LLM unloaded (reason={reason})")

    # ── generate ─────────────────────────────────────────────────────────

    def generate(self, prompt: str, stream: bool = True) -> Iterator[str]:
        """
        将 RAG prompt 发送到 llama-server /v1/chat/completions。

        stream=True  → 逐 token yield
        stream=False → yield 完整回答（一次）

        会自动将原始 prompt 字符串封装为 chat messages 格式。
        """
        from app.models.llama_server_manager import LlamaServerManager
        from app.models.llm_http_client import LLMHttpClient

        if not LlamaServerManager().is_running:
            raise RuntimeError(
                "llama-server 未运行，请先调用 LLMManager().load()"
            )

        messages = self._prompt_to_messages(prompt)
        client = LLMHttpClient(LlamaServerManager().base_url)

        if stream:
            emitted = False
            for token in client.chat_stream(messages):
                emitted = True
                yield token
            if not emitted:
                try:
                    fallback = client.chat_once_with_meta(messages)
                except Exception as e:
                    logger.warning(
                        "llama-server stream returned no visible content and "
                        f"non-stream fallback failed: {e}"
                    )
                    return
                text = str(fallback.get("text") or "").strip()
                if text:
                    logger.warning(
                        "llama-server stream returned no visible content; "
                        "falling back to non-stream response"
                    )
                    yield text
        else:
            text = client.chat_once(messages)
            if text:
                yield text

    # ── 内部辅助 ─────────────────────────────────────────────────────────

    def _resolve_model_path(self) -> str:
        """
        按优先级确定要加载的模型路径：
          1. config llama_server.model_path
          2. config llm.model_path
        """
        # llama_server 专用配置优先
        ls_path = cfg.get("llama_server.model_path", "")
        if ls_path:
            full = cfg.abs_path(ls_path)
            if full.exists():
                return str(full)
            self._last_error = f"llama_server.model_path 指定的文件不存在: {full}"
            logger.error(self._last_error)
            return ""

        # 回退到 llm.model_path
        llm_path = cfg.get("llm.model_path", "")
        if llm_path:
            full = cfg.abs_path(llm_path)
            if full.exists():
                return str(full)
            self._last_error = f"GGUF 模型文件不存在: {full}"
            logger.error(self._last_error)
            return ""

        self._last_error = "未配置 GGUF 模型路径（llama_server.model_path 或 llm.model_path）"
        logger.error(self._last_error)
        return ""

    @staticmethod
    def _prompt_to_messages(prompt: str) -> list[dict]:
        """
        将原始 RAG prompt 字符串转为 OpenAI chat messages 格式。

        约定：prompt 格式为 "...上下文...\n问题：{query}\n回答："
        我们把整段 prompt 作为 user 消息，指示模型直接输出"回答："后的内容。
        """
        # 在 system message 里添加简短指令，让模型专注输出答案部分
        system_msg = (
            "你是一个知识库问答助手。用户会提供检索到的文档片段和问题，"
            "请严格依据文档内容用中文作答，不要重复问题本身，直接输出答案。"
        )
        return [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": prompt},
        ]
