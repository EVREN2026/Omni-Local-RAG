# Omni-Local RAG — 功能架构评估报告

**评估日期:** 2026-04-11  
**评估基准:** 代码库实际实现（app/ 目录全量分析）  
**评估范围:** 架构合理性、功能完整度、代码质量、可维护性、性能风险

---

## 目录

1. [总体评分](#1-总体评分)
2. [架构设计评估](#2-架构设计评估)
3. [各功能模块评估](#3-各功能模块评估)
4. [技术选型评估](#4-技术选型评估)
5. [代码质量评估](#5-代码质量评估)
6. [性能与可靠性评估](#6-性能与可靠性评估)
7. [关键架构决策分析](#7-关键架构决策分析)
8. [改进优先级路线图](#8-改进优先级路线图)

---

## 1. 总体评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 7.5 / 10 | MVC 分层清晰，子进程沙箱是亮点，但部分耦合问题存在 |
| 功能完整度 | 6.5 / 10 | 核心链路完整，但 2 个 P0 功能为 stub，数据一致性存在缺口 |
| 代码质量 | 6.0 / 10 | 规范意识好，但有重复代码、编码问题和接口不一致 |
| 性能与可靠性 | 7.0 / 10 | 子进程隔离出色，但向量检索 O(N) 存在扩展瓶颈 |
| 可维护性 | 6.5 / 10 | 数据流三层有助于维护，但配置耦合和 stub 降低可信度 |
| **综合** | **6.7 / 10** | 原型级别可用，距生产就绪需补完 P0/P1 问题 |

---

## 2. 架构设计评估

### 2.1 整体架构：MVC + 子进程沙箱

**优势：**

```
View 层 (PyQt5) ── 信号/槽 ──► Controller 层 ── Worker(QThread) ──► Model 层
                                                        │
                                              Sub-Process Sandbox
                                           (LLM / ASR 独立进程)
```

- **MVC 分层执行良好：** Model 层无 PyQt5 依赖（验证通过），View 层不直接调用 IO/推理（通过 Controller 中转）
- **子进程沙箱是突出亮点：** LLM（`llm_subprocess.py`）和 ASR（`asr_subprocess.py`）均在独立进程中运行，通过 JSON 事件流通信。这是应对 llama-cpp-python 和 faster-whisper 在 Windows 上频繁崩溃的工程级解决方案，主进程真正做到了"永不崩溃"
- **信号/槽设计规范：** InferenceWorker 的 4 个信号设计覆盖了完整生命周期（token/context/finished/error）
- **单例管理一致：** LLMManager、EmbedManager、ChromaStore、SQLiteStore、MemoryWatcher 均采用单例，资源共享清晰

**问题：**

- **MemoryWatcher 单例需要 `_ready` 标志规避 PyQt5 sip bug：** 这是一个框架级的已知 bug，当前通过类级 `_ready` 标志解决，实属无奈之举但处理得当
- **ingest_video_clip() 破坏 MVC：** `IngestController` 中 `ingest_video_clip()` 同步调用 EmbedManager，在 UI 线程执行嵌入，可能阻塞 200ms~2s

### 2.2 数据流三层架构：亮点功能

```
L1: *.chunks.md  →  人工可读/可编辑（DataflowPanel）
        ↕ md_json_converter
L2: *.chunks.json  →  结构化 chunk 数据（API 优化输入/输出）
        ↕ json_db_sync + sync_json_to_db()
L3: vectors.db  →  SQLite + numpy 向量存储
```

**评价：** 这是整个架构中最有创意的设计。三层分离让用户可以：
1. 直接编辑 Markdown（L1）
2. 用 AI API 批量优化（L2）
3. 版本控制（L2 JSON 文件可 git 追踪）
4. 最终同步到可检索的向量库（L3）

这种"人工可编辑的知识库"理念超出了大多数 RAG 系统的设计思路，是产品差异化的核心价值。

**问题：** L2→L3 同步不是自动的，用户需手动点击"同步"按钮。编辑 chunk 后忘记同步会导致搜索结果与编辑内容不一致，这是用户体验风险。

### 2.3 配置管理架构：中度技术债

```python
# 三种配置访问模式并存（混乱）：
cfg.get("llm.n_ctx")           # 正常方式
cfg._cache["api"] = settings   # 直接操作缓存（绕过 load()）
cfg._cache.clear()             # 清空缓存（强制下次重读）
```

`cfg._cache` 是模块级私有变量，但多处代码直接操作它。这形成了"隐式契约"——任何修改 `_cache` 的代码都需要理解其内部实现，增加了维护成本。

---

## 3. 各功能模块评估

### 3.1 全局 Spotlight 搜索

**完成度：** ✅ 90%  
**评分：** 8.5 / 10

| 特性 | 状态 | 备注 |
|------|------|------|
| Alt+Space 唤起 | ✅ 完整 | keyboard 库 + invokeMethod 跨线程 |
| 淡入淡出动画 | ✅ 完整 | QPropertyAnimation 80ms |
| 多屏幕支持 | ✅ 完整 | 定位于鼠标所在屏幕 25% 高度 |
| 流式 token 输出 | ✅ 完整 | 逐 token 信号追加，光标闪烁 |
| 停止生成按钮 | ✅ 完整 | cancel() 中断标志 |
| 降级模式 | ✅ 完整 | no_results / retrieval_only / llm_load_failed |
| 热键自定义 | ✅ 完整 | config.json → ui.hotkey |
| 窗口宽度自定义 | ✅ 完整 | ui.window_width |
| 结果卡片排序 | ✅ 基础实现 | distance + is_manual，缺少同分时视频优先逻辑 |

**亮点：** `keyboard` 库 + `QMetaObject.invokeMethod(QueuedConnection)` 的跨线程热键方案非常稳健，避免了直接 `emit` 可能引发的竞态条件。

### 3.2 PDF 解析与知识导入

**完成度：** ✅ 85%  
**评分：** 7.5 / 10

| 特性 | 状态 | 备注 |
|------|------|------|
| 多解析器支持（5个） | ✅ 完整 | 用户拖拽排序，无自动降级 |
| Markdown 阶段输出 | ✅ 完整 | 保留页图引用，自动注入 |
| 结构感知分块 | ✅ 完整 | heading_stack + flush_section 算法 |
| 分块参数可调 | ✅ 完整 | ChunkWorkbench 滑块 UI |
| 稳定 chunk ID | ✅ 完整 | SHA1 基确定性 ID |
| PDF 页面预览 | ✅ 完整 | PyMuPDF 渲染 + 缩放 |
| Markdown 可编辑 | ✅ 完整 | QTextEdit 双栏 |
| 橡皮筋框选联动 | ❌ stub | `_on_pdf_selection()` 为空方法 |
| 索引时机 | ⚠️ 手动 | `index_on_import=false`，需用户手动同步 |

**问题：** 橡皮筋框选是 V1.1 规范中的 P0 功能（"可视化人工编辑台"核心交互），当前完全未实现，仅有 UI 骨架。

### 3.3 视频 ASR 与切片

**完成度：** ✅ 80%  
**评分：** 7.0 / 10

| 特性 | 状态 | 备注 |
|------|------|------|
| faster-whisper 转写 | ✅ 完整 | 子进程隔离，VAD 过滤 |
| 时间轴可视化 | ✅ 完整 | QPainter 手绘，置信度着色 |
| I/O 打点切片 | ✅ 完整 | 键盘快捷键，切片保存到 SQLite |
| 语义摘要录入 | ✅ 完整 | Enter 确认弹窗 |
| 切片向量化 | ✅ 完整 | ingest_video_clip() |
| 视频内嵌播放 | ✅ 基础 | QMediaPlayer，DirectShow 限制 |
| 波形图 | ❌ 未实现 | V1.1 规范要求 librosa，代码未见实现 |
| 帧缩略图提取 | ⚠️ 部分 | VideoCard 从 thumbs/ 加载但未见自动提取 |

**问题：** `ingest_video_clip()` 在 UI 线程同步执行嵌入（EmbedManager.encode 可能耗时 200ms~1s），在切片向量化时 UI 会短暂卡顿。

### 3.4 向量检索

**完成度：** ✅ 95%  
**评分：** 8.0 / 10

| 特性 | 状态 | 备注 |
|------|------|------|
| 余弦相似度检索 | ✅ 完整 | numpy 矩阵运算 |
| 阈值过滤 | ✅ 完整 | distance_threshold=0.7 |
| top_k 配置 | ✅ 完整 | retrieval.top_k=5 |
| CRUD 操作 | ✅ 完整 | add/query/update/delete/count |
| 事务安全 | ✅ 完整 | contextmanager + rollback |
| HNSW 索引 | ❌ 不支持 | 全表扫描 O(N) |

**ChromaDB 替换影响：** 移除 ChromaDB 的决策完全合理（AVX 崩溃问题严重），自实现方案对 10K 条数据足够，但 50K+ 条时性能需重新评估。

### 3.5 LLM 推理

**完成度：** ✅ 90%  
**评分：** 8.5 / 10

| 特性 | 状态 | 备注 |
|------|------|------|
| 子进程模式（默认）| ✅ 完整 | JSON 事件流通信，崩溃隔离 |
| 内联模式（可选）| ✅ 完整 | config.json llm.subprocess=false |
| GPU/CPU 自动回退 | ✅ 完整 | n_gpu_layers>0 失败时重试 CPU |
| 流式生成 | ✅ 完整 | Iterator[str] 生成器 |
| 取消中断 | ✅ 基础 | _cancelled 标志，token 间检查 |
| 10min 闲置卸载 | ✅ 完整 | MemoryWatcher QTimer |
| repeat_penalty | ✅ 完整 | config.json 可配置 |
| OOM 防护 | ✅ 完整 | MemoryError 捕获 → unload |

**亮点：** 子进程模式 + stdout JSON 事件流是工程质量最高的部分。diagnostics 列表（收集非 JSON 行）的错误报告机制实用。

### 3.6 数据流管理与 API 优化

**完成度：** ✅ 85%  
**评分：** 8.0 / 10

| 特性 | 状态 | 备注 |
|------|------|------|
| L1 MD 展示 | ✅ 完整 | 扫描 exports/**/*.chunks.md |
| L2 JSON 在线编辑 | ✅ 完整 | 双击 TableWidget 行编辑 |
| L1↔L2 转换 | ✅ 完整 | md_json_converter 双向 |
| L2→L3 同步 | ✅ 完整 | sync_json_to_db 增量同步 |
| API 优化（4 Provider）| ✅ 完整 | 纯 urllib，4种 action |
| QA 样本 few-shot | ✅ 完整 | qa_memory.jsonl 集成 |
| L3 计数显示 | ✅ 完整 | vectors.db COUNT(*) |
| 自动同步触发 | ❌ 缺失 | 编辑后需手动触发同步 |

### 3.7 系统托盘

**完成度：** ⚠️ 70%  
**评分：** 6.0 / 10

| 特性 | 状态 | 备注 |
|------|------|------|
| 4 状态图标 | ✅ 完整 | green/yellow/gray/red |
| 双击唤起 Spotlight | ✅ 完整 | |
| 释放内存菜单项 | ✅ 完整 | unload(reason="manual") |
| 打开编辑器菜单项 | ✅ 完整 | |
| 完全退出菜单项 | ✅ 完整 | |
| 偏好设置菜单项 | ❌ stub | `_open_prefs()` 仅记录日志 |
| 首次最小化气泡提示 | ❌ 未实现 | V1.1 规范要求，代码中未见 |

### 3.8 QA 记忆与评测

**完成度：** ✅ 90%（V1.1 规范中未包含此功能，为新增功能）  
**评分：** 8.0 / 10

| 特性 | 状态 | 备注 |
|------|------|------|
| JSONL 追加记录 | ✅ 完整 | 每次推理后 |
| Markdown 可读版本 | ✅ 完整 | 同步写入 |
| human_review 占位 | ✅ 完整 | verdict/accuracy_score 等字段 |
| API 优化 few-shot 集成 | ✅ 完整 | 过滤 unreviewed 条目 |

---

## 4. 技术选型评估

### 4.1 向量存储：SQLite + numpy 替代 ChromaDB

**决策评分：** 9 / 10（正确且必要的决策）

**支持理由：**
1. chromadb 0.5.x 依赖 hnswlib，Windows 预编译 wheel 使用 AVX/AVX-512 指令集，在部分 Intel/AMD CPU（无 AVX 支持）上触发致命访问违规
2. 这类崩溃发生在 C++ 层，Python 无法捕获，会直接终止进程
3. 自实现方案仅依赖 SQLite（stdlib）+ numpy（已是必需依赖），零额外 C++ 依赖

**局限性：**
- 全表扫描 O(N)：10K 条 < 200ms 可接受，50K 条后可能超 SLA
- 无 HNSW / IVF 近似检索
- 无持久化索引（每次查询都重建矩阵）

**建议：** 当数据量超过 3 万条时，考虑引入 FAISS（CPU 版无 AVX 要求的 FLAT 索引）或分页批量检索。

### 4.2 LLM 运行：子进程隔离模式

**决策评分：** 9.5 / 10（工程实践的最佳选择）

llama-cpp-python 在 Windows 上存在：
- CUDA 后端 DLL 加载失败（WinError 1114）
- 访问违规崩溃（C++ 内存错误）
- 不同 llama.cpp 版本与 GGUF 格式不兼容

子进程模式将这些崩溃完全隔离，主进程只接收 `LLMSubprocessError` Python 异常，可以正常展示错误信息并继续运行。代价是每次推理需启动子进程（约 2~5s 首次启动），但对于 RAG 场景（用户等待 TTFT）可接受。

### 4.3 PDF 解析：多后端用户选择

**决策评分：** 8.0 / 10

5 个解析器各有擅长场景：

| 解析器 | 最适合 | 局限 |
|--------|--------|------|
| docling | 学术 PDF、有结构标题 | 需要 ONNX，子进程模式下启动慢 |
| unstructured | 通用文档，容错性好 | 结构识别不如 docling |
| mineru | 中文 PDF，布局复杂 | 需独立 venv，CLI 启动开销 |
| marker | 高精度，GPU 加速 | 对 GPU 要求高 |
| ocr | 扫描版 PDF | 速度慢，精度受 tesseract 限制 |

**问题：** 不支持自动降级意味着用户必须了解各解析器的适用场景，对非技术用户不友好。

### 4.4 Embedding：BGE-M3 常驻 CPU

**决策评分：** 7.5 / 10

BGE-M3 的 1024 维向量对于跨语言检索（中英混合）是合理选择。CPU 常驻虽占用 ~1GB 内存，但避免了每次推理时的加载延迟（避免 TTFT 增加 10~30s）。

**潜在问题：** 低内存机器（8GB RAM）上，BGE-M3(1GB) + Gemma Q4(1.5GB) + 系统 = 约 4GB+，接近内存压力线。`idle_timeout_minutes` 仅卸载 LLM 而不卸载 BGE-M3，这是正确的权衡。

### 4.5 ASR：faster-whisper 子进程模式

**决策评分：** 8.5 / 10

与 LLM 子进程模式同样的动机。`vad_filter=True` 减少长静音段的噪声。`language=zh` 针对中文内容优化，但对中英混合语音可能效果有限（可配置 `language=null` 自动检测）。

---

## 5. 代码质量评估

### 5.1 优点

**信号/槽设计规范：**
- InferenceWorker 4 信号、IngestWorker 8 信号覆盖完整生命周期
- SearchController 使用 `_cache_invalid_ids` 过滤编辑中条目，体现了对数据一致性的思考

**错误处理：**
- main.py 中的 `_except_hook` + `faulthandler.enable()` 组合保证了未捕获异常的可见性
- 子进程的 diagnostics 列表收集非 JSON 输出，错误报告信息丰富

**离线安全：**
- 启动时三重 env var 强制离线（TRANSFORMERS_OFFLINE / HF_HUB_OFFLINE / HF_DATASETS_OFFLINE）
- startup_check 验证 BGE-M3 必需子文件，防止触发 HF 下载

### 5.2 问题

**接口不一致（ChromaStore.list_by_source）：**
```python
# json_db_sync.py 中：
try:
    rows = ChromaStore().list_by_source(source_name)  # 该方法不存在
except AttributeError:
    rows = _scan_all_chunks(source_name)               # 回退全表扫描
```
这是一个已知接口缺口，`except AttributeError` 是"假设方法不存在"的硬编码规避。正确做法是在 `ChromaStore` 中实现 `list_by_source()`。

**代码重复（_native_backend_error）：**
```python
# 完全相同的函数在两个文件中出现：
# llm_manager.py:118 和 llm_subprocess.py:~30
@staticmethod
def _native_backend_error(error: OSError) -> str:
    detail = str(error)
    if "access violation" in detail.lower():
        return "llama-cpp-python 原生后端初始化失败..."
    return f"llama-cpp-python 原生后端加载失败: {detail}"
```
子进程设计意图是"不 import 任何 app 模块"，但共享工具函数可以单独抽取到不依赖 app 的 utils 模块中。

**ChunkWorkbench 分块逻辑外泄：**
```python
# chunk_workbench.py 中直接导入 worker 内部函数：
from app.workers.ingest_worker import _markdown_to_items
from app.models.stable_ids import stable_chunk_id
```
View 层直接调用 worker 内部函数（以 `_` 开头的私有函数）违反了 MVC 边界。这些分块函数应提升为公共 API 或移到 Model 层。

**is_manual 标志语义不清：**
- `ChromaStore.update()` 中 `is_manual=1` 意为"经过热更新"
- `ChunkWorkbench` 中 `is_manual=True` 意为"用户手动切块"
- `json_db_sync` 中 `is_manual=True` 触发强制重索引
- 这三种语义理论上相同但实际上被混用，导致所有手动切块均会触发全量重索引

**ingest_worker.py BOM + 乱码：**
```python
# 文件第一行有 UTF-8 BOM，注释中有乱码
﻿"""
IngestWorker 鈥?chunks a document...
"""
```
表明文件曾被非 UTF-8 编辑器保存，历史编码问题未清理。

### 5.3 复杂度热点

| 文件 | 实际行数 | 复杂度原因 |
|------|---------|---------|
| `ingest_worker.py` | ~550 行 | 分块算法 + PDF 图像导出 + re-embed 混合 |
| `views/dataflow_panel.py` | ~400 行 | 三层 UI + ConvertWorker + SyncWorker + ApiWorker 协调 |
| `views/knowledge_editor.py` | ~350 行 | 6 个标签页 + 信号连接 + 跨组件协调 |
| `models/llm_manager.py` | ~234 行 | 内联/子进程双模式 + GPU 回退 + 诊断信息 |

---

## 6. 性能与可靠性评估

### 6.1 延迟分析（估算）

| 操作 | 估算延迟 | SLA | 评估 |
|------|---------|-----|------|
| 热键呼出 | 20~80ms | < 100ms | ✅ 满足 |
| BGE-M3 编码（1 sentence） | 50~200ms (CPU) | — | 可接受 |
| 向量检索（10K 条） | 30~100ms | < 200ms | ✅ 满足 |
| LLM 子进程启动（首次） | 2000~5000ms | — | ⚠️ 影响 TTFT |
| LLM 首 token（子进程已启动后） | 200~800ms | < 1500ms | 待测量 |
| LLM 生成速度 | 5~15 tok/s (CPU) | > 5 tok/s | ✅ 预期满足 |
| PDF 解析（docling, 10页） | 5~30s | — | 可接受（后台线程） |
| ASR（base, 10min 视频, CPU） | 2~5min | < 3min | ⚠️ 接近边界 |

**TTFT 子进程开销分析：** 每次查询都重新启动 LLM 子进程（`subprocess.Popen`），首次进程启动约需 2~5s（加载 GGUF 模型）。这意味着：
- 若 LLM 未卸载（10min 内）：每次查询仍需启动新子进程
- 若用户连续多次查询：每次都有 2~5s 的进程启动开销

**建议：** 考虑复用子进程（保持进程运行，通过 stdin 发送多次请求），或至少在 TTFT 前预启动进程。

### 6.2 内存使用分析

| 场景 | 预期内存 |
|------|---------|
| 应用启动后（BGE-M3 未加载） | ~200MB |
| BGE-M3 加载后（常驻） | ~1.2GB |
| BGE-M3 + Gemma-2B-Q4（推理中） | ~2.7GB |
| 10min 闲置后（LLM 卸载） | ~1.2GB |
| SLA 要求（闲置后） | < 600MB |

**问题：** BGE-M3 常驻 ~1GB，即使 LLM 卸载后内存占用仍远超 600MB SLA。若要满足"闲置内存 < 600MB"，需要卸载 BGE-M3，但这会让下次检索时有 10~30s 的重新加载延迟。这是一个**需要产品决策**的权衡：
- 当前决策：BGE-M3 常驻，不满足 600MB SLA 但响应快
- 替代方案：闲置时卸载 BGE-M3，满足 SLA 但用户体验变差

### 6.3 可靠性评估

**高可靠性：**
- LLM / ASR 子进程崩溃不影响主进程（验证可行）
- startup_check 防止启动时触发网络下载
- MemoryError 捕获防止 OOM 崩溃

**中等可靠性：**
- ChromaStore 全表扫描的连接管理（per-query 新建连接）有轻微资源浪费，但 SQLite 的 WAL 模式可以缓解

**低可靠性（需关注）：**
- `ingest_video_clip()` 在 UI 线程同步执行嵌入，若 EmbedManager 崩溃，整个 UI 可能无响应
- DataflowPanel `_save_chunk_edit` 全量覆盖 JSON 文件，高频编辑下有数据丢失风险（写入中断）

---

## 7. 关键架构决策分析

### 7.1 已正确实施的决策 ✅

| 决策 | 评分 | 影响 |
|------|------|------|
| LLM 子进程模式 | 10/10 | 消除 Windows DLL 崩溃的根本性方案 |
| ChromaDB → SQLite+numpy | 9/10 | 消除 AVX 崩溃，零额外依赖 |
| BGE-M3 离线强制（env var） | 9/10 | 真正实现零网络请求 |
| 数据流三层（L1/L2/L3） | 9/10 | 创新性设计，知识库可版本控制 |
| 稳定 chunk ID（SHA1） | 8/10 | 支持增量同步，避免重复索引 |
| QA 记忆（JSONL）| 8/10 | API 优化 few-shot 样本积累机制 |
| startup_check 8 项检查 | 8/10 | 冷启动体验好，防止隐式网络访问 |

### 7.2 需要重新评估的决策 ⚠️

| 决策 | 问题 | 建议 |
|------|------|------|
| 每次推理启动新 LLM 子进程 | TTFT 有 2~5s 子进程启动开销 | 考虑长驻子进程模式，复用已启动的进程 |
| ingest_video_clip 同步嵌入 | UI 线程阻塞 | 移到 QThread Worker |
| is_manual 泛用导致全量更新 | ChunkWorkbench 的所有 chunk 均 is_manual=True | 区分"手动切块"和"手动编辑"两种状态 |
| L2→L3 手动同步 | 用户易忘记同步，搜索结果不一致 | 提供可选的"自动同步"开关 |

### 7.3 尚未实施的重要决策 ❌

| 待决策 | 重要性 | 说明 |
|--------|--------|------|
| PDF 橡皮筋框选功能 | P0 | 核心差异化功能，当前完全未实现 |
| 偏好设置对话框 | P1 | 用户需要 UI 而非直接编辑 JSON |
| BGE-M3 闲置卸载策略 | P1 | 需产品决策：600MB SLA vs 响应速度 |
| 波形图可视化 | P2 | librosa 已安装但未集成到 VideoWorkbench |
| ChromaStore.list_by_source | P1 | 修复接口缺口消除 AttributeError 兜底 |

---

## 8. 改进优先级路线图

### Phase 1 — 修复 P0 缺陷（1~2 周）

**目标：** 消除功能 stub，实现真正的生产可用性

1. **实现 PDF 橡皮筋框选联动**
   - `PDFWorkbench._on_pdf_selection()` 添加实际逻辑
   - 根据选区坐标匹配 Markdown 中对应文本块并定位光标
   - 技术路径：`fitz.Page.get_text("dict")` 获取词坐标，与橡皮筋矩形做相交判断

2. **实现偏好设置对话框**
   - `TrayIcon._open_prefs()` 打开配置对话框
   - 至少包含：LLM 参数、Embedding 设备、检索参数、热键设置
   - 可复用现有 `ApiSettingsPanel` 的 UI 模式

### Phase 2 — 修复 P1 问题（2~3 周）

3. **ingest_video_clip 异步化**
   - 将 `IngestController.ingest_video_clip()` 中的 embed + add 移到 QThread
   - 添加进度信号 `clip_ingest_finished(dict)`

4. **实现 ChromaStore.list_by_source()**
   - 在 `chroma_store.py` 中添加：
     ```python
     def list_by_source(self, source_name: str) -> List[Dict]:
         with self._conn() as conn:
             rows = conn.execute(
                 "SELECT * FROM vectors WHERE pdf_payload LIKE ?",
                 (f'%"file":"%{source_name}%"%',)
             ).fetchall()
         return [dict(r) for r in rows]
     ```
   - 移除 `json_db_sync.py` 中的 `AttributeError` 兜底

5. **DataflowPanel 自动触发 cache invalidation**
   - chunk 编辑保存后自动调用 `SearchController.invalidate_cache(chunk_id)`
   - 或提供"编辑后需同步"的明显 UI 提示

6. **修复 config.py _DEFAULT 中缺失 min_section_chars**
   ```python
   "chunking": {
       "max_chars": 1800,
       "overlap_chars": 240,
       "keep_heading_path": True,
       "min_section_chars": 80,  # 补充此项
   }
   ```

7. **logger 读取 log_retention_days 配置**
   ```python
   backupCount = cfg.get("log_retention_days", 30)
   ```

### Phase 3 — 改善技术债（3~4 周）

8. **清理 ingest_worker.py 编码问题**
   - 移除 BOM，修复乱码注释

9. **提取 _markdown_to_items 为公共 API**
   - 将 `ingest_worker.py` 中的私有函数提升到 `app/models/chunker.py`
   - `ChunkWorkbench` 从 models 层导入，而非直接调用 worker 私有函数

10. **考虑 LLM 子进程复用**
    - 评估保持子进程长驻的可行性（通过 stdin/stdout 多轮交互）
    - 目标：消除每次查询 2~5s 的进程启动开销

11. **is_manual 语义澄清**
    - 引入 `is_user_edited: bool` 字段区分"手动切块"（总为 True）和"手动编辑"
    - 只有 `is_user_edited=True` 的 chunk 才触发强制重索引

### Phase 4 — 功能增强（4~6 周）

12. **视频波形图**
    - 集成 librosa 到 VideoWorkbench 时间轴渲染

13. **自动帧缩略图提取**
    - IngestController 在 ASR 完成后异步提取关键帧到 `data/thumbs/`

14. **BGE-M3 闲置卸载（可选）**
    - 在 MemoryWatcher 中添加 BGE-M3 卸载逻辑（product decision required）
    - 提供配置项 `embed.unload_with_llm: bool`

---

*评估基于代码库 2026-04 实际状态。评分和建议仅供参考，具体决策需结合产品目标和资源约束。*
