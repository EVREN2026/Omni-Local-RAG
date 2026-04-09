import time
from typing import List

from PyQt5.QtCore import QThread, pyqtSignal

from app.models.chroma_store import ChromaStore
from app.models.embed_manager import EmbedManager
from app.models.llm_manager import LLMManager
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
                self.token_generated.emit("（未检索到相关内容，请先导入知识文档）")
                self.generation_finished.emit(True)
                return

            # Step 2: build prompt & generate
            prompt = _build_prompt(self.query, results)
            if not llm.load():
                self.error_occurred.emit("模型加载失败，请检查 models/ 目录")
                self.generation_finished.emit(False)
                return

            t1 = time.perf_counter()
            token_count = 0
            for token in llm.generate(prompt, stream=True):
                if self._cancelled:
                    break
                self.token_generated.emit(token)
                token_count += 1

            elapsed = time.perf_counter() - t1
            tps = token_count / elapsed if elapsed > 0 else 0
            logger.info(f"Generation: {token_count} tokens @ {tps:.1f} tok/s")
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
