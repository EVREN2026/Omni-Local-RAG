# Omni-Local RAG 系统开发与验收规范文档

**文档版本:** V1.1  
**文档日期:** 2026-04-09  
**适用对象:** 核心开发团队、QA 测试团队  
**核心定位:** 极简交互、零隐私泄露、深度多模态绑定的桌面级本地知识库

---

## 目录

1. [项目概况](#1-项目概况)
2. [软件需求规格说明 (SRS)](#2-软件需求规格说明-srs)
3. [系统架构设计](#3-系统架构设计)
4. [代码开发规范](#4-代码开发规范)
5. [多模态数据规范 (Data Schema)](#5-多模态数据规范-data-schema)
6. [验收标准 (Acceptance Criteria)](#6-验收标准-acceptance-criteria)
7. [风险与降级策略](#7-风险与降级策略)
8. [八周敏捷开发计划](#8-八周敏捷开发计划)
9. [附录](#9-附录)

---

## 1. 项目概况

### 1.1 基本信息

| 字段 | 内容 |
|------|------|
| 项目名称 | Omni-Local RAG |
| 运行环境 | Windows 10/11 (x64)，8GB+ RAM |
| Python 版本 | Python 3.10+ |
| 文档状态 | 正式发布 |

### 1.2 核心技术栈

| 层次 | 组件 | 版本要求 | 用途 |
|------|------|----------|------|
| GUI 框架 | PyQt5 | ≥ 5.15 | 桌面界面与事件系统 |
| 推理引擎 | llama-cpp-python | ≥ 0.2.x | 加载 Gemma-2-2B-IT-Q4_K_M.gguf |
| 向量检索 | ChromaDB | ≥ 0.4.x | 向量特征存储与语义检索 |
| Embedding | sentence-transformers (BGE-M3) | ≥ 2.x | 文本向量化，约 1GB 显存 |
| PDF 解析 | Docling | latest | PDF 结构化、表格与标题提取 |
| 语音转写 | faster-whisper | ≥ 0.10 | 视频 ASR，生成带时间戳字幕 |
| 元数据存储 | SQLite 3 | 内置 | 跨模态对齐映射表 |
| 打包工具 | PyInstaller | ≥ 5.x | 打包独立 EXE |

### 1.3 核心功能矩阵

| 功能域 | 核心能力 | 优先级 |
|--------|----------|--------|
| 全局检索 | Alt+Space 唤起无边框 Spotlight，毫秒级响应 | P0 |
| 多模态 RAG | PDF 文本/图像 + 视频切片联合语义检索 | P0 |
| 人工编辑台 | PDF 纠错 + 视频打点 + 跨模态对齐绑定 | P0 |
| 资源调度 | 模型动态卸载，闲置内存 < 600MB | P1 |
| 零隐私 | 完全离线，无任何网络请求（模型下载除外） | P0 |

---

## 2. 软件需求规格说明 (SRS)

### 2.1 UI/UX 交互需求

#### 2.1.1 Spotlight 全局搜索

**触发与隐藏逻辑：**

- 按 `Alt + Space` 全局唤起无边框搜索窗口
- 再次按 `Alt + Space` 或窗口**失去焦点**时立即隐藏（非销毁）
- 窗口呼出后，输入框必须自动获取键盘焦点，无需鼠标点击
- `Esc` 键亦可触发隐藏

**视觉规格：**

- 支持 Windows Acrylic 毛玻璃特效（需系统 DWM 支持，降级时使用半透明纯色背景）
- 圆角半径：12px；无系统边框（`Qt.FramelessWindowHint`）
- 窗口尺寸：宽度固定 680px，高度随结果数量自适应展开（最大 80% 屏幕高度）
- 居中显示于主显示器，支持多显示器环境（显示于鼠标所在屏幕）
- 呼出/隐藏动画：透明度淡入淡出，时长 80ms（超过 100ms SLA 前完成）

#### 2.1.2 多模态结果瀑布流

**流式文本输出（Typewriter Effect）：**

- AI 生成的总结答案以单 Token 为粒度实时追加，禁止等待全部生成后再渲染
- Token 渲染间隔≤ 16ms（对齐 60fps），避免 UI 卡顿
- 生成过程中显示光标闪烁动效；生成完毕后光标消失

**图文锚点卡片（PDF Card）：**

- 展示字段：PDF 页面截图缩略图 + 高亮来源文本段落 + 文件名/页码
- 缩略图尺寸：120×90px，支持懒加载
- 双击卡片：调用 `os.startfile()` 以系统默认 PDF 阅读器打开源文件，并尝试跳转至对应页码（通过 URL Fragment `#page=N`）
- 高亮文本：对命中的关键词在摘要中用 `<mark>` 样式标注（QSS 实现）

**视频播放卡片（Video Card）：**

- 展示字段：视频帧缩略图（取片段中间帧）+ 语义摘要文本 + 时间戳范围（`MM:SS - MM:SS`）
- 内嵌 `QMediaPlayer` + `QVideoWidget`，点击缩略图或播放按钮立即在卡片内起播
- 起播时间戳精确到秒，误差需在 ±1s 以内（见验收标准 5.2）
- 支持全屏按钮，点击后弹出独立播放器窗口

**卡片排序规则：**

1. 相关性得分（ChromaDB cosine distance 升序）
2. `is_manual = True` 的条目优先展示
3. 同分时，视频卡片优先于纯文本卡片

#### 2.1.3 系统托盘与后台驻留

**生命周期：**

- 点击窗口关闭按钮（或 `Alt+F4`）：**隐藏**窗口，程序不退出，进程持续驻留
- 仅通过托盘右键菜单"完全退出"才真正终止进程
- 首次最小化时弹出一次气泡提示："Omni-Local RAG 已在后台运行，按 Alt+Space 唤起"

**托盘图标状态机：**

| 状态 | 图标颜色 | 触发条件 |
|------|----------|----------|
| 就绪 | 绿色 | 模型已加载，等待输入 |
| 处理中 | 黄色（闪烁） | 推理/检索/ASR 进行中 |
| 已卸载 | 灰色 | 模型因闲置超时被卸载 |
| 错误 | 红色 | 出现未恢复的异常 |

**右键菜单项：**

```
偏好设置          → 打开 ConfigDialog
立即释放内存      → 触发 ModelManager.unload_all()
打开知识库编辑器  → 打开 KnowledgeEditorWindow
─────────────────
完全退出          → QApplication.quit()
```

---

### 2.2 数据处理与对齐需求

#### 2.2.1 PDF 预处理流程

```
输入 PDF
   │
   ├─► Docling.parse()
   │     ├─ 提取标题层级 (H1/H2/H3)
   │     ├─ 提取表格 → Markdown 格式
   │     └─ 提取段落文本 + 页码 + 坐标 (coords)
   │
   ├─► 生成知识切片
   │     ├─ 按语义段落切块（最大 512 tokens，重叠 64 tokens）
   │     └─ 为每个切片生成唯一 anchor_id (UUID v4)
   │
   └─► 存入 ChromaDB + 更新 SQLite metadata 表
```

**失败降级：**
- 加密 PDF / 纯图片 PDF → 捕获 `DoclingParseError` → 自动调用 Tesseract OCR 降级处理 → 弹窗告知用户
- 降级后的切片 `is_manual = False`，提示用户进入编辑台校验

#### 2.2.2 视频预处理流程

```
导入视频文件
   │
   ├─► 后台 QThread: faster-whisper.transcribe()
   │     ├─ 输出: [{start, end, text}, ...]
   │     └─ 进度信号 → 托盘图标变黄 + 进度条
   │
   ├─► ASR 结果存入 SQLite (video_transcripts 表)
   │
   └─► 提取视频帧缩略图 (每 30s 取 1 帧，存 /data/thumbs/)
```

#### 2.2.3 可视化人工编辑台（Human-in-the-loop）

**PDF 纠错工作台：**

- 左侧：PDF 原始渲染（基于 `PyMuPDF` 渲染为 QPixmap）
- 右侧：可编辑 Markdown 文本（`QTextEdit`）
- 支持鼠标框选左侧区域，右侧自动定位至对应文本块
- 保存时：更新 ChromaDB 向量（触发重新 Embedding）+ 设置 `is_manual = True`

**视频切片工作台：**

- 时间轴轨道：ASR 字幕块可视化（颜色深浅代表置信度）
- 波形图：通过 `librosa` 或 `ffmpeg` 提取音频波形数据渲染
- 快捷键操作：
  - `I`：标记片段 Start
  - `O`：标记片段 End
  - `Enter`：确认切片，弹出语义摘要输入框
  - `Space`：播放/暂停预览
- 每个手动切片生成独立 UUID，存入 SQLite `video_clips` 表

**跨模态对齐绑定：**

- 编辑台底部提供"关联 PDF 段落"下拉框，列出所有 PDF anchor_id（显示预览文本）
- 建立映射关系存入 SQLite `cross_modal_map` 表：

```sql
CREATE TABLE cross_modal_map (
    id          TEXT PRIMARY KEY,
    video_clip_id TEXT NOT NULL,
    pdf_anchor_id TEXT NOT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    note        TEXT
);
```

---

### 2.3 冷启动与容错体验

#### 2.3.1 启动依赖检查序列

```python
# 启动时按序检查，任一失败触发引导页
STARTUP_CHECKS = [
    CheckItem("GGUF 模型文件", path="models/gemma-2-2b-it-q4_k_m.gguf"),
    CheckItem("ChromaDB 数据目录", path="data/chroma/"),
    CheckItem("BGE-M3 模型缓存", path="models/bge-m3/"),
    CheckItem("SQLite 数据库", path="data/omni.db", auto_create=True),
]
```

- 引导页设计：单页向导，列出缺失项目 + 操作说明 + 可选"一键下载"按钮（仅触发浏览器打开 HuggingFace 链接）
- 所有文件路径相对于 EXE 所在目录，**禁止**使用绝对路径或系统环境变量

#### 2.3.2 异常防崩溃策略

| 异常场景 | 处理方式 |
|----------|----------|
| PDF 解析失败 | 捕获异常 → 降级 OCR → 弹窗提示 → 继续运行 |
| LLM OOM | 捕获 `MemoryError` → 触发卸载 → 弹窗提示"内存不足，已释放模型" |
| ChromaDB 查询失败 | 捕获异常 → 返回空结果 → UI 显示"检索服务暂时不可用" |
| ASR 进程崩溃 | 子进程隔离，崩溃不影响主进程 → 弹窗"语音转写失败，请重试" |
| 视频文件损坏 | ffprobe 预检 → 提前拒绝 → 弹窗提示具体原因 |

---

## 3. 系统架构设计

### 3.1 模块层次图

```
┌─────────────────────────────────────────────────────────┐
│                      View Layer (PyQt5)                  │
│  SpotlightWindow  │  KnowledgeEditorWindow  │  TrayIcon  │
│  ResultCardList   │  PDFEditWorkbench        │  ConfigDlg │
└──────────────────────────┬──────────────────────────────┘
                           │ Signals & Slots
┌──────────────────────────▼──────────────────────────────┐
│                   Controller Layer                        │
│  HotkeyController  │  SearchController  │  EditController│
│  ModelManager      │  IngestController  │  MemoryWatcher │
└──────┬─────────────────────┬────────────────────────────┘
       │                     │
┌──────▼──────┐   ┌──────────▼──────────────────────────┐
│  QThread    │   │           Model Layer                 │
│  Workers:   │   │  ChromaDB  │  SQLite  │  FileSystem   │
│  - Infer    │   │  BGE-M3    │  Docling │  faster-whsp  │
│  - Ingest   │   │  LlamaCpp  │          │               │
│  - ASR      │   └────────────────────────────────────  ┘
└─────────────┘
```

### 3.2 目录结构规范

```
OmniLocalRAG/
├── main.py                    # 程序入口
├── app/
│   ├── controllers/
│   │   ├── hotkey_controller.py
│   │   ├── search_controller.py
│   │   ├── ingest_controller.py
│   │   └── memory_watcher.py
│   ├── models/
│   │   ├── chroma_store.py
│   │   ├── sqlite_store.py
│   │   ├── llm_manager.py      # 单例 LLM
│   │   └── embed_manager.py    # 单例 BGE-M3
│   ├── views/
│   │   ├── spotlight_window.py
│   │   ├── result_cards.py
│   │   ├── knowledge_editor.py
│   │   ├── pdf_workbench.py
│   │   ├── video_workbench.py
│   │   └── tray_icon.py
│   ├── workers/
│   │   ├── inference_worker.py
│   │   ├── ingest_worker.py
│   │   └── asr_worker.py
│   └── utils/
│       ├── logger.py
│       ├── config.py
│       └── startup_check.py
├── assets/
│   ├── icons/                  # 托盘图标 (绿/黄/灰/红)
│   └── styles/
│       └── main.qss
├── models/                     # .gguf 与 BGE-M3 模型文件
├── data/
│   ├── chroma/                 # ChromaDB 持久化目录
│   ├── thumbs/                 # 视频帧缩略图缓存
│   └── omni.db                 # SQLite 数据库
├── logs/                       # 按日期滚动日志
├── config.json                 # 用户配置文件
└── OmniLocal.spec              # PyInstaller 打包配置
```

---

## 4. 代码开发规范

### 4.1 架构设计规范 (MVC 强制约束)

| 层次 | 职责 | 禁止事项 |
|------|------|----------|
| Model | 数据存取、向量检索、模型推理 | 不得导入任何 PyQt5 UI 组件 |
| View | 界面渲染、用户输入捕获 | 不得直接调用 IO 或推理方法 |
| Controller | 协调 Model 与 View，管理 Worker 生命周期 | 不得在主线程执行耗时操作（> 50ms） |

### 4.2 异步推理与通信机制

所有耗时操作**必须**通过 `QThread` + 信号槽通信，严禁 `QApplication.processEvents()` 替代方案：

```python
from PyQt5.QtCore import QThread, pyqtSignal
from typing import List

class InferenceWorker(QThread):
    """
    职责：执行向量检索 + LLM 流式生成
    线程安全：所有输出通过信号传递，禁止直接操作 UI 对象
    """
    token_generated = pyqtSignal(str)        # 单 Token 流式输出
    context_retrieved = pyqtSignal(list)     # 检索到的上下文切片列表
    generation_finished = pyqtSignal(bool)   # True=成功, False=失败/中断
    error_occurred = pyqtSignal(str)         # 错误信息字符串

    def __init__(self, query: str, top_k: int = 5):
        super().__init__()
        self.query = query
        self.top_k = top_k
        self._cancelled = False

    def cancel(self):
        """外部调用以中断生成"""
        self._cancelled = True

    def run(self):
        try:
            # Step 1: 向量检索（耗时，需计时）
            t0 = time.perf_counter()
            results = chroma_store.query(self.query, n_results=self.top_k)
            retrieval_ms = (time.perf_counter() - t0) * 1000
            logger.info(f"Retrieval top-{self.top_k} took {retrieval_ms:.1f}ms")

            self.context_retrieved.emit(results)

            # Step 2: 构建 Prompt
            prompt = build_rag_prompt(self.query, results)

            # Step 3: llama-cpp 流式生成
            t1 = time.perf_counter()
            token_count = 0
            for token in llm_manager.instance.generate(prompt, stream=True):
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
            llm_manager.unload()
            self.error_occurred.emit("内存不足，模型已自动释放，请重启后重试")
            self.generation_finished.emit(False)
        except Exception as e:
            logger.error(f"Inference error: {e}", exc_info=True)
            self.error_occurred.emit(str(e))
            self.generation_finished.emit(False)
```

### 4.3 内存与资源调度

#### 4.3.1 单例模式（强制）

```python
# app/models/llm_manager.py
class LLMManager:
    _instance: Optional['LLMManager'] = None
    _llm: Optional[Llama] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, config: dict) -> bool:
        """加载模型，若已加载则直接返回 True"""
        if self._llm is not None:
            return True
        try:
            self._llm = Llama(
                model_path=config["model_path"],
                n_gpu_layers=config.get("n_gpu_layers", 0),
                n_ctx=config.get("n_ctx", 4096),
                verbose=False
            )
            return True
        except Exception as e:
            logger.error(f"LLM load failed: {e}")
            return False

    def unload(self):
        """释放模型内存"""
        self._llm = None
        gc.collect()
        # 若有 CUDA 上下文，额外清空
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
```

#### 4.3.2 动态卸载计时器

```python
# app/controllers/memory_watcher.py
IDLE_TIMEOUT_MS = 10 * 60 * 1000  # 10 分钟

class MemoryWatcher(QObject):
    def __init__(self):
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setInterval(IDLE_TIMEOUT_MS)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_idle_timeout)

    def reset(self):
        """每次用户交互或窗口显示时调用，重置计时"""
        self._timer.stop()
        self._timer.start()

    def _on_idle_timeout(self):
        if not spotlight_window.isVisible():
            LLMManager().unload()
            EmbedManager().unload()
            tray_icon.set_state("unloaded")
            logger.info("Models unloaded due to idle timeout")
```

#### 4.3.3 显存分配策略

| 组件 | 驻留策略 | 显存占用 |
|------|----------|----------|
| BGE-M3 | 常驻内存/显存，不卸载 | ~1.0 GB |
| Gemma-2-2B Q4 | 按需加载，闲置 10min 后卸载 | ~1.5 GB (CPU) / ~1.8 GB (GPU) |
| ChromaDB | 进程生命周期内持久连接 | < 200 MB |

`config.json` 配置项：

```json
{
  "llm": {
    "model_path": "models/gemma-2-2b-it-q4_k_m.gguf",
    "n_gpu_layers": 0,
    "n_ctx": 4096,
    "max_tokens": 512,
    "temperature": 0.7
  },
  "embed": {
    "model_path": "models/bge-m3",
    "device": "cpu"
  },
  "retrieval": {
    "top_k": 5,
    "distance_threshold": 0.7
  },
  "idle_timeout_minutes": 10
}
```

### 4.4 日志追踪规范

```python
# app/utils/logger.py
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

def setup_logger() -> logging.Logger:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger("omni_rag")
    logger.setLevel(logging.DEBUG)

    # 按天滚动，保留 30 天
    handler = TimedRotatingFileHandler(
        filename=log_dir / "app.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8"
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)
    return logger
```

**必须记录的关键事件：**

| 事件 | 日志级别 | 包含字段 |
|------|----------|----------|
| 应用启动完成 | INFO | 总启动耗时(ms)、模型加载状态 |
| 向量检索完成 | INFO | query 摘要、top_k 数量、耗时(ms) |
| 推理完成 | INFO | token 数量、生成速度(tok/s) |
| 模型卸载 | INFO | 触发原因（idle/manual/oom） |
| OOM 异常 | ERROR | 完整异常栈、当前内存使用量 |
| 文件解析失败 | WARNING | 文件路径、异常类型、降级方式 |

---

## 5. 多模态数据规范 (Data Schema)

### 5.1 ChromaDB 文档 Metadata 结构

所有送入 ChromaDB 的分块数据，其 `metadata` 必须严格遵循以下结构：

| 字段名 | 数据类型 | 约束 | 说明 |
|--------|----------|------|------|
| `id` | String | Required | 知识切片的唯一 UUID v4 |
| `content` | String | Required | 送入 Embedding 的纯文本（用于检索） |
| `source_type` | Enum | Required | 枚举值：`pdf` \| `video` \| `hybrid` |
| `anchor_id` | String | Optional | 业务对齐键（跨模态关联凭证） |
| `pdf_payload` | JSON String | Optional | 例：`{"file":"a.pdf","page":5,"coords":[x,y,w,h]}` |
| `video_payload` | JSON String | Optional | 例：`{"file":"b.mp4","start":125,"end":150}` |
| `is_manual` | Boolean | Required | 是否经过人工校对工具确认 |
| `created_at` | ISO8601 String | Required | 切片创建时间 |
| `version` | Integer | Required | 每次人工修改后递增，用于热更新判断 |

### 5.2 SQLite 数据库表结构

```sql
-- 视频转写结果
CREATE TABLE video_transcripts (
    id          TEXT PRIMARY KEY,
    video_file  TEXT NOT NULL,
    start_sec   REAL NOT NULL,
    end_sec     REAL NOT NULL,
    text        TEXT NOT NULL,
    confidence  REAL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 人工切片（覆盖或补充 ASR）
CREATE TABLE video_clips (
    id            TEXT PRIMARY KEY,
    video_file    TEXT NOT NULL,
    start_sec     REAL NOT NULL,
    end_sec       REAL NOT NULL,
    semantic_summary TEXT NOT NULL,
    chroma_id     TEXT,              -- 对应 ChromaDB 的 document id
    is_manual     BOOLEAN DEFAULT 1,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 跨模态对齐映射
CREATE TABLE cross_modal_map (
    id             TEXT PRIMARY KEY,
    video_clip_id  TEXT NOT NULL REFERENCES video_clips(id),
    pdf_anchor_id  TEXT NOT NULL,
    note           TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 系统配置（键值对）
CREATE TABLE app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

### 5.3 热更新触发机制

```
用户在编辑台点击"保存"
   │
   ├─► 更新 SQLite 记录（updated_at、semantic_summary 等）
   │
   ├─► IngestWorker.re_embed(chunk_id)
   │     ├─ 调用 BGE-M3 生成新向量
   │     └─ ChromaDB.update(id=chunk_id, embeddings=[...], metadata={...})
   │
   └─► 触发 SearchController.invalidate_cache(chunk_id)
         └─ 下次搜索自动命中最新版本
```

---

## 6. 验收标准 (Acceptance Criteria)

### 6.1 性能验收 (Performance SLA)

| 指标 | 目标值 | 测试方法 |
|------|--------|----------|
| 快捷键呼出延迟 | < 100ms | 从按键事件到窗口完全可见+输入框聚焦 |
| TTFT（首字时延） | < 1.5s | 从 Enter 按下到第一个 Token 渲染在 UI |
| 推理吞吐量（纯 CPU） | > 5 tok/s | i5 10代 CPU，关闭 GPU offload |
| 闲置内存占用 | < 600MB | 触发 10min 卸载后，任务管理器工作集 |
| 向量检索耗时 | < 200ms | top_k=5，库中 10,000 条文档 |

### 6.2 功能与准确性验收 (Functional SLA)

| 功能项 | 验收标准 | 测试方法 |
|--------|----------|----------|
| 精准召回 | Top-1 命中率 = 100% | 针对 20 条人工标记的视频切片，逐一提问，验证首条结果命中 |
| 播放锚定精度 | 起播误差 ≤ ±1s | 记录点击时间戳与实际 `QMediaPlayer.position()`，计算差值 |
| 数据热更新 | 修改后下一次查询立即体现 | 修改切片摘要 → 立即搜索相同问题 → 验证结果文本已更新 |
| PDF 双击打开 | 正确打开对应页面 | 双击图文卡片，系统默认 PDF 阅读器打开并定位正确页码 |
| 防崩溃 | 主进程不崩溃 | 导入 10 个加密 PDF → 验证全程无崩溃，弹窗提示正确 |
| 冷启动引导 | 引导页正常弹出 | 删除 models/ 目录后启动，验证引导页显示缺失项 |

### 6.3 交付与部署验收 (Deployment SLA)

| 指标 | 标准 |
|------|------|
| 免环境运行 | `OmniLocal.exe` 在全新 Windows 11（无 Python/VC++ 环境）双击可启动 |
| 目录整洁 | 运行后不向 `C:\Users\*\AppData`、注册表、系统 PATH 写入任何内容 |
| 文件自包含 | 模型文件在 `models/`，用户数据在 `data/`，日志在 `logs/`，无其他路径污染 |
| 卸载干净 | 删除整个程序目录即完成卸载，无残留 |
| 包体积 | 除模型文件外，EXE + 依赖 DLL 总计 < 500MB |

---

## 7. 风险与降级策略

| 风险 | 概率 | 影响 | 降级/缓解措施 |
|------|------|------|---------------|
| Docling 解析大型 PDF 超时 | 中 | 中 | 设置 30s 超时，超时后自动降级 OCR |
| BGE-M3 在低内存机器 OOM | 低 | 高 | 提前检查可用内存，< 4GB 时警告并提供 minilm 替代方案 |
| faster-whisper 转写长视频耗时过长 | 高 | 低 | 后台进度条显示，不阻塞主线程，支持取消 |
| PyInstaller 打包遗漏 DLL | 中 | 高 | CI 流程中增加裸机启动测试，维护 `.spec` 中的 `binaries` 白名单 |
| QMediaPlayer 格式不支持 | 中 | 中 | 导入时用 ffprobe 转码为 H.264 MP4 兼容格式 |
| Windows 11 热键被其他软件占用 | 低 | 中 | 注册失败时弹出提示，允许用户在设置中自定义热键组合 |

---

## 8. 八周敏捷开发计划

### 总体里程碑

```
Week 1    Week 2    Week 3    Week 4    Week 5    Week 6    Week 7    Week 8
  │         │         │         │         │         │         │         │
  ▼         ▼         ▼         ▼         ▼         ▼         ▼         ▼
基建+UI  异步推理  检索管道   PDF编辑  视频切片  多模态流  内存调优  打包封版
```

### 详细任务分解

#### Week 1 — 基建与 UI 交互

**目标：** 核心 UI 骨架可运行，热键与托盘生命周期闭环

| # | 任务 | 负责人 | 验收标准 |
|---|------|--------|----------|
| 1.1 | 搭建 PyQt5 项目骨架（目录结构、入口、QSS 基础样式） | 全员 | `python main.py` 无报错启动 |
| 1.2 | 实现 `SpotlightWindow`：无边框、圆角、毛玻璃特效 | FE | 视觉效果与设计稿一致 |
| 1.3 | 实现全局热键注册（keyboard 库 + 主线程安全回调） | FE | Alt+Space 呼出/隐藏响应时间 < 100ms |
| 1.4 | 实现系统托盘图标与右键菜单（4 个菜单项响应） | FE | 托盘菜单所有项可点击，颜色状态切换正常 |
| 1.5 | 实现 `MemoryWatcher` 基础框架（含 QTimer） | BE | 控制台可观察到计时器触发日志 |
| 1.6 | 搭建 `Logger` 模块（按日期滚动） | BE | `logs/app_<date>.log` 正常生成 |

**交付物：** 可快捷呼出/隐藏的 UI 窗口，托盘图标状态切换正常，日志输出正常

---

#### Week 2 — 异步推理引擎

**目标：** 能对硬编码文本进行流式问答，UI 不卡死

| # | 任务 | 负责人 | 验收标准 |
|---|------|--------|----------|
| 2.1 | 封装 `LLMManager`（单例、加载/卸载、配置读取） | BE | 单元测试：重复获取实例为同一对象 |
| 2.2 | 实现 `InferenceWorker`（QThread + 信号定义） | BE | 流式 Token 信号正常触发 |
| 2.3 | UI 实现打字机效果（连接 `token_generated` 信号） | FE | 文字逐字追加，无闪烁或积压 |
| 2.4 | 实现 OOM 保护逻辑 + 错误信号弹窗 | BE | 模拟内存不足，弹窗提示正确内容 |
| 2.5 | 实现推理过程中托盘图标变黄（闪烁动效） | FE | 推理期间图标持续黄色闪烁 |
| 2.6 | 实现停止生成按钮（调用 `worker.cancel()`） | FE | 点击停止后生成立即中断 |

**交付物：** 输入框提问能流畅吐出回复，界面在推理时不卡死

---

#### Week 3 — 检索与数据管道

**目标：** 具备完整的单文档 RAG 能力

| # | 任务 | 负责人 | 验收标准 |
|---|------|--------|----------|
| 3.1 | 封装 `EmbedManager`（BGE-M3 单例，CPU/GPU 自动选择） | BE | 向量维度正确（1024 维），耗时 < 1s/chunk |
| 3.2 | 封装 `ChromaStore`（初始化、add、query、update、delete） | BE | 单元测试覆盖所有 CRUD 操作 |
| 3.3 | 编写 PDF 文本切片脚本（含 anchor_id 生成） | BE | 100 页 PDF 切片入库 < 60s |
| 3.4 | 实现 `IngestWorker`（后台批量向量化 + 入库） | BE | 入库进度通过信号通知 UI |
| 3.5 | 串联检索与生成（`context_retrieved` 信号触发卡片渲染） | FE+BE | 问答结果包含"参考来源"文件名和页码 |
| 3.6 | 编写 RAG Prompt 模板（含上下文注入格式） | BE | 3 条参考文档均出现在答案上方 |

**交付物：** 程序具备单文本 RAG 能力，回答带有参考来源标注

---

#### Week 4 — PDF 可视化编辑台

**目标：** 能对 PDF 识别结果进行人工校对并热更新向量

| # | 任务 | 负责人 | 验收标准 |
|---|------|--------|----------|
| 4.1 | 集成 Docling（结构化解析 + 表格提取） | BE | 标准 PDF 解析正确率 > 90% |
| 4.2 | 实现 `PDFWorkbench` 左右分栏 UI | FE | 左侧 PDF 渲染清晰，右侧可编辑 |
| 4.3 | 实现框选联动（左侧框选 → 右侧定位对应文本块） | FE | 框选精度在段落级别 |
| 4.4 | 实现保存逻辑（SQLite 更新 + 触发热更新） | BE | 保存后 ChromaDB 向量立即更新 |
| 4.5 | 实现加密/图片 PDF 降级处理 + OCR 模式 | BE | 加密 PDF 不崩溃，提示降级 |
| 4.6 | `is_manual = True` 标记逻辑 | BE | 保存后 metadata.is_manual 值正确 |

**交付物：** 能在 UI 上修改 PDF 识别错误，并立即生效至向量库

---

#### Week 5 — 视频切片辅助台

**目标：** 能导入视频、ASR 转写、人工打点并存储语义标签

| # | 任务 | 负责人 | 验收标准 |
|---|------|--------|----------|
| 5.1 | 集成 faster-whisper（后台 ASR，进度信号） | BE | 10 分钟视频转写 < 3 分钟（CPU） |
| 5.2 | 实现视频时间轴 UI（字幕块渲染 + 波形图） | FE | 时间轴拖拽流畅，字幕块颜色代表置信度 |
| 5.3 | 实现快捷键打点（I/O 标记 Start/End，Enter 确认） | FE | 打点误差 < 0.5s |
| 5.4 | 实现语义摘要录入弹窗 | FE | 录入内容正确存入 `video_clips` 表 |
| 5.5 | 实现视频片段向量化入库 | BE | 切片入库后可被语义搜索命中 |
| 5.6 | 视频帧缩略图提取（ffmpeg，存入 `data/thumbs/`） | BE | 缩略图生成正确，文件名含 clip_id |

**交付物：** 能导入视频，人工标记 30 秒高光片段并打上语义标签

---

#### Week 6 — 多模态卡片流

**目标：** RAG 问答能同时召回 PDF 卡片和视频卡片，点击视频卡片精准起播

| # | 任务 | 负责人 | 验收标准 |
|---|------|--------|----------|
| 6.1 | 实现 `PDFCard` 组件（缩略图 + 高亮文本 + 双击打开） | FE | 双击正确打开对应 PDF 页面 |
| 6.2 | 实现 `VideoCard` 组件（缩略图 + 摘要 + 时间戳） | FE | 点击后弹出内嵌播放器 |
| 6.3 | 集成 `QMediaPlayer`（含起播时间戳设置） | FE | 播放起始误差 ≤ ±1s |
| 6.4 | 实现 `cross_modal_map` 绑定 UI（编辑台中的关联下拉框） | FE | 绑定关系正确存入 SQLite |
| 6.5 | 实现混合 `hybrid` source_type 的卡片渲染 | FE | `hybrid` 卡片同时展示 PDF 和视频信息 |
| 6.6 | 实现卡片排序逻辑（相关性 + is_manual 优先） | BE | 人工标记切片稳定排在前 3 位 |

**交付物：** RAG 问答能召回视频卡片，点击精准跳转对应秒数起播

---

#### Week 7 — 内存极致调优

**目标：** 通过 10 分钟闲置测试，内存骤降 1.5GB+

| # | 任务 | 负责人 | 验收标准 |
|---|------|--------|----------|
| 7.1 | 完善 `MemoryWatcher`（集成 LLM + Embed 卸载） | BE | 计时器触发后内存正确下降 |
| 7.2 | 调优 `n_gpu_layers` 边界（CPU/GPU 混合 profiling） | BE | 在 8GB RAM 机器上推理不触发系统换页 |
| 7.3 | 编写启动依赖检查 `startup_check.py` | BE | 缺失模型文件时引导页正确显示 |
| 7.4 | 压力测试：连续 50 次问答后内存无泄漏 | QA | 连续测试后内存稳定，无持续增长 |
| 7.5 | 实测 Performance SLA 全部指标 | QA | 5 项 SLA 指标全部达标 |
| 7.6 | 修复 Week 1-6 遗留 Bug | 全员 | P0/P1 Bug 清零 |

**交付物：** 观测 10 分钟闲置后，系统内存骤降 1.5GB+，SLA 全部达标

---

#### Week 8 — 打包与封版联调

**目标：** 裸机可运行的独立 EXE，全量回归通过

| # | 任务 | 负责人 | 验收标准 |
|---|------|--------|----------|
| 8.1 | 配置 `OmniLocal.spec`（隐式导入、资产、图标） | DevOps | PyInstaller 构建无 Warning |
| 8.2 | 补充运行时 DLLs（llama.dll、VC++ 运行库） | DevOps | EXE 在无 Python 环境的 Win11 正常启动 |
| 8.3 | 裸机全量回归测试（功能 SLA 28 项） | QA | 通过率 = 100% |
| 8.4 | 性能 SLA 裸机复测 | QA | 5 项 SLA 均达标 |
| 8.5 | 代码 Review + 安全扫描（bandit） | 全员 | 无 HIGH 级别安全告警 |
| 8.6 | 更新 `CHANGELOG.md` 与用户手册 | 全员 | 文档与实际功能一致 |

**交付物：** `OmniLocal.exe` 裸机可双击启动，全量回归测试 100% 通过

---

## 9. 附录

### 9.1 关键依赖版本锁定

```
# requirements.txt（核心依赖，完整版见 pyproject.toml）
PyQt5==5.15.10
llama-cpp-python==0.2.90
chromadb==0.4.24
sentence-transformers==2.7.0
docling==1.0.0
faster-whisper==1.0.3
keyboard==0.13.5
PyMuPDF==1.24.0
librosa==0.10.2
pyinstaller==6.6.0
```

### 9.2 热键方案备选

| 默认 | 备选 1 | 备选 2 | 备注 |
|------|--------|--------|------|
| `Alt + Space` | `Ctrl + Shift + Space` | `Win + Shift + F` | 用户可在设置中自定义 |

### 9.3 术语表

| 术语 | 定义 |
|------|------|
| TTFT | Time To First Token，首字时延，从用户按 Enter 到第一个 Token 出现在 UI 的时间 |
| anchor_id | PDF 知识切片的唯一业务标识符，用于跨模态关联 |
| is_manual | 标记该知识切片是否经过人工编辑台校验确认 |
| hybrid | source_type 枚举值，表示该切片同时关联了 PDF 和视频内容 |
| OOM | Out Of Memory，内存溢出 |
| n_gpu_layers | llama-cpp 配置项，控制模型中有多少层卸载到 GPU 计算 |
| BGE-M3 | BAAI General Embedding M3，多语言多功能文本嵌入模型 |

---

*文档由 Omni-Local RAG 核心开发团队维护。如有疑问或变更请通过 Issue 提交评审。*
