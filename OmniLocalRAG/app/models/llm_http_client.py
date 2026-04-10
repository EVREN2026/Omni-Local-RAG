"""
llm_http_client.py — llama-server OpenAI-compatible HTTP 客户端。

llama-server 实现了 OpenAI Chat Completions API：
  POST /v1/chat/completions
  GET  /health

本模块封装两种调用模式：
  • stream=True  → Server-Sent Events (SSE) 流式 token 迭代器
  • stream=False → 一次性返回完整文本

零依赖，只用标准库 urllib / http.client + json。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

from app.utils import config as cfg
from app.utils.logger import logger


# ── 公共 API ──────────────────────────────────────────────────────────────────

class LLMHttpClient:
    """
    OpenAI-compatible HTTP 客户端，指向本地 llama-server。

    用法::

        client = LLMHttpClient("http://127.0.0.1:8000")
        for token in client.chat_stream([{"role": "user", "content": "你好"}]):
            print(token, end="", flush=True)
    """

    def __init__(self, base_url: str | None = None) -> None:
        port = int(cfg.get("llama_server.port", 8000))
        host = cfg.get("llama_server.host", "127.0.0.1")
        self._base_url = (base_url or f"http://{host}:{port}").rstrip("/")

    # ── 流式生成 ─────────────────────────────────────────────────────────

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        """
        发送 /v1/chat/completions（stream=True），逐 token yield 文本片段。
        遇到错误抛出 RuntimeError。
        """
        payload = self._build_payload(messages, stream=True)
        url = f"{self._base_url}/v1/chat/completions"
        req = self._make_request(url, payload)

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                yield from self._parse_sse(resp)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(
                f"llama-server 返回 HTTP {e.code}: {body[:300]}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"无法连接到 llama-server ({self._base_url}): {e.reason}"
            ) from e

    def chat_once(self, messages: list[dict]) -> str:
        """
        发送 /v1/chat/completions（stream=False），返回完整生成文本。
        """
        payload = self._build_payload(messages, stream=False)
        url = f"{self._base_url}/v1/chat/completions"
        req = self._make_request(url, payload)

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(
                f"llama-server 返回 HTTP {e.code}: {body[:300]}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"无法连接到 llama-server ({self._base_url}): {e.reason}"
            ) from e

    def health(self) -> bool:
        """返回 /health 是否 200 OK。"""
        try:
            with urllib.request.urlopen(
                f"{self._base_url}/health", timeout=3
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    # ── 内部辅助 ─────────────────────────────────────────────────────────

    def _build_payload(self, messages: list[dict], stream: bool) -> dict:
        return {
            "model":             "local",   # llama-server 忽略此字段，但 API 规范要求
            "messages":          messages,
            "max_tokens":        int(cfg.get("llm.max_tokens",      512)),
            "temperature":       float(cfg.get("llm.temperature",   0.7)),
            "repeat_penalty":    float(cfg.get("llm.repeat_penalty", 1.1)),
            "stream":            stream,
        }

    @staticmethod
    def _make_request(url: str, payload: dict) -> urllib.request.Request:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

    @staticmethod
    def _parse_sse(response) -> Iterator[str]:
        """
        解析 Server-Sent Events 流。
        每个 data: {...} 行包含一个 OpenAI-style chunk。
        遇到 data: [DONE] 时停止。
        """
        buffer = b""
        for raw_chunk in response:
            buffer += raw_chunk
            # SSE 以 \n\n 分隔事件
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue
                if line_str.startswith("data:"):
                    data = line_str[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                        delta = (
                            chunk.get("choices", [{}])[0]
                                 .get("delta", {})
                                 .get("content", "")
                        )
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, IndexError, KeyError) as e:
                        logger.debug(f"SSE 解析跳过: {line_str!r} ({e})")


# ── 便捷工厂 ──────────────────────────────────────────────────────────────────

def get_client() -> LLMHttpClient:
    """返回使用当前 config 配置的 LLMHttpClient 实例。"""
    return LLMHttpClient()
