# 视频文案提取器

在本机提取抖音口播、讲解视频的完整讲述稿，支持链接或 MP4 导入。使用 React + FastAPI + MLX Whisper，不调用在线转写服务。

## 启动

适用于 Apple 芯片 Mac。需要 macOS 14 或更新版本、uv、Node.js 22.12 或更新版本和 FFmpeg。通过 Homebrew 安装缺少的工具：

```sh
brew install uv node@22 ffmpeg
```

在项目目录运行：

```sh
./start.command
```

也可以在 Finder 中双击 `start.command`。首次运行下载 Python 依赖并构建界面，之后自动打开 [本地工作台](http://127.0.0.1:8765)。请保留终端窗口，按 Ctrl+C 停止服务。

默认选择精确模式（完整 large-v3，模型约 3.1 GB），也可选择快速模式（turbo，约 1.6 GB）。首次使用对应模式时点击下载模型；已准备的模型会保留，不需重复下载。模型下载及抖音视频获取需要联网；模型就绪后，导入本地 MP4 可以离线转写。音视频和文案不会上传到第三方。

## 使用

1. 粘贴一条抖音分享链接或整段分享内容，也可以切换为本地 MP4 导入。
2. 点击提取，查看任务阶段；处理中可以取消。
3. 提取结果自动保存到本地历史。校对编辑稿，等待“已保存”，然后复制或导出 TXT。
4. 在本地历史中搜索、打开、重命名或删除文稿；重启后仍可继续编辑，并可切换查看不可修改的原始转写。

粘贴完整抖音分享内容时，文稿自动使用其中的视频标题，去掉分享口令、日期、话题标签和链接；只粘贴网址时仍使用“链接文案”。标题不会混入转写正文，仍可手动重命名。旧历史未保存原分享文本，无法自动补回原视频标题。

优先支持普通话、30 分钟以内、500 MB 以内的视频。有完整可获取字幕时优先采用，否则本地转写全段音频；画面中嵌入的字幕不会进行 OCR。语音转写可能与画面字幕有差异，术语等内容仍需校对。

抖音可能限制下载，遇到失败可重试或将已保存的 MP4 导入。本工具不自动读取浏览器 Cookie。一次只处理一个任务。编辑稿自动保存；遇到保存失败或版本冲突时，先重试或导出当前稿再切换。识别准确度取决于音质、口音和术语，精确模式也需人工校对。

## 本地文件

默认数据目录为 `~/Library/Application Support/LocalVideoTranscript/`，保存模型、`history.sqlite3` 历史数据库及工具的临时文件。历史只包含文案和来源信息，不长期保存音视频。任务完成、失败或取消后清理临时媒体，不删除用户原文件或已导出的文案。可通过 `TRANSCRIPT_DATA_DIR` 环境变量指定其他目录。

同一数据目录只能运行一个服务实例。网页与 API 由本机 `127.0.0.1` 提供，不向局域网开放。

## 开发与检查

```sh
uv sync --python 3.12 --locked --extra local
npm ci --prefix frontend
# 终端一：API
.venv/bin/python -m app.launch --no-browser
# 如使用 Homebrew 的 node@22，请先将其 bin 目录加入当前终端 PATH
# export PATH="/opt/homebrew/opt/node@22/bin:$PATH"
# 终端二：前端开发服务器
npm run dev --prefix frontend
# 自动化验证
./scripts/check.sh
```

自动化测试不下载模型、不访问抖音。真实模型与视频验证另外记录在 [0.2 版本验证记录](docs/validation-v0.2.md)，查看实际通过范围及限制。

- [下一版设计](docs/superpowers/specs/2026-10-31-history-accuracy-design.md)
- [产品设计](docs/superpowers/specs/2026-10-21-video-transcript-mvp-design.md)
- [技术方案](docs/technical-design.md)
- [实现计划](docs/superpowers/plans/2026-10-21-mvp-implementation.md)
