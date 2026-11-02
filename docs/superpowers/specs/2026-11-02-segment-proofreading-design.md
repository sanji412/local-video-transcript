# 0.3 分段回听与局部重新识别

用户已授权完成两项 P0：分段时间戳、点击回听，以及仅对选中片段重新识别。沿用本地 React/FastAPI/MLX Whisper 工作台，不调用在线识别，不额外下载模型。

## 使用流程

新提取文稿保留真实识别段或完整字幕的起止秒数。分段校对区显示时间范围、可编辑文字、回听和重新识别按钮；复制及 TXT 导出始终包含完整编辑稿。原始转写全文只读。旧文稿无时间数据时继续沿用全文编辑，提示重新导入原视频才能获得分段功能，不伪造对齐。

点击回听定位到该段开始，到该段结束自动暂停，支持暂停和原生音频控件。重新识别使用当前选择的本地模型，在独立可取消进程中只解码该段音频，沿用全局单任务限制。完成后显示候选结果，不自动修改文稿；点击“采用识别结果”才按当前版本保存该段。其他段文字、全部时间戳和原始转写保持不变。没有更准确结果的保证。

按段编辑自动保存，采用结果前先保存当前编辑。所有保存使用文稿 revision，冲突时保留本地稿并提示重新载入；失败、取消、失联不得覆盖已保存或未保存编辑。识别中禁止切换文稿和启动其他任务，可取消。完成的局部识别即使重连也能恢复候选。

## 音频管理

只缓存 16 kHz 单声道 PCM WAV，不保留视频。导入区“随文稿保存回听音频”默认关闭：临时音频仅用于本次校对，下一次全文提取或服务重启时清理。开启则随历史保留到用户清除。文稿提供“清除回听音频”，仅清除音频，文案和时间戳仍在；删除文稿同时清理关联音频。无音频时仍可编辑时间段，但不能回听或重识别。

## 数据及接口契约

- HistoryRecord 在旧字段外增加 `segments: [{id: int, start: float, end: float, original_text: str, text: str}]`、`audio_available: bool`、`audio_retained: bool`。旧稿 segments 为 []。
- 分段稿的 `edited_text` 恒等于各段 text 以换行连接；普通全文 PATCH 不得破坏分段关系（不一致编辑返回 409）。原始全文及段 original_text 不可变。段数最多 3000，每段不超过 10000 字，全文不超过 200000 字，时间为有限数且 0 <= start < end <= 1800，按时间排序。
- POST `/api/tasks/link` 增加可选 `retain_audio: bool = false`；上传文件表单同名字段，默认 false。
- GET `/api/history/{id}/audio`：本机 WAV，支持 Range；不存在返回 404。
- DELETE `/api/history/{id}/audio`：受会话令牌保护，成功返回更新后的 HistoryRecord；有任何任务执行时返回 409。
- PATCH `/api/history/{id}/segments/{segment_id}`：`{revision:int,text:str}`，返回整个更新后的 HistoryRecord，保留乐观锁。
- POST `/api/history/{id}/segments/{segment_id}/recognize`：`{revision:int,mode:'fast'|'accurate'}`，202 返回 Task。校验文稿版本、音频和模型可用，不覆盖文字。
- Task 增加 `kind:'task'|'retry'`，局部任务另含 `history_id` 和 `segment_id`。局部完成时 text 为候选，history_id 指向原文稿；候选仅存在当前任务状态，不额外改写文稿。普通任务完成语义不变。

## 进程及文件边界

主任务 worker 返回 `{text,source,segments}`，任务工作目录中生成 `audio.wav`。完成前由管理器原子发布到数据目录 `audio/{record_id}.wav`，并保存记录，然后清理工作目录；异常需回滚音频，不能假报完成。已有无 segments 的测试 worker 继续兼容。

局部任务 payload 为 `{kind:'retry',input:work/source.wav,start,end,model_dir,work_dir,mode,parent_pid,...}`。管理器先复制缓存到任务目录，worker 用 WAV 帧裁剪，调用相同离线模型，仅返回 `{text,source:'transcription'}`。不重新下载视频。取消、超时、父进程崩溃仍清理整个进程组，候选不写入历史。

历史数据库采用增量建表保留旧记录，音频路径只由已验证记录 ID 推导，不接收任意外部文件路径。音频缓存恢复与清理不跟随符号链接。全局单任务、30 分钟与 500 MB 限制保持。

## 验证

测试覆盖时间戳和过滤一致性、字幕时序、WAV 局部裁剪、离线调用、数据库升级、版本冲突、单段保存不影响其他段、音频 Range/清除/过期、局部取消和候选采用、前端切换及请求竞态。全部后端/前端测试、类型检查、构建通过后提交。另用实际本地样本验证时间段、回听文件和局部识别；界面检查桌面及窄屏。
