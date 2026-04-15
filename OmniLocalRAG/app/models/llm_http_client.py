"""
Minimal HTTP client for a local llama-server instance.

Supported endpoints:
- POST /v1/chat/completions
- POST /completion
- GET  /health
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

from app.utils import config as cfg
from app.utils.logger import logger

_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LLMHttpClient:
    """OpenAI-compatible HTTP client for a local llama-server."""

    def __init__(self, base_url: str | None = None) -> None:
        port = int(cfg.get("llama_server.port", 8000))
        host = cfg.get("llama_server.host", "127.0.0.1")
        self._base_url = (base_url or f"http://{host}:{port}").rstrip("/")

    def chat_stream(self, messages: list[dict]) -> Iterator[str]:
        """Yield streamed text chunks from `/v1/chat/completions`."""
        payload = self._build_payload(messages, stream=True)
        url = f"{self._base_url}/v1/chat/completions"
        req = self._make_request(url, payload)

        try:
            with _LOCAL_OPENER.open(req, timeout=300) as resp:
                yield from self._parse_sse(resp)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(f"llama-server returned HTTP {e.code}: {body[:300]}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Unable to connect to llama-server ({self._base_url}): {e.reason}") from e

    def chat_once(self, messages: list[dict]) -> str:
        """Return full non-stream chat completion text."""
        return self.chat_once_with_meta(messages)["text"]

    def chat_once_with_meta(self, messages: list[dict]) -> dict[str, Any]:
        """Return text plus server usage and timing metadata."""
        payload = self._build_payload(messages, stream=False)
        data = self._post_json(f"{self._base_url}/v1/chat/completions", payload)
        return {
            "text": data.get("choices", [{}])[0].get("message", {}).get("content", ""),
            "usage": data.get("usage", {}) or {},
            "timings": data.get("timings", {}) or {},
            "raw": data,
        }

    def completion_once(self, prompt: str, **overrides: Any) -> dict[str, Any]:
        """Return non-stream output from the native `/completion` endpoint."""
        payload: dict[str, Any] = {
            "prompt": prompt,
            "n_predict": int(cfg.get("llm.max_tokens", 512)),
            "temperature": float(cfg.get("llm.temperature", 0.7)),
            "stream": False,
        }
        payload.update(overrides)
        return self._post_json(f"{self._base_url}/completion", payload)

    def health(self) -> bool:
        """Return whether `/health` responds with 200 OK."""
        try:
            with _LOCAL_OPENER.open(f"{self._base_url}/health", timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _build_payload(self, messages: list[dict], stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": "local",
            "messages": messages,
            "max_tokens": int(cfg.get("llm.max_tokens", 512)),
            "temperature": float(cfg.get("llm.temperature", 0.7)),
            "repeat_penalty": float(cfg.get("llm.repeat_penalty", 1.1)),
            "stream": stream,
        }
        top_k = int(cfg.get("llm.top_k", 40))
        if top_k > 0:
            payload["top_k"] = top_k

        top_p = float(cfg.get("llm.top_p", 0.9))
        if 0.0 < top_p <= 1.0:
            payload["top_p"] = top_p

        min_p = float(cfg.get("llm.min_p", 0.05))
        if 0.0 <= min_p <= 1.0:
            payload["min_p"] = min_p

        payload["presence_penalty"] = float(cfg.get("llm.presence_penalty", 0.0))
        payload["frequency_penalty"] = float(cfg.get("llm.frequency_penalty", 0.0))
        return payload

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = self._make_request(url, payload)
        try:
            with _LOCAL_OPENER.open(req, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise RuntimeError(f"llama-server returned HTTP {e.code}: {body[:300]}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Unable to connect to llama-server ({self._base_url}): {e.reason}") from e

    @staticmethod
    def _make_request(url: str, payload: dict[str, Any]) -> urllib.request.Request:
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
        """Parse OpenAI-style SSE events and yield text deltas."""
        buffer = b""
        for raw_chunk in response:
            buffer += raw_chunk
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
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, IndexError, KeyError) as e:
                        logger.debug(f"SSE parse skipped: {line_str!r} ({e})")


def get_client() -> LLMHttpClient:
    return LLMHttpClient()
