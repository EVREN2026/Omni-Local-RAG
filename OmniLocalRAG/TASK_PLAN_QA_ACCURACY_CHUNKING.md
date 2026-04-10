# Omni-Local RAG 问答准确性验证与切片优化计划

## 当前里程碑

CPU 框架已跑通：

- Markdown 导入完成。
- BGE-M3 在 CPU 路径生成 embedding。
- VectorStore 能实时检索 Markdown 文档内容。
- 本地 GGUF LLM 已启用，并通过子进程调用 llama-cpp-python。
- JSON / Markdown chunk 导出可用于人工审查。

当前重点从“功能打通”切换到“问答准确性验证”和“切片算法优化”。

## 核心原则

- SQLite 仍是运行时主数据源。
- VectorStore 仍是语义检索主入口。
- `data/exports/**/*.chunks.json` 和 `data/exports/**/*.chunks.md` 作为人工审查知识库快照。
- `data/eval/qa_dialogue_memory.md` 作为问答验证记忆文件。
- 不把 Markdown/JSON 替代 SQLite 或 VectorStore。
- 每次切片算法调整后必须重新导入文档并比较问答质量。

## 阶段 1：问答记忆与评测闭环

目标：每次问答都能留下可审查证据。

已新增：

- `data/eval/qa_dialogue_memory.md`
- `data/eval/qa_dialogue_memory.jsonl`
- 自动记录字段：
  - question
  - retrieved chunks
  - answer
  - mode
  - expected answer
  - verdict
  - accuracy score
  - retrieval score
  - chunking action

验收标准：

- 每次提问后，问答记忆文件自动追加记录。
- 人工能从记录中判断错误属于检索问题、切片问题还是 LLM 生成问题。
- 至少积累 30 个高价值问题作为第一批评测集。

## 阶段 2：基于导出 chunk 的准确性基线

目标：用现有导出的 `.chunks.json` / `.chunks.md` 验证切片质量。

当前审查对象：

- `data/exports/markdown/full.chunks.json`
- `data/exports/markdown/full.chunks.md`
- `data/exports/markdown/INSTALL.chunks.json`
- `data/exports/markdown/INSTALL.chunks.md`

检查维度：

- chunk 是否过短，例如标题、作者、日期被单独切成无上下文片段。
- chunk 是否过长，导致检索命中后 LLM 难以定位关键答案。
- 表格是否被压缩为不可读 HTML。
- 标题层级是否丢失。
- 同一章节的步骤、注意事项、参数解释是否被拆散。
- top-k 是否命中正确章节。
- answer 是否有 chunk 支持。

验收标准：

- 第一轮 30 个问题中，retrieval_miss 比例低于 20%。
- correct + partial 比例高于 70%。
- 每个 wrong/unsupported 样本能定位到原因。

## 阶段 3：Markdown 结构化切片算法

目标：替换当前 `_rough_split()` 的粗糙空格切分。

当前状态：已完成第一版结构化 Markdown chunker，重新导入 Markdown 后生效。

优化方向：

- 按 Markdown heading 建立 section path。
- 保留标题上下文，例如 `# / ## / ###` 作为每个 chunk 的前缀。
- 段落、列表、代码块、表格按 block 类型处理。
- 表格转为更可检索的文本描述，而不是直接保留长 HTML。
- 小标题和短段落合并到相邻正文，避免孤立 chunk。
- 控制 chunk 字符数和 token 数。
- 保留 overlap，但优先在自然段边界重叠。

建议配置：

- `chunking.max_chars`: 1800
- `chunking.overlap_chars`: 240
- `chunking.keep_heading_path`: true

验收标准：

- 同一 Markdown 文档重新导入后，chunk_count 更合理。
- 问题能命中完整上下文，而不是只命中标题。
- 表格类问题能命中字段名和值。
- `qa_dialogue_memory.md` 中 retrieval_score 明显提升。

## 阶段 4：评测集固化

目标：将人工问答样本变成可重复运行的评测集。

计划新增：

- `data/eval/golden_qa.md`
- `data/eval/golden_qa.jsonl`
- `scripts/eval_qa.py`

评测字段：

- question
- expected_answer
- must_include
- source_file
- expected_chunk_keywords
- verdict

自动评测先做弱规则：

- 是否检索到 expected_chunk_keywords。
- 回答是否包含 must_include。
- 是否出现明显 unsupported 内容。

高级模型人工复核：

- 用本模型检查 answer 是否被 retrieved chunks 支持。
- 将错误归因到 retrieval、chunking、prompt、LLM。

## 阶段 5：Prompt 与引用格式优化

目标：让本地 LLM 回答更可验证。

Prompt 调整方向：

- 要求回答引用 chunk 序号。
- 不允许使用检索内容之外的知识补充关键事实。
- 信息不足时必须说“当前知识库不足”。
- 对操作步骤类问题输出分步答案。
- 对参数类问题输出表格或字段解释。

验收标准：

- 回答能指出来源。
- unsupported 下降。
- 人工复核成本降低。

## 阶段 6：PDF 导入与图像关联

目标：在 Markdown 闭环稳定后恢复 PDF 深度能力。

任务：

- PDF 文本 chunk 与 page/coords 绑定。
- PDF 页面图片导出为审查图。
- UI 中点击 chunk 能定位到 PDF 页面区域。
- Docling/Marker/PyMuPDF 转换结果统一进入结构化 block。
- PDF 导出层增加 page image reference。

验收标准：

- PDF chunk 能对应页面截图。
- 人工能检查“答案来自 PDF 哪一页哪一区域”。
- PDF 转 Markdown 与原 PDF 图像可并排验证。

## 阶段 7：视频导入与视频切片

目标：视频导入不崩溃，并能形成可检索的时间片段。

已完成基础保护：

- faster-whisper ASR 改为子进程。
- ASR 子进程崩溃不会杀主 UI。

后续任务：

- ASR 文本按时间窗口切片。
- 手动视频 clip 与 ASR transcript 绑定。
- clip summary 入 VectorStore。
- 搜索结果点击后跳转视频时间点。
- 视频 chunk 导出 JSON/Markdown 供人工审查。

验收标准：

- 视频文本可检索。
- 检索结果能定位到时间段。
- 手动切片能补充语义摘要。

## 第一轮执行顺序

1. 使用当前 `full.md` 连续提问 30 个问题。
2. 检查 `data/eval/qa_dialogue_memory.md`。
3. 标记每个问题的 verdict 和 chunking action。
4. 汇总前三类错误：
   - 检索未命中。
   - chunk 太碎或太长。
   - LLM 没有忠实使用检索内容。
5. 实施 Markdown 结构化切片。
6. 重新导入同一 Markdown。
7. 重跑同一批问题。
8. 对比 correct / partial / wrong / retrieval_miss 比例。

## 下一步开发任务（已完成 2026-04-10）

- ✅ 修复 `_markdown_to_items()` 孤立标题 chunk 问题，heading 自动携带正文上下文。
- ✅ 短行元数据（Author、Date、Version）合并到相邻正文，消除孤立 chunk。
- ✅ MetadataExporter 导出新增 `heading_path`、`block_type` 字段。
- ✅ 增加 `data/eval/golden_qa.md` 和 `data/eval/golden_qa.jsonl`（10 条标准评测问题）。
- ✅ 增加 `scripts/eval_qa.py` 自动评测脚本（retrieval-only 弱规则）。
- ✅ Prompt 模板升级：chunk 序号引用、不足时明确说明、操作步骤分步输出。

---

## 阶段 8：API 模型优化流水线（数据三层架构）

**目标**：构建与模型无关的知识优化闭环。任何外部 API（OpenAI/Claude/Gemini/本地 API）都能对切片数据进行分析和优化，结果自动更新数据库，提升本地模型问答质量。

### 核心架构：三层数据文件

| 层级 | 文件类型 | 角色 | 读写方 |
|------|----------|------|--------|
| L1 人工层 | `.md` 文件 | 便于人工阅读和手动纠错 | 人工编辑 / UI 展示 |
| L2 接口层 | `.json` 文件 | API 快速理解的结构化数据 | API 模型读写 / 程序转换 |
| L3 机器层 | SQLite / VectorDB | 程序高速调度和检索 | 程序内部 |

### 三层数据流向

```
[原始文档]
    │ 导入
    ▼
[L3 数据库] ←── json_db_sync ──► [L2 JSON]
                                      │
                                      │ md_json_converter
                                      ▼
                                  [L1 Markdown]
                                      │ 人工纠错
                                      ▼
                                  [L1 Markdown] ──► md_json_converter ──► [L2 JSON]
                                                                              │
                                                                    api_optimize_worker
                                                                              │
                                                                              ▼
                                                                    [API 模型分析优化]
                                                                              │
                                                                              ▼
                                                                    [L2 JSON 优化结果]
                                                                              │
                                                                    json_db_sync
                                                                              │
                                                                              ▼
                                                                    [L3 数据库更新]
```

### 任务模板机制

- 每次 API 优化任务都基于固定模板文件 `data/templates/api_optimize_task.md`。
- 模板定义：输入数据格式、优化目标、输出字段规范。
- 保证不同 API 模型调度时行为一致，结果可对比。

### 8.1 MD ↔ JSON 双向转换器

**交付文件**：`app/models/md_json_converter.py`

功能：

- `md_to_json(md_path) -> dict`：解析 `.chunks.md` 人工纠错内容，转为结构化 JSON，保留 heading_path、block_type、手动修改标记。
- `json_to_md(chunks_json, output_path)`：将 JSON chunk 数据渲染为可读 Markdown，格式与 MetadataExporter 输出兼容，人工可直接编辑。
- 增量合并：人工在 MD 文件中修改的 content、verdict、notes 字段，合并回 JSON 时保留，不覆盖机器生成字段。

**验收**：

- 一条 chunk 经过 MD → JSON → MD 往返，内容无损。
- 人工在 MD 中修改 content，合并后 JSON 中 `is_manual: true` 字段自动置位。

### 8.2 JSON → 数据库同步器

**交付文件**：`app/models/json_db_sync.py`

功能：

- `sync_json_to_db(json_path)`：读取 `.chunks.json`，对比 VectorStore 和 SQLite 中现有记录。
  - 新增 chunk → embed + 写入 VectorStore。
  - content 变更的 chunk（`is_manual: true` 或 API 优化结果）→ re-embed + 更新 VectorStore。
  - 删除标记的 chunk → 从 VectorStore 中移除。
- `export_db_to_json(source_file, output_path)`：将数据库中某文档的所有 chunk 导出为 JSON（逆向）。

**验收**：

- JSON 中手动修改的 chunk 经 sync 后，下次检索能命中新内容。
- 批量 sync 100 条 chunk < 30s（CPU 路径）。

### 8.3 API 优化工作线程

**交付文件**：`app/workers/api_optimize_worker.py`

工作流（任务流）：

```
1. 读取 data/templates/api_optimize_task.md 任务模板
2. 加载输入 JSON（chunks + qa_dialogue_memory）
3. 按 batch_size 分批打包为 API Prompt
4. 调用 API（OpenAI / Claude / Gemini / 自定义 endpoint）
5. 解析 API 返回的优化建议（JSON 格式）
6. 将优化结果写入新版 JSON 文件
7. 可选：自动触发 json_db_sync 更新数据库
```

信号定义：

```python
batch_started   = pyqtSignal(int, int)    # (batch_index, total_batches)
batch_finished  = pyqtSignal(dict)        # 本批次优化结果摘要
optimize_done   = pyqtSignal(str)         # 输出 JSON 路径
error_occurred  = pyqtSignal(str)
```

支持的 API 模型（通过配置切换）：

- OpenAI（GPT-4o / GPT-4 Turbo）
- Anthropic Claude（claude-3-5-sonnet）
- Google Gemini
- 兼容 OpenAI 格式的本地 API（LM Studio / Ollama / vLLM）

**验收**：

- 10 条 chunk 完成一轮优化并写入 JSON < 60s（网络正常时）。
- API 报错不崩溃，错误信息通过信号传回 UI。
- 不同 API 提供商切换只需修改 config.json，不改代码。

### 8.4 任务模板文件

**交付文件**：`data/templates/api_optimize_task.md`

内容：固定的 API 优化任务描述模板，定义：

- 角色设定（RAG 切片质量审核员）
- 输入格式说明（chunk JSON 字段含义）
- 优化任务列表：
  - 识别过短/无上下文的 chunk，建议合并方向
  - 识别截断章节，建议 overlap 调整
  - 基于对话历史中的 retrieval_miss 样本，指出问题章节
  - 输出改写后的 content（如需要）
- 输出格式约束（JSON Schema）

每次 API 调用都附加此模板作为 system prompt 的一部分，保证调度一致性。

### 8.5 API 设置控制面板

**交付文件**：`app/views/api_settings_panel.py`

UI 组件：

- API 提供商下拉选择（OpenAI / Claude / Gemini / 自定义）
- Base URL 输入框（自定义 endpoint 支持）
- API Key 输入框（明文/密码模式切换）
- 模型名称输入框（可自由填写）
- Temperature / Max tokens 滑块
- 测试连接按钮（发送 ping 请求验证连通性）
- 保存配置按钮（写入 config.json 的 `api` 节点）

### 8.6 数据流可视化面板

**交付文件**：`app/views/dataflow_panel.py`

UI 布局（三栏）：

```
┌─────────────────┬──────────────────┬─────────────────┐
│   L1 Markdown   │    L2 JSON       │  L3 数据库状态  │
│                 │                  │                 │
│  [文件列表]     │  [chunk 表格]    │  [向量数量]     │
│  [打开编辑]     │  [字段展示]      │  [上次同步时间] │
│  [转换→JSON]    │  [API 优化]      │  [同步到DB]     │
│                 │  [转换→MD]       │                 │
└─────────────────┴──────────────────┴─────────────────┘
```

交互功能：

- 左栏：列出 `data/exports/**/*.chunks.md`，点击在编辑器中打开，支持直接修改并保存。
- 中栏：展示选中文件的 chunk 列表（表格视图），每行可内联编辑 content，标注 is_manual。
- 右栏：展示数据库同步状态，点击"同步到DB"触发 `json_db_sync`，进度条实时显示。
- 顶部：API 优化触发按钮，选择输入文件后一键启动 `api_optimize_worker`，结果实时流入中栏。

**验收**：

- MD 文件内容变更后点击"转换→JSON"，JSON 文件立即更新。
- JSON 中修改一条 chunk content，点击"同步到DB"，数据库中对应记录更新，下次检索命中新内容。
- API 优化完成后，中栏 chunk 表格自动刷新显示优化建议。

---

## 阶段 10：PDF 导入可视化与纠错工作台

**背景**：当前 PDF 导入无任何进度反馈，转换方式无法通过 UI 配置，转换完成后缺乏可视化纠错界面。用户无法知晓转换是否在进行、选择哪种解析器，也无法对照 PDF 原文手动纠错 Markdown。

**目标**：
- PDF 导入配置通过 UI 面板调整（解析器选择、参数）。
- 导入过程展示实时进度条和阶段提示。
- 转换完成后在纠错工作台中展示 PDF 源文与 Markdown 并排对比。
- 支持人工编辑 Markdown 内容并保存回数据库（re-embed）。

---

### 10.1 PDF 导入配置面板

**交付文件**：`app/views/pdf_import_panel.py`

UI 组件：

- **解析器选择**：单选或优先级排序列表（docling / marker / pymupdf / ocr）
- **OCR 语言**：下拉选择（chi_sim+eng / eng / chi_sim）
- **Marker 设置**：批处理大小、输出格式（markdown / json）
- **分块参数**：max_chars 滑块（500–3000）、overlap_chars 滑块（0–500）、keep_heading_path 勾选框
- **页面范围**：起始页 / 结束页输入（留空表示全部）
- **保存为默认**：将当前配置写入 `config.json` 的 `pdf` 节点
- **开始导入按钮**：触发导入流程，按钮导入中置灰，完成后恢复

配置字段映射（写入 `config.json`）：

```json
{
  "pdf": {
    "parser_order": ["marker", "pymupdf", "ocr"],
    "ocr_lang": "chi_sim+eng",
    "marker_batch_size": 4,
    "page_start": null,
    "page_end": null
  },
  "chunking": {
    "max_chars": 1800,
    "overlap_chars": 240,
    "keep_heading_path": true
  }
}
```

**验收**：

- 修改解析器顺序后重新导入，实际使用的解析器按新顺序尝试。
- 配置保存后重启程序仍然生效。

---

### 10.2 导入进度条与阶段提示

**修改文件**：`app/workers/ingest_worker.py`、`app/views/knowledge_editor.py`

新增信号：

```python
stage_changed   = pyqtSignal(str)          # 当前阶段文字描述
progress        = pyqtSignal(int, int)     # (已完成chunk数, 总chunk数) — 已有，复用
page_progress   = pyqtSignal(int, int)     # (已处理页数, 总页数) — 新增
parse_done      = pyqtSignal(str, int)     # (markdown_path, total_pages) — 解析完成，进入切块
```

进度阶段划分：

| 阶段 | `stage_changed` 文字 | 进度条含义 |
|------|---------------------|------------|
| 启动解析器 | `"正在用 {parser} 解析 PDF..."` | 不确定进度（marquee 模式） |
| 逐页转换 | `"转换中：第 {n}/{total} 页"` | page_progress 驱动 |
| 切块 & 向量化 | `"向量化切块 {n}/{total}"` | progress 信号驱动 |
| 写入数据库 | `"写入数据库..."` | 不确定进度 |
| 完成 | `"导入完成：共 {n} 个切块"` | 进度条满格 |

UI 变更（`knowledge_editor.py`）：

- 当前 `_status_label` 升级为含进度条的组合控件（`QProgressBar` + `QLabel`）。
- 进度条支持 marquee 模式（`setRange(0, 0)`）和确定进度模式切换。
- 导入完成后自动跳转到「PDF 纠错」标签页，显示转换结果。

**验收**：

- 导入 10 页 PDF，能看到"转换中：第 n/10 页"实时刷新。
- 切块阶段能看到"向量化切块 n/total"实时刷新。
- 完成后状态栏显示总切块数，进度条归满。

---

### 10.3 PDF 纠错工作台升级

**修改文件**：`app/views/pdf_workbench.py`

当前状态：左侧渲染 PDF 页面，右侧显示对应 chunk 文本，支持橡皮筋选区和手动编辑保存。

升级目标：完整对比纠错工作台，支持全文浏览和批量纠错。

#### 布局升级（三栏）

```
┌──────────────────┬────────────────────┬──────────────────────┐
│   PDF 原文渲染   │  Markdown 全文编辑  │   切块列表 & 状态    │
│                  │                    │                      │
│  [上一页/下一页] │  [可编辑 QTextEdit] │  [chunk ID / 页码]   │
│  [页码 n/total]  │  [行号显示]        │  [内容预览]          │
│  [缩放比例]      │  [高亮当前页对应段] │  [is_manual 标记]    │
│                  │                    │  [保存选中切块]      │
│  [橡皮筋选区]    │  [保存全文]        │  [同步所有到DB]      │
└──────────────────┴────────────────────┴──────────────────────┘
```

功能细节：

- **左栏（PDF 渲染）**：
  - PyMuPDF 渲染当前页为 QPixmap，支持缩放（50% / 75% / 100% / 150%）。
  - 页面间导航（上一页 / 下一页 / 页码跳转输入框）。
  - 橡皮筋选区：拖拽选定区域，右键菜单"添加为新切块"。

- **中栏（Markdown 全文编辑器）**：
  - 加载 `data/exports/pdf/<stem>.chunks.md` 全文，可直接在编辑器中修改。
  - 切换 PDF 页码时，自动滚动到对应页的 Markdown 内容（通过 `<!-- page: N -->` 注释定位）。
  - 支持语法高亮（标题、代码块、表格用不同颜色区分）。
  - 「保存全文」按钮：将编辑内容写回 `.chunks.md`，并触发 `md_to_json` + `sync_json_to_db` 全量同步。

- **右栏（切块列表）**：
  - 表格显示所有切块：chunk_id、page、内容前 60 字、is_manual 状态、向量状态（已嵌入 / 待更新）。
  - 点击切块行：左栏跳转到对应 PDF 页，中栏滚动到对应段落并高亮。
  - 「保存选中切块」：仅 re-embed 当前选中的切块（单条快速更新）。
  - 「同步所有到DB」：批量同步所有 is_manual=true 的切块到向量数据库。

#### 新增：导入完成自动加载

- `IngestWorker.finished` 信号触发后，`PDFWorkbench.load_file(path)` 自动调用，无需手动切换。
- 加载时右栏切块列表从 `.chunks.json` 读取，中栏加载 `.chunks.md`，左栏渲染第一页。

**验收**：

- 导入完成后自动展示 PDF 第一页和对应 Markdown，无需任何手动操作。
- 点击右栏任一切块，左栏 PDF 跳转到对应页面，中栏 Markdown 滚动到对应段落。
- 在中栏修改任意文字后保存，右栏该切块标记 `is_manual=true`，数据库中内容更新，下次搜索命中新内容。

---

### 10.4 解析器状态与降级提示

**修改文件**：`app/workers/ingest_worker.py`

增强现有 `degraded_mode` 信号，携带更多上下文：

```python
degraded_mode = pyqtSignal(str, str, str)
# (attempted_parser, fallback_parser, reason)
# 例："marker", "pymupdf", "marker 未安装，已切换到 pymupdf"
```

UI 中以非阻塞 `QToolTip` 或状态栏消息展示，不弹模态对话框阻断流程。

**验收**：

- marker 未安装时，状态栏显示"已切换到 pymupdf"，不弹窗打断用户。
- 所有解析器都失败时，弹出错误对话框并中止，不写入损坏数据。

---

### 10.5 单元测试

**新增测试文件**：`tests/test_pdf_import_panel.py`、`tests/test_ingest_progress_signals.py`

测试覆盖：

- `PdfImportPanel` 初始化，读取 config.json 正确填充控件。
- 配置修改后写入 config.json 对应字段。
- `IngestWorker` 发出 `stage_changed` 和 `page_progress` 信号（mock 解析器）。
- `PDFWorkbench.load_file()` 正确加载 `.chunks.json` 和 `.chunks.md`。
- 切块内容修改后 `is_manual=true` 正确标记。

**验收**：

- `PYTHONPATH=. python3 -m unittest discover -s tests -p "test_*.py"` 全部通过。

---

## 阶段 9：全链路集成验收

目标：三层数据架构完整闭环运行。

验收清单：

- [ ] 导入 `full.md` → L3 数据库写入，L2 JSON 和 L1 MD 自动导出。
- [ ] 人工在 L1 MD 中修改一条 chunk → 转换到 L2 JSON → 同步到 L3 数据库。
- [ ] API 优化任务执行 → 优化结果写入 L2 JSON → 同步到 L3 数据库。
- [ ] 重新搜索命中人工修改后的内容。
- [ ] `scripts/eval_qa.py` 对比优化前后 retrieval_score 提升。
- [ ] 全部 20 条单元测试仍然通过。

---

## 文件交付清单（新增）

| 文件路径 | 所属模块 | 完成状态 |
|----------|----------|----------|
| `app/models/md_json_converter.py` | MD ↔ JSON 转换器 | ⏳ |
| `app/models/json_db_sync.py` | JSON → 数据库同步 | ⏳ |
| `app/workers/api_optimize_worker.py` | API 优化线程 | ⏳ |
| `app/views/api_settings_panel.py` | API 控制面板 | ⏳ |
| `app/views/dataflow_panel.py` | 数据流面板 | ⏳ |
| `data/templates/api_optimize_task.md` | 任务模板 | ⏳ |
| `data/eval/golden_qa.md` | 标准评测集（可读） | ✅ |
| `data/eval/golden_qa.jsonl` | 标准评测集（机器） | ✅ |
| `scripts/eval_qa.py` | 自动评测脚本 | ✅ |

### 阶段 10 新增文件

| 文件路径 | 所属模块 | 完成状态 |
|----------|----------|----------|
| `app/views/pdf_import_panel.py` | PDF 导入配置面板 | ✅ |
| `app/views/pdf_workbench.py`（升级） | PDF 纠错工作台（三栏布局） | ✅ |
| `app/workers/ingest_worker.py`（修改） | 新增 stage_changed / page_progress 信号 | ✅ |
| `app/views/knowledge_editor.py`（修改） | 进度条组合控件 + 自动跳转纠错标签页 | ✅ |
| `tests/test_pdf_import_panel.py` | 导入面板配置读写测试 | ✅ |
| `tests/test_ingest_progress_signals.py` | IngestWorker 进度信号测试 | ✅ |
