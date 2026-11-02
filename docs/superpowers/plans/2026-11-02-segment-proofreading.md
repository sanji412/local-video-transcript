# 0.3 分段校对实施计划

**Goal:** 完成已授权的分段回听、局部本地重识别，保护已有文稿。

**Architecture:** 媒体引擎返回真实时间段；SQLite 保存段编辑，管理器保存有限生命周期的 WAV。界面在候选确认前不替换文字。接口以同日设计文档为准。

**Tech Stack:** 现有 React/TypeScript、FastAPI、SQLite、MLX Whisper、Python wave，无新增依赖。

## Global Constraints

- 全部识别在本地，沿用快速和精确模型；全局仅一个 worker。
- 原始全文不可改，单段替换不能修改其他段；保存必须校验 revision。
- 30 分钟、500 MB，最多 3000 个有效段，正文最多 200000 字。
- 音频默认临时缓存，显式选择后随历史保留，支持清除。
- 每批改动补充测试、验证并提交 Git；复用当前干净功能分支。

## 任务分工及检查点

- [x] 媒体引擎（主代理）：先在 `backend/tests/test_media.py`、`test_media_integration.py`、`test_worker.py` 写失败用例；再在 `media.py`、`worker.py` 保存真实 segments，保持旧纯文本函数兼容，增加受限 WAV 局部裁剪与 retry 协议。定向测试、提交。
- [x] 后端数据/API（后端子代理及主代理）：补充 history/manager/API 测试并实现增量存储、音频生命周期、单段 PATCH、局部任务及取消，使用受控 worker 验证持久化和失败回滚，定向测试、提交。
- [x] 前端：增加时间段界面和保存/候选交互测试；修改 api/App/useManuscript，新增 `SegmentEditor.tsx`，沿用视觉样式，支持音频控件、段自动保存、候选明确采用、临时音频提示和清除。保留旧稿兼容及全稿复制导出，定向测试、构建、提交。
- [x] 独立复核：修复候选跨文稿导航、局部失败重试路由、音频卸载和 StrictMode 问题；补充回归测试，最终复核通过。
- [x] 全量验收：196 项测试、Ruff、类型和构建通过；真实离线识别、实际 HTTP 工作流、桌面/窄屏检查完成。已更新使用与验证文档，服务升级后确认原有 6 条完整文稿未变。全部记录随本计划提交。

验收细节见 [分段校对验证记录](../../validation-v0.3.md)。功能仍在原工作分支，未推送或合并。
