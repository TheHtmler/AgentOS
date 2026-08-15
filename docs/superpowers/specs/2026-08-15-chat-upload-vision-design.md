# 聊天上传 Vision 多模态（Gemma 4）

日期：2026-08-15  
状态：已实现  
前置：`2026-08-14-chat-report-upload-analysis-design.md`

## 决策

- **AG-UI 仍以文本 + `artifact_id` 为主**（不强制前端传 base64）。
- Run 时服务端读取 `UPLOAD_ROOT` 原文件，把图片（及 PDF 首页渲染）作为 `BinaryContent` / AG-UI `BinaryInputContent` 注入当前用户轮次。
- **原文件优先落盘**：校验通过后写入 `UPLOAD_ROOT` 并建 Artifact；OCR 为可选备份文本。OCR 宕机时上传仍成功（`meta.ocr_status=failed`），解读依赖 Vision 看原图。
- **OCR 成功时文本写入 Artifact**，供 `read_artifact`、Case、预览；Vision 与 OCR 并存。
- 开关：`UPLOAD_VISION_ENABLED`（默认 true）。

## 非目标

- 前端 AG-UI 原生拖图协议改造（可选后续）
- 音频多模态
- 强制所有 PDF 全文逐页 Vision（默认最多前 N 页）
