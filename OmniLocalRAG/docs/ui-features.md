# Omni-Local RAG — UI 界面功能清单

## 1. 搜索框（SpotlightWindow）

| 属性 | 值 |
|------|-----|
| 类名 | `SpotlightWindow` |
| 窗口类型 | 无边框置顶 QWidget（Alt+Space 唤起） |
| UI 文件 | `spotlight_window.ui` |

### 功能流程

**输入 → 检索 → 回答生成**

1. 用户在搜索框输入问题，按回车触发检索
2. 系统执行 LLM 路由分类 + SQL 文本检索 + 自验证搜索
3. 检索结果直接在预览面板中显示为多个命中区块
4. LLM 流式生成回答，实时追加到预览面板

### 控件列表

| 控件 | objectName | 功能 |
|------|-----------|------|
| 拖拽手柄 | `SpotlightDragHandle` | 窗口拖动标题栏，显示"Omni-Local RAG" |
| 搜索输入框 | `SearchInput` | 单行输入，placeholder"请输入问题..."，支持清除按钮 |
| 停止按钮 | `StopButton` | 停止 LLM 生成，默认隐藏 |
| 计时标签 | `SearchTimingLabel` | 显示路由/检索/生成耗时，默认隐藏 |
| 预览面板 | `KnowledgeCard` | 全宽显示回答 + 所有命中内容 |
| 预览标题 | `KnowledgeCardTitle` | 显示"搜索结果 · N 条命中" |
| 预览元信息 | `KnowledgeCardMeta` | 显示首个结果的来源/页码/类型 |
| 回答区域 | `KnowledgeCardBody` | QTextBrowser，支持 Ctrl+滚轮缩放、HTML渲染、图片显示、引用链接 |
| 复制引用按钮 | `KnowledgeActionButton` | 复制首个结果的引用文本 |
| 打开源文件按钮 | `KnowledgeActionButton` | 打开首个结果的源文件 |

### 交互特性

- **窗口操作**：拖拽移动、边缘拖拽调整大小、Esc 隐藏、失焦自动隐藏
- **回答渲染**：Markdown 图片/链接、HTML 表格、关键词高亮、引用序号 `[1]` 可点击
- **命中内容**：每个检索结果显示为独立区块，含标题面包屑、语义描述、正文、源文件链接、anchor_id/video_clip_id 追溯

---

## 2. 系统托盘（TrayIcon）

| 属性 | 值 |
|------|-----|
| 类名 | `TrayIcon` |
| 窗口类型 | QSystemTrayIcon |
| UI 文件 | `tray_menu.ui` |

### 菜单项

| 菜单项 | actionName | 功能 |
|--------|-----------|------|
| 偏好设置 | `preferencesAction` | 打开偏好设置对话框 |
| 加载模型 | `loadModelsAction` | 后台线程加载 BGE-M3 + Gemma 模型 |
| 立即释放内存 | `freeMemoryAction` | 后台线程卸载模型 + gc.collect() |
| 打开知识库编辑器 | `openEditorAction` | 显示 KnowledgeEditor 主窗口 |
| （分隔线） | `separatorAction` | — |
| 完全退出 | `quitAction` | 退出应用 |

### 交互特性

- **托盘图标**：4 种状态（ready=绿、busy=黄、unloaded=灰、error=红）
- **双击托盘**：切换搜索框显示/隐藏
- **CJK 字体**：菜单字体优先使用 Microsoft YaHei UI，确保中文正常显示
- **模型操作**：在后台线程执行，操作期间禁用加载/释放按钮，完成后弹出气泡通知

---

## 3. 知识库编辑器（KnowledgeEditor）

| 属性 | 值 |
|------|-----|
| 类名 | `KnowledgeEditor` |
| 窗口类型 | QMainWindow（1200×760） |
| UI 文件 | `knowledge_editor.ui` |

### 工具栏

| 控件 | objectName | 功能 |
|------|-----------|------|
| 导入文件按钮 | `importFileButton` | 打开 PdfImportPanel 选择文件并导入 |
| 导入视频按钮 | `importVideoButton` | 选择视频文件并启动 ASR 转写 |

### 状态栏

| 控件 | objectName | 功能 |
|------|-----------|------|
| 阶段标签 | `stageLabel` | 显示当前处理阶段（就绪/正在转换/已完成/错误） |
| 进度条 | `progressBar` | 文件转换进度，默认隐藏 |

### 标签页（6 个 Tab）

| Tab 名称 | 类名 | 功能说明 |
|----------|------|----------|
| PDF纠错 | `PDFWorkbench` | PDF 预览 + Markdown 全文编辑 |
| 分块管理 | `ChunkWorkbench` | 导入 Markdown 并执行切块 |
| 视频切片 | `VideoWorkbench` | 视频播放 + ASR + 手动标记切片 |
| 数据流管理 | `DataflowPanel` | 三栏数据流可视化管理 |
| 跨模态绑定 | `CrossModalPanel` | 文档 Anchor 与视频 Clip 绑定管理 |
| API设置 | `ApiSettingsPanel` | 外部 API 模型配置 |

### 跨模态绑定区（底部）

| 控件 | objectName | 功能 |
|------|-----------|------|
| 视频切片下拉 | `clipCombo` | 选择视频切片 |
| 关联文档锚点下拉 | `anchorCombo` | 选择文档 Anchor |
| 绑定按钮 | `bindButton` | 创建跨模态绑定记录 |

---

## 4. PDF 纠错工作台（PDFWorkbench）

| 属性 | 值 |
|------|-----|
| 类名 | `PDFWorkbench` |
| 窗口类型 | QWidget（Tab 内嵌） |
| UI 文件 | `pdf_workbench.ui` |

### 布局：左右双栏 Splitter

#### 左栏 — PDF 预览

| 控件 | objectName | 功能 |
|------|-----------|------|
| 文件名标签 | `fileLabel` | 显示当前 PDF 文件名 |
| 上一页 | `prevPageButton` | ◀ 翻页 |
| 页码输入 | `pageSpin` | 直接跳转页码 |
| 总页数 | `pageTotalLabel` | 显示 "/ N" |
| 下一页 | `nextPageButton` | ▶ 翻页 |
| 缩小 | `zoomOutButton` | - 缩小 PDF |
| 放大 | `zoomInButton` | + 放大 PDF |
| 原始大小 | `actualSizeButton` | 100% 显示 |
| 适应整页 | `fitPageButton` | 自动适应视口 |
| 缩放标签 | `zoomLabel` | 显示当前缩放比例 |
| PDF 滚动区 | `pdfScroll` | PDF 页面渲染区域 |

#### 右栏 — Markdown 编辑

| 控件 | objectName | 功能 |
|------|-----------|------|
| 保存 Markdown | `saveMarkdownButton` | 保存编辑后的 Markdown + 导出页图 |
| Markdown 编辑器 | `markdownEditor` | QTextEdit，等宽字体，全文编辑 |

### 交互特性

- **PDF 选区映射**：在 PDF 上框选文字，自动在 Markdown 编辑器中定位对应文本
- **PyMuPDF 渲染**：依赖 fitz 库，未安装时显示提示

---

## 5. 分块管理工作台（ChunkWorkbench）

| 属性 | 值 |
|------|-----|
| 类名 | `ChunkWorkbench` |
| 窗口类型 | QWidget（Tab 内嵌） |
| UI 文件 | `chunk_workbench.ui` |

### 控件列表

| 控件 | objectName | 功能 |
|------|-----------|------|
| 文件标签 | `fileLabel` | 显示当前 Markdown 文件名 |
| 导入 Markdown | `importMarkdownButton` | 选择 .md 文件导入 |
| 启动切块 | `startChunkButton` | 执行切块操作 |

### 分块参数

| 参数 | 控件 | 范围 | 默认值 |
|------|------|------|--------|
| 最大字符数 (max_chars) | Slider + SpinBox | 400–4000，步长 100 | 1800 |
| 重叠字符数 (overlap_chars) | Slider + SpinBox | 0–800，步长 50 | 240 |
| 最小段落长度 (min_section_chars) | SpinBox | 20–500，步长 10 | 80 |
| 保留标题路径前缀 | CheckBox | — | ✓ |

### 其他控件

| 控件 | objectName | 功能 |
|------|-----------|------|
| 状态标签 | `statusLabel` | 显示切块状态/结果数量 |
| 切块表格 | `chunkTable` | 5 列：#、页、Heading path、Block、内容预览 |

---

## 6. 视频切片工作台（VideoWorkbench）

| 属性 | 值 |
|------|-----|
| 类名 | `VideoWorkbench` |
| 窗口类型 | QWidget（Tab 内嵌） |
| UI 文件 | `video_workbench.ui` |

### 控件列表

| 控件 | objectName | 功能 |
|------|-----------|------|
| 状态标签 | `statusLabel` | 显示视频加载/转写状态 |
| 进度条 | `progressBar` | ASR 转写进度 |
| 时间线 | `_TimelineWidget` | 自绘时间线，显示 ASR 段落 + 播放头 |
| 播放按钮 | `playButton` | ▶ 播放 / ⏸ 暂停 |
| 外部播放 | `openExternalButton` | 用系统默认播放器打开 |
| 位置标签 | `positionLabel` | 显示当前播放位置 MM:SS |
| 标记开始 | `markInButton` | [I] 标记切片起始点 |
| 标记结束 | `markOutButton` | [O] 标记切片结束点 |
| 确认切片 | `confirmClipButton` | → 输入语义摘要并保存切片到 DB + 向量化 |
| 标记信息 | `markLabel` | 显示 In/Out 时间点 |
| 片段计数 | `clipListLabel` | 显示已标记片段数量 |

### 快捷键

| 按键 | 功能 |
|------|------|
| I | 标记开始点 |
| O | 标记结束点 |
| Enter | 确认切片 |
| Space | 播放/暂停 |

---

## 7. 数据流管理面板（DataflowPanel）

| 属性 | 值 |
|------|-----|
| 类名 | `DataflowPanel` |
| 窗口类型 | QWidget（Tab 内嵌） |
| UI 文件 | `dataflow_panel.ui` |

### 顶部工具栏

| 控件 | objectName | 功能 |
|------|-----------|------|
| 标题 | `titleLabel` | "数据流管理" |
| AI 优化按钮 | `apiOptimizeButton` | 调用外部 API 优化选中 JSON |
| 自动同步复选框 | `autoSyncCheckBox` | 优化后自动同步到 DB |
| 进度条 | `progressBar` | API 优化进度 |
| 日志标签 | `logLabel` | 单行操作日志 |

### 三栏 Splitter 布局

#### 左栏 — L1 Markdown（人工可读）

| 控件 | objectName | 功能 |
|------|-----------|------|
| 文件列表 | `markdownList` | QListWidget，列出所有 .chunks.md 文件 |
| 刷新列表 | `refreshFilesButton` | 重新扫描导出目录 |
| 打开编辑 | `openMarkdownButton` | 用系统编辑器打开 MD 文件 |
| 转换 → JSON | `mdToJsonButton` | MD → JSON 转换 |

#### 中栏 — L2 JSON（API 接口数据）

| 控件 | objectName | 功能 |
|------|-----------|------|
| JSON 路径 | `jsonPathEdit` | 只读，显示当前 JSON 文件路径 |
| 浏览 | `browseJsonButton` | 手动选择 JSON 文件 |
| Chunk 表格 | `chunkTable` | 5 列：#、Heading path、Block、is_manual、内容预览 |
| 内联编辑器 | `chunkEditor` | QTextEdit，双击行打开编辑，最大高度 120px |
| 保存修改 | `saveChunkButton` | 保存编辑内容到 JSON + 标记 is_manual |
| 重新加载 | `reloadJsonButton` | 重新加载当前 JSON |
| 转换 → MD | `jsonToMdButton` | JSON → MD 转换 |

#### 右栏 — L3 数据库（检索引擎）

| 控件 | objectName | 功能 |
|------|-----------|------|
| DB 统计 | `dbStatsLabel` | 显示向量数量 + 数据库文件名 |
| 同步状态 | `syncStatusLabel` | 显示新增/变更/未变/合计 |
| 检查变更 | `checkDiffButton` | 检查 JSON 与 DB 的差异 |
| 同步到 DB | `syncButton` | 将 JSON 数据同步到向量数据库 |
| 同步进度 | `syncProgressBar` | 同步进度条 |

---

## 8. 跨模态绑定面板（CrossModalPanel）

| 属性 | 值 |
|------|-----|
| 类名 | `CrossModalPanel` |
| 窗口类型 | QWidget（Tab 内嵌） |
| UI 文件 | `cross_modal_panel.ui` |

### 顶部

| 控件 | objectName | 功能 |
|------|-----------|------|
| 标题 | `titleLabel` | "跨模态绑定管理" |
| 状态 | `statusLabel` | 显示 Anchors/Clips/Bindings 计数 |
| 刷新 | `refreshButton` | 刷新所有表格 |

### 三栏 Splitter 布局

#### 左栏 — 文档 Anchor

| 控件 | objectName | 功能 |
|------|-----------|------|
| 筛选输入 | `anchorFilterEdit` | 模糊筛选 Anchor 列表 |
| Anchor 表格 | `anchorTable` | 5 列：来源、anchor_id、页码、标题、内容预览 |

#### 中栏 — 视频 Clip

| 控件 | objectName | 功能 |
|------|-----------|------|
| 筛选输入 | `clipFilterEdit` | 模糊筛选 Clip 列表 |
| Clip 表格 | `clipTable` | 5 列：视频、时间、摘要、clip_id、向量 |

#### 右栏 — 绑定记录

| 控件 | objectName | 功能 |
|------|-----------|------|
| 筛选输入 | `bindingFilterEdit` | 模糊筛选绑定列表 |
| 绑定表格 | `bindingTable` | 7 列：视频、时间、摘要、Anchor来源、Anchor、状态、绑定ID |
| 绑定选中 | `bindSelectedButton` | 将选中的 Anchor + Clip 创建绑定 |
| 修复到选中 Anchor | `repairSelectedButton` | 修改绑定记录指向新 Anchor |
| 删除绑定 | `deleteBindingButton` | 删除选中的绑定记录 |

---

## 9. API 设置面板（ApiSettingsPanel）

| 属性 | 值 |
|------|-----|
| 类名 | `ApiSettingsPanel` |
| 窗口类型 | QWidget（Tab 内嵌） |
| UI 文件 | `api_settings_panel.ui` |

### 提供商选择

| 控件 | objectName | 功能 |
|------|-----------|------|
| 提供商下拉 | `providerCombo` | openai / claude / gemini / custom |
| Base URL | `baseUrlEdit` | API 基础地址，切换提供商自动填充默认值 |
| API Key | `apiKeyEdit` | 密码模式输入，支持显示/隐藏切换 |
| 显示/隐藏按钮 | `showKeyButton` | 切换 API Key 可见性 |
| 模型名称 | `modelEdit` | 如 gpt-4o-mini / claude-3-5-sonnet |

### 生成参数

| 控件 | objectName | 范围 | 默认值 |
|------|-----------|------|--------|
| Temperature | `tempSlider` + `tempValueLabel` | 0.0–2.0 | 0.2 |
| Max tokens | `maxTokensSpin` | 256–16384 | 4096 |
| Batch size | `batchSizeSpin` | 1–50 | 10 |
| 超时 | `timeoutSpin` | 10–300 秒 | 60 |

### 操作

| 控件 | objectName | 功能 |
|------|-----------|------|
| 测试连接 | `testButton` | 后台线程 ping API，显示成功/失败 |
| 保存配置 | `saveButton` | 保存到 config.json |
| 状态日志 | `statusEdit` | QTextEdit，显示测试结果和操作日志 |

---

## 10. 偏好设置对话框（PreferencesDialog）

| 属性 | 值 |
|------|-----|
| 类名 | `PreferencesDialog` |
| 窗口类型 | QDialog |
| UI 文件 | `preferences_dialog.ui` |

### 标签页（5 个 Tab）

#### llama-server

| 参数 | 控件 | 说明 |
|------|------|------|
| llama-server 目录 | `serverDirEdit` + 浏览 | 服务端二进制所在目录 |
| GGUF 模型文件 | `serverModelPathEdit` + 浏览 | llama-server 专用模型 |
| 监听地址 | `serverHostEdit` | 默认 127.0.0.1 |
| 监听端口 | `serverPortSpin` | 1024–65535，默认 8000 |
| GPU 层数 | `serverGpuLayersSpin` | -1–9999，默认 999 |
| 上下文长度 | `serverCtxSizeSpin` | 512–131072，默认 8192 |
| Flash Attention | `serverFlashAttnCheck` | 默认开启 |
| 禁用内存映射 | `serverNoMmapCheck` | 默认开启 |
| NUMA 策略 | `serverNumaCombo` | 空/distribute/isolate/numactl |
| 额外参数 | `serverExtraArgsEdit` | 自定义命令行参数 |
| 缺少二进制时自动下载 | `serverAutoDownloadCheck` | 默认开启 |

#### LLM 推理

| 参数 | 控件 | 说明 |
|------|------|------|
| 启用 LLM 推理 | `llmEnabledCheck` | 总开关 |
| GGUF 模型文件 | `llmModelPathEdit` + 浏览 | 独立 LLM 模型 |
| GPU 层数 | `llmGpuLayersSpin` | 0–200 |
| 上下文长度 | `llmNCtxSpin` | 512–32768 |
| 最大输出 token | `llmMaxTokensSpin` | 64–4096，默认 512 |
| Temperature | `llmTemperatureSpin` | 0.0–2.0，默认 0.7 |
| Repeat penalty | `llmRepeatPenaltySpin` | 1.0–2.0，默认 1.1 |
| 空闲卸载超时 | `idleTimeoutSpin` | 1–120 分钟，默认 10 |

#### 检索

| 参数 | 控件 | 说明 |
|------|------|------|
| Top-K | `topKSpin` | 1–50，默认 5 |
| 距离阈值 | `distanceThresholdSpin` | 0.0–1.0，默认 0.7 |

#### 界面 / 热键

| 参数 | 控件 | 说明 |
|------|------|------|
| 全局热键 | `hotkeyEdit` | 默认 alt+space |
| 搜索窗口宽度 | `windowWidthSpin` | 500–1600 px |
| 窗口透明度 | `windowOpacitySpin` | 0.5–1.0 |
| 动画时长 | `animationMsSpin` | 0–500 ms |

#### 分块参数

| 参数 | 控件 | 说明 |
|------|------|------|
| 最大 chunk 长度 | `maxCharsSpin` | 200–8000 字符，默认 1800 |
| 重叠长度 | `overlapCharsSpin` | 0–2000 字符，默认 240 |
| 最小段落长度 | `minSectionCharsSpin` | 10–500 字符，默认 80 |
| 保留 heading 路径 | `keepHeadingPathCheck` | 默认开启 |

### 操作按钮

| 按钮 | 功能 |
|------|------|
| 保存 | 写入 config.json 并关闭 |
| 取消 | 放弃修改并关闭 |

---

## 11. 文件导入配置对话框（PdfImportPanel）

| 属性 | 值 |
|------|-----|
| 类名 | `PdfImportPanel` |
| 窗口类型 | QDialog（模态） |
| UI 文件 | `pdf_import_panel.ui` |

### 控件列表

| 控件 | objectName | 功能 |
|------|-----------|------|
| 文件路径 | `fileEdit` | 只读，显示选中的文件路径 |
| 浏览按钮 | `browseButton` | 选择 PDF/Office/文本文件 |

### 转换技术类型（左栏）

| 控件 | objectName | 功能 |
|------|-----------|------|
| 解析器列表 | `parserList` | 可拖拽排序的解析器列表（docling/marker/unstructured/mineru/ocr） |

### 技术介绍（右栏）

| 控件 | objectName | 功能 |
|------|-----------|------|
| 标题 | `parserIntroTitleLabel` | 解析器名称 |
| 技术说明 | `parserIntroTextLabel` | 解析器技术说明 |
| 图像数据 | `parserIntroImageLabel` | 是否生成图像数据 |
| 基础信息 | `parserIntroBasicLabel` | 依赖和兼容性说明 |
| 参数表单 | 动态生成 | 每个解析器的可配置参数 |

### 页面范围

| 控件 | objectName | 功能 |
|------|-----------|------|
| 起始页 | `startPageSpin` | 0=全部，1–9999 |
| 结束页 | `endPageSpin` | 0=全部，1–9999 |

### 操作按钮

| 按钮 | 功能 |
|------|------|
| 保存并开始导入 | 保存配置到 config.json + accept |
| Cancel | 取消导入 |

---

## 12. 视频播放对话框（VideoPlayerDialog）

| 属性 | 值 |
|------|-----|
| 类名 | `VideoPlayerDialog` |
| 窗口类型 | QDialog（800×500） |
| UI 文件 | `video_player.ui` |

### 控件列表

| 控件 | objectName | 功能 |
|------|-----------|------|
| 视频画面 | `QVideoWidget` | 嵌入式视频渲染 |
| 播放按钮 | `playButton` | ▶ 播放 / ⏸ 暂停 |
| 进度条 | `seekBar` | 拖拽跳转播放位置 |
| 全屏按钮 | `fullscreenButton` | ⛶ 切换全屏 |

---

## 13. 首次启动引导（StartupGuideDialog）

| 属性 | 值 |
|------|-----|
| 类名 | `StartupGuideDialog` |
| 窗口类型 | QDialog |
| UI 文件 | `startup_guide.ui` |

### 控件列表

| 控件 | objectName | 功能 |
|------|-----------|------|
| 介绍标签 | `introLabel` | "检测到以下必需文件缺失..." |
| 缺失项列表 | `itemsLayout`（动态） | 每项显示：! 图标 + 名称 + 路径 + 下载按钮 |
| 提示标签 | `hintLabel` | "将模型文件放入 models/ 目录..." |
| 关闭按钮 | `closeButton` | "关闭并退出" |

---

