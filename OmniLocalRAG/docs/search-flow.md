# OmniLocalRAG — 用户检索完整执行流程

> 文档版本：2026-04-16-v2（知识图谱遍历 + 三层模糊匹配架构）
> 覆盖范围：用户按下 Enter 到回答渲染完毕的全链路，含所有分支路径
> 核心变更：检索阶段零 LLM 调用，三层模糊匹配（精确子串 → N-gram → 编辑距离），仅最终回答生成依赖 llama-server

---

## 总体架构

```
[UI 主线程]                         [Worker 子线程 QThread]
────────────────────────────────────────────────────────────
SpotlightWindow
  └─ SearchController
       └─ InferenceWorker.run()
            │
            ├─ Phase 1: 检索（零 LLM，纯内存 ~10-50ms）
            │    ├─ GraphTraverser._condense()            → 启发式查询改写
            │    ├─ KnowledgeGraph.find_nodes()           → 三层模糊匹配起始节点
            │    │    ├─ Layer 1: 精确子串匹配（最高分）
            │    │    ├─ Layer 2: N-gram 部分匹配（中分）
            │    │    └─ Layer 3: 编辑距离模糊匹配（低分）
            │    ├─ KnowledgeGraph.traverse_bfs()         → BFS 扩展相关节点
            │    ├─ GraphTraverser._rank_by_community()   → 社区内聚度排序（同三层模糊）
            │    └─ GraphTraverser._resolve_chunks()      → 获取 chunk 内容
            │
            ├─ context_retrieved.emit()  → 立即发射检索结果（不等 LLM）
            │
            ├─ Phase 2: LLM 加载（仅生成需要）
            │    └─ LLMManager.load()    → 确保 llama-server 启动
            │
            ├─ Phase 3: 构建提示词
            │    └─ _build_prompt()      → 构建含对话历史的 RAG 提示词
            │
            └─ Phase 4: 流式生成
                 └─ LLMManager.generate() → 流式 token 迭代器
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
memory = _ConversationMemory()           # 对话滑窗（最近 5 轮）
top_k = int(cfg.get("retrieval.top_k", 5))
```

---

### 3.2 — 图遍历检索：`GraphTraverser.search()`

**文件：** `app/models/graph_traverser.py`

```python
kg = KnowledgeGraph()
if kg.is_loaded():
    traverser = GraphTraverser()
    search_result = traverser.search(
        query=self.query,
        memory=memory.recent(),
        top_k=top_k,
    )
else:
    # 降级: ChromaStore.search_text()
    search_result = _fallback_text_search(...)
```

**完整流程：**

```
GraphTraverser.search()
│
├─ Phase 1: _condense() — 启发式查询改写
│   └─ 检测代词 → 拼接上一轮 standalone_query
│
├─ Phase 2: KnowledgeGraph.find_nodes() — 三层模糊匹配
│   ├─ Layer 1: 精确子串匹配（score: heading=3.0, label=2.5, other=1.5）
│   ├─ Layer 2: N-gram 部分匹配（score: ratio × 1.5）
│   └─ Layer 3: 编辑距离模糊匹配（score: similarity × 1.0, threshold ≥ 0.7）
│
├─ Phase 3: KnowledgeGraph.traverse_bfs() — BFS 遍历扩展
│   └─ 从匹配节点出发，max_depth=3, max_nodes=50
│
├─ Phase 4: _rank_by_community() — 社区感知排序（同三层模糊匹配）
│   ├─ 三层模糊匹配 → relevance 分数
│   ├─ 社区内聚度 → cohesion 加分
│   ├─ 节点度数 → degree 加分
│   └─ 综合排序: score = relevance × 2.0 + cohesion × 1.0 + degree_bonus × 0.5
│
└─ Phase 5: _resolve_chunks() — 从 ChromaStore 获取 chunk 内容
    └─ 通过 chunk_id 查询 vectors 表
```

#### Phase 1: `_condense()` — 启发式查询改写

```python
# 检测追问代词（它、这个、那个、上面、上述...）
if any(marker in query for marker in _FOLLOWUP_MARKERS):
    condensed = f'基于"{last_standalone_query}"：{query}'
```

#### Phase 2: `find_nodes()` — 三层模糊匹配

**核心算法**，替代精确子串匹配，实现模糊检索能力：

```python
def find_nodes(self, query: str, top_k: int = 5) -> List[Tuple[float, str]]:
    terms = self._extract_terms(query)       # 提取中英文关键词
    query_ngrams = self._build_ngrams(terms) # 生成 2-gram 片段

    for nid, ndata in self._G.nodes(data=True):
        combined = f"{label} {heading} {category}"
        score = 0.0

        # Layer 1: 精确子串匹配（最高分）
        for term in terms:
            if term in combined:
                if term in heading: score += 3.0
                elif term in label: score += 2.5
                else: score += 1.5

        # Layer 2: N-gram 部分匹配（中分）
        if score == 0.0:
            ngram_hits = sum(1 for ng in query_ngrams if ng in combined)
            if ngram_hits > 0:
                score += (ngram_hits / len(query_ngrams)) * 1.5

        # Layer 3: 编辑距离模糊匹配（低分）
        if score == 0.0:
            for term in terms:
                for ntoken in node_tokens:
                    similarity = 1.0 - (edit_distance / max_len)
                    if similarity >= 0.7:
                        score += similarity * 1.0
```

**三层匹配策略详解：**

| 层级 | 方法 | 分数范围 | 适用场景 | 示例 |
|------|------|---------|---------|------|
| Layer 1 | 精确子串匹配 | 1.5-3.0 | 查询词完整出现在节点文本中 | 查"工控机IP配置" → 命中"工控机IP配置教程" |
| Layer 2 | N-gram 部分匹配 | 0-1.5 | 查询词部分片段匹配 | 查"网络参数设置" → "网络参"匹配"网络参数" |
| Layer 3 | 编辑距离模糊匹配 | 0-1.0 | 拼写变体、近似词 | 查"configration" → 匹配"configuration" (sim=0.87) |

**辅助方法：**

- `_build_ngrams(terms, n=2)`: 从关键词生成 2-gram 片段。如 `"工控机"` → `["工控", "控机"]`
- `_edit_distance(s1, s2)`: Levenshtein 编辑距离（动态规划，O(mn)）。如 `edit_distance("config", "cnfig") = 1`
- `_extract_terms(query)`: 提取中英文关键词，正则 `_KEYWORD_RE = [A-Za-z0-9_.:/\\-]{2,}|[\u4e00-\u9fff]{2,}`

#### Phase 3: `traverse_bfs()` — BFS 遍历

```python
def traverse_bfs(self, start_nodes, max_depth=3, max_nodes=50):
    """从匹配节点出发，BFS 扩展到相关节点。"""
    visited = set(start_nodes)
    for _ in range(max_depth):
        next_frontier = set()
        for n in frontier:
            for neighbor in G.neighbors(n):
                if neighbor not in visited and len(visited) < max_nodes:
                    next_frontier.add(neighbor)
                    edges.append((n, neighbor))
        visited.update(next_frontier)
        frontier = next_frontier
    return visited, edges
```

#### Phase 4: `_rank_by_community()` — 社区感知排序

与 `find_nodes()` 使用**相同的三层模糊匹配**计算 relevance 分数，叠加社区内聚度和节点度数加分：

```
score = relevance × 2.0 + cohesion × 1.0 + degree_bonus × 0.5
```

- **relevance**: 三层模糊匹配分数（与 find_nodes 相同算法）
- **cohesion**: 社区内聚度 = 实际边数 / 最大可能边数（0-1）
- **degree_bonus**: min(degree / 10, 1.0)，高度连接节点更中心

#### Phase 5: `_resolve_chunks()` — 获取 chunk 内容

```python
for nid in ranked_nodes[:top_k]:
    chunk_id = ndata.get("chunk_id", nid)
    row = chroma._conn().execute("SELECT * FROM vectors WHERE id=?", (chunk_id,)).fetchone()
    chunk = ChromaStore._row_to_dict(row)
```

---

### 3.3 — 降级路径

图未加载或为空时，自动降级为 `ChromaStore.search_text()`（SQL LIKE 搜索）：

```python
if not kg.is_loaded():
    route_category = _heuristic_category(self.query)
    results = chroma.search_text(self.query, limit=top_k)
    search_result = {
        "router_source": "text_search_fallback",
        "validated": False,
        ...
    }
```

---

### 3.4 — 发射检索结果信号

```python
self.timings_ready.emit({
    "stage": "retrieval",
    "route_ms": ..., "locate_ms": ..., "validate_ms": ...,
    "retrieval_total_ms": ..., "top_k": ..., "hits": len(results),
    "attempts": ..., "validated": ...,
    "router_source": "graph_traversal",  # 或 "text_search_fallback"
    "reasoning_steps": [...],
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
                                          ┌─ KnowledgeGraph.is_loaded()?
                                          │   ├─ Yes → GraphTraverser.search()
                                          │   │   ├─ _condense() → standalone_query
                                          │   │   ├─ find_nodes() → 三层模糊匹配起始节点
                                          │   │   │   ├─ Layer 1: 精确子串 (heading=3.0, label=2.5)
                                          │   │   │   ├─ Layer 2: N-gram 部分匹配 (×1.5)
                                          │   │   │   └─ Layer 3: 编辑距离模糊 (≥0.7, ×1.0)
                                          │   │   ├─ traverse_bfs() → 扩展相关节点
                                          │   │   ├─ _rank_by_community() → 社区感知排序
                                          │   │   └─ _resolve_chunks() → ChromaStore 查询
                                          │   │
                                          │   └─ No → ChromaStore.search_text() (SQL LIKE)
                                          │
                                   ←───── timings_ready（stage=retrieval）
  ← _on_timings() — "匹配 Xms 遍历 Xms 排序 Xms 分类 tech_manual 图遍历 ✓"
                                   ←───── context_retrieved(results)
  ← _on_context(results)
      build_card() × N 张
      自动选中 card[0]
                                          _build_prompt(standalone_query, results, category, memory.recent())
                                          LLMManager.load()
                                            → LlamaServerManager().ensure_started()
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
├─ KnowledgeGraph.is_loaded() == True
│   └─ GraphTraverser.search() — 图遍历检索
│       ├─ _condense() — 启发式查询改写
│       │
│       ├─ find_nodes() — 三层模糊匹配
│       │   ├─ Layer 1 命中 → 返回匹配节点（高分）
│       │   ├─ Layer 2 命中 → 返回部分匹配节点（中分）
│       │   ├─ Layer 3 命中 → 返回模糊匹配节点（低分）
│       │   └─ 全层未命中 → 空列表
│       │
│       ├─ 无匹配节点 → _fallback_text_search()
│       │   └─ ChromaStore.search_text() → SQL LIKE 搜索
│       │
│       ├─ traverse_bfs() → BFS 扩展
│       │
│       ├─ _rank_by_community() → 社区感知排序
│       │   ├─ 三层模糊匹配 → relevance
│       │   ├─ 社区内聚度 → cohesion
│       │   └─ 节点度数 → degree_bonus
│       │
│       └─ _resolve_chunks() → ChromaStore 查询
│           └─ 返回 {category, standalone_query, chunks, heading_paths,
│                   router_source="graph_traversal", validated=True}
│
├─ KnowledgeGraph.is_loaded() == False
│   └─ 降级: ChromaStore.search_text()
│       └─ 返回 {router_source="text_search_fallback", validated=False}
│
├─ 无检索结果
│   └─ token_generated.emit("（未检索到相关内容…）")
│       mode="no_results"
│
├─ 有结果，cfg.llm.enabled == False
│   └─ token_generated.emit(_build_retrieval_answer())
│       mode="retrieval_only"
│
├─ 有结果，LLM 加载失败
│   └─ token_generated.emit(_build_retrieval_answer("加载失败…"))
│       mode="llm_load_failed"
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
| 查询改写后 | `standalone_query: str` | 启发式改写结果，代词替换 |
| 模糊匹配后 | `matched: List[Tuple[float, str]]` | `[(score, node_id), ...]` 按分数降序 |
| BFS 遍历后 | `visited: Set[str], edges: List[Tuple]` | 访问节点集合 + 遍历边列表 |
| 社区排序后 | `ranked_nodes: List[str]` | 按综合分数排序的节点 ID 列表 |
| search 后 | `search_result: dict` | `category, standalone_query, chunks, heading_paths, router_source, attempts, validated, timings, reasoning_steps` |
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

1. **知识图谱遍历检索（零 LLM）**
   检索阶段完全由内存知识图谱驱动，不依赖任何 LLM 调用。
   关键词匹配→BFS 遍历→社区排序→置信度过滤，全程纯内存操作。
   检索延迟从 ~1-2s 降至 ~10-50ms。

2. **三层模糊匹配**
   `find_nodes()` 和 `_rank_by_community()` 均使用三层模糊匹配策略：
   - **Layer 1 — 精确子串匹配**：查询词完整出现在节点文本中，heading 匹配得分最高(3.0)，label 次之(2.5)，其他(1.5)
   - **Layer 2 — N-gram 部分匹配**：将查询词拆为 2-gram 片段，计算匹配比例 × 1.5。处理部分匹配、截断查询
   - **Layer 3 — 编辑距离模糊匹配**：Levenshtein 距离计算相似度，阈值 ≥ 0.7 才计入。处理拼写变体、近似词
   - 三层逐级降级：Layer 1 命中则跳过 Layer 2/3，Layer 2 命中则跳过 Layer 3

3. **LLM 仅用于生成**
   llama-server 仅在检索完成后、生成回答前加载。
   搜索链路中唯一的 LLM 调用是最终的流式生成。
   入库时 auto_tag() 也使用 LLM（可选）。

4. **知识图谱构建**
   从 ChromaStore + SQLiteStore 自动构建：
   - 每个 chunk → 图节点（label=heading_path+category）
   - heading_path 层级 → references 边
   - cross_modal_map → shares_data_with 边
   - 同 category + 同 source → conceptually_related_to 边
   - 同 source 相邻 chunk → references 边

5. **社区检测（Louvain）**
   应用启动时运行 Louvain 社区检测，将节点聚类为社区。
   搜索时优先返回高内聚社区的结果，跨社区通过 god nodes 桥接。
   图变更时增量重聚类。

6. **降级路径**
   图未加载/为空时，自动降级为 ChromaStore.search_text()（SQL LIKE 搜索）。
   模糊匹配无结果时，也降级到文本搜索。
   保证在任何情况下搜索功能可用。

7. **heading_path 精确定位**
   利用文档的父子标题结构实现内容精确定位，比向量模糊匹配更精准。
   图中 heading_path 层级边保留了这一优势。

8. **ChromaStore 简化**
   移除了 embedding BLOB 和 sparse_payload 列，新增 heading_path 列和 SQL 索引。
   查询方法从向量余弦+稀疏打分改为 SQL WHERE/LIKE 查询。

9. **`_render_preview` 调用频率**
   每收到一个 token 调用一次，高速生成时可能每秒几十次调用 `setHtml()`，存在性能瓶颈。

10. **信号线程安全**
    Qt 跨线程信号默认使用队列连接（`Qt.QueuedConnection`），所有 UI 槽在主线程事件循环中执行，无数据竞争。

11. **溯源链接**
    回答中的 `[1]`、`[2]` 引用标记渲染为可点击链接（`anchor://` / `video://` scheme），
    点击后跳转到对应的命中内容卡片或打开视频播放器。

---

## 启动的服务与进程

| 服务 | 启动时机 | 进程类型 | 端口/路径 | 说明 |
|------|---------|---------|-----------|------|
| **llama-server.exe** | 首次生成时懒加载 | 子进程（`subprocess.Popen`） | `127.0.0.1:8000` | Gemma-4-2b 模型，仅用于回答生成 + 入库 auto_tag |
| **KnowledgeGraph** | 应用启动时加载 | 进程内（NetworkX 内存图） | `data/knowledge_graph/graph.json` | 知识图谱，承载检索逻辑 |
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

---

## 知识图谱数据模型

### 节点属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `id` | str | 唯一标识（chunk_id） |
| `label` | str | 显示标签（heading_path + category） |
| `file_type` | str | 文件类型（document/code/image） |
| `source_file` | str | 来源文件名 |
| `source_location` | str | 文件内位置 |
| `chunk_id` | str | 关联的 ChromaStore chunk ID |
| `category` | str | 分类（tech_manual/business_sop/...） |
| `heading_path` | str | 标题层级路径 |

### 边属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `relation` | str | 关系类型（references/shares_data_with/conceptually_related_to） |
| `confidence` | str | 置信度级别（EXTRACTED/INFERRED/AMBIGUOUS） |
| `confidence_score` | float | 置信度分数（0-1） |
| `source_file` | str | 来源文件 |
| `weight` | float | 边权重 |

### 边生成规则

| 规则 | 关系 | 置信度 | 说明 |
|------|------|--------|------|
| heading_path 层级 | `references` | EXTRACTED (1.0) | `A > B > C` → A→B, B→C |
| cross_modal_map | `shares_data_with` | EXTRACTED (1.0) | PDF anchor ↔ video clip |
| 同 category + 同 source | `conceptually_related_to` | EXTRACTED (1.0) | 同类同源关联 |
| 同 source 相邻 chunk | `references` | EXTRACTED (1.0) | 顺序关系 |
