# 工具调用行 UI（icon + toolName + params）

日期：2026-08-13  
状态：accepted（已实现）  
前置：`docs/superpowers/specs/2026-08-03-inline-tool-call-ui-design.md`、`docs/superpowers/specs/2026-08-03-chat-process-ui-polish-design.md`

## 背景

当前过程组内工具折叠行使用 emoji + **中文动词句**（如「已搜索网页：…」）。用户希望更接近参考 UI：主行一眼看到英文工具名与关键参数，执行完成后由模型在过程组外总结。

## 目标

1. 折叠行结构：`SVG 图标 | toolName | 关键参数`；副行状态文案。
2. 保留过程组外壳（「处理中… / 已处理 {时长}」）与可展开详情。
3. 最终助手总结仍在过程组外（既有编排）。
4. 历史回放与实时流式共用同一行样式。

## 非目标

- 拆掉过程组
- per-tool 耗时（需后端事件，另开需求）
- 大改 Thinking / HITL 审批卡外观
- 后端 API / run_events 变更

## 决策摘要

| 项     | 选择                                                  |
| ------ | ----------------------------------------------------- |
| 主文案 | 英文 `toolName` + 关键参数（非中文动词句）            |
| 过程组 | 保留外壳，只改组内工具行                              |
| 图标   | 线框 SVG（`currentColor`），按工具/域映射；不用 emoji |
| 耗时   | 仅过程组级 Run 时长；无单工具耗时                     |

---

## 一、折叠行布局

```
[SVG]  toolName   keyParam…                    ▸
       执行中… / 已完成 / 失败 / 待审批
```

- 主行：图标 + 英文 `toolName`（稍强调）+ 截断关键参数（约 48–64 字）
- 副行：状态短词（见下表）
- 整行可点切换展开；右侧 ▸/▾
- 参数与 toolName 浅色分隔，**不用**「正在搜索：」整句

### 副行状态

| status            | 文案    |
| ----------------- | ------- |
| running           | 执行中… |
| done              | 已完成  |
| error             | 失败    |
| awaiting_approval | 待审批  |

### 展开区

保持现有信息：优先 query/url/expression 等已识别字段；否则 raw `argsText`；`provider`；`result`/`error` summary。

---

## 二、关键参数摘要

从 `argsText` JSON 解析（流式半截失败时不崩 UI）：

| toolName           | 主行参数                               |
| ------------------ | -------------------------------------- |
| `web_search`       | `query`                                |
| `fetch_url`        | 短 URL（hostname + path）              |
| `read_artifact`    | `artifact_id`（可截断）                |
| `time_diff`        | `start`→`end`（缺 end 则 `start`→now） |
| `calculate`        | `expression`                           |
| `growth_assess`    | `sex` + 年龄/身高体重要点              |
| `knowledge_search` | `query`                                |
| `case_*`           | 有则取首个短字段，否则仅 toolName      |
| 未知 / 解析失败    | 省略参数或 `…`                         |

---

## 三、SVG 图标映射

线框约 16px，`currentColor`：

| 工具                          | 图标语义              |
| ----------------------------- | --------------------- |
| `web_search`                  | search                |
| `fetch_url` / `read_artifact` | link / document       |
| `calculate`                   | calc                  |
| `time_diff`                   | clock                 |
| `growth_assess`               | chart                 |
| `knowledge_search`            | book                  |
| `case_*`                      | folder / user         |
| `mcp_*`                       | literature / external |
| 默认                          | gear                  |

可放在 `tool-call-card.tsx` 或旁路 `tool-icons.tsx`。

---

## 四、改动面

| 文件                                                        | 改动                           |
| ----------------------------------------------------------- | ------------------------------ |
| `apps/web/src/components/chat/tool-call-card.tsx`           | 折叠行结构与摘要逻辑           |
| `apps/web/src/components/chat/tool-icons.tsx`（新建，可选） | SVG 映射                       |
| `apps/web/src/app/globals.css`                              | `.agentos-tool-call*` 双行布局 |
| 若有 headline 单测                                          | 更新断言                       |

基本不动：`process-group.tsx`、`chat-panel.tsx` 编排、后端。

---

## 五、验收

1. 运行中：`icon + web_search + query…`，副行「执行中…」
2. 完成后：副行「已完成」；展开有 result；组外有模型总结
3. `time_diff` / `calculate` 等按 §2 显示参数
4. 暗色主题可读；历史回放同样式
5. `pnpm --filter web exec tsc --noEmit` 通过
