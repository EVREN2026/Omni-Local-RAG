# Qt Designer UI Files

这些界面已经拆分为 `.ui` 文件，可直接使用 `Qt Designer` 打开编辑，并在运行时由 Python 动态加载。

## 已接入运行的 UI
- `knowledge_editor.ui` -> `app/views/knowledge_editor.py`
- `spotlight_window.ui` -> `app/views/spotlight_window.py`
- `pdf_import_panel.ui` -> `app/views/pdf_import_panel.py`
- `cross_modal_panel.ui` -> `app/views/cross_modal_panel.py`
- `api_settings_panel.ui` -> `app/views/api_settings_panel.py`
- `pdf_workbench.ui` -> `app/views/pdf_workbench.py`
- `preferences_dialog.ui` -> `app/views/preferences_dialog.py`
- `search_result_card.ui` -> `app/views/result_cards.py`
- `startup_guide.ui` -> `app/views/startup_guide.py`
- `tray_menu.ui` -> `app/views/tray_icon.py`
- `video_player.ui` -> `app/views/video_player.py`
- `video_workbench.ui` -> `app/views/video_workbench.py`

## 已创建 UI 文件但尚未接入运行
- `chunk_workbench.ui`
- `dataflow_panel.ui`

## 运行时加载方式
公共加载工具在：`app/utils/ui_loader.py`

目前统一采用两类模式：
- 普通 `QWidget / QDialog`：通过 `load_ui(...)` 加载
- 特殊对象（例如托盘 `QMenu`）：通过 `uic.loadUiType(...)` 生成并由 Python 容器接管逻辑

## 维护建议
- 在 Designer 里优先调整布局、边距、分栏、占位控件和文本
- 关键控件请保留 `objectName`，否则会触发运行时显式报错
- 可选容器如果层级调整了，尽量保留命名布局或宿主控件，方便 Python 自动回退
