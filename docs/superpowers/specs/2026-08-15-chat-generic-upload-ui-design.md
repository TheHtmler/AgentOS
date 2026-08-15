# 聊天通用附件上传 + 缩略图展示

日期：2026-08-15  
状态：草案（待实现）  
前置：`2026-08-14-chat-report-upload-analysis-design.md`、`2026-08-15-chat-upload-vision-design.md`

## 目标

把「报告解读」固定流程改成**通用文件附件**：

1. 用户上传 PDF/图片后，在输入区看缩略图；可只发附件，也可配文字一起发。
2. 发送后，用户气泡内展示缩略图（图片）或文件芯片（PDF）。
3. Agent 按**用户意图**处理附件；不默认强制化验报告解读。

## 非目标

- 灯箱 / 全屏大图
- 扩展到 PDF/图片以外的类型
- 助手气泡内嵌用户原图
- 未发送附件的服务端清理任务（可后续）

## 决策摘要

| 项 | 选择 |
|---|---|
| 上传时机 | 选文件即 `POST /v1/uploads` 落盘（方案 A） |
| 发送 | 有附件即可发；文字可选 |
| 发出的文本 | 用户正文（可空）+ `artifact_id=<uuid>` 行（后端注入 / Vision） |
| 预览 | 未发送：composer 上传区；已发送：用户气泡 |
| 原图读取 | 新增属主校验的 content GET + Web BFF |
| Agent 提示 | 通用附件指引；报告流程仅在用户意图明确时 |

## 交互

### Composer（未发送）

1. 点附件 → 选文件 → 立刻上传。
2. 成功后在输入框上方显示：
   - 图片：缩略图（`GET` 原图 URL）
   - PDF：文件图标 + 文件名
3. 可移除未发送附件（仅 UI 侧移出 pending 列表；磁盘 Artifact 可保留）。
4. **禁止**上传成功后自动 `sendMessage` 固定「解读报告」文案。
5. 发送按钮：有 pending 附件或非空文字即可启用。

### 发送载荷

```
{用户输入，可为空}

artifact_id=<uuid1>
artifact_id=<uuid2>
```

- 前端可把 `artifact_id` 行对用户气泡做结构化渲染（缩略图），不必把裸 uuid 大段展示给用户。
- 持久化消息仍含 `artifact_id=`，以便历史重载与后端解析。

### 用户气泡（已发送）

- 解析 content 中的 `artifact_id`。
- 图片：缩略图（同源 BFF URL）。
- PDF：文件名芯片。
- 可见文字仅展示用户正文部分（去掉或折叠 `artifact_id` 行）。

## API

### 已有

- `POST /v1/uploads`：落盘 + Artifact（OCR 可选）——保持。

### 新增

`GET /v1/uploads/{artifact_id}/content`

- 鉴权：当前用户 session。
- 校验：`get_owned_artifact`（属主；Case 作用域与现有 read 一致）。
- 响应：原文件 bytes + 正确 `Content-Type`；可选 `Content-Disposition: inline`。
- 404：不存在或无权。

Web BFF：`GET /api/uploads/[artifactId]/content` → 转发 cookie/session。

上传响应可继续返回 `artifact_id`、`title`、`mime_type`（前端缩略图分支用）。

## Agent 提示词

- 将 `REPORT_ANALYSIS_INSTRUCTIONS` 改为通用 **`UPLOAD_ATTACHMENT_INSTRUCTIONS`**（或等价命名）：
  - 有附件时先理解用户文字意图；无文字则简要确认看到了什么并询问需要什么帮助。
  - 仅当用户要解读化验/检查报告时，再走 knowledge_search + 教育性解读 + Case HITL。
  - Vision / OCR / `read_artifact` 用法保留。
- **不要**再在「只要挂了 `case_context_read`」时无条件追加报告专章；改为有 upload 注入块或消息含 `artifact_id` 时追加通用附件指引（实现可选：始终挂薄指引，报告细则内嵌「若用户意图是…」）。

## 文案 / UI 措辞

- 「报告」→「文件 / 附件」；placeholder / title / notice 同步中性化。

## 验收

1. 选图 → composer 出现缩略图；不自动开 Run。
2. 无字点发送 → 用户气泡有图；模型按通用附件意图响应。
3. 有字 + 图一起发 → 气泡有图+文字；模型跟文字意图。
4. 刷新历史会话 → 仍能从 `artifact_id` 渲染缩略图。
5. 非属主无法通过 content URL 取图。
6. 明确说「解读这份化验单」时仍可走知识库 + Case 路径。

## 风险

- 历史消息里旧的「请结合知识库解读…」文案仍存在；不影响新交互。
- 无字发送依赖模型不瞎套报告模板——靠提示词约束。
