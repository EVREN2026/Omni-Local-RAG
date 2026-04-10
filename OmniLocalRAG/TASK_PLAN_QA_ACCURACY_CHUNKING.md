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

## 下一步开发任务

- 用 `full.md` 重新导入，生成新版 `data/exports/markdown/full.chunks.*`。
- 在 chunk 导出中增加 heading_path、block_type、source_span。
- 基于 `qa_dialogue_memory.md` 汇总第一批 30 个问答样本。
- 增加 golden QA 文件和评测脚本。
