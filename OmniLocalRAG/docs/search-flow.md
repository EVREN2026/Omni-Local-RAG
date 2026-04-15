# OmniLocalRAG — 用户检索完整执行流程

> 文档版本：2026-04-12  
> 覆盖范围：用户按下 Enter 到回答渲染完毕的全链路，含所有分支路径

---

## 总体架构

```
[UI 主线程]                         [Worker 子线程 QThread]
────────────────────────────────────────────────────────────
SpotlightWindow
  └─ SearchController
       └─ InferenceWorker.run()
            ├─ EmbedManager.encode_payloads()   → dense + sparse 向量
            ├─ ChromaStore.query()              → 排序结果列表
            ├─ LLMManager.load()               → 确保 llama-server 启动
            └─ LLMManager.generate()           → 流式 token 迭代器
                 └─ LLMHttpClient.chat_stream() → HTTP SSE → llama-server
```

信号跨线程传递（Qt 队列连接，安全）：

| 信号 | 方向 | 接收方 |
|---|---|---|
| `timings_ready` | Worker → Controller → UI | `_on_timings()` |
| `context_retrieved` | Worker → Controller | `_on_context()` 过滤后转发 |
| `context_ready` | Controller → UI | `_on_context()` |
| `token_generated` | Worker → Controller → UI | `_on_token()` |
| `generation_finished` | Worker → Controller → UI | `_on_finished()` |
| `error_occurred` | Worker → Controller → UI | `_on_error()` |

---

## 阶段 0 — 窗口初始化（应用启动时，仅执行一次）

**文件：** `app/views/spotlight_window.py:62–90`

```python
self._search_ctrl = SearchController(self)   # L64 — 创建控制器
self._setup_flags()    # Qt 窗口标志：无边框、置顶、透明背景
self._build_ui()       # 加载 spotlight_window.ui，绑定所有子控件
self._connect_signals()# 连接所有信号/槽
self._apply_styles()   # 读取 assets/styles/main.qss
```

**`_connect_signals()` 完整连接表（L209–221）：**

| 信号源 | 槽方法 |
|---|---|
| `_input.returnPressed` | `_on_search` |
| `_stop_btn.clicked` | `_search_ctrl.cancel` |
| `_search_ctrl.token_ready` | `_on_token` |
| `_search_ctrl.context_ready` | `_on_context` |
| `_search_ctrl.timings_ready` | `_on_timings` |
| `_search_ctrl.search_finished` | `_on_finished` |
| `_search_ctrl.error_occurred` | `_on_error` |
| `_cursor_timer`（500ms） | `_blink_cursor` |

---

## 阶段 1 — 用户按 Enter：`_on_search()`

**文件：** `app/views/spotlight_window.py:393–410`

```
输入：query: str（用户输入的原始文本）
```

执行步骤：

| 行号 | 操作 |
|---|---|
| L394 | `query = self._input.text().strip()` |
| L395 | 空字符串直接返回（守卫） |
| L398 | `self._active_query = query` — 存储供后续 `_render_preview` 使用 |
| L399–401 | 清空 `_answer_plain`、`_context_results`、`_selected_result` |
| L402 | `_answer.clear()` — 清空 QTextBrowser |
| L403 | `_clear_cards()` — 删除所有结果卡片 Widget |
| L404 | `_render_preview(streaming=False)` — 渲染空状态 HTML |
| L405–407 | 显示"正在检索…"标签、显示取消按钮 |
| L408 | `_cursor_timer.start()` — 启动 500ms 光标闪烁计时器 |
| L409 | `_search_ctrl.search(query)` — 交给控制器 |
| L410 | `MemoryWatcher().reset()` — 重置内存监控 |

**`_clear_cards()` 内部（L484–492）：**
- 对 `_result_cards` 中每个 Widget 调用 `deleteLater()`
- 重置 `_cards_label` 为 "搜索结果"
- 调用 `_sync_selected_result_ui()` → 标题置为"资料卡"，隐藏 meta，禁用两个按钮
- 调用 `_update_body_visibility()` → 显示空状态提示标签

---

## 阶段 2 — 控制器启动 Worker：`SearchController.search()`

**文件：** `app/controllers/search_controller.py:24–37`

```python
def search(self, query: str) -> None:
    if self._worker and self._worker.isRunning():
        self._worker.cancel()    # 取消上一次未完成的检索
        self._worker.wait(500)   # 最多等 500ms 让旧线程退出

    self._worker = InferenceWorker(query)   # 创建新 Worker
    # 绑定 5 个信号：
    self._worker.context_retrieved.connect(self._on_context)   # 过滤后转发
    self._worker.timings_ready.connect(self.timings_ready)     # 直通
    self._worker.token_generated.connect(self.token_ready)     # 直通
    self._worker.generation_finished.connect(self.search_finished)  # 直通
    self._worker.error_occurred.connect(self.error_occurred)   # 直通
    self._worker.start()    # 启动 QThread，调用 run()
```

**控制器内的唯一过滤逻辑 `_on_context()`（L46–49）：**

```python
def _on_context(self, results: list) -> None:
    # 过滤掉自上次搜索后被手动编辑过的 chunk
    filtered = [r for r in results if r.get("id") not in self._cache_invalid_ids]
    self.context_ready.emit(filtered)
    self._cache_invalid_ids.clear()
```

正常情况下 `_cache_invalid_ids` 为空，`filtered == results`。

---

## 阶段 3 — Worker 线程：`InferenceWorker.run()`

**文件：** `app/workers/inference_worker.py`

整个 `run()` 被 `try/except` 包裹（异常处理见阶段 7）。

### 3.1 — 初始化

```python
overall_t0 = time.perf_counter()         # 全程计时起点
embed  = EmbedManager()                  # 获取单例（此时不加载模型）
chroma = ChromaStore()                   # 获取单例
llm    = LLMManager()                    # 获取单例
top_k  = int(cfg.get("retrieval.top_k", 5))   # 默认返回 5 条
embed_was_loaded = embed.is_loaded       # 记录模型是否已预热
llm_was_loaded   = llm.is_loaded         # 记录 llama-server 是否已就绪
```

---

### 3.2 — 文本向量化：`EmbedManager.encode_payloads()`

**文件：** `app/models/embed_manager.py`

```python
t0 = time.perf_counter()
[query_payload] = embed.encode_payloads([self.query], return_sparse=True)
q_vec        = list(query_payload.get("dense") or [])   # 1024 维 float 列表
query_sparse = query_payload.get("sparse") or None       # {token_id: weight} 或 None
embed_ms = (time.perf_counter() - t0) * 1000
```

**`encode_payloads()` 内部流程：**

```
模型是否已加载？
├─ 否 → load()
│       ├─ 检查 CUDA 可用性，确定 device
│       ├─ 尝试 FlagEmbedding.BGEM3FlagModel（支持 dense+sparse+colbert）
│       └─ 失败则降级 SentenceTransformer（仅 dense）
└─ 是 → 直接进入编码

路径 A: FlagEmbedding 后端
    model.encode(texts, return_dense=True, return_sparse=True)
    → dense_vecs  (1024维，L2规范化)
    → sparse_vecs (保留 top-96 词权重，{str: float})

路径 B: SentenceTransformers 后端
    model.encode(texts, normalize_embeddings=True)
    → dense_vecs 只，sparse=None
```

**输出：**
- `q_vec: list[float]` — 1024 维稠密向量（BGE-M3）
- `query_sparse: dict[str, float] | None` — 词 ID → 权重映射

---

### 3.3 — 向量检索：`ChromaStore.query()`

**文件：** `app/models/chroma_store.py:149–227`

```python
results = chroma.query(
    q_vec,
    n_results=top_k,
    query_text=self.query,
    query_sparse=query_sparse,
)
```

**内部执行步骤：**

**① 读取配置**
```python
threshold     = cfg.get("retrieval.distance_threshold", 0.7)
mode          = cfg.get("retrieval.mode", "hybrid")
dense_weight  = 0.55   # config: retrieval.hybrid_dense_weight
sparse_weight = 0.45   # config: retrieval.hybrid_sparse_weight
```

**② 全表扫描**
```python
rows = conn.execute("SELECT * FROM vectors").fetchall()
# 注意：O(N) 全表读取，无 WHERE 过滤
```

**③ 规范化查询向量**
```python
q = np.array(embedding, dtype=np.float32)
q = q / np.linalg.norm(q)   # 单位化
```

**④ 计算稠密余弦得分**
```python
mat   = np.frombuffer(所有行 embedding 字节).reshape(N, D)
mat   = mat / ||mat||   # 逐行单位化
dists = (1.0 - mat @ q).tolist()          # 余弦距离 = 1 - cos_sim
dense_scores = [max(0.0, 1.0 - d) for d in dists]
```

**⑤ 计算稀疏（关键词）得分**
```python
use_sparse = mode != "dense" AND (query_sparse OR query_text 非空)
if use_sparse:
    for row in rows:
        score = 0.0
        if query_sparse:
            score += 稀疏向量点积(query_sparse, row.sparse_payload)
        doc_text = 规范化(content + heading_path + display_content + ...)
        if query_phrase in doc_text:
            score += 6.0   # 完整短语命中奖励
        for term in 拆分(query_text):
            if term in doc_text:
                score += 1.0
                if 精确词（含数字/符号）:
                    score += 1.5
```

**⑥ 加权合并并排序**
```python
final_score = dense_weight  * (dense_i  / max_dense)
            + sparse_weight * (sparse_i / max_sparse)
# 按 final_score 降序排列，余弦距离升序作为并列时决胜
```

**⑦ 筛选并构建结果**
```python
for (final_score, dist, idx, dense_score, sparse_score) in ranked:
    if len(results) >= n_results: break
    if dist > threshold AND sparse_score <= 0: continue   # 过滤低质量结果
    entry = dict(row)
    entry["dense_score"]     = dense_score
    entry["sparse_score"]    = sparse_score
    entry["retrieval_score"] = final_score
    entry["retrieval_mode"]  = "hybrid" | "dense"
    entry["pdf_payload"]     = json.loads(...)   # 反序列化 JSON 字符串
    entry["video_payload"]   = json.loads(...)
    results.append(entry)
```

**输出：** `results: list[dict]`，每条包含：

```
id, content, document, source_type, anchor_id,
pdf_payload (dict), video_payload (dict),
distance, dense_score, sparse_score, retrieval_score, retrieval_mode
```

---

### 3.4 — 发射检索结果信号

```python
self.timings_ready.emit({
    "stage": "retrieval",
    "embed_ms": ..., "vector_ms": ..., "retrieval_total_ms": ...,
    "retrieval_mode": ..., "top_k": ..., "hits": len(results),
})

self.context_retrieved.emit(_build_display_results(results))
```

**`_build_display_results()` — 跨模态扩展：**

```python
display_results = list(results)   # 复制原始结果

# 查询 SQLite 中的 PDF ↔ 视频绑定关系
mappings = SQLiteStore().list_cross_modal()

# 对每个命中结果的 anchor_id，若有绑定的视频片段
# → 追加一条合成的 video 类型结果卡片
display_results.append({
    "source_type": "video",
    "video_payload": {"file": ..., "start": ..., "end": ...},
    ...
})
```

---

## 阶段 4 — UI 线程接收结果：`_on_context()`

**文件：** `app/views/spotlight_window.py:425–443`

```python
def _on_context(self, results: list) -> None:
    self._clear_cards()
    self._context_results = list(results or [])
    if not results:
        self._render_preview(...)
        return                     # 无结果直接退出

    for idx, r in enumerate(results):
        card = build_card(r, parent=self._cards_widget)   # 创建卡片 Widget
        card.set_best_match(idx == 0)                     # 第一张卡金色样式
        card.clicked.connect(lambda c=card, result=r:
            self._select_result_card(c, result))          # 点击 → 选中
        self._cards_layout.insertWidget(...)
        self._result_cards.append(card)
        if idx == 0:
            self._select_result_card(card, r)             # 自动选中第一张
    self._update_body_visibility()
```

---

## 阶段 5 — 构建结果卡片：`SearchResultCard`

**文件：** `app/views/result_cards.py:19–76`

每张卡片从 `search_result_card.ui` 加载布局，填充四个 QLabel：

| 控件名 | 显示内容 | 数据来源 |
|---|---|---|
| `titleLabel` | heading_path 最后一段（≤72字符） | `pdf_payload.heading_path` |
| `sourceTagLabel` | "MD" / "PDF" / "VIDEO" / "DOC" | `source_type` |
| `metaLabel` | `文件名 · 第N页 · block_type · § 父级标题` | `pdf_payload` 多字段 |
| `summaryLabel` | semantic_description 或 content 摘要（≤110字） | `pdf_payload.metadata` |

`titleLabel` 和 `metaLabel` 均设置完整 `heading_path` 为 tooltip，鼠标悬停可查看完整路径。

---

## 阶段 6 — 选中结果卡片：`_select_result_card()`

**文件：** `app/views/spotlight_window.py:494–506`

```python
def _select_result_card(self, card, result) -> None:
    # 取消前一张选中状态（移除 QSS "selected" property）
    self._selected_card.set_selected(False)
    # 设置新选中
    self._selected_card   = card
    self._selected_result = result
    card.set_selected(True)
    # 更新右侧面板
    self._update_results_header()    # "搜索结果 · N 条"
    self._scroll_card_into_view(card)
    self._sync_selected_result_ui()  # 更新标题/meta/按钮状态
    self._render_preview(streaming=self._cursor_timer.isActive())
```

**`_sync_selected_result_ui()` 按钮状态逻辑（L558–575）：**
- `copyReferenceButton` — 有结果时始终启用
- `openSourceButton` — 仅当 `_resolve_source_path(result) is not None` 时启用

---

## 阶段 7 — 渲染预览面板：`_render_preview()`

**文件：** `app/views/spotlight_window.py:535–556`

每次调用完整重建 HTML 并调用 `_answer.setHtml()`。触发时机：

- 选中新卡片时（立即）
- 每收到一个流式 token（`_on_token`）
- 每 500ms 光标闪烁（`_blink_cursor`）
- 生成完成后（`_on_finished`，最后一次）

**`_build_preview_document()` 构建的两段式 HTML（L1018–1093）：**

**段落一 — 回答区：**
```html
<section class="preview-block preview-answer">
  <div class="preview-label">回答</div>
  <p>...流式 token 累积文本...<span>▋</span>  <!-- 闪烁光标 --></p>
</section>
```

**段落二 — 命中内容区（选中卡片）：**
```html
<section class="preview-block">
  <div class="preview-label">命中内容</div>
  <!-- heading_path 面包屑 -->
  <p style="font-size:11px;">快速入门 › 2.2 工控机IP配置教程 › c. 在弹出的新窗口中</p>
  <!-- semantic_description 斜体 -->
  <p style="font-style:italic;">教程/操作步骤块，共约5个步骤。</p>
  <!-- display_content（含图片内嵌、关键词高亮） -->
  <p>- i. 点击 <mark>IP</mark> 地址...</p>
  <img src="file:///.../_page_7_Picture_7.jpeg">
  <!-- 跳转链接 -->
  <p><a href="file:///...UV.chunks.md#...">📄 在文档中查看：UV.chunks.md · 第0页</a></p>
</section>
```

**图片路径解析 `_resolve_local_src()`（L723–739）：**

```
src 是 URL？ → 直接使用
src 是绝对路径？ → 转为 file:// URI
src 是相对路径？ → 按顺序在以下目录搜索：
  1. 项目根目录
  2. data/
  3. data/exports/
  4. data/exports/markdown/
  5. data/exports/pdf/
  6. data/exports/pdf/<各子目录>（含 marker_output 等）
  7. data/eval/
```

**文档文件定位 `_resolve_source_path()`（L953–1015）：**

```
1. data/exports/markdown/<stem>.chunks.md  ← 优先（人工可读 L1 文件）
2. data/exports/markdown/<stem>.md
3. data/exports/pdf/<含stem的子目录>/*.md   ← marker 输出目录
4. data/<file_name>                         ← 原始源文件（兜底）
```

---

## 阶段 8 — LLM 处理

### 8.1 — 无检索结果

```python
answer = "（未检索到相关内容，请先导入知识文档）"
self.token_generated.emit(answer)    # 整条作为一个 token 发出
_record_qa_memory(mode="no_results")
self.generation_finished.emit(True)
```

### 8.2 — LLM 已禁用（`cfg.llm.enabled = false`）

```python
answer = _build_retrieval_answer(results, "本地 LLM 当前已禁用…")
self.token_generated.emit(answer)
_record_qa_memory(mode="retrieval_only")
self.generation_finished.emit(True)
```

### 8.3 — LLM 加载：`LLMManager.load()`

**文件：** `app/models/llm_manager.py:54–87`

```
llm.load() 流程：
├─ llm.enabled == False → return False
├─ LlamaServerManager().is_running → return True（已就绪，跳过）
├─ 解析 model_path（优先 llama_server.model_path，次选 llm.model_path）
└─ LlamaServerManager().ensure_started(model_path)
   → 启动 llama-server 子进程
   → 轮询 GET /health，最多等 startup_timeout=180s
   → 返回 True/False
```

### 8.4 — LLM 加载失败

```python
answer = _build_retrieval_answer(results, f"本地 LLM 加载失败…\n原因：{detail}")
self.token_generated.emit(answer)
_record_qa_memory(mode="llm_load_failed")
self.generation_finished.emit(True)
```

### 8.5 — 构建提示词：`_build_prompt()`

格式：
```
以下是从知识库中检索到的相关内容（共 N 条，编号 [1]-[N]）：

[1] [Path: 快速入门 > 2.2 工控机IP配置教程 > c. 在弹出的新窗口中] [Type: tutorial]
教程/操作步骤块，主题为【c. 在弹出的新窗口中】，共约5个步骤。
- i. 点击"使用下面的 IP 地址"。
- ii. 依次填写：...
    （来源 chunk: markdown:uv-348ad9a3:p0:928a40...）

[2] [Path: ...] [Type: parameter_table]
...

---
请严格遵守以下规则回答问题：
1. 只能使用上方检索内容作答...
2. 回答时用 [序号] 标注来源...
3. 内容不足时直说"没有找到足够信息"...
4. 操作步骤类问题输出分步答案...
5. 参数类问题优先结合参数表和图片...
6. 图片引用原样保留...
7. 数值/路径/参数名忠实原文...
8. 用中文回答。

问题：<query>

回答：
```

### 8.6 — 流式生成：`LLMManager.generate()`

**文件：** `app/models/llm_manager.py:97–141`

```
generate(prompt, stream=True)
├─ _prompt_to_messages(prompt)
│  → [{"role": "system", "content": "你是一个知识库问答助手..."},
│     {"role": "user",   "content": <完整 RAG 提示词>}]
│
├─ LLMHttpClient(base_url).chat_stream(messages)
│  → POST http://127.0.0.1:8000/v1/chat/completions
│  → stream=True（Server-Sent Events）
│  → 每个 SSE 事件解析为一个 token 字符串
│  → yield token
│
└─ 若 stream 无输出 → 降级调用 chat_once_with_meta()
   → POST（非流式）→ 整段文本作为单个 token yield
```

**Worker 中的生成循环：**

```python
for token in llm.generate(prompt, stream=True):
    if self._cancelled: break        # 检查取消标志
    self.token_generated.emit(token) # 发送到 UI
    answer_parts.append(token)
```

---

## 阶段 9 — token 到达 UI：`_on_token()`

**文件：** `app/views/spotlight_window.py:412–418`

每收到一个 token：

```python
def _on_token(self, token: str) -> None:
    self._answer_plain += token          # 追加到累积文本
    self._render_preview(streaming=True) # 重建完整 HTML，setHtml()
    self._answer.verticalScrollBar().setValue(maximum)  # 自动滚到底部
```

同时 `_cursor_timer` 每 500ms 触发 `_blink_cursor()`：

```python
def _blink_cursor(self) -> None:
    self._cursor_visible = not self._cursor_visible
    if self._cursor_timer.isActive():
        self._render_preview(streaming=True)  # 切换 ▋ 可见性
```

---

## 阶段 10 — 记录 QA 记忆：`QAMemoryRecorder.record()`

**文件：** `app/models/qa_memory.py:21–50`

生成完成后（非取消）自动记录：

```python
entry = {
    "id":        uuid4(),
    "created_at": ISO时间戳,
    "mode":       "llm" | "no_results" | "retrieval_only" | "llm_load_failed",
    "query":      原始问题,
    "answer":     完整回答,
    "retrieved_context": [  # 每条结果规范化，content 截断到 1200 字符
        {"rank": 1, "anchor_id": ..., "distance": ..., "pdf_payload": ...},
        ...
    ],
    "human_review": {       # 空白待填写
        "expected_answer": "", "verdict": "unreviewed",
        "accuracy_score": None, ...
    },
}
# 追加写入两个文件：
# data/eval/qa_dialogue_memory.jsonl  （每行一条 JSON）
# data/eval/qa_dialogue_memory.md     （格式化 Markdown）
```

---

## 阶段 11 — 生成完成：`_on_finished()`

**文件：** `app/views/spotlight_window.py:470–474`

```python
def _on_finished(self, success: bool) -> None:
    self._cursor_timer.stop()              # 停止闪烁计时器
    self._render_preview(streaming=False)  # 最终渲染（无光标）
    self._answer.verticalScrollBar().setValue(0)  # 滚回顶部
    self._stop_btn.hide()                  # 隐藏取消按钮
```

`success=True`：正常完成；`success=False`：被取消或出错（UI 行为相同）。

---

## 阶段 12 — 错误路径：`_on_error()`

**文件：** `app/views/spotlight_window.py:476–482`

由 `error_occurred` 信号触发（OOM 或 run() 中未捕获异常）：

```python
def _on_error(self, msg: str) -> None:
    self._cursor_timer.stop()
    self._answer_plain = f"[错误] {msg}"   # 将错误消息作为"回答"显示
    self._render_preview(streaming=False)
    self._stop_btn.hide()
    self._timing_label.setText("检索失败")
```

注意：`error_occurred` 之后 `generation_finished` 也会被发出，`_on_finished()` 随后再次调用 `_render_preview()`，但 `_answer_plain` 已是错误信息，结果不变。

---

## 阶段 13 — 用户后续交互

### 13a — 键盘上下键切换结果

**文件：** `app/views/spotlight_window.py:598–605, 520–533`

`eventFilter` 捕获 `_input` 上的上下键：

```python
if event.key() == Qt.Key_Down: self._move_result_selection(+1)
if event.key() == Qt.Key_Up:   self._move_result_selection(-1)

def _move_result_selection(self, step):
    next_idx  = clamp(current + step, 0, len(cards)-1)
    next_card = self._result_cards[next_idx]
    self._select_result_card(next_card, self._context_results[next_idx])
```

→ 触发 `_select_result_card()` → `_sync_selected_result_ui()` + `_render_preview()`

### 13b — "在文档中查看"按钮

**文件：** `app/views/spotlight_window.py:583–592`

```python
def _open_selected_source(self) -> None:
    path = _resolve_source_path(self._selected_result)
    if path is None: return
    os.startfile(str(path))   # Windows：用默认程序打开
```

按 `_resolve_source_path()` 优先级依次查找：
1. `data/exports/markdown/<stem>.chunks.md`
2. `data/exports/markdown/<stem>.md`
3. `data/exports/pdf/<stem目录>/*.md`
4. `data/<原始文件名>`

### 13c — "复制引用"按钮

**文件：** `app/views/spotlight_window.py:577–581`

```python
citation = _result_reference_text(result)
# 格式："2.2 工控机IP配置教程 | guide.pdf · 第12页 · tutorial | anchor_id=abc-123"
QApplication.clipboard().setText(citation)
```

### 13d — 取消按钮

**文件：** `search_controller.py:39–41`、`inference_worker.py:699–700`

```python
# UI 点击取消按钮 → _search_ctrl.cancel()
def cancel(self):
    self._worker.cancel()        # 设置 _cancelled = True

# Worker 生成循环中每个 token 后检查：
for token in llm.generate(...):
    if self._cancelled: break    # 完成当前 token 后退出
# 取消后：不记录 QA 记忆，generation_finished.emit(False)
```

---

## 完整信号时序图

```
[UI 主线程]                                    [Worker 子线程]
────────────────────────────────────────────────────────────────
用户按 Enter
  → _on_search()
    → _search_ctrl.search(query)
      → InferenceWorker(query).start()
                                   ─────→ run() 开始
                                          BGE-M3 编码 query（懒加载）
                                          ChromaStore 全表扫描 + 混合打分
                                   ←───── timings_ready（stage=retrieval）
  ← _on_timings() — 更新计时标签
                                   ←───── context_retrieved(results)
  ← [Controller._on_context 过滤]
  ← _on_context(results)
      build_card() × N 张
      自动选中 card[0]
      _sync_selected_result_ui()
      _render_preview(streaming=True)
                                          [无结果] token_generated(提示文字)
                                          [LLM禁用] token_generated(检索结果)
                                          LLMManager.load() — 启动 llama-server
                                          [加载失败] token_generated(错误+检索结果)
                                          _build_prompt(query, results)
                                          LLMManager.generate(prompt, stream=True)
                                            → POST /v1/chat/completions (SSE)
                                   ←───── token_generated(token₁)
  ← _on_token(token₁)
      _answer_plain += token₁
      _render_preview(streaming=True)
  ← _blink_cursor() [每500ms]      ←───── token_generated(token₂)
  ← _on_token(token₂) ...                 ...（重复直到生成完成）
                                   ←───── timings_ready（stage=complete）
  ← _on_timings() — 更新完整耗时         QAMemoryRecorder.record()
                                   ←───── generation_finished(True/False)
  ← _on_finished()
      _cursor_timer.stop()
      _render_preview(streaming=False)   最终渲染
      scrollbar → 顶部
      _stop_btn.hide()
────────────────────────────────────────────────────────────────
```

---

## 全路径决策树

```
InferenceWorker.run()
│
├─ EmbedManager 抛出 RuntimeError（模型文件缺失/CUDA错误）
│    └─ error_occurred.emit(str(e))
│       generation_finished.emit(False)
│
├─ ChromaStore.query() 返回 []（数据库为空或全被过滤）
│    └─ token_generated.emit("（未检索到相关内容…）")
│       mode="no_results"
│       generation_finished.emit(True)
│
├─ 有结果，但 cfg.llm.enabled == False
│    └─ token_generated.emit(_build_retrieval_answer())
│       mode="retrieval_only"
│       generation_finished.emit(True)
│
├─ 有结果，LLM 启用，LLMManager.load() 失败
│    └─ token_generated.emit(_build_retrieval_answer("加载失败…"))
│       mode="llm_load_failed"
│       generation_finished.emit(True)
│
├─ 有结果，LLM 加载成功，进入生成循环：
│    ├─ 正常流式输出 token₁…tokenN
│    │    └─ timings_ready(complete)
│    │       QAMemoryRecorder.record(mode="llm")
│    │       generation_finished.emit(True)
│    │
│    ├─ 用户取消（_cancelled=True）
│    │    └─ 跳过 QA 记忆
│    │       generation_finished.emit(False)
│    │
│    ├─ LLMHttpClient 抛出异常（网络/协议错误）
│    │    ├─ 若已有部分 answer_text → 正常结束
│    │    └─ 若 answer_text 为空 → token_generated.emit(_build_retrieval_answer("生成失败…"))
│    │       generation_finished.emit(True)
│    │
│    └─ 生成结果为空字符串（模型返回无内容）
│         └─ token_generated.emit(_build_retrieval_answer("没有返回可见答案…"))
│            generation_finished.emit(True)
│
└─ MemoryError（任何位置）
     LLMManager().unload(reason="oom")
     error_occurred.emit("内存不足…")
     generation_finished.emit(False)
```

---

## 各阶段边界数据结构

| 阶段 | 对象 | 关键字段 |
|---|---|---|
| encode 后 | `query_payload: dict` | `dense: list[float]`（1024维）、`sparse: dict[str,float]\|None` |
| chroma.query 后 | `result: dict` | `id, content, source_type, anchor_id, pdf_payload(dict), distance, dense_score, sparse_score, retrieval_score, retrieval_mode` |
| _build_display_results 后 | 增广列表 | 同上 + 合成的 `source_type="video"` 卡片 |
| SearchResultCard 内 | `_data: dict` | 完整 result dict；构建时提取 title/tag/meta/summary |
| LLM prompt | `prompt: str` | N 个编号段落（含 path/type/desc/content）+ 8条规则 + 问题 |
| 生成后 | `answer_text: str` | 流式 token 拼接结果，或降级检索格式化文本 |
| QA 记录 | `entry: dict` | uuid、时间戳、mode、query、answer、规范化命中列表、空白人工审核字段 |

---

## 架构注意事项

1. **ChromaStore 全表扫描**  
   `SELECT * FROM vectors` 无 WHERE 条件，每次查询读取全部向量，性能随数据库大小线性下降。

2. **EmbedManager 懒加载**  
   BGE-M3 在首次查询时才加载，第一次查询需额外承担模型加载时间（~2–5秒）。

3. **`_render_preview` 调用频率**  
   每收到一个 token 调用一次，高速生成时可能每秒几十次调用 `setHtml()`，存在性能瓶颈。

4. **信号线程安全**  
   Qt 跨线程信号默认使用队列连接（`Qt.QueuedConnection`），所有 UI 槽在主线程事件循环中执行，无数据竞争。

5. **`_cancelled` 标志**  
   是普通 Python `bool`，依赖 CPython GIL 保证跨线程可见性，严格意义上不是线程安全的（但在 CPython 下实际有效）。
