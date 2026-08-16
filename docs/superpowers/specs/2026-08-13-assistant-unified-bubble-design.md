# 助手回合统一气泡

日期：2026-08-13  
状态：accepted（已实现）  
前置：工具行 UI（`2026-08-13-tool-call-row-ui-design.md`）、过程组 polish

## 目标

一轮助手输出收进**同一个** `agentos-message-assistant` 气泡：thinking 行 → 工具行 → 最终文字。去掉气泡外的「处理中 / 已处理」过程组外壳。

## 决策

| 项         | 选择                                                         |
| ---------- | ------------------------------------------------------------ |
| 过程组外壳 | 移除（扁平铺在助手泡内）                                     |
| Thinking   | 运行中「思考…」，结束后「思考」；不展示 reasoning 原文       |
| 工具行     | 沿用 icon + toolName + params                                |
| 流式       | 始终渲染助手泡；草稿正文直接在泡内底部，不再折进「分析」note |
| HITL       | 审批条仍在 viewport 下方（泡外）                             |

## 非目标

- 改后端事件；展示完整 thinking 文本；把审批卡塞进气泡

## 改动面

- `chat-panel.tsx`：过程步骤挂到 assistant 渲染；删除 user 后 ProcessGroup / suppressOutsideAssistant / foldAnalysisIntoProcess
- `globals.css`：泡内过程栈间距（可选）
- 可保留 `process-group.tsx` 文件但不再引用（或删未用 import）

## 验收

1. 有工具的一轮：仅一个助手泡，内含 thinking/工具/正文
2. 无工具：助手泡仅正文（与现一致）
3. 流式不出现「过程组在用户下、助手泡被隐藏」
4. `tsc --noEmit` 通过
