# 本地视频文案提取器 Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent media/UI tasks and code review. Execute continuously in this session; track each deliverable below.

**Goal:** 实现可在当前 Mac 本地运行、支持抖音链接和 MP4 的完整文案提取工具。

**Architecture:** React 网页与 FastAPI 同源部署，API 管理单个任务，独立进程执行下载及 MLX 转写。模型下载独立触发，推理仅读取本地模型；任务临时文件结束后清理。

**Tech Stack:** React、TypeScript、Vite、FastAPI、uv、yt-dlp、FFmpeg、mlx-whisper、pytest、Vitest。

## Global Constraints

- 所有转写在本机完成，不调用在线转写服务，不上传音视频或文案。
- 首版面向当前 Apple M5、16GB 内存 Mac，优先支持普通话、30 分钟以内的单条口播或讲解视频。
- 服务仅监听 `127.0.0.1`，修改请求检查来源及会话令牌。
- 首版不提供跨会话历史；临时媒体与用户原文件严格分离。
- 每个实现批次更新相关测试、验证通过后创建 Git commit。

## 接口契约

前端 API 全部以 `/api` 开头。修改请求带 `X-Session-Token`，令牌从 `GET /api/session` 获取。错误格式为 `{detail: string}`。

```ts
type ModelStatus = { status: 'missing'|'downloading'|'ready'|'error'; detail: string; progress: number|null };
type Task = { id: string; status: 'running'|'completed'|'failed'|'cancelled'; stage: string; detail: string; text: string; source: 'subtitle'|'transcription'|null; error: string|null };
// GET /api/session -> {token:string, model:ModelStatus, dependencies:{ffmpeg:boolean,ffprobe:boolean}, active_task:Task|null}
// GET /api/model -> ModelStatus
// POST /api/model/download -> ModelStatus (202)
// POST /api/tasks/link {share_text:string} -> Task (202)
// POST /api/tasks/file multipart file -> Task (202)
// GET /api/tasks/{id} -> Task
// POST /api/tasks/{id}/cancel -> Task
```

API 启动 worker 的命令为 `python -m app.worker PAYLOAD_FILE`，payload 含 `kind` (`task`/`model`)、`work_dir`、`model_dir`，任务另含 `source` (`link`/`file`) 和 `input`。worker 从 stdout 逐行输出 JSON 事件：`{type:'stage',stage,detail}`、`{type:'result',text,source}`、`{type:'model',status,detail,progress}`、`{type:'error',detail}`；禁止 stdout 输出其他内容。CLI 负责为下载/转码子进程继承进程组，父进程取消整个组后清理任务目录。

## Task 1: 媒体、字幕、模型与 worker

**Files:** `backend/app/media.py`、`models.py`、`worker.py`；对应 `backend/tests/test_media.py`、`test_models.py`、`test_worker.py`。

**Interfaces:** `media.extract_link(text)->str`，`media.run_pipeline(payload:dict,emit:Callable)->dict`，返回 `{text,source}`；`models.model_status(path:Path)->dict`，`models.prepare_model(path:Path,emit:Callable)->None`。模块不能导入 API 或任务管理器。

- [x] 先写链接、多链接、字幕完整性、媒体时长、无语音和本地路径测试并运行，确认缺少模块导致失败。
- [x] 实现严格输入域名及重定向校验、yt-dlp 有界下载、FFmpeg 转码、字幕保守校验、MLX 离线转写与显式模型下载。
- [x] worker 输出事件并将内部错误转成可操作的中文提示；验证 stdout 协议。
- [x] 运行 `uv run pytest backend/tests/test_media.py backend/tests/test_models.py backend/tests/test_worker.py`，通过后提交。

示例验收断言：
```python
assert extract_link('看看 https://v.douyin.com/abc/ 这个视频') == 'https://v.douyin.com/abc/'
# model_status(empty_path)['status'] == 'missing'
# 未覆盖全段的字幕必须转写，不能把片段字幕作为完整结果。
```

## Task 2: 本地服务与任务生命周期

**Files:** `pyproject.toml`、`backend/app/config.py`、`manager.py`、`main.py`、`launch.py`；`backend/tests/test_api.py`、`test_manager.py`。

**Interfaces:** `create_app(settings=None,manager=None)`；Settings 提供受控工作目录、模型目录、上传上限与运行超时。API 遵守上述契约。

- [x] 先编写来源、令牌、单任务、上传限制、取消清理、模型状态和错误响应测试，运行确认失败。
- [x] 实现同源 API、流式上传限制、工作进程事件读取、超时与进程组取消、启动时残留清理。
- [x] 运行 `uv run pytest backend/tests/test_api.py backend/tests/test_manager.py`，通过后提交。

示例验收断言：
```python
assert client.post('/api/tasks/link', json={'share_text':'bad'}).status_code == 403
# 一个任务运行时再次提交返回 409；取消完成后临时目录不存在。
```

## Task 3: 网页工作台

**Files:** `frontend/package.json`、Vite/TypeScript 配置、`frontend/src/App.tsx`、`api.ts`、`styles.css`、`App.test.tsx`。

**Interfaces:** 使用上述 API；编辑后的全文是复制与下载的唯一数据源。

- [x] 先写初始界面、模型准备、链接/文件提交、状态轮询、错误重试、取消和编辑导出测试，运行确认缺少组件导致失败。
- [x] 实现响应式中文工作台、键盘可访问性、状态与来源标注；所有字体/图形使用本地或系统资源。
- [x] 运行 `npm test -- --run` 和 `npm run build`，通过后提交。

界面方向：浅蓝灰底、白色工作面、深蓝文字与钴蓝操作色；系统中文字体、衬线文稿正文、等宽状态文字；左右素材/文稿两区，窄屏纵向。文稿编辑区的行号作为唯一明显的编辑器视觉特征，不显示虚构任务或数据。

## Task 4: 整合、实际验证与交付

**Files:** `README.md`、`start.command`、`scripts/check.sh`、`docs/validation.md`、补充集成测试。

- [x] 编写并运行启动/静态网页/真实 FFmpeg 冒烟测试，确认缺失部分失败后实现。
- [x] 下载一个候选 MLX 模型，用本地语音样本完成真实转写；测试无语音、本地离线和取消清理，记录环境与结果。
- [x] 验证一条公开抖音样本的获取结果，实际失败需如实记录并保留导入入口。
- [x] 运行后端全套、前端全套、类型检查、生产构建、文档和 Git 格式检查；独立审查并修复重要问题。
- [x] 完成 README、启动脚本和验收记录，提交所有变更；报告实际验证范围和剩余限制。

## 执行结果

四个任务已实现并完成验证。用户已确认时长上限调整为 30 分钟。真实样本、离线验证、测试结果及限制见 [验证记录](../../validation.md)。
