# Omni-Local RAG 系统开发与验收规范文档

**文档版本:** V2.0  
**文档日期:** 2026-04-11  
**适用对象:** 核心开发团队、QA 测试团队  
**核心定位:** 极简交互、零隐私泄露、深度多模态绑定的桌面级本地知识库  
**变更摘要:** 基于实际代码库（commit 2026-04）全面修订，修正向量库实现、LLM 架构、PDF 解析、目录结构及多项规范偏差

---

## 目录

1. [项目概况](#1-项目概况)
2. [软件需求规格说明 (SRS)](#2-软件需求规格说明-srs)
3. [系统架构设计](#3-系统架构设计)
4. [代码开发规范](#4-代码开发规范)
5. [多模态数据规范 (Data Schema)](#5-多模态数据规范-data-schema)
6. [验收标准 (Acceptance Criteria)](#6-验收标准-acceptance-criteria)
7. [风险与降级策略](#7-风险与降级策略)
8. [已知技术债务与待解决问题](#8-已知技术债务与待解决问题)
9. [附录](#9-附录)

---

## 1. 项目概况

### 1.1 基本信息

| 字段 | 内容 |
|------|------|
| 项目名称 | Omni-Local RAG |
| 运行环境 | Windows 10/11 (x64)，8GB+ RAM |
| Python 版本 | Python 3.12（venv 内，`cp312-win_amd64`） |
| 文档状态 | 正式发布 |

### 1.2 核心技术栈（实际版本）

| 层次 | 组件 | 实际版本 | 用途 |
|------|------|----------|------|
| GUI 框架 | PyQt5 | 5.15.10 | 桌面界面与事件系统 |
| 推理引擎 | llama-cpp-python | 0.3.19（本地 wheel） | 加载 `gemma-2-2b-it-Q4_K_M.gguf` |
| 向量存储 | **SQLite + numpy**（自实现） | stdlib + numpy 1.26.4 | 替代 ChromaDB，零 C++ 依赖 |
| Embedding | sentence-transformers (BGE-M3) | 2.7.0 | 文本向量化，CPU 常驻 |
| PDF 解析 | **多后端可配置**（见 1.3） | 各不同 | PDF 结构化，用户选择优先级 |
| 语音转写 | faster-whisper | 1.0.3 | 视频 ASR，生成带时间戳字幕 |
| 元数据存储 | SQLite 3 | 内置 | 跨模态对齐映射表、转写结果 |
| 打包工具 | PyInstaller | 6.6.0 | 打包独立 EXE |

> **重要变更：** ChromaDB 已被完全移除。原因：chromadb 0.5.x 依赖 hnswlib，其 Windows 预编译 wheel 使用 AVX/AVX-512 指令集，在部分 CPU 上触发致命访问违规且无法被 Python 捕获。当前使用 `app/models/chroma_store.py` 中的自实现向量存储（SQLite BLOB + numpy 余弦相似度），数据文件位于 `data/vectors/vectors.db`。

### 1.3 PDF 解析后端（可配置，无自动降级）

| 解析器名称 | 库 | 版本 | 特点 |
|------------|-----|------|------|
| `docling` | docling | 1.0.0 | 默认首选，结构化强，支持子进程隔离模式 |
| `unstructured` | unstructured[pdf] | 0.15.7 | 通用型，支持 strategy 配置 |
| `mineru` | MinerU（独立 venv）| 2.0.6 | CLI 调用，Pillow 版本冲突需独立 venv |
| `marker` | marker-pdf | 1.10.2 | 高精度，支持 CUDA 加速 |
| `ocr` | PyMuPDF + pytesseract | 1.24.0 / 0.3.10 | OCR 兜底，支持中英文 |

用户在 `PdfImportPanel` 拖拽排序，列表第一项为实际使用的解析器。**不自动降级**，如需降级须手动更改顺序。

### 1.4 核心功能矩阵（实际实现状态）

| 功能域 | 核心能力 | 优先级 | 实现状态 |
|--------|----------|--------|----------|
| 全局检索 | Alt+Space 唤起无边框 Spotlight | P0 | ✅ 已实现 |
| 流式 RAG 问答 | BGE-M3 检索 + LLM 流式生成 | P0 | ✅ 已实现 |
| 多 PDF 解析后端 | 5 个解析器用户自选优先级 | P0 | ✅ 已实现 |
| 分块工作台 | Markdown 结构感知分块 + 参数调节 | P0 | ✅ 已实现 |
| 视频 ASR + 切片 | faster-whisper + I/O 打点 | P0 | ✅ 已实现 |
| 数据流三层管理 | L1 MD → L2 JSON → L3 VectorDB | P0 | ✅ 已实现 |
| API 优化工作流 | 调用外部 LLM API 批量优化 chunk | P1 | ✅ 已实现 |
| 子进程安全沙箱 | LLM/ASR 在独立进程运行，主进程不崩溃 | P0 | ✅ 已实现 |
| 跨模态对齐绑定 | video clip ↔ PDF anchor 绑定管理 | P1 | ✅ 基础实现 |
| 资源调度 | LLM 闲置 10min 自动卸载 | P1 | ✅ 已实现 |
| 零隐私 | 完全离线（env var 强制禁止 HF 网络） | P0 | ✅ 已实现 |
| QA 记忆记录 | 每次推理结果写 JSONL + Markdown | P2 | ✅ 已实现 |
| PDF 橡皮筋框选 | 左侧框选联动右侧文本 | P1 | ⚠️ UI 已有，逻辑 stub 未实现 |
| 偏好设置对话框 | ConfigDialog 完整配置界面 | P2 | ⚠️ 菜单项存在，功能 stub 未实现 |

---

## 2. 软件需求规格说明 (SRS)

### 2.1 UI/UX 交互需求

#### 2.1.1 Spotlight 全局搜索

**触发与隐藏逻辑：**

- 按 `Alt + Space` 全局唤起无边框搜索窗口（热键可在 `config.json` 的 `ui.hotkey` 中配置）
- 再次按 `Alt + Space` 或窗口**失去焦点**时立即隐藏（非销毁）
- 窗口呼出后，输入框必须自动获取键盘焦点，无需鼠标点击
- `Esc` 键亦可触发隐藏

**实现细节（与规范 V1.1 的差异）：**

- 热键注册使用 `keyboard` 库，回调在后台线程触发，通过 `QMetaObject.invokeMethod(..., Qt.QueuedConnection)` 安全切换到 Qt 主线程（而非直接 `emit`）
- `closeEvent` 被 ignore，窗口永不销毁，仅调用 `hide()`
- 拖拽通过 `eventFilter` 监听 drag_handle 的鼠标事件实现

**视觉规格：**

- 圆角半径 12px；无系统边框（`Qt.FramelessWindowHint`）
- 窗口宽度：`config.json` 中 `ui.window_width`（默认 680px）
- 窗口透明度：`ui.window_opacity`（默认 0.95）
- 呼出/隐藏动画：`QPropertyAnimation(b"windowOpacity")`，时长 `ui.animation_ms`（默认 80ms）
- 定位于鼠标所在屏幕，垂直位置在屏幕高度的 25% 处

#### 2.1.2 多模态结果卡片

**流式文本输出（Typewriter Effect）：**

- AI 生成的总结答案以单 Token 为粒度实时追加，每次 `token_generated(str)` 信号触发追加
- 生成过程中显示 `▋` 光标闪烁动效（`QTimer` 每 500ms 切换）；生成完毕后光标消失
- 提供"停止生成"按钮，调用 `search_ctrl.cancel()` → `InferenceWorker.cancel()`

**降级模式（无 LLM 时）：**

- `no_results`：未检索到任何内容，显示提示文本
- `retrieval_only`：LLM 禁用（`llm.enabled=false`），直接格式化展示检索结果
- `llm_load_failed`：LLM 加载失败，同上直接展示检索结果

**图文锚点卡片（PDF Card）：**

- 展示字段：文件名/页码 + 来源文本段落
- 双击卡片：调用 `os.startfile()` 以系统默认 PDF 阅读器打开（Windows 专用）
- 文件路径解析顺序：先尝试 `data/{file_name}`，再使用 `pdf_payload` 中存储的绝对路径

**视频播放卡片（Video Card）：**

- 展示字段：视频帧缩略图（从 `data/thumbs/{id}.jpg` 懒加载）+ 语义摘要文本 + 时间戳范围
- 点击缩略图或播放按钮：弹出 `VideoPlayerDialog`（含 seek 进度条 + 全屏按钮）
- `VideoPlayerDialog` 在 `mediaStatusChanged → LoadedMedia` 时自动 `setPosition(start_ms)` 后播放

**卡片排序规则（实际实现）：**

由 `SearchController._on_context()` 中的过滤逻辑处理：
1. ChromaDB cosine distance 升序（已由 VectorStore 返回时排序）
2. `is_manual = True` 的条目优先展示
3. `_cache_invalid_ids` 中的 chunk 被过滤（编辑后等待重索引的条目）

#### 2.1.3 系统托盘与后台驻留

**生命周期：**

- 点击窗口关闭按钮：**隐藏**窗口（`closeEvent` ignore），程序不退出
- 仅通过托盘右键菜单"完全退出"才真正调用 `QApplication.quit()`
- `app.setQuitOnLastWindowClosed(False)` 确保所有窗口关闭时进程不退出

**托盘图标状态机：**

| 状态 | 图标文件 | 触发条件 |
|------|----------|----------|
| 就绪 | `tray_green.png` | 模型已加载，等待输入 |
| 处理中 | `tray_yellow.png` | 推理/检索/ASR 进行中 |
| 已卸载 | `tray_gray.png` | LLM 因闲置超时被卸载 |
| 错误 | `tray_red.png` | 出现未恢复的异常 |

> 图标文件不存在时自动降级为 16×16 空白 QPixmap，不崩溃。

**右键菜单项（实际实现）：**

```
偏好设置          → _open_prefs() [⚠️ stub，仅记录日志]
立即释放内存      → LLMManager().unload(reason="manual") + gc.collect()
打开知识库编辑器  → editor.show() / raise_() / activateWindow()
─────────────────
完全退出          → QApplication.quit()
```

---

### 2.2 数据处理与对齐需求

#### 2.2.1 PDF 导入三阶段工作流

实际工作流与规范 V1.1 描述有重大变化，现采用三阶段分离：

```
阶段 1: 文件 → Markdown  (IngestWorker, 后台线程)
   │
   ├─► _selected_parser_name() 获取用户配置的第一个解析器
   ├─► parser.to_markdown(file_path) → markdown_text
   ├─► _export_pdf_page_images() → data/exports/pdf/{stem}_images/ (PyMuPDF 1.5x 渲染)
   ├─► _inject_page_images_if_needed() → 注入页图引用（无图时）
   └─► 写入 data/exports/pdf/{stem}.converted.md
       信号: stage_changed, page_progress, parse_done(file, chunk_count)

阶段 2: Markdown → Chunks (ChunkWorkbench, 用户手动)
   │
   ├─► _markdown_to_items(md_text, max_chars, overlap_chars, ...) → List[ChunkItem]
   ├─► stable_chunk_id(source_type, source_name, page, heading_path, content) → 确定性 ID
   ├─► MetadataExporter.export(chunks) → data/exports/pdf/{stem}.chunks.{json|md}
   └─► 信号: chunks_available(list)

阶段 3: JSON → VectorDB (DataflowPanel._SyncWorker, 用户手动触发)
   │
   ├─► sync_json_to_db(json_path)
   │     ├─ 新增 chunk → EmbedManager.encode() + ChromaStore.add()
   │     ├─ 变更 chunk (is_manual=True 或 content 变化) → re-embed + update
   │     └─ (默认不删除 DB 中孤立 chunk)
   └─► L3 计数更新
```

> **关键差异：** V1.1 规范中 IngestWorker 直接嵌入向量，当前实现中向量化移交给 ChunkWorkbench 用户手动触发，`index_on_import` 配置项默认为 `false`。

**分块算法（Markdown 结构感知）：**

- 维护 `heading_stack`（层级标题栈），标题不独立成块，作为 `heading_path` 前缀附加到后续内容
- `flush_section()` 在遇到新标题时将当前 section 分块并清空
- `_split_markdown_section()` 按 `max_chars` 分割，优先在段落 / 句子 / 逗号边界断开
- 代码块（` ``` ` 或 `~~~`）作为整体不拆分
- 前导小块（`heading_path` 为空且长度 < `min_section_chars * 2`）合并到下一块

**分块参数（`config.json` → `chunking` 节）：**

```json
{
  "chunking": {
    "max_chars": 1800,
    "overlap_chars": 240,
    "keep_heading_path": true,
    "min_section_chars": 80
  }
}
```

#### 2.2.2 视频预处理流程

```
导入视频文件
   │
   ├─► ASRWorker (QThread) — 默认子进程模式
   │     ├─ 启动 asr_subprocess.py 子进程（CLI 参数）
   │     ├─ 解析 stdout JSON 事件流: {"type":"segment","segment":{...}}
   │     ├─ 进度估算: seg["end"] / total_duration
   │     ├─ SQLiteStore().insert_transcript() — 每段入库
   │     └─ 信号: segment_ready(dict), progress(float), finished(bool)
   │
   └─► 视频时长探测:
         优先: QMediaPlayer.duration()
         备用: ffprobe CLI (PATH 中必须可用)
```

**ASR 参数（`config.json` → `asr` 节）：**

```json
{
  "asr": {
    "subprocess": true,
    "model_size": "base",
    "device": "cpu",
    "compute_type": "int8",
    "language": "zh",
    "beam_size": 5,
    "vad_filter": true
  }
}
```

**视频切片打点（VideoWorkbench）：**

- 时间轴：纯 `QPainter` 手绘，ASR 段落蓝色色块（透明度由置信度决定），红色竖线播放头
- 键盘快捷键：`I`（mark_in）/ `O`（mark_out）/ `Enter`（confirm_clip）/ `Space`（toggle_play）
- 切片保存：`SQLiteStore().insert_clip()` → `IngestController.ingest_video_clip()`（UI 线程同步嵌入，⚠️ 阻塞问题见第 8 节）

**置信度计算（非标准）：** `confidence = avg_logprob + 1.0`（faster-whisper 的 `avg_logprob` 通常为负值，+1 映射到约 0~1）

#### 2.2.3 数据流三层架构

| 层级 | 文件格式 | 位置 | 工具 |
|------|----------|------|------|
| L1 | `*.chunks.md` | `data/exports/{type}/{stem}.chunks.md` | 人工直接编辑，`DataflowPanel` |
| L2 | `*.chunks.json` | `data/exports/{type}/{stem}.chunks.json` | `md_json_converter`, `MetadataExporter` |
| L3 | SQLite BLOB 向量 | `data/vectors/vectors.db` | `ChromaStore` (自实现) |

**L1 ↔ L2 转换（`md_json_converter.py`）：**

- `md_to_json(md_path, base_json_path)` — 以 base JSON 为基础保留机器字段，仅覆盖人工编辑字段（content/notes/heading_path），content 变化自动设 `is_manual=True`
- `json_to_md(chunks, output_path)` — 反向导出，含 roundtrip 安全校验

**L2 ↔ L3 同步（`json_db_sync.py`）：**

- `sync_json_to_db(json_path, auto_delete_missing=False)` — 增量同步
- `export_db_to_json(source_name)` — DB → JSON 反向导出（用于 API 优化前的数据准备）

#### 2.2.4 API 优化工作流（新增）

支持调用外部 LLM API 对 chunk JSON 进行批量语义优化：

**支持的 Provider：**

| Provider | 认证方式 | 默认 URL |
|----------|----------|----------|
| OpenAI | `Authorization: Bearer {key}` | `https://api.openai.com/v1/chat/completions` |
| Claude | `x-api-key: {key}` + `anthropic-version: 2023-06-01` | `https://api.anthropic.com/v1/messages` |
| Gemini | URL 附加 `?key={key}` | `https://generativelanguage.googleapis.com/...` |
| Custom | OpenAI 兼容协议 | 用户自定义 |

**4 种优化 action：** `keep` / `update` / `delete` / `merge_with_next`

**QA 样本集成：** `QAMemoryRecorder` 记录的已审阅样本（verdict ≠ "unreviewed"）会作为 few-shot 上下文发送给 API。

#### 2.2.5 跨模态对齐绑定

编辑台中的跨模态绑定 UI（`CrossModalPanel`）：

- 左列：所有 PDF anchor（从 `data/exports/**/*.chunks.json` 全量加载）
- 中列：所有视频 clip（从 SQLite `video_clips` 表加载）
- 右列：绑定记录（含状态：OK / Anchor缺失 / Clip缺失）
- 绑定：`SQLiteStore().insert_cross_modal(video_clip_id, pdf_anchor_id)`
- 修复：`SQLiteStore().update_cross_modal_anchor()` 重指向新 anchor

---

### 2.3 冷启动与容错体验

#### 2.3.1 启动依赖检查序列（实际实现）

`startup_check.py` 执行 8 项检查（`run_all()` 在 `main.py` 调用）：

| 检查项 | 路径 | auto_create | required_children |
|--------|------|-------------|-------------------|
| GGUF 模型文件 | `config.json` → `llm.model_path` | No | — |
| BGE-M3 模型目录 | `config.json` → `embed.model_path` | No | pytorch_model.bin, tokenizer.json, config.json, modules.json |
| 向量存储目录 | `data/vectors/` | Yes | — |
| SQLite 数据库 | `data/omni.db` | Yes（touch） | — |
| 导出目录 | `data/exports/` | Yes | — |
| 评测记录目录 | `data/eval/` | Yes | — |
| 缩略图目录 | `data/thumbs/` | Yes | — |
| API 模板目录 | `data/api_templates/` | Yes | — |

> BGE-M3 的 `required_children` 检查是防止 sentence-transformers 在启动时触发网络下载的关键机制。

引导页（`StartupGuideDialog`）展示缺失项 + 下载链接（通过 `webbrowser.open()` 打开系统浏览器），关闭对话框（reject）则退出程序。

**离线强制措施（在 `main.py` 最顶部）：**

```python
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
```

#### 2.3.2 子进程安全沙箱（新架构）

为隔离 llama-cpp-python 和 faster-whisper 的原生后端崩溃，两者均默认运行在独立子进程中：

| 组件 | 子进程入口 | 通信协议 | 崩溃隔离 |
|------|-----------|----------|----------|
| LLM | `app.models.llm_subprocess` | stdin JSON → stdout JSON 事件流 | ✅ 子进程崩溃不影响 Qt 主进程 |
| ASR | `app.workers.asr_subprocess` | CLI 参数 → stdout JSON 事件流 | ✅ 子进程崩溃不影响 Qt 主进程 |

子进程内 `faulthandler.disable()` 防止原生崩溃输出污染 stdout JSON 流。非 JSON 行收集到诊断列表（最多 20 行），用于错误报告。

#### 2.3.3 启动顺序（`main.py`）

```
1. faulthandler.enable() + 设置 TRANSFORMERS/HF 离线环境变量
2. 预加载 torch（防止 DLL 加载顺序问题）
3. QApplication 初始化（setQuitOnLastWindowClosed=False）
4. startup_check.run_all() → 缺失项 → StartupGuideDialog
5. SQLiteStore().init() — 创建表结构
6. ChromaStore().connect() — 连接向量库（非致命，失败则搜索禁用）
7. EmbedManager — 懒加载，首次使用时初始化
8. 创建 Controllers（Ingest / Search / Hotkey）
9. 创建 Views（KnowledgeEditor / SpotlightWindow / TrayIcon）
10. MemoryWatcher().start() — 启动闲置计时器
11. HotkeyController.register() — 注册全局热键
12. TrayIcon.show() → app.exec_()
```

#### 2.3.4 异常防崩溃策略

| 异常场景 | 处理方式 |
|----------|----------|
| PDF 解析失败 | 捕获异常，错误写日志，`error_occurred` 信号通知 UI |
| LLM OOM | `InferenceWorker` 捕获 `MemoryError` → `LLMManager().unload(reason="oom")` → `error_occurred` 信号 |
| VectorStore 查询失败 | 捕获异常 → 返回空列表 → UI 进入降级模式 |
| ASR 子进程崩溃 | 子进程隔离，`ASRWorker` 检测到非零退出码 → `error_occurred` 信号 |
| LLM 子进程崩溃 | 子进程隔离，`LLMSubprocessError` 异常 → `InferenceWorker.error_occurred` 信号 |
| 热键注册失败 | `QMessageBox.warning()` 提示用户，但程序继续运行 |
| 系统托盘不可用 | 日志警告，程序继续运行 |
| 视频格式不支持（DirectShow 错误 0x80040266） | `os.startfile` / `subprocess` 外部播放器回退 |
| 图标文件不存在 | 16×16 空白 QPixmap 兜底 |

---

## 3. 系统架构设计

### 3.1 模块层次图（实际实现）

```
┌────────────────────────────────────────────────────────────────────┐
│                        View Layer (PyQt5)                           │
│  SpotlightWindow  │  KnowledgeEditor  │  TrayIcon                  │
│  ResultCards      │  ChunkWorkbench   │  PDFWorkbench               │
│  VideoWorkbench   │  DataflowPanel    │  CrossModalPanel            │
│  PdfImportPanel   │  ApiSettingsPanel │  VideoPlayerDialog          │
│  StartupGuideDialog                                                 │
└────────────────────────────┬───────────────────────────────────────┘
                             │ Signals & Slots
┌────────────────────────────▼───────────────────────────────────────┐
│                      Controller Layer                               │
│  HotkeyController  │  SearchController  │  IngestController        │
│  MemoryWatcher(singleton)                                           │
└──────┬──────────────────────┬──────────────────────────────────────┘
       │                      │
┌──────▼──────┐   ┌───────────▼──────────────────────────────────┐
│  QThread    │   │              Model Layer                       │
│  Workers:   │   │  ChromaStore(自实现,SQLite+numpy)             │
│  - Infer    │   │  SQLiteStore    │  EmbedManager(BGE-M3)        │
│  - Ingest   │   │  LLMManager     │  QAMemoryRecorder            │
│  - ASR      │   │  MetadataExporter│  StableIds                  │
│  - ApiOpt   │   │  md_json_converter│ json_db_sync              │
└──────┬──────┘   └──────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────┐
│               Sub-Process Sandbox Layer                  │
│  llm_subprocess.py  │  asr_subprocess.py                │
│  (JSON 事件流，faulthandler 关闭，崩溃隔离)             │
└──────────────────────────────────────────────────────────┘
```

### 3.2 实际目录结构

```
OmniLocalRAG/
├── main.py                        # 程序入口（含启动顺序编排）
├── config.json                    # 用户配置文件（完整 schema 见第 4 节）
├── app/
│   ├── controllers/
│   │   ├── hotkey_controller.py   # keyboard 库 + QMetaObject 跨线程
│   │   ├── search_controller.py   # InferenceWorker 生命周期 + cache invalidation
│   │   ├── ingest_controller.py   # IngestWorker + ASRWorker 协调
│   │   └── memory_watcher.py      # 单例, QTimer 10min 闲置卸载 LLM
│   ├── models/
│   │   ├── chroma_store.py        # SQLite+numpy 向量存储（替代 ChromaDB）
│   │   ├── embed_manager.py       # BGE-M3 单例，CUDA 兼容检测，离线强制
│   │   ├── llm_manager.py         # LLM 单例，子进程模式（默认）/ 内联模式
│   │   ├── llm_subprocess.py      # LLM 子进程入口（独立进程）
│   │   ├── sqlite_store.py        # 多模态元数据 SQLite（4 张表）
│   │   ├── json_db_sync.py        # L2 JSON ↔ L3 VectorDB 同步
│   │   ├── md_json_converter.py   # L1 MD ↔ L2 JSON 双向转换
│   │   ├── metadata_exporter.py   # ingest 结果导出为 .chunks.{json,md}
│   │   ├── qa_memory.py           # QA 推理记录（JSONL + Markdown）
│   │   └── stable_ids.py          # 确定性 chunk ID 生成（SHA1 base）
│   ├── views/
│   │   ├── spotlight_window.py    # 无边框搜索窗口
│   │   ├── knowledge_editor.py    # 主编辑器窗口（6 个标签页）
│   │   ├── tray_icon.py           # 系统托盘（4 状态）
│   │   ├── result_cards.py        # PDFCard / VideoCard / HybridCard
│   │   ├── chunk_workbench.py     # Markdown 分块工作台（阶段 2）
│   │   ├── pdf_workbench.py       # PDF 预览 + Markdown 编辑双栏
│   │   ├── video_workbench.py     # 视频 ASR + 时间轴打点切片
│   │   ├── video_player.py        # VideoPlayerDialog（含 seek）
│   │   ├── dataflow_panel.py      # L1/L2/L3 数据流三层可视化
│   │   ├── cross_modal_panel.py   # 跨模态绑定管理（三栏）
│   │   ├── pdf_import_panel.py    # PDF 导入配置对话框（可拖拽排序 parser）
│   │   ├── api_settings_panel.py  # API 配置面板（4 provider + ping 测试）
│   │   └── startup_guide.py       # 启动引导对话框
│   ├── workers/
│   │   ├── inference_worker.py    # RAG 推理 QThread
│   │   ├── ingest_worker.py       # 文档解析 QThread（阶段 1）
│   │   ├── asr_worker.py          # ASR QThread（默认子进程模式）
│   │   ├── asr_backend.py         # faster-whisper 纯后端（inline/subprocess 共用）
│   │   ├── asr_subprocess.py      # ASR 子进程入口（独立进程）
│   │   └── api_optimize_worker.py # 外部 API 批量 chunk 优化 QThread
│   ├── parsers/
│   │   └── document_parsers.py    # 5 个 PDF 解析器适配器 + PARSER_REGISTRY
│   └── utils/
│       ├── config.py              # JSON 配置加载（点分路径，惰性缓存）
│       ├── logger.py              # 双 handler 日志（文件轮转 + 控制台）
│       └── startup_check.py       # 启动检查项（8 项）
├── assets/
│   ├── icons/                     # tray_green/yellow/gray/red.png
│   └── styles/
│       └── main.qss               # 全局 QSS 样式
├── models/                        # .gguf 与 BGE-M3 模型文件（不入 git）
├── data/
│   ├── vectors/
│   │   └── vectors.db             # 向量存储（SQLite，替代 chroma/）
│   ├── exports/
│   │   ├── pdf/                   # {stem}.converted.md + {stem}.chunks.{json,md}
│   │   └── video/                 # 视频 chunk 导出
│   ├── eval/                      # qa_dialogue_memory.{jsonl,md}
│   ├── thumbs/                    # 视频帧缩略图缓存（{clip_id}.jpg）
│   ├── api_templates/             # API 提示模板
│   └── omni.db                    # SQLite 元数据（video_transcripts 等 4 张表）
├── logs/                          # 按天滚动日志（app.log + app.YYYY-MM-DD）
├── tests/                         # pytest 测试套件（11 个测试文件）
└── OmniLocal.spec                 # PyInstaller 打包配置
```

> **已废弃路径（V1.1 → V2.0）：**
> - `data/chroma/` → 替换为 `data/vectors/vectors.db`
> - `app/models/embed_manager.py` 中不再有 `unload()` 设计为常用，文档注释明确"仅应用退出时调用"

---

## 4. 代码开发规范

### 4.1 架构设计规范 (MVC 强制约束)

| 层次 | 职责 | 禁止事项 |
|------|------|----------|
| Model | 数据存取、向量检索、模型推理 | 不得导入任何 PyQt5 UI 组件 |
| View | 界面渲染、用户输入捕获 | 不得直接调用 IO 或推理方法（通过 Controller 信号中转） |
| Controller | 协调 Model 与 View，管理 Worker 生命周期 | 不得在主线程执行耗时操作（> 50ms） |
| SubProcess | LLM / ASR 原生后端隔离 | 不得导入任何 app.* 模块（防止循环依赖） |

### 4.2 异步推理与通信机制

所有耗时操作**必须**通过 `QThread` + 信号槽通信。热键回调需通过 `QMetaObject.invokeMethod` 切换到主线程：

```python
# hotkey_controller.py 中的跨线程安全模式
def _on_hotkey(self):
    """在 keyboard 库的后台线程中被调用，不可直接操作 Qt"""
    QMetaObject.invokeMethod(self, "_emit_triggered", Qt.QueuedConnection)

@pyqtSlot()
def _emit_triggered(self):
    self.triggered.emit()
```

**InferenceWorker 完整流程：**

```python
class InferenceWorker(QThread):
    token_generated = pyqtSignal(str)
    context_retrieved = pyqtSignal(list)
    generation_finished = pyqtSignal(bool)
    error_occurred = pyqtSignal(str)

    def run(self):
        # Step 1: 嵌入查询向量
        q_emb = EmbedManager().encode(self.query)
        # Step 2: 向量检索
        results = ChromaStore().query(q_emb, n_results=self.top_k)
        self.context_retrieved.emit(results)
        # Step 3: 构建 RAG Prompt（中文 6 条规则，引用格式 [1]）
        prompt = self._build_rag_prompt(self.query, results)
        # Step 4: LLM 流式生成（子进程模式）
        for token in LLMManager().generate(prompt, stream=True):
            if self._cancelled:
                break
            self.token_generated.emit(token)
        # Step 5: QA 记录
        self._record_qa_memory(results, answer)
        self.generation_finished.emit(not self._cancelled)
```

### 4.3 内存与资源调度

#### 4.3.1 LLM 管理（子进程模式为默认）

```python
# config.json 中 llm.subprocess = true（默认）
# LLMManager.generate() → _generate_subprocess(prompt)
#   → 启动 app.models.llm_subprocess 子进程
#   → stdin: JSON payload
#   → stdout: {"type":"token","text":"..."} 事件流
#   → stderr: 合并到 stdout（非 JSON 行 → diagnostics 列表）
```

`unload(reason)` 中 `reason` 取值及含义：
- `"idle_timeout"` — MemoryWatcher 10 分钟闲置触发
- `"manual"` — 托盘菜单"立即释放内存"
- `"oom"` — InferenceWorker 捕获 MemoryError 触发

#### 4.3.2 动态卸载计时器（MemoryWatcher 单例）

```python
# memory_watcher.py
IDLE_TIMEOUT_MS = cfg.get("idle_timeout_minutes", 10) * 60 * 1000

class MemoryWatcher(QObject):
    _instance = None
    _ready = False  # 解决 PyQt5/sip 在 __init__ 前访问实例属性的问题

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def reset(self):
        """每次搜索、窗口显示时调用，重置闲置计时"""
        self._timer.stop()
        self._timer.start()

    def _on_idle_timeout(self):
        LLMManager().unload(reason="idle_timeout")
        # BGE-M3 (EmbedManager) 不卸载，常驻
        self._tray.set_state("unloaded")
```

#### 4.3.3 资源占用参考

| 组件 | 驻留策略 | 内存占用（参考） |
|------|----------|----------|
| BGE-M3 | 常驻，不卸载 | ~1.0 GB (CPU) |
| Gemma-2-2B Q4_K_M | 按需加载，10min 闲置卸载 | ~1.5 GB (CPU) / ~1.8 GB (GPU) |
| VectorStore (SQLite) | 进程生命周期内（per-query 开关连接） | < 50 MB（取决于数据量） |
| SQLiteStore (元数据) | 进程生命周期内 | < 10 MB |

#### 4.3.4 完整 `config.json` Schema

```json
{
  "llm": {
    "enabled": true,
    "subprocess": true,
    "model_path": "models/gemma-2-2b-it-Q4_K_M.gguf",
    "n_gpu_layers": 0,
    "n_ctx": 4096,
    "max_tokens": 512,
    "temperature": 0.7,
    "repeat_penalty": 1.1
  },
  "embed": {
    "model_path": "models/bge-m3",
    "device": "cpu"
  },
  "retrieval": {
    "top_k": 5,
    "distance_threshold": 0.7
  },
  "qa_memory": {
    "enabled": true,
    "path": "data/eval/qa_dialogue_memory.md",
    "jsonl_path": "data/eval/qa_dialogue_memory.jsonl",
    "max_context_chars": 1200
  },
  "chunking": {
    "max_chars": 1800,
    "overlap_chars": 240,
    "keep_heading_path": true,
    "min_section_chars": 80
  },
  "pdf": {
    "parser_order": ["docling","unstructured","mineru","marker","ocr"],
    "index_on_import": false,
    "parser_options": {
      "docling": {},
      "unstructured": {"strategy":"auto","languages":"eng,chi_sim"},
      "mineru": {"command":"mineru","extra_args":""},
      "marker": {"device":"cuda"},
      "ocr": {"lang":"chi_sim+eng","scale":2}
    },
    "start_page": 0,
    "end_page": 0
  },
  "asr": {
    "subprocess": true,
    "model_size": "base",
    "device": "cpu",
    "compute_type": "int8",
    "language": "zh",
    "beam_size": 5,
    "vad_filter": true
  },
  "ui": {
    "hotkey": "alt+space",
    "window_width": 680,
    "window_opacity": 0.95,
    "animation_ms": 80
  },
  "api": {
    "provider": "openai",
    "key": "",
    "url": "https://api.openai.com/v1/chat/completions",
    "model": "gpt-4o-mini",
    "temperature": 0.3,
    "max_tokens": 2048
  },
  "exports": {
    "path": "data/exports",
    "write_json": true,
    "write_markdown": true
  },
  "idle_timeout_minutes": 10,
  "log_retention_days": 30
}
```

### 4.4 日志规范

**实际实现：** 双 handler（文件轮转 + 控制台），模块级单例，Guard 防重复注册：

```python
# utils/logger.py
def setup_logger() -> logging.Logger:
    logger = logging.getLogger("omni_rag")
    if logger.handlers:
        return logger  # 防止重复注册
    logger.setLevel(logging.DEBUG)
    # 文件 handler: DEBUG 级，按天轮转，保留 30 天（硬编码，未读 config）
    file_handler = TimedRotatingFileHandler(
        filename=log_dir / "app.log", when="midnight", backupCount=30
    )
    # 控制台 handler: INFO 级
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
```

> **已知问题：** `config.json` 中 `log_retention_days=30` 配置项存在，但 `TimedRotatingFileHandler` 的 `backupCount` 是硬编码 30，未读取该配置。

**必须记录的关键事件：**

| 事件 | 日志级别 | 包含字段 |
|------|----------|----------|
| 应用启动完成 | INFO | 总启动耗时(ms) |
| 向量检索完成 | INFO | query 摘要、top_k、耗时(ms) |
| 推理完成 | INFO | token 数量、生成速度(tok/s) |
| 模型卸载 | INFO | reason（idle_timeout/manual/oom） |
| OOM 异常 | ERROR | 完整异常栈 |
| 文件解析失败 | WARNING | 文件路径、异常类型 |
| 子进程异常退出 | ERROR | exit code、diagnostics 信息 |

### 4.5 Chunk ID 稳定性规范

使用 `stable_chunk_id()` 生成**确定性可重现**的 chunk ID：

- 格式：`{source_type}:{file_label}-{source_digest8}:{page_label}:{content_digest14}`
- 基于 SHA1，`source_name` 取前 8 位，`content_key`（含 source_type + page + heading_path + content）取前 14 位
- 内容微小变化即产生新 ID（属预期行为，用于 `json_db_sync` 增量检测）
- 使用 `used_ids: MutableSet` 去重，重复时追加 `-2`, `-3`...

---

## 5. 多模态数据规范 (Data Schema)

### 5.1 VectorStore (vectors.db) 表结构

所有向量数据存储在 `data/vectors/vectors.db` 的 `vectors` 表：

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | TEXT | PRIMARY KEY | 稳定 chunk ID（`stable_chunk_id()` 生成） |
| `content` | TEXT | NOT NULL | 送入 Embedding 的纯文本 |
| `embedding` | BLOB | NOT NULL | float32 原始字节（numpy `tobytes()`） |
| `source_type` | TEXT | NOT NULL | 枚举：`pdf` \| `video` \| `markdown` \| `hybrid` |
| `anchor_id` | TEXT | DEFAULT '' | 业务对齐键 |
| `pdf_payload` | TEXT | DEFAULT '{}' | JSON 字符串，例：`{"file":"a.pdf","page":5}` |
| `video_payload` | TEXT | DEFAULT '{}' | JSON 字符串，例：`{"file":"b.mp4","start":125,"end":150}` |
| `is_manual` | INTEGER | DEFAULT 0 | 0=自动，1=人工确认或手动切块 |
| `created_at` | TEXT | — | ISO8601 UTC 时间 |
| `version` | INTEGER | DEFAULT 1 | 每次 `update()` 时 `version+1` |

**查询接口（`ChromaStore.query()`）：**
- 输入：float 列表（已由 EmbedManager 生成）
- 执行：全表扫描 + numpy 矩阵余弦相似度计算（O(N)）
- 过滤：`1 - cosine_similarity > distance_threshold`（默认 0.7）则丢弃
- 返回：按 distance 升序，最多 top_k 条

> **性能注意：** 当前实现为全表扫描，无 HNSW 索引。10,000 条记录时仍可接受（< 200ms SLA），超过 50,000 条后需评估性能。

### 5.2 SQLite (omni.db) 表结构

```sql
-- 视频转写结果（faster-whisper 每段输出）
CREATE TABLE video_transcripts (
    id          TEXT PRIMARY KEY,
    video_file  TEXT NOT NULL,
    start_sec   REAL NOT NULL,
    end_sec     REAL NOT NULL,
    text        TEXT NOT NULL,
    confidence  REAL,            -- avg_logprob + 1.0
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 人工切片（覆盖或补充 ASR）
CREATE TABLE video_clips (
    id               TEXT PRIMARY KEY,
    video_file       TEXT NOT NULL,
    start_sec        REAL NOT NULL,
    end_sec          REAL NOT NULL,
    semantic_summary TEXT NOT NULL,
    chroma_id        TEXT,        -- 对应 vectors.db 中的 id
    is_manual        BOOLEAN DEFAULT 1,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 跨模态对齐映射
CREATE TABLE cross_modal_map (
    id             TEXT PRIMARY KEY,
    video_clip_id  TEXT NOT NULL REFERENCES video_clips(id),
    pdf_anchor_id  TEXT NOT NULL,
    note           TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 应用配置（键值对）
CREATE TABLE app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

> **注意：** `cross_modal_map` 的外键约束存在，但 `PRAGMA foreign_keys = ON` 未启用，不会自动级联删除。

### 5.3 Chunk JSON Schema（`.chunks.json`）

```json
{
  "chunks": [
    {
      "id": "pdf:document-a1b2c3d4:p5:e6f7g8h9i0j1k2",
      "source_type": "pdf",
      "source_name": "document.pdf",
      "page": 5,
      "heading_path": "第一章 / 1.1 背景",
      "block_type": "text",
      "content": "...",
      "notes": "",
      "is_manual": false,
      "created_at": "2026-04-11T10:00:00+00:00",
      "version": 1
    }
  ]
}
```

### 5.4 热更新机制（单 Chunk Re-embed）

```
用户在 DataflowPanel 中双击编辑 chunk 内容
   │
   ├─► 内存中修改 _current_chunks 列表，设 is_manual=True
   ├─► 重写整个 .chunks.json 文件（全量覆盖，非增量）
   │
   └─► SearchController.invalidate_cache(chunk_id) 调用时机 [⚠️ 当前未自动触发]
         └─ 下次搜索前需手动触发 L2→L3 同步，或重启程序

手动触发热更新:
   DataflowPanel "同步到向量库" 按钮
      → _SyncWorker → sync_json_to_db(json_path)
            ├─ 变更 chunk → EmbedManager.encode() + ChromaStore.update()
            └─ 新增 chunk → ChromaStore.add()
```

---

## 6. 验收标准 (Acceptance Criteria)

### 6.1 性能验收 (Performance SLA)

| 指标 | 目标值 | 测试方法 |
|------|--------|----------|
| 快捷键呼出延迟 | < 100ms | 从按键事件到窗口完全可见+输入框聚焦 |
| TTFT（首字时延） | < 1.5s | 从 Enter 按下到第一个 Token 渲染在 UI |
| 推理吞吐量（纯 CPU） | > 5 tok/s | i5 10代 CPU，n_gpu_layers=0 |
| 闲置内存占用 | < 600MB | 触发 10min 卸载后，任务管理器工作集 |
| 向量检索耗时 | < 200ms | top_k=5，库中 10,000 条文档（全表扫描） |
| PDF 阶段 1 解析（docling） | < 60s / 100页 | 标准 PDF，子进程模式 |
| 分块速度 | < 5s / 100页 Markdown | ChunkWorkbench 手动触发 |

### 6.2 功能与准确性验收 (Functional SLA)

| 功能项 | 验收标准 | 测试方法 |
|--------|----------|----------|
| 向量检索精准召回 | Top-1 命中率 ≥ 90% | 针对 20 条人工标记的切片，逐一提问 |
| 播放锚定精度 | 起播误差 ≤ ±1s | 记录点击时间戳与实际 QMediaPlayer.position() |
| 分块热更新 | 修改后同步到向量库后下次查询立即体现 | 编辑 chunk → 触发同步 → 搜索相同问题 |
| PDF 双击打开 | Windows 系统默认 PDF 阅读器打开 | 双击 PDF 卡片，验证文件打开 |
| 防崩溃（LLM 子进程） | 模拟 LLM 子进程崩溃，主进程不崩溃 | 手动 kill llm_subprocess 进程 |
| 防崩溃（PDF 解析失败） | PDF 解析失败，error_occurred 信号触发 | 导入加密 PDF 或损坏文件 |
| 冷启动引导 | 引导页正常弹出，显示缺失项 | 删除 models/ 目录后启动 |
| 离线强制 | 无网络时程序正常运行（不触发 HF 下载） | 断开网络，验证 TRANSFORMERS_OFFLINE 生效 |
| 多解析器切换 | 更换 parser_order 首项后重新解析结果不同 | 分别用 docling/marker 解析同一 PDF |
| API 优化 ping | API 设置中 ping 按钮成功返回 | 使用有效 API Key 测试 OpenAI/Claude |
| QA 记忆写入 | 每次推理后 JSONL 追加新记录 | 检查 data/eval/qa_dialogue_memory.jsonl |

### 6.3 交付与部署验收 (Deployment SLA)

| 指标 | 标准 |
|------|------|
| 免环境运行 | `OmniLocal.exe` 在全新 Windows 11（无 Python/VC++ 环境）双击可启动 |
| 目录整洁 | 运行后不向 `AppData`、注册表、系统 PATH 写入任何内容 |
| 文件自包含 | 模型在 `models/`，数据在 `data/`，日志在 `logs/`，向量在 `data/vectors/` |
| 卸载干净 | 删除整个程序目录即完成卸载 |
| 子进程隔离 | LLM/ASR 子进程崩溃不导致主进程崩溃或数据丢失 |

---

## 7. 风险与降级策略

| 风险 | 概率 | 影响 | 降级/缓解措施 |
|------|------|------|---------------|
| docling 子进程崩溃/超时 | 中 | 中 | DoclingParser 有 isolated 模式（`subprocess.run`）+ PyMuPDF 文本兜底 |
| llama-cpp-python DLL 不兼容 | 高（Windows） | 高 | 默认子进程模式隔离崩溃，主进程只收 LLMSubprocessError |
| BGE-M3 在低内存机器 OOM | 低 | 高 | 提前检查可用内存，CUDA 兼容检测 + CPU 自动回退 |
| CUDA 编码失败 | 中 | 中 | EmbedManager 检测 sm_xy 架构兼容性，自动 CPU 重载（`_reload_on_cpu`） |
| faster-whisper 转写长视频耗时过长 | 高 | 低 | 子进程隔离 + 进度信号 + `proc.terminate()` 取消 |
| QMediaPlayer DirectShow 格式不支持 | 中 | 中 | 错误码 0x80040266 检测，`os.startfile`/`subprocess` 外部播放器回退 |
| Windows 热键被其他软件占用 | 低 | 中 | 注册失败 → `QMessageBox.warning()`，允许 config.json 自定义 `ui.hotkey` |
| ChromaStore 全表扫描性能退化 | 中（数据量 > 5万条） | 中 | 当前为 O(N) 实现，超量时需引入批量分页或 FAISS 索引 |
| MinerU Pillow 版本冲突 | 高 | 低 | MinerU 独立 venv 安装，主 venv 使用 Pillow 10.3.0 |
| `is_manual=True` 泛滥 | 中 | 低 | ChunkWorkbench 生成的所有 chunk 均设 is_manual=True，导致 json_db_sync 全量更新 |

---

## 8. 已知技术债务与待解决问题

此节记录当前代码中已知的问题，供后续版本优先修复：

| 优先级 | 类别 | 描述 | 涉及文件 |
|--------|------|------|---------|
| P0 | 功能缺失 | PDF 橡皮筋框选联动（`_on_pdf_selection()`）是空方法 | `views/pdf_workbench.py:~240` |
| P0 | 功能缺失 | 托盘"偏好设置"（`_open_prefs()`）是 stub，仅记录日志 | `views/tray_icon.py:~80` |
| P1 | 性能问题 | `ingest_video_clip()` 在 UI 线程同步执行嵌入，可能卡顿 | `controllers/ingest_controller.py:~90` |
| P1 | 接口不一致 | `ChromaStore.list_by_source()` 不存在，`json_db_sync.py` 有 `AttributeError` 兜底回退全表扫描 | `models/chroma_store.py`, `models/json_db_sync.py` |
| P1 | 数据一致性 | DataflowPanel 编辑 chunk 后不自动触发 `SearchController.invalidate_cache()`，需用户手动同步 | `views/dataflow_panel.py:~200` |
| P1 | 配置缺失 | `chunking.min_section_chars` 未在 `config.py` 的 `_DEFAULT` 中声明，有隐式默认值（80） | `utils/config.py`, `views/chunk_workbench.py` |
| P2 | 日志不一致 | `log_retention_days` 配置项存在但未被 logger 读取（`backupCount=30` 硬编码） | `utils/logger.py` |
| P2 | 代码重复 | `_native_backend_error()` 函数在 `llm_manager.py` 和 `llm_subprocess.py` 中完全相同 | 两个文件 |
| P2 | 格式漂移 | `MetadataExporter` 生成的 MD 不含 `is_manual` 字段，`md_json_converter` 生成的 MD 包含 | `models/metadata_exporter.py`, `models/md_json_converter.py` |
| P2 | 跨平台兼容 | `os.startfile()` 在 PDFCard 和 DataflowPanel 中无跨平台回退（Linux/macOS 会报错） | `views/result_cards.py`, `views/dataflow_panel.py` |
| P2 | 编码问题 | `ingest_worker.py` 文件头有 BOM 字符和乱码注释（`鈥?`），表明历史编码问题 | `workers/ingest_worker.py` |
| P3 | 无缓存 | `CrossModalPanel._load_all_anchors()` 每次刷新都全量重读所有 JSON 文件 | `views/cross_modal_panel.py` |
| P3 | ChunkWorkbench | 所有手动切块均设 `is_manual=True`，可能导致 json_db_sync 全量更新开销 | `views/chunk_workbench.py` |

---

## 9. 附录

### 9.1 关键依赖版本锁定（实际 requirements.txt）

```
PyQt5==5.15.10
sentence-transformers==2.7.0
docling==1.0.0
PyMuPDF==1.24.0
unstructured[pdf]==0.15.7
marker-pdf==1.10.2
pytesseract==0.3.10
faster-whisper==1.0.3
librosa==0.10.2
soundfile==0.12.1
keyboard==0.13.5
pyinstaller==6.6.0
numpy==1.26.4
Pillow==10.3.0
lxml==4.9.4
rich==13.9.4
onnxruntime==1.18.1
pypdfium2==4.30.0
# llama-cpp-python: 本地 wheel llama_cpp_python-0.3.19-cp312-cp312-win_amd64.whl
# MinerU 2.0.6: 独立 venv（Pillow>=11 冲突），由 install_cpu.bat/install_cuda.bat 安装
```

### 9.2 热键方案

| 默认 | 配置项 | 备选示例 |
|------|--------|--------|
| `alt+space` | `config.json → ui.hotkey` | `ctrl+shift+space`，`ctrl+alt+space` |

热键字符串格式遵循 `keyboard` 库规范（小写，`+` 连接，如 `ctrl+shift+space`）。

### 9.3 测试套件（`tests/`）

| 测试文件 | 覆盖功能 |
|---------|---------|
| `test_embed_manager_device.py` | EmbedManager CPU/CUDA 设备切换 |
| `test_inference_retrieval_fallback.py` | InferenceWorker 降级模式（no_results/retrieval_only） |
| `test_ingest_progress_signals.py` | IngestWorker 进度信号 |
| `test_llm_manager_load.py` | LLMManager 加载/卸载/子进程模式 |
| `test_pdf_import_panel.py` | PdfImportPanel UI 逻辑 |
| `test_pdf_vector_export.py` | PDF 解析 + 向量导出 |
| `test_qa_memory.py` | QAMemoryRecorder JSONL/Markdown 写入 |
| `test_sqlite_metadata_store.py` | SQLiteStore CRUD |
| `test_video_asr_worker.py` | ASRWorker 信号与子进程模式 |

运行测试：`python -m pytest tests/ -v`（需要激活 venv）

### 9.4 术语表

| 术语 | 定义 |
|------|------|
| TTFT | Time To First Token，首字时延 |
| anchor_id | PDF 知识切片的唯一业务标识符，用于跨模态关联 |
| is_manual | 标记该知识切片是否经过人工编辑台操作（值为 1）或由系统自动生成（值为 0） |
| hybrid | source_type 枚举值，表示该切片同时关联了 PDF 和视频内容 |
| OOM | Out Of Memory，内存溢出 |
| n_gpu_layers | llama-cpp 配置项，控制模型中有多少层卸载到 GPU |
| BGE-M3 | BAAI General Embedding M3，多语言多功能文本嵌入模型 |
| VectorStore | 本项目中指 `chroma_store.py` 的自实现 SQLite+numpy 向量存储 |
| SubProcess Sandbox | LLM/ASR 在独立子进程中运行以隔离原生后端崩溃的架构模式 |
| L1/L2/L3 | 数据流三层：L1=.chunks.md，L2=.chunks.json，L3=vectors.db |
| stable_chunk_id | 基于 SHA1 的确定性 chunk 标识符，内容变化则 ID 变化 |
| QA Memory | `QAMemoryRecorder` 记录的每次推理 QA 对，用于 API 优化的 few-shot 样本 |

---

*文档由 Omni-Local RAG 核心开发团队维护。基于代码库 2026-04 实际状态修订。如有疑问或变更请通过 Issue 提交评审。*
