#!/usr/bin/env python
"""Compare llama-server throughput across different request paths."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.llama_server_manager import LlamaServerManager
from app.models.llm_http_client import LLMHttpClient
from app.models.llm_manager import LLMManager


def _summarize(label: str, wall_ms: float, text: str, usage: dict[str, Any], timings: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": label,
        "wall_ms": round(wall_ms, 1),
        "output_chars": len(text),
        "text": text,
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "tokens_predicted": timings.get("predicted_n"),
        "predicted_per_second": timings.get("predicted_per_second"),
        "prompt_per_second": timings.get("prompt_per_second"),
    }


def _run_completion(client: LLMHttpClient, prompt: str, n_predict: int, temperature: float) -> dict[str, Any]:
    t0 = time.perf_counter()
    data = client.completion_once(prompt, n_predict=n_predict, temperature=temperature, stream=False)
    wall_ms = (time.perf_counter() - t0) * 1000
    timings = data.get("timings", {}) or {}
    usage = {
        "completion_tokens": data.get("tokens_predicted"),
        "prompt_tokens": data.get("tokens_evaluated"),
    }
    return _summarize("completion", wall_ms, str(data.get("content", "") or ""), usage, timings)


def _run_chat(client: LLMHttpClient, label: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    t0 = time.perf_counter()
    data = client.chat_once_with_meta(messages)
    wall_ms = (time.perf_counter() - t0) * 1000
    return _summarize(label, wall_ms, str(data.get("text", "") or ""), data.get("usage", {}) or {}, data.get("timings", {}) or {})


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark llama-server request paths")
    parser.add_argument("--base-url", default="", help="Override base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--prompt", default="Only output digits: 11+12=", help="Prompt to send")
    parser.add_argument("--max-tokens", type=int, default=64, help="Maximum output tokens for the probe")
    parser.add_argument("--temperature", type=float, default=0.0, help="Temperature for the probe")
    args = parser.parse_args()

    if args.base_url:
        client = LLMHttpClient(args.base_url)
    else:
        if not LLMManager().load():
            print(json.dumps({"error": LLMManager().last_error}, ensure_ascii=False, indent=2))
            return 1
        client = LLMHttpClient(LlamaServerManager().base_url)

    result = {
        "base_url": client._base_url,
        "benchmarks": [
            _run_completion(client, args.prompt, args.max_tokens, args.temperature),
            _run_chat(client, "chat_user_only", [{"role": "user", "content": args.prompt}]),
            _run_chat(client, "chat_app_style", LLMManager._prompt_to_messages(args.prompt)),
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
