# MMA/PA 知识积累清单与 MCP / Skills 调研

日期：2026-08-11
状态：调研笔记（非实现规格）  
关联：`docs/12-domain-agents-and-patient-context.md`

## 1. MMA/PA Agent 需要积累的基础知识（按层）

### 1.1 疾病本体与分型（必须先标签化）

检索与回答前必须能区分，避免混用结论：

| 标签建议             | 含义                                                            |
| -------------------- | --------------------------------------------------------------- |
| `isolated_mma`       | 孤立型甲基丙二酸血症（如 mut0 / mut−）                          |
| `cobalamin_disorder` | 细胞内钴胺素代谢缺陷（cblA/B/C/D/F 等；部分伴同型半胱氨酸异常） |
| `pa`                 | 丙酸血症（PCCA/PCCB）                                           |
| `gene:*`             | 如 `MMUT`、`PCCA`、`PCCB`、`MMAA`…                              |
| `age_band:*`         | 新生儿 / 婴儿 / 儿童 / 成人随访                                 |
| `region:*`           | 指南适用地区（EU/US/CN 等）                                     |

### 1.2 公共知识包（建议入库优先级）

**P0 — 核心临床（先做）**

1. **GeneReviews**
   - Isolated Methylmalonic Acidemia：https://www.ncbi.nlm.nih.gov/books/NBK1231/
   - Propionic Acidemia：https://www.ncbi.nlm.nih.gov/books/NBK92946/
2. **诊断与管理指南（修订版）**
   - Forny et al. / JIMD：「Guidelines for the diagnosis and management of methylmalonic acidaemia and propionic acidaemia: First revision」(2021)
3. **术语与鉴别**
   - C3 升高、甲基丙二酸、甲基枸橼酸、高甘氨酸、同型半胱氨酸（区分 cblC 等）
   - NBS 路径 vs 症状出现后诊断

**P1 — 临床管理专题**

4. 急性失代偿：诱因、急诊原则、何时就医（家庭教育向，非开处方）
5. 日常饮食：天然蛋白限制、医学配方/特殊医学食品概念、饥饿/发热时的应急碳水
6. 监测：血氨、血气、阴离子间隙、甲基丙二酸/丙酰肉碱趋势、肾功能、生长
7. 并发症：肾脏、神经系统、胰腺炎、心肌病、视神经等（按亚型标注）
8. B12 反应型 vs 无反应型的差异（影响治疗叙事）

**P2 — 家庭教育与支持**

9. NORD / MedlinePlus Genetics 患者向摘要
10. TEMPLE / 代谢中心患者教育材料（需版权与审核）
11. 本地医疗团队允许共享的通用流程与术语表

**刻意不做进公共库**

- 个体化处方剂量、未审核的论坛经验、把某一患者化验当公共知识

### 1.3 患者私有上下文（与公共库严格分开）

与 `docs/12` 一致：诊断亚型、当前 CarePlan、化验时间线、用药/营养、上传报告、已确认事实（`proposed` → `confirmed`）。

### 1.4 回答质量要求（知识层配套）

- 必须带来源、版本/日期、疾病亚型适用范围
- 不确定时标明；跨亚型结论禁止默认同化
- 高风险：急性症状、擅自改饮食/药物 → 升级就医 / HITL

---

## 2. 可评估的医学 MCP

下列是可评估接入 Agent Runtime 的外部医学 MCP 候选。AgentOS 当前已有可选的只读 PubMed MCP 适配，默认关闭，工具白名单为 `pubmed_search,pubmed_get_abstract`；是否启用仍需部署环境完成依赖审计和网络配置。

### 2.1 文献与指南（MMA/PA 最相关）

| MCP                                                                                   | 用途                                                | 对 MMA/PA 的价值                    | 备注                                        |
| ------------------------------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------- | ------------------------------------------- |
| [pubmed-search-mcp](https://github.com/u9401066/pubmed-search-mcp)                    | PubMed / Europe PMC / OpenAlex 等，工具多           | 查 JIMD 指南、综述、病例            | 偏研究向，工具面大，需裁剪 allowlist        |
| [JamesANZ/medical-mcp](https://github.com/JamesANZ/medical-mcp)                       | PubMed、指南搜索、FDA、WHO 统计、儿科期刊等         | 快速文献 + clinical guidelines 入口 | 宣称本地、多数无需 Key；需安全审计          |
| [Cicatriiz/healthcare-mcp-public](https://github.com/Cicatriiz/healthcare-mcp-public) | PubMed、FDA、ClinicalTrials、ICD-10、NCBI Bookshelf | Bookshelf 可触及 GeneReviews 类内容 | 工具较杂，按只读策略接入                    |
| [kieran-heidi/medical-mcp-server](https://github.com/kieran-heidi/medical-mcp-server) | NICE / WHO / CDC / RACGP 指南抓取                   | 通用指南；MMA 专项覆盖有限          | 适合「指南站」检索，不替代 GeneReviews 入库 |

### 2.2 生长曲线（育儿/代谢随访叠加）

| MCP / 库                                                   | 用途                                          | 备注                                                        |
| ---------------------------------------------------------- | --------------------------------------------- | ----------------------------------------------------------- |
| [groowooth](https://github.com/xiaot945/groowooth) MCP     | WHO + 中国卫健委 WS/T 423-2022 评估/查表/解读 | **高度契合**「对照标准曲线」；可作 parenting / MMA 随访工具 |
| [RCPCH Digital Growth Charts](https://growth.rcpch.ac.uk/) | 英系/WHO/CDC 等计算库与 API                   | orthopaedic 级专业；授权与部署需单独评估                    |

### 2.3 AgentOS 已有能力（包括可选 MCP）

- `web_search`（Tavily / DuckDuckGo）
- `fetch_url`（Firecrawl / local）
- Runtime Context Pack（当前时间/时区）
- 用户 × Agent × Case 记忆
- `knowledge_search`（当前为带 MMA/PA 标签、来源版本和审核状态的多来源策展切片混合检索）
- `growth_assess`（WHO 2006 / NHC WS/T 423-2022）
- 可选只读 PubMed MCP（默认关闭，严格工具白名单）

在可选 MCP 未启用或结果不足时，P0 prompt 已要求：**缺公开标准时先 `web_search`/`fetch_url`，禁止让用户代查**。

### 2.4 建议接入顺序（AgentOS）

1. **内建或封装 `growth_assess`**（可基于 groowooth core / RCPCH，不必先上完整 MCP 总线）
2. **只读 MCP：PubMed + NCBI Bookshelf/GeneReviews 抓取**（严格 Tool Policy + 引用）
3. **自建 `knowledge_search`**（把 GeneReviews/JIMD 指南切片入库，带亚型、来源版本和审核状态）——这才是 MMA Agent 的主路径；当前 P0 已导入首批来源和检索评测集
4. 通用 medical-mcp 全家桶最后考虑（面太大、幻觉与合规风险更高）

---

## 3. Skills（本机 Cursor / Claude skills）

扫描 `~/.claude/skills`：**没有**现成的 MMA/医学领域 Skill。现有多为工程流程（brainstorming、TDD、飞书、HTML 等）。

**建议自建的 AgentOS / 领域 Skills（文档级，非必须立刻写代码）：**

| Skill 草案                  | 作用                                           |
| --------------------------- | ---------------------------------------------- |
| `mma-pa-answer-policy`      | 分型标签、引用要求、升级就医边界、禁止混用亚型 |
| `growth-chart-workflow`     | 缺字段时问什么；先工具评估再解读；来源标注     |
| `patient-fact-confirmation` | proposed vs confirmed 事实流程（对接 docs/12） |

这些 Skill 更适合作为 **Agent overlay / 内部 runbook**，与公开 MCP 互补。

---

## 4. 结论

- **知识积累**：以 GeneReviews + JIMD 2021 指南为骨，再挂监测/饮食/急症/并发症专题；全程亚型标签。
- **MCP**：优先评估 **groowooth（生长）** + **PubMed/Bookshelf 类只读 MCP**；MMA 主力仍应是 **自建带标签的 knowledge_search**。
- **Skills**：本地无医学 Skill，需按上表自建政策类 Skill。
- **当前 AgentOS MCP**：已具备只读 PubMed 适配，但默认关闭；外部医学 MCP 仍需逐个审计后再接入。
