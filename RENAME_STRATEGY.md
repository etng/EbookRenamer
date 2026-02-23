# 电子书文件名修正策略（预览优先）

## 目标
将目录中的 `.epub`、`.pdf` 长文件名改为更短但仍有辨识度的格式：

`主标题-主要作者-年份.ext`

示例：`Site_Reliability_Engineering-Betsy_Beyer-2016.epub`

## 为什么采用这个格式
- `主标题`：保留书的核心识别信息。
- `主要作者`：只显示第一作者，减少长度。
- `年份`：便于版本和出版时序管理。
- 保留扩展名，避免影响阅读器识别。

## 信息来源优先级

### 1) EPUB
优先读取书内元数据（OPF）：
- `dc:title`
- `dc:creator`
- `dc:date`
- `dcterms:modified`（当 `dc:date` 缺失时作为年份候选）

当元数据异常时（例如标题是 ASIN 编号），回退到原文件名推断。

### 2) PDF
优先读取 `pdfinfo` 输出：
- `Title`
- `Author`
- `CreationDate`

若以上字段缺失，则补充读取 PDF 首页文本（本地离线）：
- 优先 `pdftotext` 抽取第一页文本
- 若可用则用 `mutool` 兜底
- 从首页文本中推断标题、作者、日期（如 arXiv 行）

仍缺失时，再从文件名推断。

## 主标题提取规则
- 优先使用元数据标题。
- 若元数据标题过短（如只有 1-2 个词），且文件名中存在以该标题开头的更完整标题，优先采用更完整标题。
- 若标题可疑（如仅为编码、过短、无语义），则改用文件名推断。
- 去掉副标题分隔后的内容（如 `:`, ` - `, ` -- `），保留主标题。
- 保留关键版本信息（如 `2nd Edition`）作为主标题的一部分（当它是标题本体信息而非噪音）。

## 主要作者提取规则
- 优先使用元数据作者中的第一作者。
- 作者字段若包含多个作者（`,`、`;`、`and` 连接），只取第一个。
- 若元数据不可靠，则从原文件名括号内容提取第一作者。
- 统一转成文件名友好形式（空格转下划线）。

## 年份提取规则
- 先从 `date/CreationDate` 中抽取四位年份。
- 年份无效或缺失时，再尝试 `modified`。
- 仍缺失时，尝试从原文件名中提取。
- 仍无法确定时，使用 `UnknownYear`。

## 文件名清洗规则
- 去除来源站点噪音（如 `z-library`, `1lib`, `z-lib` 等）。
- 去除非法路径字符和多余空格。
- 使用下划线连接单词，减少跨平台问题。
- 对常见版本词做统一缩写，降低长度并保持一致性。

## 缩写规则（已在脚本中应用）
- `2nd Edition` / `Second Edition` -> `2e`
- `3rd Edition` / `Third Edition` -> `3e`
- `Revised Edition` -> `RevEd`
- `Updated Edition` -> `UpdEd`
- `International Edition` -> `IntlEd`
- `Collector's Edition` -> `CollEd`
- `Special Edition` -> `SpecEd`
- `Student Edition` -> `StuEd`
- `Edition` -> `Ed`（在未匹配到更具体规则时）
- `Release` -> `Rel`
- `Volume` -> `Vol`
- `Part` -> `Pt`
- `Number` -> `No`

## 冲突处理
如果目标文件名已存在，自动在末尾追加递增后缀：
- `...-2018.epub`
- `...-2018-2.epub`
- `...-2018-3.epub`

## 文件名长度约束
- 多数现代文件系统单个文件名上限约为 `255` 字符（不含路径）。
- Windows 在历史兼容场景下还可能受到路径总长限制（常见 `260`）。
- 脚本预览会显示：
  - `title_len`：最终标题片段长度
  - `name_len`：目标文件名总长度
- 并在接近/超过阈值时提示：
  - `>200`：`[WARN: >200]`（保守提醒）
  - `>255`：`[WARN: >255]`（可能跨平台失败）

## 执行流程
1. 扫描目录 `.epub`/`.pdf`。
2. 提取元数据并推断 `主标题/主要作者/年份`。
3. 生成“原名 -> 新名”预览。
4. 仅当用户确认后，使用 `--apply` 执行重命名。

## 预览界面模式
- 默认 `--ui auto`：自动选择最佳可用界面。
  - GUI 可用时：优先 `PySide6`，其次 `PyQt6`（Qt GUI 工作流）。
  - GUI 不可用时：优先 `textual`，其次 `rich`。
  - 都不可用时：回退到纯文本 CLI 预览。
- `--gui`：强制使用 GUI 预览（不可用时会回退并提示）。
- `--tui`：强制使用 TUI 预览（不可用时会回退并提示）。
- `--ui cli`：强制纯文本预览（适合脚本化和日志场景）。

Textual TUI 中提供底部操作按钮：
- `Apply Rename`
- `Check Update`
- `Language`
- `About`
- `Exit`

示例：
- `./rename_books_by_meta.py --dir . --gui`
- `./rename_books_by_meta.py --dir . --tui`
- `./rename_books_by_meta.py --dir . --ui cli`
- `./rename_books_by_meta.py --dir . --ui auto --apply`

## GUI 工作流（完整交互）
在 `--gui` 或 `--ui auto` 选中 Qt GUI 时，流程为：
1. 启动后等待用户点击 `Choose Folder`（不自动弹框，不提前扫描）。
2. 选择目录后开始扫描并推断目标文件名。
3. 表格中可直接编辑 `Target` 列（目标文件名）。
4. 编辑时 `Title Len` 与 `Name Len` 会实时刷新。
5. 点击 `Apply Rename` 后执行重命名。

说明：
- GUI 模式下不会使用命令行 `--apply`，请在窗口内点击按钮应用。
- 若目标名非法、重复或与现有文件冲突，会弹窗提示并阻止执行。

## GUI 标题/图标定制
- `--app-title \"My Ebook Renamer\"`：自定义窗口标题。
- `--app-icon /path/to/icon.png`：自定义窗口图标（支持 `.ico/.icns/.png`，依 Qt 平台支持而定）。

## About 信息
- GUI 中提供 `Help -> About` 菜单项和底部 `About` 按钮。
- About 弹窗显示程序版本和功能简介。
- 可点击跳转 GitHub：`https://github.com/etng/ebook-renamer`
- GUI 中提供“检查更新”按钮/菜单，读取 Release `latest.json` 判断是否有新版本。
- CLI 也支持：`--check-update`（可配合 `--update-url`）。

## 多语言（i18n）
- 已抽取界面文案到语言包目录：`locales/`
- 当前内置语言：
  - `en`（English）
  - `zh_CN`（简体中文）
  - `zh_TW`（繁體中文）
  - `ja`（日本語）
  - `vi`（Tiếng Việt）
- 默认语言：按系统语言自动选择（无法匹配时回退英文）。
- 用户可在 GUI 顶部下拉框切换语言。
- 用户一旦切换，选择会写入本地配置并持久化；后续启动优先使用用户选择，且可随时再次切换。
- 配置路径：
  - macOS: `~/Library/Application Support/ebook-renamer/config.json`
  - Linux: `~/.config/ebook-renamer/config.json`
  - Windows: `%APPDATA%\\ebook-renamer\\config.json`

## 外部工具处理
脚本使用外部工具 `pdfinfo` 获取 PDF 元数据，并使用 `pdftotext`（可选 `mutool`）做首页文本补充解析。
- 启动时先检查是否存在。
- 若不存在，自动尝试用系统包管理器安装（如 `brew/apt/dnf/yum/pacman/zypper/choco/winget`）。
- 安装失败时会给出错误提示，便于手工安装。

预留参数（当前未实现具体逻辑）：
- `--allow-ocr`：预留 OCR 回退开关（暂不执行 OCR）
- `--allow-online`：预留联网补全开关（暂不联网）

此外，GUI/TUI 所需 Python 包会按需检查并尝试自动安装（`pip --user`）：
- GUI：`PySide6`、`PyQt6`（`tkinter` 为系统内置时不安装）
- TUI：`textual`、`rich`
- 若遇到 `externally-managed-environment`（PEP 668）错误，脚本会自动重试：
  - `pip install --break-system-packages --user <package>`
  - 若仍失败，则回退到可用界面（最终至少 CLI 可用）。
- 注意：在 macOS/Homebrew Python 中，可能存在 `tkinter` 模块可见但 `_tkinter` 缺失的情况。
  脚本会在导入阶段校验可用性；若 Tk 不可用，会自动回退到 TUI/CLI，而不是崩溃。

## 打包为单文件应用（建议）
可用 `PyInstaller` 打包为单一可执行文件，例如：

`pyinstaller --onefile --windowed --name EbookRenamer --icon app.icns rename_books_by_meta.py`

打包后仍可通过参数定制：
- `--app-title`
- `--app-icon`

## Makefile 一键打包
已提供 `Makefile`，并内置默认图标生成流程（因为项目初始无 icon 文件）：

- `make icon`：生成默认图标文件
  - `assets/icon.png`
  - `assets/icon.ico`
  - `assets/icon.icns`（可生成时）
- `make build-macos`：本机打包 macOS
- `make build-linux`：通过 Docker 打包 Linux
- `make build-windows`：通过 Docker 打包 Windows
- `make build-all`：三平台全量打包
- `make release`：三平台构建后自动归档到 `release/`
- `make relase`：`make release` 的兼容别名

说明：
- Linux/Windows 目标依赖 Docker。
- 默认 cross-build 镜像：
  - `cdrx/pyinstaller-linux:python3`
  - `cdrx/pyinstaller-windows:python3`
- 打包时会自动包含 `locales/` 资源，保证发布版可用多语言。
- 在 Apple Silicon / ARM 主机上，建议显式使用：
  - `DOCKER_PLATFORM=linux/amd64`
  例如：`make build-windows DOCKER_PLATFORM=linux/amd64`
