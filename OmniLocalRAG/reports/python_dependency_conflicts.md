# Python 依赖冲突分析

生成日期: 2026-04-10

## 1. 结论

当前环境存在两类问题:

| 类型 | 结论 | 影响 |
| --- | --- | --- |
| 启动顺序/DLL 冲突 | `PyQt5` 先导入后再导入 `torch` 会触发 `c10.dll` 的 WinError 1114 | 直接导致数据流同步时 BGE-M3 embedding 加载失败 |
| Python 包版本漂移 | 全局 `C:\Python3` 中多个包版本与项目 `requirements.txt` 和彼此依赖范围不一致 | 可能导致 docling/unstructured/marker/surya/chromadb 等解析链不稳定 |

已修复代码层问题:

| 文件 | 修复 |
| --- | --- |
| `main.py` | 仅在真正运行于本项目 `venv` 时才加入 `venv` torch DLL 目录，避免全局 Python + venv DLL 混用 |
| `main.py` | 在 PyQt5 导入前预加载 `torch` runtime，避免 PyQt5 先加载后导致 torch 原生 DLL 初始化失败 |

## 2. 当前 Python 指向

当前命令行 `python` 指向:

```text
C:\Python3\python.exe
Python 3.12.6 64-bit
site-packages: C:\Python3\Lib\site-packages
```

项目内存在 `venv`:

```text
C:\Users\Windows10\Desktop\refactor-demo\OmniLocalRAG\venv
```

但当前程序日志和检测结果显示，实际使用的是全局 Python，而不是项目 `venv`。

## 3. 直接报错原因

日志:

```text
OSError: [WinError 1114] 动态链接库(DLL)初始化例程失败。
Error loading "C:\Python3\Lib\site-packages\torch\lib\c10.dll"
```

复现结果:

| 测试 | 结果 |
| --- | --- |
| `python -c "import torch"` | 成功 |
| `python -c "from PyQt5.QtWidgets import QApplication; import torch"` | 失败 |
| `python -c "import torch; from PyQt5.QtWidgets import QApplication"` | 成功 |
| 修复后 `python -c "import main; import torch"` | 成功 |
| 修复后 `python -c "import main; from app.models.embed_manager import EmbedManager; EmbedManager()._import_sentence_transformer()"` | 成功 |

因此，这次同步失败的直接触发点是:

```text
应用启动时先加载 PyQt5 -> 后续同步 DB 时首次加载 sentence-transformers -> sentence-transformers 导入 torch -> torch 原生 DLL 初始化失败
```

## 4. `pip check` 冲突

当前全局环境 `python -m pip check` 输出的冲突:

| 包 | 当前版本 | 依赖要求 | 问题 |
| --- | --- | --- | --- |
| `chromadb` | `0.5.23` | `tokenizers<=0.20.3,>=0.13.2` | 当前 `tokenizers==0.22.2` |
| `deepsearch-glm` | `0.24.0` | `numpy<2.0.0,>=1.26.4` | 当前 `numpy==2.4.4` |
| `deepsearch-glm` | `0.24.0` | `rich<14.0.0,>=13.7.0` | 当前 `rich==14.3.3` |
| `docling-ibm-models` | `1.3.3` | `lxml<5.0.0,>=4.9.1` | 当前 `lxml==6.0.3` |
| `docling-ibm-models` | `1.3.3` | `numpy<2.0.0,>=1.24.4` | 当前 `numpy==2.4.4` |
| `docling-ibm-models` | `1.3.3` | `Pillow<11.0.0,>=10.0.0` | 当前 `Pillow==12.2.0` |
| `marker-pdf` | `1.10.2` | `Pillow<11.0.0,>=10.1.0` | 当前 `Pillow==12.2.0` |
| `surya-ocr` | `0.17.1` | `Pillow<11.0.0,>=10.2.0` | 当前 `Pillow==12.2.0` |
| `unstructured-client` | `0.43.2` | `pypdfium2>=5.6.0` | 当前 `pypdfium2==4.30.0` |
| `unstructured-inference` | `1.6.6` | `pypdfium2>=5.0.0` | 当前 `pypdfium2==4.30.0` |

## 5. 当前关键包版本

全局 Python:

```text
torch==2.11.0
torchvision==0.26.0
sentence-transformers==2.7.0
transformers==4.57.6
numpy==2.4.4
onnxruntime==1.24.4
docling==1.0.0
docling-core==1.7.2
docling-ibm-models==1.3.3
unstructured==0.22.18
PyMuPDF==1.24.0
PyQt5==5.15.10
```

项目 `requirements.txt` 期望:

```text
sentence-transformers==2.7.0
docling==1.0.0
unstructured==0.15.7
PyMuPDF==1.24.0
Pillow==10.3.0
numpy==1.26.4
PyQt5==5.15.10
```

明显漂移:

| 包 | 当前 | requirements |
| --- | --- | --- |
| `numpy` | `2.4.4` | `1.26.4` |
| `Pillow` | `12.2.0` | `10.3.0` |
| `unstructured` | `0.22.18` | `0.15.7` |

## 6. 当前 `venv` 状态

项目 `venv` 存在，且 `pip check` 无破损依赖，但依赖不完整:

```text
torch==2.5.1+cu121
torchvision==0.20.1+cu121
numpy==2.4.4
Pillow==12.2.0
sentence-transformers: NOT INSTALLED
PyQt5: NOT INSTALLED
docling: NOT INSTALLED
unstructured: NOT INSTALLED
onnxruntime: NOT INSTALLED
```

因此当前不能简单切到 `venv` 运行主程序，除非先把项目依赖完整安装进去。

## 7. 推荐处理顺序

优先级 1: 使用已修复的 `main.py` 重新测试同步。

```powershell
cd C:\Users\Windows10\Desktop\refactor-demo\OmniLocalRAG
python main.py
```

如果 BGE-M3 同步仍失败，再进入优先级 2。

优先级 2: 清理全局环境，按 `requirements.txt` 回退关键包。

```powershell
cd C:\Users\Windows10\Desktop\refactor-demo\OmniLocalRAG
python -m pip install --force-reinstall numpy==1.26.4 Pillow==10.3.0 lxml==4.9.4 rich==13.9.4
python -m pip install --force-reinstall unstructured==0.15.7
python -m pip check
```

优先级 3: 建议新建干净虚拟环境，不再混用全局 Python。

```powershell
cd C:\Users\Windows10\Desktop\refactor-demo\OmniLocalRAG
py -3.12 -m venv .venv-clean
.\.venv-clean\Scripts\python.exe -m pip install -U pip setuptools wheel
.\.venv-clean\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-clean\Scripts\python.exe main.py
```

注意:

```text
当前项目同时支持 docling / unstructured / mineru / marker / OCR。
这些解析库的依赖范围天然容易冲突，长期建议把重型解析器拆成可选环境或子进程环境。
```

## 8. 最终判断

有版本冲突，而且是两层:

| 层 | 是否存在 | 说明 |
| --- | --- | --- |
| 当前同步失败的直接原因 | 是 | PyQt5 先导入导致 torch 原生 DLL 后加载失败，已通过 `main.py` 预加载 torch 修复 |
| 全局 Python 包版本冲突 | 是 | `pip check` 明确报告 numpy/Pillow/lxml/pypdfium2/tokenizers/rich 等冲突 |
| 项目 venv 可直接使用 | 否 | venv 存在但缺少主程序依赖 |

建议先用修复后的 `main.py` 验证数据流同步；如果同步通过，再安排一次环境清理，不要急着同时重装所有解析器。
