# OmniLocalRAG — 功能清单

> 更新日期：2026-04-15
> 架构版本：Gemma-4 驱动全链路（已废弃 BGE-M3(嵌入模型)）

---

## 一、搜索与问答

### 1.1 Spotlight 全局搜索窗口
- **全局热键唤起**：Alt+Space 唤起/隐藏搜索窗口（可配置）
- **无边框置顶窗口**：半透明背景、拖拽移动、边缘缩放
- **流式生成回答**：逐 token 渲染，光标闪烁动画
- **结果卡片列表**：直接生成结果，左侧显示检索命中卡片，支持键盘上下键切换
- **预览面板**：右侧显示选中卡片的完整内容、heading 面包屑、语义描述
- **计时信息栏**：显示路由/定位/验证/模型加载/生成各阶段耗时
- **可点击引用链接**：回答中 [1][2] 渲染为 `anchor://` / `video://` 链接，点击跳转到对应卡片或打开视频
- **溯源信息**：命中内容底部显示 anchor_id /)video_clip_id 可点击链接
- **取消按钮**：生成过程中可随时取消

### 1.2 多轮自证检索（GemmaRouter）
- **阶段1 — 意图路由**：Gemma-4-2b 分析用户问题+对话历史，输出 `{category, standalone_query}`
  - 支持 7 种分类：tech_manual, business_sop, company_intro, parameter, process, project_code, general
  - Few-shot 示例引导 LLM 输出结构化 JSON
  - 代词消解：将"它""这个"等指代替换为上文实际内容
  - 启发式 fallback：LLM 不可用时退化为关键词匹配+代词拼接
- **阶段2 — 文档定位**：LLM 从 heading_path 索引中选择最匹配的文档标题
  - 获取指定分类下所有 heading_path 列表
  - LLM 选择相关标题编号（可多选）
  - 按 heading_path 前缀匹配获取对应 chunks
  - 定位失败时 fallback 为 SQL LIKE 文本搜索
- **阶段3 — 验证定位**：LLM 判断定位到的内容是否足以回答用户问题
  - 输出 `{valid, reason, missing}`
  - 验证失败时扩大范围重试（category→general→文本搜索补充）
- **重试机制**：最多 `router.max_validate_retries`（默认 3）轮
- **降级模式**：llama-server 不可用时退化为启发式路由 + SQL 文本搜索

### 1.3 对话记忆
- **运行时对话滑窗**：保留最近 5 轮（可配置）对话历史
- **QA 评估记忆**：每次问答记录写入 JSONL + Markdown 文件，含检索上下文和空白人工审核字段

### 1.4 RAG 提示词构建
- 9 条严格规则：仅用检索内容作答、[序号]标注来源、不足时直说、分步输出、参数表优先、图片保留、数值忠实、中文回答、代词消解
- 含对话历史段落 + 编号检索片段（Category/Path/9Type/desc/content）

---

## 二、文档导入与处理

### 2.1 PDF 导入
- **PdfImportPanel**：文件选择对话框，支持选择 PDF 文件
- **Marker 解析器**：使用 marker 将 PDF 转换为 Markdown（唯一保留的解析器），转换过程中自动提取文档图像保存到本地，图像链接自动注入 Markdown
- **PDF 纠错工作台**（PDFWorkbench）：可视化显示转换结果，支持页面缩略图浏览

### 2.2 结构感知切片（Chunker）
- **heading_path 提取**：按 Markdown 标题层级构建父子路径（如 "快速入门 > 2.2 工控机IP配置教程 > c. 在弹出的新窗口中"）
- **block 类型识别**：text / term / tutorial / image / table / parameter_table / code
- **表格行描述增强**：为表格内容生成语义描述
- **图片上下文提取**：从附近行提取图片说明文字
- **双文本分离**：display_content（展示用）和 embedding_text（检索用）分别生成
- **退化策略**：无标题→段落窗口切片；单层标题→合并微切片
- **可配置参数**：max_chars、overlap_chars、min_section_chars、micro_chunk_chars

### 2.3 分块管理工作台（ChunkWorkbench）
- 导入 Markdown 文件并启动切片
- 可配置切片参数（最大字符、重叠、最小段落长度等）
- 表格显示所有 chunks，支持内联编辑 content
- 编辑后标记 is_manual=True
- 发射 chunks_available 信号供其他面板使用

### 2.4 数据存储
- **ChromaStore（SQLite）**：文档存储，含 category + heading_path 索引
  - `list_by_category()` — 按分类列出 chunks
  - `list_headings()` — 列出所有 heading_path（供 LLM 选择）
  - `get_by_heading()` — 按 heading_path 前缀获取 chunks
  - `search_text()` — SQL LIKE 文本搜索（降级/补充）
  - `list_by_source()` — 按源文件查找
  - 自动迁移：旧表含 embedding 列时自动重建表
- **JSON-DB 同步**（json_db_sync）：.chunks.json ↔ ChromaStore 双向同步
  - 自动推断 category（基于文件名+heading+内容关键词）
  - 增量同步：仅更新变化的 chunks
  - 支持导出 DB→JSON

### 2.5 三层数据架构
- **L1（人类层）**：.chunks.md — 可读、可手动编辑
- **L2（接口层）**：.chunks.json — 结构化、程序可读
- **L3（数据库层）**：ChromaStore SQLite — 检索用
- **md_json_converter**：L1↔L2 双向转换，支持 roundtrip 安全验证

### 2.6 元数据导出（MetadataExporter）
- 导出 chunks 为 JSON + Markdown 格式
- 写入 data/exports/ 目录

---

## 三、视频导入与切片

### 3.1 视频导入
- **文件选择**：支持 MP4/MKV/AVI/MOV 格式
- **ASR 转写**：faster-whisper 自动语音识别
  - 支持子进程模式（隔离崩溃风险）
  - 可配置：model_size、device、compute_type、language、beam_size、VAD 过滤
  - 实时进度反馈
- **转写结果存储**：写入 SQLite video_transcripts 表

### 3.2 视频工作台（VideoWorkbench）
- **视频播放**：QMediaPlayer 内嵌播放 + 外部系统播放器打开
- **时间轴可视化**：
  - ASR 段落以蓝色半透明区域显示
  - 已有切片以绿色半透明区域+边框显示
  - 红色 playhead 指示当前播放位置
- **拖拽选区切片**：在时间轴上按住拖拽选择时间范围，蓝色选区实时显示
- **Mark In/Out 按钮**：保留按钮+键盘快捷键(I/O)标记起止点
- **确认切片**：输入语义摘要 → 保存到 SQLite + ChromaStore
- **切片后直接链接文档**：确认切片后弹出 anchor 选择对话框
  - 显示所有文档 chunk（heading_path + 内容预览）
  - 支持搜索过滤、多选链接
  - 可跳过（"稍后链接"）
- **播放字幕叠加**：播放时底部显示当前时间对应的 ASR 转写文本

### 3.3 独立视频播放器（VideoPlayerDialog）
- QVideoWidget 全功能播放
- 进度条拖拽定位
- 全屏切换
- 指定起始位置播放

---

## 四、跨模态绑定

### 4.1 跨模态绑定管理（CrossModalPanel）
- **三栏布局**：文档 Anchors | 视频 Clips | 绑定关系
- **Anchor 列表**：从 .chunks.json 加载所有文档 chunk，显示来源/anchor_id/页码/标题/内容预览
- **Clip 列表**：从 SQLite 加载所有视频切片，显示视频/时间/摘要/clip_id/向量状态
- **绑定关系表**：显示视频↔文档绑定，含状态检测（Anchor缺失/Clip缺失）
- **操作**：绑定选中、修复绑定（更换 anchor）、删除绑定
- **搜索过滤**：每栏支持关键词过滤

### 4.2 跨模态数据存储（SQLiteStore）
- `video_transcripts` 表：存储 ASR 转写段落
- `video_clips` 表：存储视频切片（起止时间+语义摘要）
- `cross_modal_map` 表：存储 PDF anchor ↔ 视频 clip 绑定关系
- 完整 CRUD：insert/update/get/list/delete

### 4.3 搜索结果中的跨模态扩展
- 检索命中 PDF chunk 时，自动查找绑定的视频片段
- 合成 video 类型结果卡片追加到展示列表
- 点击 video:// 链接可打开视频播放器

---

## 五、数据流管理

### 5.1 数据流面板（DataflowPanel）
- **三列可视化**：L1 Markdown | L2 JSON/Chunks | L3 Database
- **L1 操作**：文件列表、打开/编辑、转换→JSON
- **L2 操作**：Chunk 表格、内联编辑 content、API 优化、转换→MD
- **L3 操作**：向量数统计、同步状态、同步到 DB
- **API 优化触发**：顶部按钮 + 全局进度条

### 5.2 API 优化 Worker（api_optimize_worker）
- 加载任务模板 + 输入 chunks + QA 记忆
- 分批调用云端 API 优化 chunk 内容
- 支持多 provider：OpenAI / Claude / Gemini / Custom（OpenAI 兼容端点）
- 合并优化结果，可选触发 json_db_sync 推送到 DB

---

## 六、LLM 推理

### 6.1 LLM 管理器（LLMManager）
- **单例模式**：全局唯一实例
- **llama-server HTTP 模式**：通过子进程启动 llama-server.exe，HTTP API 调用
- **流式生成**：SSE 逐 token 迭代，无输出时降级为非流式调用
- **自动 prompt→messages 转换**：原始 RAG prompt 封装为 OpenAI chat messages 格式

### 6.2 llama-server 管理（LlamaServerManager）
- **子进程启动**：subprocess.Popen 启动 llama-server.exe
- **健康检查轮询**：GET /health，最多等 180s
- **自动下载**：GitHub Releases 下载 llama-server（可配置）
- **日志转发**：daemon 线程转发子进程日志
- **配置**：model_path、host、port、ctx_size、n_gpu_layers、parallel、batch_size、flash_attn 等

### 6.3 HTTP 客户端（LLMHttpClient）
- OpenAI 兼容 API：`/v1/chat/completions`（流式+非流式）
- 原生 API：`/completion`（非流式）
- 健康检查：`/health`
- SSE 解析：逐事件解析 text delta

---

## 七、系统功能

### 7.1 全局热键（HotkeyController）
- keyboard 库注册全局热键（默认 Alt+Space）
- 线程安全：background thread → QMetaObject.invokeMethod → UI thread

### 7.2 内存监控（MemoryWatcher）
- 空闲超时自动卸载 LLM（默认 10 分钟）
- 每次用户交互重置计时器
- 卸载后更新托盘图标状态

### 7.3 系统托盘（TrayIcon）
- 状态图标：ready(绿) / busy(黄) / unloaded(灰) / error(红)
- 右键菜单：偏好设置、加载模型、释放内存、打开编辑器、退出
- 模型操作 Worker：后台线程执行加载/卸载，完成后通知
- 双击托盘图标：切换 Spotlight 窗口

### 7.4 偏好设置（PreferencesDialog）
- 可视化编辑 config.json
- LLM 参数：model_path、n_gpu_layers、n_ctx、max_tokens、temperature 等
- llama-server 参数：host、port、ctx_size、parallel 等
- 路由参数：enabled、memory_window、few_shot_examples
- 检索参数：top_k、distance_threshold
- 切片参数：max_chars、overlap_chars
- UI 参数：hotkey、window_width、window_opacity

### 7.5 启动检查（startup_check）
- 检查必要文件和模型是否存在
- 缺失时弹出 StartupGuideDialog 引导用户
- 支持自动创建目录、提供下载链接

### 7.6 分类关键词（category_keywords）
- 技术类关键词集：api/sdk/python/安装/部署/配置/故障/调试...
- 业务类关键词集：sop/流程/制度/审批/业务/运营...
- 代词标记集：它/这个/那个/其/上面/前面/刚才...
- `heuristic_category()` — 启发式分类函数
- `contains_any()` — 关键词匹配函数

---

## 八、配置体系

### 8.1 配置文件（config.json）
| 配置节 | 说明 |
|--------|------|
| `llm` | Gemma 模型参数（model_path、n_ctx、max_tokens、temperature、top_k、top_p、min_p 等） |
| `llama_server` | llama-server 子进程配置（host、port、ctx_size、parallel、flash_attn、auto_download 等） |
| `retrieval` | 检索参数（top_k、distance_threshold） |
| `router` | 路由配置（enabled、memory_window、few_shot_examples、category_filter_enabled、max_validate_retries） |
| `qa_memory` | QA 记忆配置（enabled、path、jsonl_path、max_context_chars） |
| `chunking` | 切片配置（max_chars、overlap_chars、keep_heading_path、min_section_chars、micro_chunk_chars） |
| `pdf` | PDF 配置（parser_order=["marker"]、marker_device、parser_options） |
| `asr` | ASR 配置（subprocess、model_size、device、compute_type、language、beam_size、vad_filter） |
| `ui` | UI 配置（hotkey、window_width、window_opacity、animation_ms） |
| `idle_timeout_minutes` | 空闲超时（分钟） |
| `log_retention_days` | 日志保留天数 |
| `exports` | 导出配置（path、write_json、write_markdown） |

### 8.2 配置加载（config.py）
- 深度合并默认值 + 用户配置
- Dot-separated key 访问：`cfg.get("llm.max_tokens", 512)`
- 相对路径解析：`cfg.abs_path("data/vectors/vectors.db")`

---

## 九、日志与追踪

### 9.1 RAG 追踪日志
- 每次搜索完整记录：用户问题→改写→路由→定位→验证→检索命中→提示词→LLM 输出
- 含各阶段耗时、命中数量、尝试轮数、验证结果

### 9.2 自定义日志级别
- RAG_TRACE 级别：专门记录 RAG 全链路追踪
- 标准 logger：info/warning/error 分级

---

## 十、已废弃但保留的模块

| 模块 | 说明 |
|------|------|
| `app/models/embed_manager.py` | BGE-M3 嵌入模型管理器，已不再被调用，保留文件避免破坏引用 |
