# 批量链接、重复提醒与耗时

用户已要求下一版实现批量提取、重复提醒，并显示模型提取文案的时间。沿用现有本地模型、分段校对、完整稿和自动保存。

## 范围和行为

- 增加“批量链接”入口，每批最多 20 条完整抖音分享文本或裸链接，先预览标题、无效链接和重复项，再开始。无效项不提交，重复项默认跳过，可显式重新提取。MP4 仍单个导入。
- 队列在后端逐条处理，关闭网页不停止；某条失败后继续下一条。显示等待、处理、完成、失败、重复跳过和取消，完成项可打开文稿。提供暂停（当前条完成后生效）、继续、停止全部、取消等待项，以及结束后重试失败项/单项。
- 本机只有一个媒体 worker；队列运行期间不同时进行单条提取、模型下载或局部重识别。界面保持现有文稿，批量完成项由用户主动打开，避免自动切换覆盖编辑。
- 队列快照存入独立 `queue.sqlite3`，保留最近一批。重启后恢复为暂停；上次处理中断的条目标记失败，待处理项由用户继续。新批次可替换已结束批次，不覆盖历史文稿。
- 批量的临时音频保留到下一次全文提取/新批次或服务重启；勾选保留的音频继续随文稿保存，避免每启动下一条便清理本批前面的回听音频。

## 重复判断

优先使用稳定抖音视频 ID；原链接规范化键用于快速预览及旧历史匹配。规范化移除查询参数、片段及末尾斜杠，保留短链接大小写。相同批次中重复的链接/视频默认跳过。

短链接由现有受限解析器在 worker 中解析为视频 ID，检查已知视频后再下载和识别。不同短链指向同一已知视频也跳过，显示可打开的既有文稿。旧历史若没有视频 ID，只能按原链接或可读出的长链接 ID 对比；不后台请求全部旧链接。显式“仍要重新提取”会另存一份，绝不覆盖已编辑稿。

## 耗时定义

- `recognition_seconds`：worker 中本地识别调用的实际耗时，含本次模型加载，不含下载、音频转换及排队。字幕直接提取时为 null，界面注明未调用模型。
- `total_seconds`：单条任务开始至处理完成的耗时，不含排队；覆盖下载/导入、转换、识别及清理。运行中显示已用时间。
- `media_duration_seconds`：完整视频或局部识别片段的实际时长。识别速度为时长除以模型识别耗时，以“倍实时”展示，只有正数实测耗时才计算。
- 以上数据保存在 Task 和完成的历史文稿，历史摘要也可展示；旧记录为 null，不从历史日期猜测。

## 接口契约

现有单条接口保持，新增 `force: bool=false`；发现重复时返回 409 `{detail,duplicate:HistorySummary}`，前端提供“打开已有文稿”和“仍要重新提取”。真正开始后解析出的重复用正常 Task `status:'completed',stage:'duplicate',history_id:existing.id` 表示，不新增文稿，不调用模型。

`POST /api/batches/preview` 输入 `{share_text:str}`，上限 200000 字符。返回 `{items:[{index,share_text,url,title,status:'ready'|'duplicate'|'invalid',existing:HistorySummary|null,duplicate_of:int|null,error:str|null}]}`。文本拆分与初始标题由 `app.sharing.parse_batch_share_text` 提供；预览不联网。

`POST /api/batches` 输入 `{items:[{share_text:str,force:bool=false}],mode:'fast'|'accurate',retain_audio:bool=false}`，1–20 项；返回 202 Batch。服务端重新验证各项并确认全局空闲，不能只信前端预览。

`GET /api/batches/current` 返回 `{batch:Batch|null}`。

`POST /api/batches/{id}/actions` 输入 `{action:'pause'|'resume'|'cancel'|'retry_failed'}`，返回 Batch。`POST /api/batches/{id}/items/{item_id}/actions` 输入 `{action:'cancel'|'retry',force:bool=false}`，返回 Batch；重试在当前批次非运行时进行。

Batch：`{id,status:'running'|'paused'|'completed'|'cancelled',mode,retain_audio,items:[BatchItem]}`。

BatchItem：`{id,index,url,title,share_text,status:'pending'|'running'|'completed'|'failed'|'skipped'|'cancelled',force,task_id:null|string,history_id:null|string,error:null|string,detail:str,total_seconds:null|number,recognition_seconds:null|number,media_duration_seconds:null|number}`。可增加 `duplicate_of` 指向同批原项 ID。GET 在运行项附带最新阶段和耗时，队列不包含整份正文。

`Session` 增加可选 `batch:Batch|null`；批量 worker 的 Task 增加 `batch_id`。前端不能用批量 worker 的 completed 事件自动打开文稿，而应通过批量列表选择。

## 内部接口及边界

- `app.sharing.link_identity(url) -> str` 返回 `douyin:{video_id}` 或规范化短链接键；`video_id_from_url(url) -> str|None` 不联网。`parse_batch_share_text` 处理多行、多段连续分享口令及纯链接列表，单项错误独立显示。
- worker payload 新增 `known_video_ids:list[str]` 与 `force:bool`。`run_pipeline` 在提供此字段时先解析视频 ID，重复返回 `{duplicate_video_id:str}`；worker 转为 `{type:'duplicate',video_id:str}` 事件。正常结果增加 `video_id:str|None`、`recognition_seconds:float|None`、`media_duration_seconds:float`。未提供已知 ID 字段的旧测试调用保留原解析路径。
- History 增量添加 `video_id`、`source_key` 和三个耗时字段；INSERT 明确列名，保护旧表。批量与单条均保留来源原文网址、自动标题、原始稿及段时间。
- 队列控制器可在 `batches.py` 独立实现，复用 JobManager 媒体进程与清理，不重复创建媒体 worker。管理器用同一锁保护批次占用与单条任务，停止/取消必须等进程树退出才放行下一条。
- API 输入均限长、同源和令牌保护；仅批量 JSON 路径可放宽到 2.5 MB，单条原限制不变。没有在线转写、新模型和浏览器 Cookie。

## 验证

失败/重复/取消后自动继续、批次内和跨短链接去重、强制重提取保留旧编辑、重启暂停恢复、全局任务互斥、音频缓存生命周期、模型计时范围和字幕不显示虚假耗时必须有回归测试。另检查真实离线模型计时和浏览器批量预览、队列、重复操作和历史效率展示。完成后运行全部检查，提交 Git，再安全更新本地服务。
