#!/usr/bin/env python3
"""
scripts/eval_qa.py — Offline golden QA evaluation against ChromaDB.

Usage:
    python scripts/eval_qa.py [--golden data/eval/golden_qa.jsonl]
                              [--output data/eval/eval_results.jsonl]
                              [--top-k 5]

The script:
1. Loads golden_qa.jsonl (ground-truth questions + expected keywords).
2. Encodes each question with EmbedManager (BGE-M3).
3. Queries ChromaDB top-k.
4. Applies weak-rule scoring:
   - retrieval_score: fraction of expected_chunk_keywords found in top-k chunks.
   - answer_score: fraction of must_include terms found in the top-k chunk text.
5. Assigns auto_verdict:
   - correct   : retrieval_score >= 0.6 AND answer_score == 1.0
   - partial   : retrieval_score >= 0.4 OR answer_score >= 0.5
   - retrieval_miss : retrieval_score < 0.3
   - wrong     : otherwise
6. Writes per-question results to --output and prints a summary table.

This does NOT call the LLM — it tests retrieval quality only.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Allow running from project root without installing the package
_HERE = Path(__file__).parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))


def _load_golden_qa(path: Path) -> List[Dict[str, Any]]:
    entries = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def _keyword_hit_rate(chunks: List[Dict[str, Any]], keywords: List[str]) -> float:
    """Fraction of keywords found (case-insensitive) in the combined chunk text."""
    if not keywords:
        return 1.0
    combined = " ".join(
        str(c.get("document") or c.get("content") or "").lower()
        for c in chunks
    )
    hits = sum(1 for kw in keywords if kw.lower() in combined)
    return hits / len(keywords)


def _must_include_rate(chunks: List[Dict[str, Any]], must_include: List[str]) -> float:
    """Fraction of must_include terms found in chunk text."""
    if not must_include:
        return 1.0
    combined = " ".join(
        str(c.get("document") or c.get("content") or "").lower()
        for c in chunks
    )
    hits = sum(1 for term in must_include if term.lower() in combined)
    return hits / len(must_include)


def _auto_verdict(retrieval_score: float, answer_score: float) -> str:
    if retrieval_score >= 0.6 and answer_score >= 1.0:
        return "correct"
    if retrieval_score < 0.3:
        return "retrieval_miss"
    if retrieval_score >= 0.4 or answer_score >= 0.5:
        return "partial"
    return "wrong"


def run_eval(
    golden_path: Path,
    output_path: Path,
    top_k: int = 5,
) -> None:
    from app.models.embed_manager import EmbedManager
    from app.models.chroma_store import ChromaStore

    entries = _load_golden_qa(golden_path)
    print(f"Loaded {len(entries)} golden QA entries from {golden_path}")

    embed = EmbedManager()
    if hasattr(embed, "load"):
        print("Loading embedding model...")
        if not embed.load():
            print(f"ERROR: Embedding model failed to load: {getattr(embed, 'last_error', '')}")
            sys.exit(1)

    chroma = ChromaStore()

    results = []
    verdict_counts: Dict[str, int] = {}

    for entry in entries:
        qa_id = entry.get("id", "?")
        question = entry["question"]
        keywords = entry.get("expected_chunk_keywords", [])
        must_include = entry.get("must_include", [])

        t0 = time.perf_counter()
        try:
            [q_vec] = embed.encode([question])
            chunks = chroma.query(q_vec, n_results=top_k)
        except Exception as e:
            print(f"  [{qa_id}] ERROR during retrieval: {e}")
            chunks = []
        retrieval_ms = (time.perf_counter() - t0) * 1000

        r_score = _keyword_hit_rate(chunks, keywords)
        a_score = _must_include_rate(chunks, must_include)
        verdict = _auto_verdict(r_score, a_score)
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        result = {
            "id": qa_id,
            "question": question,
            "source_section": entry.get("source_section", ""),
            "retrieval_ms": round(retrieval_ms, 1),
            "top_k_count": len(chunks),
            "retrieval_score": round(r_score, 3),
            "answer_score": round(a_score, 3),
            "auto_verdict": verdict,
            "expected_chunk_keywords": keywords,
            "must_include": must_include,
            "top_chunk_previews": [
                {
                    "id": c.get("id", ""),
                    "distance": c.get("distance"),
                    "preview": str(c.get("document") or c.get("content") or "")[:200],
                }
                for c in chunks[:3]
            ],
        }
        results.append(result)

        flag = "✓" if verdict == "correct" else ("~" if verdict == "partial" else "✗")
        print(
            f"  [{qa_id}] {flag} verdict={verdict:15s} "
            f"ret={r_score:.2f} ans={a_score:.2f} "
            f"({retrieval_ms:.0f}ms)"
        )

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Summary
    total = len(results)
    print("\n" + "=" * 60)
    print("EVAL SUMMARY")
    print("=" * 60)
    for v in ["correct", "partial", "wrong", "retrieval_miss"]:
        count = verdict_counts.get(v, 0)
        pct = count / total * 100 if total else 0
        print(f"  {v:20s}: {count:3d} / {total}  ({pct:.0f}%)")
    correct = verdict_counts.get("correct", 0)
    partial = verdict_counts.get("partial", 0)
    hit_rate = (correct + partial) / total * 100 if total else 0
    miss_rate = verdict_counts.get("retrieval_miss", 0) / total * 100 if total else 0
    print(f"\n  correct+partial rate : {hit_rate:.0f}%  (target: >70%)")
    print(f"  retrieval_miss rate  : {miss_rate:.0f}%  (target: <20%)")
    print(f"\n  Results written to: {output_path}")
    print("=" * 60)

    # Exit code: 0 if targets met, 1 otherwise
    if hit_rate >= 70 and miss_rate <= 20:
        print("\nTARGETS MET")
        sys.exit(0)
    else:
        print("\nTARGETS NOT MET — review chunking or retrieval configuration")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline golden QA eval (retrieval only)")
    parser.add_argument(
        "--golden",
        default="data/eval/golden_qa.jsonl",
        help="Path to golden QA JSONL file",
    )
    parser.add_argument(
        "--output",
        default="data/eval/eval_results.jsonl",
        help="Path to write per-question results",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of chunks to retrieve per question",
    )
    args = parser.parse_args()

    # Change working dir to project root so relative paths work
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)

    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"ERROR: Golden QA file not found: {golden_path}")
        sys.exit(1)

    run_eval(
        golden_path=golden_path,
        output_path=Path(args.output),
        top_k=args.top_k,
    )


if __name__ == "__main__":
    main()
