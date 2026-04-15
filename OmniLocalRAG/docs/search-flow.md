# OmniLocalRAG — 用户检索完整执行流程

> 文档版本：2026-04-15（Gemma 驱动全链路架构）
> 覆盖范围：用户按下 Enter 到回答渲染完毕的全链路，含所有分支路径

---

## 总体架构

```
[UI 主线程]                         [Worker 子线程 QThread]
────────────────────────────────────────────────────────────
SpotlightWindow
  └─ SearchController
       └─ InferenceWorker.run()
            ├─ LLMManager.load()           → 确保 llama-server 启动
            ├─ GemmaRouter.search()        → 多轮自证搜索
            │    ├─ route()                → 意图路由 + 查询改写
            │    │    └─ LLMHttpClient.chat_once() → JSON {category, standalone_query}
            │    ├─ locate()               → 文档定位（heading_path 选择）
            │    │    ├─ ChromaStore.list_headings(category)
            │    │    └─ LLMHttpClient.chat_once() → JSON {selected_indices}
            │    ├─ validate()             → 验证定位合理性
            │    │    └─ LLMHttpClient.chat_once() → JSON {valid, reason}
            │    └─ [重试] → 回到 locate()（最多 max_validate_retries 轮）
            ├─ _build_prompt()             → 构建含对话历史的 RAG 提示词
            └─ LLMManager.generate()       → 流式 token 迭代器
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

**文件：** `app/views/spotlight_window.py`

```python
self._search_ctrl = SearchController(self)
self._setup_flags()    # Qt 窗口标志：无边框、置顶、透明背景
self._build_ui()       # 加载 spotlight_window.ui，绑定所有子控件
self._connect_signals()# 连接所有信号/槽
self._apply_styles()   # 读取 assets/styles/main.qss
```

**`_connect_signals()` 完整连接表：**

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
| `_answer.anchorClicked` | `_on_anchor_clicked` |

---

## 阶段 1 — 用户按 Enter：`_on_search()`

**文件：** `app/views/spotlight_window.py`

```
输入：query: str（用户输入的原始文本）
```

执行步骤：

| 操作 |
|---|
| `query = self._input.text().strip()` |
| 空字符串直接返回（守卫） |
| `self._active_query = query` — 存储供后续 `_render_preview` 使用 |
| 清空 `_answer_plain`、`_context_results`、`_selected_result` |
| `_answer.clear()` — 清空 QTextBrowser |
| `_clear_cards()` — 删除所有结果卡片 Widget |
| `_render_preview(streaming=False)` — 渲染空状态 HTML |
| 显示"正在检索…"标签、显示取消按钮 |
| `_cursor_timer.start()` — 启动 500ms 光标闪烁计时器 |
| `_search_ctrl.search(query)` — 交给控制器 |
| `MemoryWatcher().reset()` — 重置内存监控 |

---

## 阶段 2 — 控制器启动 Worker：`SearchController.search()`

**文件：** `app/controllers/search_controller.py`

```python
def search(self, query: str) -> None:
    if self._worker and self._worker.isRunning():
        self._worker.cancel()    # 取消上一次未完成的检索
        self._worker.wait(500)   # 最多等 500ms 让旧线程退出

    self._worker = InferenceWorker(query)   # 创建新 Worker
    # 绑定 5 个信号
    self._worker.start()    # 启动 QThread，调用 run()
```

---

## 阶段 3 — Worker 线程：`InferenceWorker.run()`

**文件：** `app/workers/inference_worker.py`

整个 `run()` 被 `try/except` 包裹（异常处理见阶段 7）。

### 3.1 — 初始化

```python
overall_t0 = time.perf_counter()         # 全程计时起点
chroma = ChromaStore()                   # 获取单例
llm    = LLMManager()                    # 获取单例
memory = _ConversationMemory()           # 对话滑窗（最近 5 轮）
router = GemmaRouter()                   # 多轮自证路由引擎
llm_was_loaded = llm.is_loaded           # 记录 llama-server 是否已就绪
```

---

### 3.2 — LLM 加载：`LLMManager.load()`

```python
llm_loaded_now = llm.load()
```

若加载失败，进入**降级模式**：启发式路由 + SQL 文本搜索。

---

### 3.3 — 多轮自证搜索：`GemmaRouter.search()`

**文件：** `app/models/gemma_router.py`

```python
search_result = router.search(
    query=self.query,
    memory=memory.recent(),
    chroma=chroma,
)
```

**完整流程：**

```
GemmaRouter.search()
│
├─ Phase 1: route() — 意图路由
│   ├─ router.enabled == False → 启发式 fallback
│   ├─ llm.enabled == False → 启发式 fallback
│   ├─ LLM 调用失败 → 启发式 fallback
│   └─ LLM 路由成功 → {category, standalone_query, router_source="llm"}
│
├─ Phase 2: locate() — 文档定位（最多重试 max_validate_retries 轮）
│   ├─ ChromaStore.list_headings(category) → 获取该分类下所有 heading_path
│   ├─ LLM 选择最匹配的 heading_path → JSON {selected_indices}
│   ├─ ChromaStore.get_by_heading(heading_path) → 获取对应 chunks
│   └─ LLM 定位失败 → fallback: ChromaStore.search_text()
│
├─ Phase 3: validate() — 验证定位
│   ├─ LLM 判断定位内容是否足以回答问题 → JSON {valid, reason, missing}
│   └─ valid == False → 扩大范围重试（回到 Phase 2）
│
└─ 返回: {category, standalone_query, chunks, heading_paths,
│         router_source, attempts, validated, timings}
```

#### Phase 1: route() — 意图路由

**Few-shot Prompt（`router.few_shot_examples=true` 时）：**

```
你是 OmniLocalRAG 的意图路由器。请基于对话历史和当前问题输出严格 JSON，不要输出解释。
允许的 category 只有：tech_manual, business_sop, company_intro, parameter, process, project_code, general。
输出格式：{"category":"...","standalone_query":"..."}

规则：
- standalone_query 必须是独立可检索问题，将代词替换为实际指代内容。
- 如果问题偏安装、接口、技术排障、参数配置，category=tech_manual。
- 如果问题偏流程制度、业务SOP，category=business_sop。
- 如果问题偏公司介绍、概况，category=company_intro。
- 如果问题偏参数表、规格数据，category=parameter。
- 如果问题偏操作流程、步骤，category=process。
- 如果问题涉及项目代号，category=project_code。
- 不确定时 category=general。

示例：
...

对话历史：
{history_text}

当前问题：{query}
输出：
```

**启发式 fallback：**

```python
# 查询改写：检测代词，拼接上一轮查询
if any(marker in query for marker in _FOLLOWUP_MARKERS):
    condensed = f"基于"{last_standalone_query}"：{query}"

# 分类：关键词匹配
if _contains_any(text, _TECH_KEYWORDS): category = "tech_manual"
elif _contains_any(text, _BUSINESS_KEYWORDS): category = "business_sop"
else: category = "general"
```

#### Phase 2: locate() — 文档定位

**定位 Prompt：**

```
以下是 [category] 分类下的文档标题索引：
1. 快速入门 > 工控机IP配置教程 (3 chunks)
2. 快速入门 > 工控机IP配置教程 > a. 打开网络连接 (1 chunks)
3. 系统参数 > 网络参数 > 速度和双工 (2 chunks)
...

用户问题：{standalone_query}

请选择最相关的标题编号（可多选），输出 JSON：
{"selected_indices": [1, 3], "reason": "..."}
只输出 JSON，不要输出解释。
```

**定位后获取 chunks：**

```python
for heading in selected_headings:
    chunks = chroma.get_by_heading(heading["heading_path"], category=category)
    all_chunks.extend(chunks)
```

#### Phase 3: validate() — 验证定位

**验证 Prompt：**

```
用户问题：{query}

定位到的内容：
[1] 快速入门 > 工控机IP配置教程
- i. 点击"使用下面的 IP 地址"。
- ii. 依次填写：...

这些内容是否足以回答用户问题？
输出 JSON：{"valid": true/false, "reason": "...", "missing": "..."}
只输出 JSON，不要输出解释。
```

**重试策略：**
- 验证失败且重试次数 < max_validate_retries → 回到 locate() 扩大范围
- 扩大范围：先尝试 category="general"，再尝试文本搜索补充

---

### 3.4 — 发射检索结果信号

```python
self.timings_ready.emit({
    "stage": "retrieval",
    "route_ms": ..., "locate_ms": ..., "validate_ms": ...,
    "retrieval_total_ms": ..., "top_k": ..., "hits": len(results),
    "attempts": ..., "validated": ...,
})

self.context_retrieved.emit(_build_display_results(results))
```

**`_build_display_results()` — 跨模态扩展：**

```python
display_results = list(results)

# 查询 SQLite 中的 PDF ↔ 视频绑定关系
mappings = SQLiteStore().list_cross_modal()

# 对每个命中结果的 anchor_id，若有绑定的视频片段
# → 追加一条合成的 video 类型结果卡片
```

---

## 阶段 4 — UI 线程接收结果：`_on_context()`

**文件：** `app/views/spotlight_window.py`

```python
def _on_context(self, results: list) -> None:
    self._clear_cards()
    self._context_results = list(results or [])
    if not results:
        self._render_preview(...)
        return

    for idx, r in enumerate(results):
        card = build_card(r, parent=self._cards_widget)
        card.set_best_match(idx == 0)
        card.clicked.connect(lambda c=card, result=r:
            self._select_result_card(c, result))
        self._cards_layout.insertWidget(...)
        self._result_cards.append(card)
        if idx == 0:
            self._select_result_card(card, r)
    self._update_body_visibility()
```

---

## 阶段 5 — LLM 生成

### 5.1 — 无检索结果

```python
answer = "（未检索到相关内容，请先导入知识文档）"
self.token_generated.emit(answer)
_record_qa_memory(mode="no_results")
self.generation_finished.emit(True)
```

### 5.2 — LLM 已禁用

```python
answer = _build_retrieval_answer(results, "本地 LLM 当前已禁用…")
self.token_generated.emit(answer)
_record_qa_memory(mode="retrieval_only")
self.generation_finished.emit(True)
```

### 5.3 — 构建提示词：`_build_prompt()`

格式：
```
以下是最近 N 轮对话历史：
1. Q: <上一轮问题>
   A: <上一轮回答前200字>
...

---

以下是从知识库中检索到的相关内容（共 N 条，编号 [1]-[N]）：

[1] [Category: tech_manual] [File: guide.pdf] [Path: 快速入门 > 2.2 工控机IP配置教程 > c. 在弹出的新窗口中] [Type: tutorial]
教程/操作步骤块，主题为【c. 在弹出的新窗口中】，共约5个步骤。
- i. 点击"使用下面的 IP 地址"。
- ii. 依次填写：...
    （来源 chunk: markdown:uv-348ad9a3:p0:928a40...）

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
9. 如果问题包含代词（它、这个、那个等），请结合对话历史理解指代。

问题：<standalone_query>

回答：
```

### 5.4 — 流式生成：`LLMManager.generate()`

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
```

---

## 阶段 6 — token 到达 UI：`_on_token()`

每收到一个 token：

```python
def _on_token(self, token: str) -> None:
    self._answer_plain += token          # 追加到累积文本
    self._render_preview(streaming=True) # 重建完整 HTML，setHtml()
    self._answer.verticalScrollBar().setValue(maximum)  # 自动滚到底部
```

---

## 阶段 7 — 生成完成与错误处理

### 正常完成

```python
def _on_finished(self, success: bool) -> None:
    self._cursor_timer.stop()
    self._render_preview(streaming=False)
    self._answer.verticalScrollBar().setValue(0)
    self._stop_btn.hide()
```

### 错误路径

```python
def _on_error(self, msg: str) -> None:
    self._cursor_timer.stop()
    self._answer_plain = f"[错误] {msg}"
    self._render_preview(streaming=False)
    self._stop_btn.hide()
    self._timing_label.setText("检索失败")
```

---

## 阶段 8 — 用户后续交互

### 8a — 点击溯源引用链接

回答中的 `[1]`、`[2]` 等引用标记被 `_link_citations()` 渲染为可点击链接：

```python
# anchor:// 链接 → 滚动到对应结果卡片并选中
if url_str.startswith("anchor://"):
    anchor_id = url_str[len("anchor://"):]
    for idx, result in enumerate(self._context_results):
        if result.anchor_id == anchor_id:
            self._select_result_card(self._result_cards[idx], result)

# video:// 链接 → 打开视频播放器
elif url_str.startswith("video://"):
    clip_id = url_str[len("video://"):]
    # 找到对应 video 结果 → os.startfile() 打开
```

### 8b — 键盘上下键切换结果

`eventFilter` 捕获 `_input` 上的上下键：

```python
if event.key() == Qt.Key_Down: self._move_result_selection(+1)
if event.key() == Qt.Key_Up:   self._move_result_selection(-1)
```

### 8c — "在文档中查看"按钮

```python
def _open_selected_source(self) -> None:
    path = _resolve_source_path(self._selected_result)
    if path is None: return
    os.startfile(str(path))
```

### 8d — "复制引用"按钮

```python
citation = _result_reference_text(result)
QApplication.clipboard().setText(citation)
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
                                          ┌─ LLMManager.load()
                                          │   └─ LlamaServerManager().ensure_started()
                                          │       → 启动 llama-server 子进程
                                          │       → 轮询 GET /health
                                          │
                                          ├─ GemmaRouter.search()
                                          │   ├─ route()
                                          │   │   ├─ Few-shot Prompt 构建
                                          │   │   ├─ LLMHttpClient.chat_once() (非流式)
                                          │   │   └─ JSON 解析 → {category, standalone_query}
                                          │   │
                                          │   ├─ locate()
                                          │   │   ├─ ChromaStore.list_headings(category)
                                          │   │   ├─ LLMHttpClient.chat_once() → {selected_indices}
                                          │   │   └─ ChromaStore.get_by_heading() → chunks
                                          │   │
                                          │   ├─ validate()
                                          │   │   ├─ LLMHttpClient.chat_once() → {valid, reason}
                                          │   │   └─ [验证失败] → 回到 locate()（扩大范围）
                                          │   │
                                          │   └─ 返回 {chunks, heading_paths, attempts, validated}
                                          │
                                   ←───── timings_ready（stage=retrieval）
  ← _on_timings() — "路由 Xms 定位 Xms 验证 Xms 分类 tech_manual 尝试 1 轮 ✓"
                                   ←───── context_retrieved(results)
  ← _on_context(results)
      build_card() × N 张
      自动选中 card[0]
                                          _build_prompt(standalone_query, results, category, memory.recent())
                                          LLMManager.generate(prompt, stream=True)
                                            → POST /v1/chat/completions (SSE)
                                   ←───── token_generated(token₁)
  ← _on_token(token₁)
      _answer_plain += token₁
      _link_citations() — [1] → 可点击链接
      _render_preview(streaming=True)
  ← _blink_cursor() [每500ms]      ←───── token_generated(token₂)
  ← _on_token(token₂) ...                 ...（重复直到生成完成）
                                   ←───── timings_ready（stage=complete）
  ← _on_timings() — 更新完整耗时         QAMemoryRecorder.record()
                                         memory.append(query, answer, category, standalone_query)
                                         _log_rag_trace()
                                   ←───── generation_finished(True/False)
  ← _on_finished()
      _cursor_timer.stop()
      _render_preview(streaming=False)   最终渲染（含可点击引用 + 溯源信息）
      scrollbar → 顶部
      _stop_btn.hide()
────────────────────────────────────────────────────────────────
```

---

## 全路径决策树

```
InferenceWorker.run()
│
├─ LLMManager.load() 失败 → 降级模式
│   ├─ 启发式路由 → ChromaStore.search_text()
│   ├─ 无结果 → "未检索到相关内容"
│   └─ 有结果 → _build_retrieval_answer("加载失败…")
│       mode="llm_load_failed"
│       generation_finished.emit(True)
│
├─ GemmaRouter.search() — 多轮自证搜索
│   ├─ route()
│   │   ├─ router.enabled == False → 启发式 fallback
│   │   ├─ llm.enabled == False → 启发式 fallback
│   │   ├─ LLM 路由成功 → {category, standalone_query, router_source="llm"}
│   │   └─ LLM 路由失败 → 启发式 fallback
│   │
│   ├─ locate()
│   │   ├─ LLM 选择 heading_path → ChromaStore.get_by_heading() → chunks
│   │   └─ LLM 定位失败 → ChromaStore.search_text() → chunks
│   │
│   ├─ validate()
│   │   ├─ valid == True → 使用当前 chunks
│   │   └─ valid == False → 重试 locate()（扩大范围）
│   │       ├─ category != "general" → 改为 general 重试
│   │       └─ category == "general" → 补充文本搜索
│   │
│   └─ 返回 {chunks, heading_paths, attempts, validated}
│
├─ 无检索结果
│   └─ token_generated.emit("（未检索到相关内容…）")
│       mode="no_results"
│
├─ 有结果，cfg.llm.enabled == False
│   └─ token_generated.emit(_build_retrieval_answer())
│       mode="retrieval_only"
│
├─ 有结果，LLM 生成循环：
│   ├─ 正常流式输出 token₁…tokenN
│   │    └─ QAMemoryRecorder.record(mode="llm")
│   │       memory.append(query, answer, category, standalone_query)
│   │       _log_rag_trace()
│   │       generation_finished.emit(True)
│   │
│   ├─ 用户取消（_cancelled=True）
│   │    └─ 跳过 QA 记忆
│   │       generation_finished.emit(False)
│   │
│   ├─ LLMHttpClient 抛出异常
│   │    └─ 若 answer_text 为空 → _build_retrieval_answer("生成失败…")
│   │
│   └─ 生成结果为空字符串
│        └─ _build_retrieval_answer("没有返回可见答案…")
│
└─ MemoryError
     LLMManager().unload(reason="oom")
     error_occurred.emit("内存不足…")
     generation_finished.emit(False)
```

---

## 各阶段边界数据结构

| 阶段 | 对象 | 关键字段 |
|---|---|---|
| 路由后 | `route: dict` | `category: str`、`standalone_query: str`、`router_source: "llm"\|"heuristic"` |
| 定位后 | `locate: dict` | `heading_paths: list[str]`、`chunks: list[dict]`、`confidence: float` |
| 验证后 | `validate: dict` | `valid: bool`、`reason: str`、`missing: str` |
| search 后 | `search_result: dict` | `category, standalone_query, chunks, heading_paths, router_source, attempts, validated, timings` |
| ChromaStore 行 | `result: dict` | `id, content, source_type, anchor_id, category, heading_path, pdf_payload(dict), video_payload(dict)` |
| _build_display_results 后 | 增广列表 | 同上 + `route_category, query_source, standalone_query` + 合成的 `source_type="video"` 卡片 |
| LLM prompt | `prompt: str` | 对话历史段落 + N 个编号段落（含 category/path/type/desc/content）+ 9条规则 + 问题 |
| 生成后 | `answer_text: str` | 流式 token 拼接结果，`[1]`/`[2]` 渲染为可点击 `anchor://`/`video://` 链接 |
| QA 记录 | `entry: dict` | uuid、时间戳、mode、query、answer、规范化命中列表、空白人工审核字段 |
| 对话记忆 | `_ConversationMemory._qa` | deque(maxlen=5)，每条含 query/answer/category/standalone_query |

---

## ChromaStore SQL 查询方法

| 方法 | 用途 | SQL |
|---|---|---|
| `list_by_category(category)` | 列出指定分类的所有 chunk | `SELECT * FROM vectors WHERE category = ?` |
| `list_headings(category)` | 列出所有 heading_path（供 LLM 选择） | `SELECT heading_path, category, COUNT(*) FROM vectors WHERE heading_path != '' GROUP BY heading_path` |
| `get_by_heading(heading_path, category)` | 按 heading_path 前缀获取 chunks | `SELECT * FROM vectors WHERE heading_path LIKE ?` |
| `search_text(query, category, limit)` | LIKE 文本搜索（降级/补充） | `SELECT * FROM vectors WHERE content LIKE ? AND heading_path LIKE ?` |
| `list_by_source(source_name)` | 按源文件查找（同步/导出用） | `SELECT * FROM vectors WHERE pdf_payload LIKE ?` |

---

## 架构注意事项

1. **Gemma 驱动全链路**
   路由、定位、验证全部由 Gemma-4-2b 完成，不再依赖 BGE-M3 嵌入模型。
   LLM 不可用时降级为启发式路由 + SQL 文本搜索。

2. **多轮自证检索**
   路由→定位→验证→(重试) 流程确保检索结果与用户意图匹配。
   最多重试 `router.max_validate_retries`（默认 3）轮。

3. **heading_path 精确定位**
   利用文档的父子标题结构实现内容精确定位，比向量模糊匹配更精准。

4. **ChromaStore 简化**
   移除了 embedding BLOB 和 sparse_payload 列，新增 heading_path 列和 SQL 索引。
   查询方法从向量余弦+稀疏打分改为 SQL WHERE/LIKE 查询。

5. **`_render_preview` 调用频率**
   每收到一个 token 调用一次，高速生成时可能每秒几十次调用 `setHtml()`，存在性能瓶颈。

6. **信号线程安全**
   Qt 跨线程信号默认使用队列连接（`Qt.QueuedConnection`），所有 UI 槽在主线程事件循环中执行，无数据竞争。

7. **溯源链接**
   回答中的 `[1]`、`[2]` 引用标记渲染为可点击链接（`anchor://` / `video://` scheme），
   点击后跳转到对应的命中内容卡片或打开视频播放器。

---

## 启动的服务与进程

| 服务 | 启动时机 | 进程类型 | 端口/路径 | 说明 |
|------|---------|---------|-----------|------|
| **llama-server.exe** | 首次搜索时懒加载 | 子进程（`subprocess.Popen`） | `127.0.0.1:8000` | Gemma-4-2b 模型，承载路由+定位+验证+生成 |
| **SQLite vectors.db** | 首次查询时连接 | 进程内（sqlite3 连接） | `data/vectors/vectors.db` | 文档存储，含 category + heading_path 索引 |
| **SQLite omni.db** | 跨模态查询时连接 | 进程内（sqlite3 连接） | `data/omni.db` | PDF↔视频绑定关系 |

### llama-server 启动详细流程

```
LlamaServerManager().ensure_started(model_path)
│
├─ 检查 /health → 已就绪且模型一致 → 直接返回 True
│
├─ 检查 /health → 端口被占用但模型不同 → 返回 False
│
└─ 需要启动新进程：
    ├─ 1. 查找 llama-server 可执行文件
    │   ├─ config: llama_server.server_dir 指定目录
    │   ├─ 项目捆绑目录
    │   ├─ PATH 环境变量
    │   └─ 自动下载（GitHub Releases，若 auto_download=true）
    │
    ├─ 2. 验证 GGUF 模型文件存在
    │
    ├─ 3. 组装命令行参数：
    │   llama-server.exe
    │     -m models/gemma-4-2b-it-q4_k_m.gguf
    │     --host 127.0.0.1 --port 8000
    │     --ctx-size 6144 --n-gpu-layers 0
    │     --parallel 2 --batch-size 1024 --ubatch-size 512
    │     --flash-attn on --no-mmap --numa distribute
    │
    ├─ 4. subprocess.Popen() 启动子进程
    │
    ├─ 5. 启动日志转发线程（daemon=True）
    │
    └─ 6. 轮询 GET /health，每 0.5s 一次，最多等 180s
        ├─ 200 OK → 返回 True
        ├─ 子进程退出 → 返回 False
        └─ 超时 → 返回 False
```
