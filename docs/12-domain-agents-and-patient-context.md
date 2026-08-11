# 领域 Agent 与患者上下文架构

日期：2026-08-11

状态：proposed

## 决策摘要

AgentOS 不先构建一个与 MMA/PA 业务硬编码绑定的专用应用，也不先把所有通用平台能力做成大而全的框架。采用分层方案：

1. 先稳定通用 Agent Runtime、身份、资源所有权、Run、Tool、Artifact 和审计边界。
2. 将 MMA/PA 实现为第一个领域 Agent Profile 和领域知识包。
3. 将公共医学知识与用户/患者私有上下文分开存储、检索和授权。
4. 每次回答由领域 Agent、公共知识库、患者 Case 和当前 Thread 共同构成上下文。

目标是构建有来源引用、能使用患者历史、能识别资料缺口并在高风险场景升级的知识型助手，不把模型描述为可以独立诊疗的医生。

## 目标与非目标

### 目标

- 允许多个用户分别使用同一个 MMA/PA Agent。
- 每个用户可以管理一个或多个患者 Case。
- 同一患者可以有多个独立咨询 Thread，并在后续咨询中复用该患者的已确认上下文。
- 公共 MMA/PA 知识库只读共享，患者资料严格按授权范围隔离。
- 所有医学回答尽可能带有来源、版本和适用范围。
- 记录每个 Run 使用的 Agent 版本、知识库版本和患者上下文快照，支持复现和审计。

### 非目标

- 不把患者聊天记录直接训练进模型权重。
- 不把不同患者的历史消息合并为公共知识。
- 不让模型自行修改饮食、药物、剂量或急性期处理计划。
- 不在第一阶段实现组织级多租户、复杂医疗机构角色或自动医疗决策。

## 分层模型

```text
通用 AgentOS Runtime
  ├── Agent Profile：mma-pa
  ├── MMA/PA 公共知识库（共享、只读、版本化）
  ├── User（登录账户）
  │     └── Patient Case（患者主体）
  │           ├── 已确认患者事实
  │           ├── 当前医疗团队计划
  │           ├── 化验与事件时间线
  │           ├── 患者私有 Artifact
  │           └── 多个咨询 Thread
  └── Run / Message / Tool Event / Audit
```

`User` 表示使用系统的人，`Patient Case` 表示被咨询的患者主体，二者不能合并为同一个身份。一个家长可能管理多个患者；未来一个患者也可能授权多个家长或临床成员访问。

## 核心对象

| 对象                | 作用                                             | 共享范围         |
| ------------------- | ------------------------------------------------ | ---------------- |
| `AgentProfile`      | 领域 Agent 的身份、系统规则、工具范围和模型配置  | 可被多个用户使用 |
| `AgentVersion`      | Agent 行为规则的不可变版本                       | 按 Run 固定      |
| `KnowledgeBase`     | 一组有明确领域和权限范围的知识来源               | 公共或私有       |
| `KnowledgeDocument` | 资料元数据、来源、版本、审核状态和 Artifact 引用 | 按知识库授权     |
| `KnowledgeChunk`    | 文档中可检索的片段及其结构化标签                 | 按知识库授权     |
| `PatientCase`       | 患者主体和患者级访问边界                         | 私有             |
| `PatientFact`       | 从资料或对话中提取的、带来源和状态的结构化事实   | 私有             |
| `CarePlan`          | 医疗团队提供的当前计划和生效范围                 | 私有、优先级高   |
| `Thread`            | 某个用户针对某个患者和某个 Agent 的对话          | 私有             |
| `Run`               | 一次执行，记录 Agent/知识/患者上下文版本         | 私有             |
| `Artifact`          | PDF、化验单、计划、原始文档和生成结果            | 按用户和患者授权 |

当前实现映射为：`PatientCase` 对应平台通用表 `cases`，`PatientFact` 对应 `case_facts`，授权关系对应 `case_memberships`。`threads.case_id` 已绑定 Case；本轮进一步将 `runs.case_id`、`artifacts.case_id` 和 `user_memories.case_id` 纳入同一边界。Run 创建时保存 Thread 的 Case 快照，Artifact 读写和记忆召回/抽取均不能跨 Case；全局记忆使用 `case_id IS NULL`，不会自动注入 Case 会话。多看护人授权、临床扩展表和完整的患者 Artifact 上传流程仍未实现。

## 公共知识与患者上下文

### 公共 MMA/PA 知识库

公共知识库承载经过筛选和审核的领域资料，例如：

- GeneReviews、专业学会指南和共识文件。
- 经过审核的综述、研究论文和患者教育资料。
- 医疗团队允许共享的通用流程和术语说明。

每个来源至少标记：

- 疾病范围：`isolated_mma`、`cobalamin_disorder`、`pa` 或其他明确分类。
- 亚型、基因或酶学范围。
- 年龄和人群范围。
- 地区或指南适用范围。
- 来源等级、发布日期、生效日期和审核状态。
- 原始 Artifact、页码或段落位置。

MMA/PA 资料不能只使用一个无标签的向量空间。检索必须先识别疾病范围和亚型，再按标签过滤和重排，避免把孤立型 MMA、钴胺素代谢障碍和 PA 的结论互相混用。

### 患者私有上下文

患者上下文包括：

- 诊断、亚型、基因和已确认的医学信息。
- 当前医疗团队计划及其生效时间。
- 化验、住院和代谢危象时间线。
- 当前药物、营养和其他治疗记录。
- 用户上传的医疗文档和家庭记录。
- 与该患者相关的历史咨询摘要。

患者上下文不进入公共知识库，也不能出现在其他患者的检索结果、缓存或模型上下文中。

## 对话上下文组装

每次运行必须先验证用户对 `PatientCase` 的访问权，再组装以下上下文：

```text
Authenticated User
  -> Patient Case authorization
  -> selected AgentProfile and AgentVersion
  -> filtered public MMA/PA KnowledgeBase
  -> private PatientCase facts, CarePlan, and Artifacts
  -> current Thread history
  -> answer with citations, uncertainty, and escalation boundary
```

默认只加载当前患者、当前 Thread 和与问题相关的资料。不能把同一账户下其他患者的历史对话作为背景自动带入。

对话历史不能自动等同于医学事实。模型从历史消息中提取的体重、用药、饮食或诊断等内容应先进入 `proposed` 状态，并保留来源消息；只有用户确认或授权人员审核后，才能成为 `confirmed` 患者事实。旧事实被替换时保留版本和生效区间，而不是静默覆盖。

## Agent Profile 设计

`AgentProfile` 是共享的领域 Agent 定义，不是每个患者一份模型。MMA/PA Agent 至少包含：

- `slug`：例如 `mma-pa`。
- `display_name` 和描述。
- 允许访问的知识库集合。
- 系统行为规则和回答模板。
- 允许使用的 Tool 集合。
- 风险级别和 HITL 策略。
- 默认模型配置和降级策略。
- 当前发布版本。

患者的私有资料通过上下文作用域注入，而不是通过复制一份新的 Agent 或训练一个新的模型。

每个 Run 保存：

- `agent_version_id`。
- `knowledge_snapshot_id`。
- `patient_context_snapshot_id`。
- 使用的 Thread 和 Patient Case。

这样可以回答“当时 Agent 使用了哪一版资料和哪一版患者计划”。

## 工具与权限

第一阶段只提供读取型工具：

- `knowledge_search`：搜索公共领域知识。
- `read_artifact`：读取当前用户有权访问的文档。
- `patient_context_read`：读取已授权的患者事实和计划。
- `patient_timeline_read`：读取患者化验或事件时间线。

未来涉及外部操作或改变患者计划的工具必须经过：

```text
身份授权
  -> PatientCase 授权
  -> Tool Policy
  -> HITL 或临床人员确认
  -> 幂等执行
  -> Audit Event
```

Agent 不得直接访问 PostgreSQL、对象存储或任意患者目录。所有读取通过带作用域的后端工具完成。

## 医疗安全边界

MMA/PA Agent 的系统规则应要求：

- 先区分疾病范围和患者亚型，资料不足时先提问。
- 不能根据年龄、体重或单次化验值自行推导个体化处方。
- 涉及饮食、药物、剂量、急性期或住院判断时，优先引用患者医疗团队计划，并明确要求联系代谢专科团队。
- 发现急性风险信号时，遵循患者已经配置的应急计划和当地医疗流程，不由模型临时发明处理方案。
- 对资料冲突、过期计划和缺失关键数据明确标记，不用模型置信度替代医学证据。
- 每个医学结论尽可能给出来源、版本和适用范围。

## 实施阶段

### Phase A：通用基础

- 建立 `AgentProfile`/`AgentVersion` 配置边界。
- 将 `agent_id` 纳入 Thread 和 Run。
- 建立 `PatientCase` 和最小授权关系。（已落地为 `cases` / `case_memberships`，当前只有 owner。）
- 将 Artifact 设计为可绑定用户、患者、Thread 和 Run 的私有资源。（当前已落地用户与 Case 作用域校验。）
- 为公共知识库和患者私有上下文定义统一检索接口。

### Phase B：MMA/PA 领域包

- 建立公共 MMA/PA 知识库。
- 完成文档解析、分块、标签、来源和版本管理。
- 实现带亚型过滤和引用的 `knowledge_search`。
- 固化 MMA/PA Agent 的回答、安全和升级规则。

### Phase C：患者上下文

- 患者档案、CarePlan、PatientFact 和化验时间线。
- 患者私有文档上传、读取和权限检查。
- 历史消息摘要和已确认事实提取。
- 多患者切换、Thread 按患者归档和跨患者访问拒绝测试。

### Phase D：评估与临床协作边界

- 建立按疾病范围、亚型、年龄和问题类型划分的黄金问题集。
- 测试引用准确性、亚型混淆、患者串数据、过期计划和缺少资料时的拒答。
- 测试提示词注入、恶意文档和跨用户 Artifact 访问。
- 让医疗团队审核高风险问题集和升级文案。

## 验收标准

- 两个用户可以使用同一个 `mma-pa` Agent，但互相无法读取 PatientCase、Thread、Run、Artifact 或历史消息。
- 一个用户可以管理多个患者，切换患者不会污染当前 Thread 上下文。
- 同一患者的多个 Thread 可以共享已确认患者事实，但不能把未确认聊天内容当成事实。
- 公共知识库更新后，新 Run 使用新版本，旧 Run 仍能复现原始知识快照。
- 每个医学回答可以追溯到公共来源、患者资料和 Agent 版本。
- 高风险个体化建议不会在没有授权或人工确认的情况下写回患者计划。

## 参考资料

- [Isolated Methylmalonic Acidemia - GeneReviews](https://www.ncbi.nlm.nih.gov/books/NBK1231/)
- [Disorders of Intracellular Cobalamin Metabolism - GeneReviews](https://www.ncbi.nlm.nih.gov/books/NBK1328/)
- [Propionic Acidemia - GeneReviews](https://www.ncbi.nlm.nih.gov/sites/books/NBK92946/)
- [Guidelines for the diagnosis and management of methylmalonic acidaemia and propionic acidaemia: First revision](https://onlinelibrary.wiley.com/doi/10.1002/jimd.12370)
