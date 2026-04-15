#!/usr/bin/env python
"""
Interactive local model pipeline test.

Behavior:
1. Connect ChromaStore once
2. Load Gemma via llama-server once
3. Enter an interactive chat loop and show per-turn timings
"""

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

from app.models.chroma_store import ChromaStore
from app.models.llama_server_manager import LlamaServerManager
from app.models.llm_http_client import LLMHttpClient
from app.models.llm_manager import LLMManager
from app.utils import config as cfg

SYSTEM_MESSAGE = (
    "你是一个知识库问答助手。"
    "用户会提供检索到的文档片段和问题，请严格依据文档内容用中文作答。"
    "如果内容不足，请明确说明不知道，不要编造。"
)


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "...(truncated)"


def _build_retrieval_prompt(query: str, results: list[dict[str, Any]]) -> str:
    if not results:
        return (
            "当前知识库中没有检索到足够相关的内容。\n"
            f"问题：{query}\n"
            "请先说明知识库未命中，再给出最保守的回答。"
        )

    context_parts: list[str] = []
    for i, item in enumerate(results, 1):
        source = item.get("anchor_id") or item.get("id", "")
        text = str(item.get("document") or item.get("content") or "").strip()
        distance = item.get("distance")
        if isinstance(distance, (int, float)):
            context_parts.append(f"[{i}] source={source} distance={distance:.4f}\n{text}")
        else:
            context_parts.append(f"[{i}] source={source}\n{text}")

    return (
        "请严格根据下面检索到的知识库内容回答。\n"
        "如果内容不足以回答，请明确说知识库中没有足够信息。\n"
        "回答时尽量带引用编号，例如 [1]。\n\n"
        "检索内容：\n"
        f"{chr(10).join(context_parts)}\n\n"
        f"问题：{query}\n\n"
        "回答："
    )


def _messages_for_turn(
    query: str,
    retrieval_enabled: bool,
    results: list[dict[str, Any]],
    history: list[dict[str, str]],
    max_history_turns: int,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_MESSAGE}]

    if history and max_history_turns > 0:
        keep_messages = max_history_turns * 2
        messages.extend(history[-keep_messages:])

    if retrieval_enabled:
        messages.append({"role": "user", "content": _build_retrieval_prompt(query, results)})
    else:
        messages.append({"role": "user", "content": query})

    return messages


def _startup(args: argparse.Namespace) -> tuple[ChromaStore | None, LLMManager, dict[str, float]]:
    timings: dict[str, float] = {}

    store: ChromaStore | None = None
    if not args.no_retrieval:
        store = ChromaStore()
        t0 = time.perf_counter()
        if not store.connect():
            raise RuntimeError("ChromaStore failed to connect")
        timings["store_connect"] = round((time.perf_counter() - t0) * 1000, 1)

    llm = LLMManager()
    t0 = time.perf_counter()
    if not llm.load():
        raise RuntimeError(f"Gemma llama-server failed to load: {llm.last_error}")
    timings["llm_load"] = round((time.perf_counter() - t0) * 1000, 1)

    return store, llm, timings


def _run_turn(
    query: str,
    args: argparse.Namespace,
    store: ChromaStore | None,
    client: LLMHttpClient,
    history: list[dict[str, str]],
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "query": query,
        "retrieval_enabled": not args.no_retrieval,
        "top_k": args.top_k,
        "timings_ms": {},
    }

    results: list[dict[str, Any]] = []

    if not args.no_retrieval and store is not None:
        t0 = time.perf_counter()
        results = store.search_text(query, limit=args.top_k)
        report["timings_ms"]["text_query"] = round((time.perf_counter() - t0) * 1000, 1)

    messages = _messages_for_turn(
        query=query,
        retrieval_enabled=not args.no_retrieval,
        results=results,
        history=history,
        max_history_turns=args.max_history_turns,
    )

    t0 = time.perf_counter()
    llm_data = client.chat_once_with_meta(messages)
    report["timings_ms"]["generation_wall"] = round((time.perf_counter() - t0) * 1000, 1)
    report["timings_ms"]["total"] = round(sum(report["timings_ms"].values()), 1)

    answer = str(llm_data.get("text", "") or "")
    report["answer"] = answer
    report["answer_chars"] = len(answer)
    report["retrieved_count"] = len(results)
    report["retrieved_sources"] = [
        {
            "id": item.get("id", ""),
            "anchor_id": item.get("anchor_id", ""),
            "distance": item.get("distance"),
            "content_chars": len(str(item.get("document") or item.get("content") or "")),
        }
        for item in results
    ]
    report["retrieved_chunks"] = [
        {
            "id": item.get("id", ""),
            "anchor_id": item.get("anchor_id", ""),
            "distance": item.get("distance"),
            "content": _truncate(str(item.get("document") or item.get("content") or "").strip(), args.text_limit),
        }
        for item in results
    ]
    report["messages"] = messages
    report["generation"] = {
        "usage": llm_data.get("usage", {}) or {},
        "timings": llm_data.get("timings", {}) or {},
    }
    return report


def _print_startup(startup: dict[str, float], args: argparse.Namespace) -> None:
    print("Startup:")
    for key, value in startup.items():
        print(f"  {key}: {value} ms")
    print(f"  retrieval_enabled: {not args.no_retrieval}")
    print(f"  top_k: {args.top_k}")
    print(f"  max_history_turns: {args.max_history_turns}")
    print()
    print("Commands:")
    print("  /exit  quit")
    print("  /clear clear chat history")
    print()


def _print_turn(report: dict[str, Any], args: argparse.Namespace) -> None:
    print()
    print("Assistant:")
    print(report["answer"])
    print()
    print("Turn timings:")
    for key, value in report["timings_ms"].items():
        print(f"  {key}: {value} ms")

    usage = report.get("generation", {}).get("usage", {})
    timings = report.get("generation", {}).get("timings", {})
    if usage:
        print("Usage:")
        print(f"  prompt_tokens: {usage.get('prompt_tokens')}")
        print(f"  completion_tokens: {usage.get('completion_tokens')}")
        print(f"  total_tokens: {usage.get('total_tokens')}")
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        if cached is not None:
            print(f"  cached_tokens: {cached}")
    if timings:
        print("Server timings:")
        print(f"  prompt_ms: {timings.get('prompt_ms')}")
        print(f"  predicted_ms: {timings.get('predicted_ms')}")
        print(f"  predicted_per_second: {timings.get('predicted_per_second')}")

    if not args.no_retrieval:
        print(f"Retrieved chunks: {report.get('retrieved_count', 0)}")
        for item in report.get("retrieved_chunks", []):
            print(
                f"  - {item.get('anchor_id') or item.get('id')} "
                f"(distance={item.get('distance'):.4f})"
            )
            print(f"    {item.get('content')}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive Gemma local chat test with text retrieval")
    parser.add_argument("--top-k", type=int, default=5, help="Number of retrieved chunks to include")
    parser.add_argument("--no-retrieval", action="store_true", help="Skip retrieval and chat with Gemma only")
    parser.add_argument("--max-history-turns", type=int, default=2, help="How many previous user+assistant turns to keep")
    parser.add_argument("--text-limit", type=int, default=300, help="Preview limit for retrieved chunk text")
    parser.add_argument("--json", action="store_true", help="Print each turn as JSON")
    args = parser.parse_args()

    try:
        store, _llm, startup = _startup(args)
    except Exception as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        return 1

    _print_startup(startup, args)

    mgr = LlamaServerManager()
    client = LLMHttpClient(mgr.base_url)
    history: list[dict[str, str]] = []

    while True:
        try:
            query = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not query:
            continue
        if query.lower() in {"/exit", "exit", "quit", "/quit"}:
            break
        if query.lower() == "/clear":
            history.clear()
            print("History cleared.")
            continue

        turn = _run_turn(query, args, store, client, history)
        if args.json:
            print(json.dumps(turn, ensure_ascii=False, indent=2))
        else:
            _print_turn(turn, args)

        history.append({"role": "user", "content": query})
        history.append({"role": "assistant", "content": turn["answer"]})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
