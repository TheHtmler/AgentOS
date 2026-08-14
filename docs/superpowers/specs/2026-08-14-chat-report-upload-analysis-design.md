# 聊天报告上传 + 知识综合分析 + Case 闭环

日期：2026-08-14  
状态：已实现  
前置：
- Case / CaseFact / HITL（`2026-08-06-case-memory-knowledge-nhc-design.md`，已实现）
- Artifact + `read_artifact`（fetch_url 后续已落地）
- Ops 知识导入 + 本机 PaddleOCR（`2026-08-14-ops-knowledge-import-design.md`，已实现）
- AG-UI 纯文本用户消息（当前硬约束）

## 背景

运营可在 Ops 导入「血尿串联」等公共指南到 `knowledge_*`，聊天侧 `knowledge_search` 可检索。  
用户需要在 **AgentOS 前端**上传自己的化验单（图片/PDF），系统 OCR 后结合公共知识综合解读，并把关键指标写入 Case（经 HITL），形成闭环。

当前缺口：Web 无附件；AG-UI 拒绝非文本 content；OCR 仅挂在 Ops 导入路径；无「用户报告 → Artifact(upload)」写入入口。

## 目标（闭环）

1. Web 聊天支持上传 **图片 + PDF**。
2. 服务端 OCR/抽字，**本机保留原文件**，正文写入 **Artifact(`kind=upload`)**（Case/Thread 作用域）。
3. 自动发起（或一键）分析 Run：Agent 使用 `read_artifact` + `knowledge_search` 综合解读。
4. 抽取关键指标为 `case_facts(proposed)`，用户经现有 HITL 确认为 `confirmed`。
5. 公共知识与用户报告严格隔离：报告永不写入 Ops 知识库。

## 非目标

- 多模态模型直接看图（本期 OCR；扫描件走 PaddleOCR）
- 对象存储（S3/OSS）；本期 Mac mini 本地目录
- 用户报告进入 `knowledge_*`
- 全病种专用解析器 / 完美结构化实验室字典
- 修改 General Agent 强制 Case；无 `case_enabled` 时只解读不写档
- Ops 知识切片质量大修（可另开任务）

## 决策摘要

| 项 | 选择 |
|----|------|
| 架构 | Artifact 中心 + 文本 AG-UI（方案 B） |
| 原文件 | 本机磁盘保留 + Artifact 存 OCR 文本（便于复查） |
| 消息 | 用户消息仍为字符串；携带 `artifact_id` 引用；Run 侧注入报告摘要 |
| 知识 | 仅 `knowledge_search` 读公共库 |
| Case | 复用 extract → proposed → HITL confirm |
| OCR | 复用 `ocr_client` / `pdf_extract` |
| 上限 | 20MB；PDF ≤ 50 页；单次最多 N=3 个附件（可配置） |

---

## 一、端到端流程

```text
[Web] 选择文件 → POST /api/uploads (BFF) → Agent API /v1/uploads
         ↓
   校验 mime/大小 → 存原文件到 UPLOAD_ROOT/{user_id}/{artifact_id}/...
         ↓
   图片: OCR | PDF: pdf_extract → 正文
         ↓
   create_artifact(kind=upload, content=正文, mime_type, meta={path, pages, ocr_pages...})
         ↓
[Web] 展示附件卡片（文件名、字符数、OCR 状态）
         ↓
   用户点「分析」或上传后自动发送：
   UserMessage(text): 「请结合知识库解读我上传的报告。artifact_id=<uuid>」
         ↓
[Run] 注入：报告标题/前 K 字预览 + artifact_id 提示
      Agent: read_artifact → knowledge_search → 综合回答
         ↓
[Post-run / 工具] 指标 → case_facts proposed → HITL 确认
```

---

## 二、存储

### 2.1 原文件

- 环境变量 `UPLOAD_ROOT`（默认 `services/agent-api/data/uploads`，gitignore）。
- 路径：`{UPLOAD_ROOT}/{owner_user_id}/{artifact_id}/{safe_filename}`。
- `artifacts.meta` 至少包含：`original_filename`、`stored_path`（相对 UPLOAD_ROOT）、`byte_size`、`sha256`（可选）、`text_layer_pages`/`ocr_pages`（PDF）。

### 2.2 Artifact

沿用现有表；`kind='upload'`；`content` = OCR/抽字全文（受 `artifact_max_chars` 截断并记 meta）；`mime_type` = 原文件类型（如 `application/pdf`、`image/jpeg`）；`title` = 原文件名或用户标题。

权限：仅 `owner_user_id`；与 Case 成员一致时可读（与现有 artifact ACL 对齐）。

### 2.3 不新建「报告表」

化验时间线若未来需要，再以 Case 扩展表挂载；本期用 `case_facts` + tag（如 `化验`、`报告`）足够闭环。

---

## 三、API

### 3.1 `POST /v1/uploads`（用户 session）

multipart：`file`（必填）、`thread_id`（必填）、可选 `title`。

行为：

1. 校验用户拥有该 Thread；解析绑定 `case_id`（若有）。
2. 校验扩展名/mime：`pdf, png, jpg, jpeg, webp`；大小 ≤ 20MB。
3. OCR/抽字；失败 → 4xx/502（OCR 宕机），不落半成品 Artifact（或落 `meta.status=failed` 由实现二选一：**推荐失败不建 Artifact**）。
4. 写磁盘 + `create_artifact`。
5. 返回：

```json
{
  "artifact_id": "...",
  "title": "...",
  "mime_type": "application/pdf",
  "content_chars": 1234,
  "ocr_pages": 2,
  "text_layer_pages": 0,
  "case_id": "..." 
}
```

### 3.2 Web BFF

`apps/web`：`POST /api/uploads` 转发 cookie/session 与 multipart；超时 ≥ 120s。

### 3.3 不改 AG-UI content 类型

继续 `Only text user messages`；附件通过上传 API + 文本引用闭环。

---

## 四、Web UI

1. 聊天输入区：附件按钮；多选最多 3；列表可移除。
2. 上传中禁用发送或显示进度；失败可重试。
3. 成功后消息区附件条：文件名、页/字数、入口「请 AI 分析」（预填文案 + artifact_id）。
4. 可选：上传成功自动发送分析（设置默认 **开**，可在提交前取消）。
5. 不在浏览器做 OCR。

---

## 五、Agent 与 Case

### 5.1 Run 注入

当用户消息含 `artifact_id=<uuid>`（或约定 JSON 前缀）且 Artifact 属主匹配：

- 将短预览（如前 1500 字）+ 标题写入本 Run 的 context（类似 Case facts 注入），并提示必须 `read_artifact` 读全文窗口。
- `case_enabled` Agent：提醒可把稳定化验结论写入 Case（走 HITL）。

### 5.2 工具

- 必备：`read_artifact`、`knowledge_search`（已有）。
- 可选薄工具 `list_thread_uploads`（列本 Thread 最近 upload Artifact）——若时间紧可不做，靠消息内 id。

### 5.3 系统提示（垂类 overlay 增量）

分析报告时：

1. 先概括报告类型与关键数值（注明 OCR 可能有误）。
2. `knowledge_search` 检索相关公共知识（如串联质谱/血尿相关词）。
3. 对照解释；区分「知识库依据」与「模型推断」。
4. 非诊断声明；紧急症状建议就医。
5. 稳定指标 → proposed Case facts，勿静默覆盖他人。

### 5.4 Case 写入

复用现有 post-run extract / `case_attribution_confirm` HITL；`source_thread_id` / `source_run_id` 照旧。  
tags 建议含 `报告`；`key` 能映射则映射（如已有槽），否则自由 `content`。

无 `case_enabled`：跳过写档，只完成解读。

---

## 六、安全与隐私

- 上传需登录；Thread 所有权校验。
- 原文件目录不可被静态 URL 公网直链；仅 API 鉴权下载（**本期可只保留文件、暂不开放下载 API**；meta 供运维）。
- SSRF：不适用（用户上传非 URL 抓取）。
- OCR Key 仅服务端。
- 日志避免打印完整化验正文。

---

## 七、配置

| 变量 | 含义 |
|------|------|
| `UPLOAD_ROOT` | 原文件根目录 |
| `UPLOAD_MAX_BYTES` | 默认 20_000_000 |
| `UPLOAD_MAX_FILES_PER_MESSAGE` | 默认 3 |
| `OCR_*` | 与知识导入共用 |

---

## 八、测试与验收

### 自动化

- 上传 PDF/图片（mock OCR）→ Artifact kind=upload + 磁盘文件存在。
- 非 Thread owner → 403。
- 超大/超页 → 400。
- 消息含 artifact_id → Run 注入预览（单元/集成）。
- case_enabled 路径：分析后可产生 proposed（可 mock extract）。

### 人工（Mac mini）

1. Ops 已有血尿串联类知识；OCR health 正常。
2. 聊天上传一张报告图 + 一份 PDF。
3. 自动/手动分析：回答引用知识要点。
4. HITL 确认后 Case 侧栏可见事实。
5. General Agent：可解读、不写 Case。

---

## 验收标准

- [x] Web 可上传图片/PDF 并看到成功附件
- [x] Artifact `upload` 可读；原文件在 UPLOAD_ROOT
- [x] 分析回复结合 `knowledge_search`（知识库有命中时）
- [x] case_enabled 下可 HITL 确认写入 Case
- [x] 用户报告未进入 `knowledge_documents`
- [x] AG-UI 仍只接受文本用户消息

---

## 实现顺序建议

1. Upload API + 磁盘 + Artifact + 测试  
2. Web BFF + 附件 UI + 分析发送文案  
3. Run 注入 artifact 预览  
4. 垂类 prompt 增量 + Case/HITL 联调  
5. 文档与 deploy（UPLOAD_ROOT、OCR）
