import time
from typing import List

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
from app.models.embed_manager import EmbedManager
from app.models.llm_manager import LLMManager
from app.models.qa_memory import QAMemoryRecorder
from app.utils import config as cfg
from app.utils.logger import logger

_RAG_PROMPT_TEMPLATE = """\
以下是从知识库中检索到的相关内容：

{context}

---
请根据以上内容，用中文回答下面的问题。如果检索内容不足以回答，请直接说明。

问题：{query}

回答："""


def _build_prompt(query: str, results: list) -> str:
    context_parts = []
    for i, r in enumerate(results, 1):
        source = r.get("anchor_id") or r.get("id", "")
        context_parts.append(f"[{i}] {r['document']}  (来源: {source})")
    return _RAG_PROMPT_TEMPLATE.format(
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
        document = str(result.get("document") or result.get("content") or "").strip()
        if len(document) > 900:
            document = document[:900].rstrip() + "..."
        lines.extend(
            [
                f"## 结果 {i}",
                f"- 来源: {source}",
                "",
                document,
                "",
            ]
        )
    return "\n".join(lines).strip()


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


class InferenceWorker(QThread):
    token_generated = pyqtSignal(str)
    context_retrieved = pyqtSignal(list)
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
            embed = EmbedManager()
            chroma = ChromaStore()
            llm = LLMManager()

            # Step 1: embed query
            top_k = cfg.get("retrieval.top_k", 5)
            t0 = time.perf_counter()
            [q_vec] = embed.encode([self.query])
            results = chroma.query(q_vec, n_results=top_k)
            retrieval_ms = (time.perf_counter() - t0) * 1000
            logger.info(f"Retrieval top-{top_k}: {len(results)} hits in {retrieval_ms:.1f}ms")
            self.context_retrieved.emit(results)

            if not results:
                answer = "（未检索到相关内容，请先导入知识文档）"
                self.token_generated.emit(answer)
                _record_qa_memory(self.query, results, answer, mode="no_results")
                self.generation_finished.emit(True)
                return

            # Step 2: build prompt & generate
            prompt = _build_prompt(self.query, results)
            if not cfg.get("llm.enabled", True):
                answer = _build_retrieval_answer(
                    results,
                    "本地 LLM 当前已禁用，先展示 VectorStore 检索结果。",
                )
                self.token_generated.emit(answer)
                _record_qa_memory(self.query, results, answer, mode="retrieval_only")
                self.generation_finished.emit(True)
                return

            if not llm.load():
                detail = llm.last_error or "请检查 models/ 目录和 llama-cpp-python 安装"
                answer = _build_retrieval_answer(
                    results,
                    f"本地 LLM 加载失败，已降级为检索结果直出。\n原因：{detail}",
                )
                self.token_generated.emit(answer)
                _record_qa_memory(self.query, results, answer, mode="llm_load_failed")
                self.generation_finished.emit(True)
                return

            t1 = time.perf_counter()
            token_count = 0
            answer_parts = []
            for token in llm.generate(prompt, stream=True):
                if self._cancelled:
                    break
                self.token_generated.emit(token)
                answer_parts.append(token)
                token_count += 1

            elapsed = time.perf_counter() - t1
            tps = token_count / elapsed if elapsed > 0 else 0
            logger.info(f"Generation: {token_count} tokens @ {tps:.1f} tok/s")
            if not self._cancelled:
                _record_qa_memory(self.query, results, "".join(answer_parts), mode="llm")
            self.generation_finished.emit(not self._cancelled)

        except MemoryError:
            logger.error("OOM during inference", exc_info=True)
            LLMManager().unload(reason="oom")
            self.error_occurred.emit("内存不足，模型已自动释放，请重启后重试")
            self.generation_finished.emit(False)
        except Exception as e:
            logger.error(f"InferenceWorker error: {e}", exc_info=True)
            self.error_occurred.emit(str(e))
            self.generation_finished.emit(False)
