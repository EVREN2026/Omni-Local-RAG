# Omni-Local RAG — 开发任务计划

**项目:** Omni-Local RAG  
**计划版本:** V1.0  
**创建日期:** 2026-04-09  
**总任务数:** 23 项（Week 1–8）

---

## 进度总览

| 周期 | 任务数 | 状态 |
|------|--------|------|
| Week 1 — 基建与 UI 交互 | 5 | ✅ 已完成 |
| Week 2 — 异步推理引擎 | 3 | ✅ 已完成 |
| Week 3 — 检索与数据管道 | 3 | ✅ 已完成 |
| Week 4 — PDF 可视化编辑 | 2 | ✅ 已完成 |
| Week 5 — 视频切片辅助台 | 2 | ✅ 已完成 |
| Week 6 — 多模态卡片流 | 2 | ✅ 已完成 |
| Week 7 — 内存极致调优 | 3 | ✅ 已完成 |
| Week 8 — 打包与封版联调 | 3 | ⏳ 待执行 |

---

## Week 1 — 基建与 UI 交互

### ✅ T1 · 搭建 PyQt5 项目骨架与目录结构

**交付文件:**
```
OmniLocalRAG/
├── main.py
├── config.json
├── requirements.txt
├── OmniLocal.spec
├── app/{controllers,models,views,workers,utils}/
├── assets/{icons,styles/main.qss}
├── models/  data/{chroma,thumbs}/  logs/
```

**验收:** `python main.py` 无报错启动

---

### ✅ T2 · 实现 SpotlightWindow 无边框毛玻璃 UI

**交付文件:** `app/views/spotlight_window.py`

**关键实现:**
- `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint` 无边框
- Windows Acrylic 毛玻璃 / 降级半透明背景
- 宽度固定 680px，高度自适应（最大 80% 屏幕高度）
- 多显示器支持（显示于鼠标所在屏幕）
- 呼出/隐藏动画 80ms（透明度淡入淡出）
- 输入框自动聚焦，`Esc` 隐藏

---

### ✅ T3 · 实现全局热键注册与呼出/隐藏逻辑

**交付文件:** `app/controllers/hotkey_controller.py`

**关键实现:**
- `keyboard` 库注册 `Alt+Space`（suppress=True）
- 热键回调通过 `QMetaObject.invokeMethod` 切回主线程
- 注册失败弹窗提示，支持 `config.json` 自定义热键

**验收:** Alt+Space 呼出延迟 < 100ms

---

### ✅ T4 · 实现系统托盘图标四态状态机

**交付文件:** `app/views/tray_icon.py`

**状态机:**

| 状态 | 颜色 | 触发条件 |
|------|------|----------|
| ready | 绿色 | 模型已加载 |
| busy | 黄色闪烁 | 推理/ASR 进行中 |
| unloaded | 灰色 | 模型卸载后 |
| error | 红色 | 未恢复异常 |

**右键菜单:** 偏好设置 / 立即释放内存 / 打开知识库编辑器 / 完全退出

---

### ✅ T5 · 实现 Logger 模块与 MemoryWatcher 框架

**交付文件:** `app/utils/logger.py` · `app/controllers/memory_watcher.py`

**Logger:**
- `TimedRotatingFileHandler`，`midnight` 滚动，保留 30 天
- 格式: `YYYY-MM-DD HH:MM:SS [LEVEL] module: message`

**MemoryWatcher:**
- `QTimer` 单例，10 分钟无交互触发 `LLMManager.unload()`
- `reset()` 在每次用户交互/窗口显示时调用

---

## Week 2 — 异步推理引擎

### ✅ T6 · 封装 LLMManager 单例与 EmbedManager 单例

**交付文件:** `app/models/llm_manager.py` · `app/models/embed_manager.py`

**LLMManager 关键设计:**
```python
class LLMManager:
    _instance = None  # 严格单例
    def load(self) -> bool: ...      # 懒加载，已加载直接返回 True
    def unload(self, reason): ...    # None + gc.collect() + cuda.empty_cache()
    def generate(self, prompt, stream=True) -> Iterator[str]: ...
```

**EmbedManager:**
- BGE-M3 常驻内存，不随 MemoryWatcher 卸载
- `encode(texts) -> List[List[float]]`，normalize=True

---

### ✅ T7 · 实现 InferenceWorker 异步推理线程

**交付文件:** `app/workers/inference_worker.py`

**信号定义:**
```python
token_generated  = pyqtSignal(str)    # 单 Token 流式
context_retrieved = pyqtSignal(list)  # 检索结果列表
generation_finished = pyqtSignal(bool)
error_occurred   = pyqtSignal(str)
```

**run() 流程:** embed query → ChromaDB.query() → build_prompt() → LLM.generate(stream=True)

**日志:** 检索耗时(ms) + 生成速度(tok/s)

---

### ✅ T8 · 实现打字机效果渲染与停止生成按钮

**交付文件:** `app/views/spotlight_window.py`（`_on_token` / `_blink_cursor`）

**关键实现:**
- Token 追加间隔 ≤ 16ms（对齐 60fps）
- `▋` 光标闪烁动效（QTimer 500ms）
- 停止按钮调用 `InferenceWorker.cancel()`
- 推理期间托盘变黄

---

## Week 3 — 检索与数据管道

### ✅ T9 · 封装 ChromaStore 与 IngestWorker 数据管道

**交付文件:** `app/models/chroma_store.py` · `app/workers/ingest_worker.py`

**ChromaStore API:**
```python
add(content, embedding, source_type, ...) -> str  # chunk_id
query(embedding, n_results) -> List[dict]
update(doc_id, content, embedding) -> None
delete(doc_id) -> None
```

**IngestWorker:**
- Docling 解析 → 失败降级 OCR → 切片（512 tokens / 64 overlap）
- 进度信号 `progress(current, total)`
- `re_embed(chunk_id, new_content)` 热更新

---

### ✅ T10 · PDF 切片脚本与 SQLite 数据库初始化

**交付文件:** `app/models/sqlite_store.py`

**建表 DDL:** `video_transcripts` · `video_clips` · `cross_modal_map` · `app_config`

**切片规格:** UUID v4 anchor_id，metadata 含 pdf_payload JSON + created_at + version=1

**验收:** 100 页 PDF 入库 < 60s

---

### ✅ T11 · 串联 Retriever + Generator 完整 RAG 流程

**交付文件:** `app/controllers/search_controller.py`

**流程:** `SearchController.search(query)` → `InferenceWorker.run()` → 信号驱动 UI 渲染

**RAG Prompt 模板:** 3 条参考文档 + 来源标注 + 中文回答指令

---

## Week 4 — PDF 可视化编辑台

### ✅ T12 · 实现 PDFWorkbench 可视化编辑工作台

**交付文件:** `app/views/pdf_workbench.py`

**布局:** QSplitter 左右分栏（550:550）
- 左：PyMuPDF 渲染 PDF 页面（1.5× 缩放），支持橡皮筋框选
- 右：QTextEdit 可编辑 Markdown
- 框选联动：`_SelectablePDFLabel` 捕获鼠标区域 → `selection_changed` 信号 → 右侧定位

**降级:** 加密/图片 PDF → Tesseract OCR → 弹窗告知

---

### ✅ T13 · 实现 PDF 编辑保存与热更新逻辑

**保存链路:**
```
用户点击"保存" → ingest_ctrl.re_embed_chunk(id, new_content)
  → IngestWorker.re_embed() → EmbedManager.encode() → ChromaDB.update()
  → search_ctrl.invalidate_cache(id)
  → 下次搜索命中最新内容
```

**metadata 更新:** `version+1`、`is_manual=True`、`updated_at=now`

---

## Week 5 — 视频切片辅助台

### ✅ T14 · 集成 faster-whisper ASR 与视频预处理流程

**交付文件:** `app/workers/asr_worker.py`

**ASR 流程:** `WhisperModel.transcribe()` → 逐段 `segment_ready` 信号 → SQLite `video_transcripts` 入库

**预处理:** ffprobe 预检文件完整性；每 30s 提取帧缩略图存 `data/thumbs/{clip_id}.jpg`

**验收:** 10 分钟视频转写 < 3 分钟（CPU base 模型）

---

### ✅ T15 · 开发 VideoWorkbench 视频切片工作台

**交付文件:** `app/views/video_workbench.py`

**快捷键:**
| 键 | 动作 |
|----|------|
| `I` | 标记开始（In-point） |
| `O` | 标记结束（Out-point） |
| `Enter` | 确认切片，弹出语义摘要输入框 |
| `Space` | 播放/暂停 |

**时间轴:** `_TimelineWidget`，ASR 块颜色深浅代表置信度，播放头实时跟踪

**验收:** 打点误差 < 0.5s，切片存入 SQLite 后可被语义搜索命中

---

## Week 6 — 多模态卡片流

### ✅ T16 · 实现 PDFCard + VideoCard 多模态卡片组件

**交付文件:** `app/views/result_cards.py` · `app/views/video_player.py`

**PDFCard:** 缩略图 120×90（懒加载）+ 关键词高亮 + 双击 `os.startfile()` 打开对应页

**VideoCard:** 缩略图 + 时间范围 `MM:SS - MM:SS` + 点击内嵌 `QMediaPlayer` 起播

**VideoPlayerDialog:** 独立弹窗，`QMediaPlayer.setPosition(start_ms)` 精准起播

**验收:** 播放起始误差 ≤ ±1s

---

### ✅ T17 · 实现跨模态对齐绑定与瀑布流卡片排序

**交付文件:** `app/views/knowledge_editor.py`

**绑定 UI:** 编辑器底部下拉框选择 video_clip_id + pdf_anchor_id → `SQLiteStore.insert_cross_modal()`

**卡片排序规则:**
1. `distance`（cosine）升序
2. `is_manual=True` 优先
3. 同分时 video 优先于 pdf

---

## Week 7 — 内存极致调优

### ✅ T18 · 完善 MemoryWatcher 动态卸载与资源调优

**交付文件:** `app/controllers/memory_watcher.py`（完整实现）

**行为:** 窗口隐藏超 10min → `LLMManager.unload()` + 托盘变灰  
**BGE-M3 常驻** 不受 MemoryWatcher 影响

**验收:** 闲置 10min 后任务管理器工作集 < 600MB（节约 1.5GB+）

---

### ✅ T19 · 编写启动依赖检查与冷启动引导页

**交付文件:** `app/utils/startup_check.py` · `app/views/startup_guide.py`

**检查项:**
- `models/gemma-2-2b-it-q4_k_m.gguf`
- `models/bge-m3/`
- `data/chroma/`（auto_create）
- `data/omni.db`（auto_create）
- `data/thumbs/`（auto_create）

**引导页:** 列出缺失项 + 操作说明 + "前往下载" 按钮（触发浏览器打开 HuggingFace）

---

## Week 8 — 打包与封版联调

### ⏳ T21 · 配置 PyInstaller 打包并补充运行时 DLLs

**交付文件:** `OmniLocal.spec`（已创建，待实际构建验证）

**待执行:**
- [ ] 补充 `binaries` 中 `llama.dll` 实际路径
- [ ] 添加 app.ico 图标
- [ ] 在有依赖环境的机器上运行 `pyinstaller OmniLocal.spec`
- [ ] 验证构建无 WARNING

**命令:**
```bash
pyinstaller OmniLocal.spec
```

**验收:** 生成 `dist/OmniLocal/OmniLocal.exe`，无 Python 环境可双击启动

---

### ⏳ T22 · 裸机全量回归测试与部署 SLA 验收

**测试环境:** 全新 Windows 11（无 Python / VC++ 环境）

**Performance SLA 复测清单:**
- [ ] 热键呼出 < 100ms
- [ ] TTFT < 1.5s
- [ ] 纯 CPU 推理 > 5 tok/s（i5 10代）
- [ ] 闲置内存 < 600MB
- [ ] 向量检索 < 200ms（10k 文档）

**Functional SLA 验收清单（28 项）:**
- [ ] Top-1 召回率 = 100%（20 条标注切片）
- [ ] 视频播放起始误差 ≤ ±1s
- [ ] 数据热更新即时生效
- [ ] PDF 双击正确打开对应页面
- [ ] 导入 10 个加密 PDF 不崩溃
- [ ] 冷启动引导页正确显示缺失项
- [ ] 连续 50 次问答内存无泄漏

**Deployment SLA:**
- [ ] EXE 裸机双击可启动
- [ ] 无注册表/AppData 污染
- [ ] 删除目录即完成卸载
- [ ] EXE + DLL 包体积 < 500MB（不含模型）

---

### ⏳ T23 · 代码安全扫描与最终代码审查

**安全扫描:**
```bash
pip install bandit
bandit -r app/ -ll
```
**目标:** 无 HIGH 级别告警

**Code Review 检查点:**
- [ ] MVC 层次边界无越权（Model 不引入 PyQt5 UI）
- [ ] 所有 IO 操作在 QThread Worker 中执行
- [ ] 无硬编码绝对路径
- [ ] 异常全部被捕获，主进程不崩溃

---

## 文件交付清单

| 文件路径 | 所属模块 | 完成状态 |
|----------|----------|----------|
| `main.py` | 程序入口 | ✅ |
| `config.json` | 配置 | ✅ |
| `requirements.txt` | 依赖 | ✅ |
| `OmniLocal.spec` | 打包配置 | ✅（待实测） |
| `app/utils/logger.py` | 日志 | ✅ |
| `app/utils/config.py` | 配置读取 | ✅ |
| `app/utils/startup_check.py` | 依赖检查 | ✅ |
| `app/models/llm_manager.py` | LLM 单例 | ✅ |
| `app/models/embed_manager.py` | Embedding 单例 | ✅ |
| `app/models/chroma_store.py` | 向量存储 | ✅ |
| `app/models/sqlite_store.py` | 关系型存储 | ✅ |
| `app/controllers/hotkey_controller.py` | 全局热键 | ✅ |
| `app/controllers/search_controller.py` | 搜索调度 | ✅ |
| `app/controllers/ingest_controller.py` | 导入调度 | ✅ |
| `app/controllers/memory_watcher.py` | 内存监控 | ✅ |
| `app/workers/inference_worker.py` | 推理线程 | ✅ |
| `app/workers/ingest_worker.py` | 入库线程 | ✅ |
| `app/workers/asr_worker.py` | ASR 线程 | ✅ |
| `app/views/spotlight_window.py` | 搜索主窗口 | ✅ |
| `app/views/tray_icon.py` | 系统托盘 | ✅ |
| `app/views/result_cards.py` | 结果卡片 | ✅ |
| `app/views/video_player.py` | 视频播放器 | ✅ |
| `app/views/knowledge_editor.py` | 知识库编辑器 | ✅ |
| `app/views/pdf_workbench.py` | PDF 纠错台 | ✅ |
| `app/views/video_workbench.py` | 视频切片台 | ✅ |
| `app/views/startup_guide.py` | 冷启动引导 | ✅ |
| `assets/styles/main.qss` | 界面样式 | ✅ |

---

## 下一步行动（Week 8 待执行）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 放入模型文件
# models/gemma-2-2b-it-q4_k_m.gguf
# models/bge-m3/

# 3. 安全扫描
bandit -r app/ -ll

# 4. 打包
pyinstaller OmniLocal.spec

# 5. 裸机测试
# 将 dist/OmniLocal/ 复制到无 Python 的 Windows 11 机器
# 双击 OmniLocal.exe，验证 SLA 清单
```

---

*由 Omni-Local RAG 开发团队维护 · 2026-04-09*
