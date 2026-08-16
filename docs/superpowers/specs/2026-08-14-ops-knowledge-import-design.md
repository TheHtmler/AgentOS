# Ops 知识库多途径导入（含本地 OCR）

日期：2026-08-14  
状态：已实现  
前置：`2026-08-13-ops-admin-shell-knowledge-agents-design.md`（已实现）

## 背景

当前知识内容几乎只能通过 `seed/knowledge/mma_pa_chunks.json` + `scripts/seed_knowledge.py` 入库；Ops 仅支持列表、元数据/审核 PATCH、切片与快照只读。运营需要在控制台用多种方式导入资料，且 PDF 常见图文混排/扫描页，必须能 OCR。

Mac mini 上已有面向小程序的 **PaddleOCR HTTP 服务**（本地推理，可经 frp 对外），本设计优先复用该服务，而不是在 agent-api 进程内再嵌一套 Tesseract。

## 目标

1. Ops 提供统一导入入口，支持：**JSON、纯文本、链接、文件（`.txt` / `.md` / `.json`）、PDF**。
2. 所有途径归一为「文档 + chunks」，再走与 seed 一致的 **同 slug 覆盖 + 打快照**。
3. PDF：优先抽取文字层；页内文字不足时渲染为图，调用 **本机 PaddleOCR**。
4. 导入后可跳转文档详情；默认 `review_status=curated`。

## 非目标

- 云端 Document Intelligence / 第三方付费 OCR（本期不接）
- Chunk 在线富文本编辑、快照一键 restore
- 普通用户（非 ops）投稿公共库
- 完美还原复杂表格/版式结构
- 多知识库切换（本期固定 `mma-pa`，API 预留 `base` 参数）

## 决策摘要

| 项         | 选择                                          |
| ---------- | --------------------------------------------- |
| 架构       | 统一 ingest 管道（方案 A）                    |
| 冲突       | 同 base + slug → 快照后覆盖（与 seed 一致）   |
| OCR        | 复用 Mac mini 已有 PaddleOCR HTTP（本机优先） |
| PDF 文本层 | PyMuPDF（`pymupdf`）                          |
| 链接正文   | 已有依赖 `trafilatura`                        |
| 上限       | 单文件 ≤ 20MB；PDF ≤ 50 页                    |
| 默认审核   | `curated`；来源字段可选手填                   |

---

## 一、统一管道

```
入口（JSON / text / url / file / pdf）
        │
        ▼
  normalize → DocumentSpec { slug, title, source_*, version_label, review_status, chunks[] }
        │
        ▼
  upsert_document(base="mma-pa", spec, created_by=ops_subject)
        │
        ├── 若已有 chunks → KnowledgeDocumentSnapshot(created_by=ops_subject)
        ├── 替换 chunks（可选 embedding，与 seed 相同开关）
        └── 返回 ImportResult
```

- 复用/抽出 `knowledge_store` 中「单文档 upsert + 快照」逻辑，避免只服务 seed 的硬编码路径；seed CLI 可继续调同一底层。
- `created_by`：seed 仍为 `"system"`；Ops 导入为 ops subject（如 `admin`）。

### 1.1 切块规则（文本 / 链接 / PDF 抽出正文后）

1. 先识别标题（Markdown `#`、第 X 章/节、`1.` / `一、`）和 PDF 页标记 `[第 N 页]`，再合并硬换行，按段落打包。
2. 默认 `max_chars=900`、重叠 `150`；超长段再按句号切。切片正文重复所属标题，便于检索。
3. 每块生成 `chunk_index`、`title`（标题或首句）、`section_label`（页码 · 标题）。
4. JSON 途径若已自带 `chunks`，**不再二次切块**（仅校验）。

### 1.2 Slug

- JSON：使用 payload 内 slug。
- 文本/链接/PDF：运营必填 slug（小写、数字、连字符）；UI 可根据标题预填可编辑建议。

---

## 二、各途径行为

| mode    | 输入                                          | 处理                                                                                                              |
| ------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `json`  | 粘贴或 `.json` 文件                           | 兼容现有 seed：单文档 `{document, chunks}` 或多文档 `{knowledge_base?, documents[]}`；可只导入其中文档到 `mma-pa` |
| `text`  | 标题 + 正文，或 `.txt`/`.md`                  | 切块；`source_kind` 默认 `curated_summary`                                                                        |
| `url`   | URL + slug + 标题（标题可空则用页面标题）     | `httpx` 拉取 → `trafilatura` 抽正文 → 切块；`source_url`=URL；失败返回明确错误（不静默空写入）                    |
| `file`  | multipart；按扩展名分流到 json/text/pdf/image | 同上                                                                                                              |
| `pdf`   | `.pdf` 文件                                   | 见第三节                                                                                                          |
| `image` | `.jpg` / `.jpeg` / `.png` / `.webp`           | 走本机 PaddleOCR，再按文本切块                                                                                    |

### 2.1 API

`POST /v1/ops/knowledge/import`（需 ops session）

- **JSON body**（`mode=json|text|url`）：字段见下。
- **multipart**（`mode=file` 或带 `file` 的 pdf/json/text）：`mode`、`slug`（非 json 时）、`title`、可选 `source_*`、`version_label`、`base`（默认 `mma-pa`）、`file`。

响应示例：

```json
{
  "documents": [
    {
      "id": "...",
      "slug": "...",
      "title": "...",
      "chunk_count": 12,
      "overwrote": true,
      "ocr_pages": 3,
      "text_layer_pages": 10
    }
  ]
}
```

错误：400 校验/空正文/超限；404 base 不存在；502 OCR 服务不可用；413 超大小（若代理层未拦）。

### 2.2 上限与超时

| 限制         | 值                                              |
| ------------ | ----------------------------------------------- |
| 上传体积     | 20MB                                            |
| PDF 页数     | 50                                              |
| URL 响应体   | 5MB                                             |
| 导入请求超时 | PDF+OCR 允许较长（建议 API 120s；Ops BFF 对齐） |

---

## 三、PDF + OCR（复用 Mac mini 服务）

### 3.1 流程（逐页）

1. PyMuPDF 打开 PDF，页数 > 50 → 400。
2. 对每一页：
   - 抽取文字层；若去空白后字符数 ≥ `OCR_TEXT_MIN_CHARS`（建议 40）→ 采用文字层。
   - 否则将该页渲染为 PNG/JPEG（适当 DPI，如 150–200）→ `POST {OCR_BASE_URL}/ocr` 或兼容 `/ocr/file`（以实际运行中的服务为准）→ 拼接行文本。
3. 全书文本按切块规则入库；`ImportResult` 统计 `ocr_pages` / `text_layer_pages`。
4. OCR 连续失败（服务 down / 401）→ 整次导入失败并提示检查 OCR 服务；单页空结果可记警告但仍继续（最终若全书为空 → 400）。

### 3.2 配置（agent-api `.env`，不入库）

| 变量                 | 含义                                                    |
| -------------------- | ------------------------------------------------------- |
| `OCR_BASE_URL`       | 例：`http://127.0.0.1:8787`（本机 OCR，优先于公网 frp） |
| `OCR_API_KEY`        | 与现有服务 `X-API-Key` 一致                             |
| `OCR_TEXT_MIN_CHARS` | 页文字层阈值，默认 40                                   |
| `OCR_ENABLED`        | 默认 true；false 时 PDF 仅文字层，不足则报错            |

说明：历史上存在两套相近接口文档——Mac mini 教程中的 `POST /ocr`（返回 `{text}`），以及仓库外 `paddleocr-service` 的 `POST /ocr/file`（返回 `{lines:[{text,...}]}`）。实现时做 **适配层**：按响应 JSON 形状归一为纯文本；部署文档写明探测 `/health` 与实际 path。

### 3.3 与小程序 OCR 的关系

- **同一台 Mac mini、同一 PaddleOCR 进程**可服务小程序与 AgentOS。
- AgentOS **默认走本机 loopback**，不绕公网 frp，降低延迟与密钥暴露面。
- 不把 OCR 模型加载进 agent-api 进程，避免与聊天推理抢内存。

---

## 四、Ops UI

| 路由                | 内容                                                 |
| ------------------- | ---------------------------------------------------- |
| `/knowledge`        | 增加「导入」按钮；保留能力说明（更新为已支持多途径） |
| `/knowledge/import` | Tab：JSON / 文本 / 链接 / 文件（含 PDF）             |

交互：

- 提交中禁用按钮；成功展示 chunk 数、是否覆盖、OCR 页数；链到详情。
- PDF/大文件显示「可能较慢」提示。
- 不在前端做 OCR。

BFF：`apps/ops` 增加 `POST /api/ops/knowledge/import`，转发 multipart/JSON 与超时设置。

---

## 五、测试与验收

### 5.1 自动化

- 文本切块单元测试（短文、超长段、空输入）。
- JSON import：新建 + 同 slug 覆盖产生 snapshot。
- URL：mock httpx + trafilatura 输入。
- PDF：fixture 纯文本 PDF；OCR 路径 mock HTTP 客户端（不要求 CI 起 PaddleOCR）。
- 超页数/空正文 → 400。

### 5.2 人工（Mac mini）

1. `curl $OCR_BASE_URL/health` 正常。
2. Ops 导入短 `.txt`、seed 形 JSON、一个外链、一个扫描感 PDF。
3. 同 slug 再导一次 → 快照 +1，正文更新。
4. 聊天侧 `knowledge_search` 能命中新切片（若 embedding 开启则等嵌入完成）。

---

## 六、部署注意

1. agent-api 增加依赖：`pymupdf`；OCR 为 HTTP 客户端（httpx 已有）。
2. 确认 Mac mini OCR launchd/进程在跑；`.env` 配置 `OCR_BASE_URL` / `OCR_API_KEY`。
3. `macmini-deploy.sh` 或 ops/API 文档增加「导入前检查 OCR health」一句。
4. Nginx/反代若限制 body，需 ≥ 20MB（仅本机 API 也可只改 uvicorn/系统层）。

---

## 验收标准

- [ ] Ops 可完成 JSON / 文本 / 链接 / txt/md / PDF 导入并出现在列表
- [ ] 同 slug 覆盖会生成快照且 `created_by` 为运营账号
- [ ] 图多 PDF 在 OCR 可用时能入库；OCR 关闭或宕机时有明确中文错误
- [ ] 超过 20MB 或 50 页被拒绝
- [ ] 现有 seed CLI 与检索行为不回归
