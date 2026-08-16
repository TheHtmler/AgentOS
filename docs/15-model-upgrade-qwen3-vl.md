# 模型升级：gemma4:e4b → qwen3-vl:8b-instruct 实施步骤

解决「对话僵硬、报告分析说废话」的模型层根因。旧模型 `agentos-gemma4:8k`(4.5B 有效参数、8k 上下文）被超长 system prompt + 工具 schema + 历史 + 图片挤爆，Ollama 静默截断后模型退回啰嗦的默认行为。

选型结论：

- **qwen3-vl:8b-instruct**(Q4_K_M,6.1GB,原生 256k 上下文，vision + tools + 中文强）。
- 用 **instruct** 而非默认 tag(`qwen3-vl:8b` 是 thinking 版，会先输出大段推理痕迹，不适合聊天直答）。
- 不选 qwen3:14b（纯文本，丢报告图直读能力；16GB 也偏紧）;32b 全系 16GB 放不下。
- 要求 Ollama ≥ 0.12.7(qwen3-vl 模板依赖）。

内存预算（16GB Mac mini，需同时跑 Postgres / agent-api / web / ops):

| 项                       | 估算                                           |
| ------------------------ | ---------------------------------------------- |
| 模型权重（Q4_K_M)        | 6.1GB                                          |
| KV cache @ num_ctx 16384 | ~2.5GB（约 150MB/千 token）                    |
| 视觉编码器 + 激活        | ~0.5–1GB                                       |
| 合计                     | ~9–10GB，接近 macOS Metal 默认上限（约 10.6GB) |

## 步骤 0：前置检查（Mac mini)

```bash
ollama --version          # 必须 >= 0.12.7，否则 brew upgrade ollama
sysctl -n hw.memsize      # 确认 17179869184 (16GB)
ollama list               # 确认 agentos-gemma4:8k 还在（回滚用）
```

## 步骤 1：拉取模型并创建自定义实例

仓库已新增 `infra/ollama/Modelfile.agentos-qwen3vl-16k`(num_ctx 16384)。在 Mac mini 仓库根目录：

```bash
git pull
ollama pull qwen3-vl:8b-instruct    # 6.1GB；认准 -instruct 后缀
ollama create agentos-qwen3vl:16k -f infra/ollama/Modelfile.agentos-qwen3vl-16k
ollama list                         # 应看到 agentos-qwen3vl:16k
```

## 步骤 2:Ollama 层冒烟（不经过应用，先验证模型本身）

```bash
# 纯文本：应直接回答，无思考过程外泄、无废话
curl -s http://127.0.0.1:11434/api/chat -d '{
  "model": "agentos-qwen3vl:16k", "stream": false,
  "messages": [{"role":"user","content":"用一句话说明你是谁"}]
}' | python3 -m json.tool | grep -E "content|eval_count"

# 视觉：随便一张报告/化验单照片，应能读出具体数值
IMG=$(base64 -i /path/to/report.jpg | tr -d '\n')
curl -s http://127.0.0.1:11434/api/chat -d "{
  \"model\": \"agentos-qwen3vl:16k\", \"stream\": false,
  \"messages\": [{\"role\":\"user\",\"content\":\"列出这张报告里的所有指标名和数值\",\"images\":[\"$IMG\"]}]
}" | python3 -m json.tool | grep content
```

通过标准：文本回答直给；视觉回答包含报告里的真实数值（抽查 3 个指标核对原图）。

## 步骤 3：修改服务配置

编辑 Mac mini 上 `services/agent-api/.env`:

```ini
OLLAMA_MODEL=agentos-qwen3vl:16k
MODEL_MAX_OUTPUT_TOKENS=4096     # 16k 窗口下输入余量 ~12k，输出预算不变
MODEL_MAX_CONCURRENT_RUNS=1      # 先保守跑顺；内存有余量再调回 2~3
MODEL_TEMPERATURE=0.3            # 不变
```

若 Ollama 加载失败或运行中被挤掉（内存不足），调高 Metal 显存上限后再试：

```bash
sudo sysctl iogpu.wired_limit_mb=12288
```

## 步骤 4：应用层冒烟

```bash
cd services/agent-api
uv run pytest -q                          # 全量测试（不应有模型相关回归）
uv run python scripts/smoke_agent.py      # 走真实 agent 链路一句中文应答
```

再启动服务，在 web UI 里实测两类问题：

1. 纯文本事实问答（如「现在身高体重多少」)→ 应 1–4 行直答。
2. 上传报告图 + 「帮我分析这张报告」→ 首行应是报告类型 + 关键异常，无开场免责长文。

## 步骤 5：重启生效

```bash
launchctl kickstart -k gui/$(id -u)/com.local.agentos-api
tail -f /tmp/agentos-api.err.log          # 确认无 Ollama 连接/加载错误
```

## 步骤 6：验收清单

- [ ] `ollama list` 显示 `agentos-qwen3vl:16k`,API 日志无模型加载报错
- [ ] 报告上传实测：首行直给发现，数值与原图一致，无大段免责声明
- [ ] 多轮对话（≥5 轮）不丢上下文、不复述问题
- [ ] token 用量确认不再顶格（input_tokens 可超过旧 8k 上限且回答正常）:

```sql
SELECT input_tokens, output_tokens, model_request_count, created_at
FROM runs ORDER BY created_at DESC LIMIT 10;
```

- [ ] 高峰时 `top` / 活动监视器确认内存未长期 swap（Swap used 持续增长则需降并发或 num_ctx)

## 回滚

模型实例互不影响，改回配置重启即可，无需重新拉模型：

```bash
# services/agent-api/.env 改回:
#   OLLAMA_MODEL=agentos-gemma4:8k
#   MODEL_MAX_CONCURRENT_RUNS=3
launchctl kickstart -k gui/$(id -u)/com.local.agentos-api
```

## 常见坑

- **拉错版本**:`qwen3-vl:8b` 默认是 thinking 版，会在回答前输出推理块；必须用 `qwen3-vl:8b-instruct`。
- **num_ctx 贪大**:32k 的 KV 约 4.6GB,16GB 机器会顶爆；先 16k，内存富余再考虑 24k。
- **Ollama 版本旧**:< 0.12.7 不认识 qwen3-vl 模板，升级后重试。
- **体感速度**:8B Q4 在 M 系芯片约 15–25 tok/s，长报告解读比 e4b 慢是正常的，质量优先。

## 事故记录：双 PDF 上传 400 溢出（2026-08-15)

**现象**：一次上传两份 PDF 报告，前端显示 canceled。**根因**：请求实测 17,883 tokens > 16,384 窗口，Ollama 返回 400；与内存无关（Ollama 进程正常）。教训与修复：

- 一页 144dpi A4 渲染图在 qwen3-vl 上约 **2.5k tokens**（动态分辨率），不是直觉的 1.2k——`VISION_RESERVE_PER_IMAGE` 已按此校准。
- 多附件轮次现在自动降级：预览 3000 字符/份（合计 6000)、每个 PDF 只渲染第 1 页、图片总数 ≤2；全文仍可由 read_artifact 分页读取（有 step 级护栏兜底）。
- 进一步加硬：`cap_vision_to_budget` 按校准后的页价估算整个请求头，**装不下就先丢图片**（OCR 文本 + read_artifact 才是数据通道，视觉只是交叉核对）——双 PDF 轮次在 16k 下通常纯文本运行，保证不再 400。
- 溢出时两条聊天链路都会给用户明确文案（「一次分析一份报告」)，不再裸显示 canceled。

**如果双 PDF 场景是刚需**：把 Modelfile 的 `num_ctx` 提到 24576(KV 约 3.7GB,16GB 机器需先 `sudo sysctl iogpu.wired_limit_mb=12288`，并确认 `OLLAMA_NUM_PARALLEL=1`),`.env` 的 `MODEL_CONTEXT_WINDOW` 同步改为 24576，重建模型实例后重启服务。改完用同样的双 PDF 用例复测，并观察 swap。

## 参数预算说明（换模型后哪些动、哪些不动)

- `MODEL_MAX_OUTPUT_TOKENS=4096` **保持不变**。它是单轮输出上限而非上下文大小：4096 token ≈ 2500–3000 中文字，对报告解读已充裕；调大只会挤占 16k 窗口里的输入预算并助长啰嗦。输出被截断时产品层已有提示（`output_limits.py`)。
- `READ_ARTIFACT_MAX_CHARS` 已随升级从 1500 调到 6000，上传预览从 1500 提到单附件 6000 / 单轮合计 12000：单页报告通常一次注入即可，无需 read_artifact 翻页；长文翻页轮次也减半。每个模型 step 前还有 `context_budget.make_step_history_processor` 做压力检查（对应 deepseek-harness 的 pre-step 压缩触发点），翻页堆积不再能顶爆窗口。
- `MODEL_TEMPERATURE=0.3` 保持：事实型回答要稳定。若观察到重复循环（Qwen instruct 低温度的已知倾向），再上调到 0.6。
- `HISTORY_MAX_RUNS=4` 保持：若验收时 `runs.input_tokens` 频繁逼近 12k，先把它降到 3。
- `num_ctx` 是唯一真正的扩容杠杆：内存验证有余量后可重建为 24576(`ollama create` 改 Modelfile 即可）,KV 约增至 3.7GB，需先 `sudo sysctl iogpu.wired_limit_mb=12288`。
- Mac mini 上建议给 Ollama 服务设 `OLLAMA_NUM_PARALLEL=1`，与应用层 `MODEL_MAX_CONCURRENT_RUNS=1` 对齐，防止并发请求翻倍占 KV。

## 上下文工程改造（2026-08-15，参考 deepseek-harness)

对照 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) 的 `token-meter` / `compaction` / system-prompt 组装设计落地，详见 `docs/implementation-progress.md`:

- **指令/数据分离**:`build_instructions()` 只产出稳定指令（基础契约 + 能力段 + Agent overlay);时间、memory、Case、附件预览由 `build_context_snapshot()` 组成 user 角色快照，随当轮注入、不落库（新 run 放历史末尾，HITL resume 放历史开头以免拆散工具配对）。
- **输入预算护栏**:`context_budget.apply_context_budget()` 在每轮 run 前估算历史 token，超预算先首尾裁剪旧工具结果，再按 user 消息边界整段丢最老 run；裁剪动作全部记服务端日志，不再依赖 Ollama 静默截断。`MODEL_CONTEXT_WINDOW`(=Modelfile 的 num_ctx）是预算基准。
- **溢出兜底**:SSE 链路捕获 provider 侧 context overflow 后只保留最新 run 重试一次；run 完成后 `input_tokens` 逼近输入预算时打 warning。
- **报告解读指令重写**：指标面板表（数值逐字取自附件，读不清标「待核对」)+ 每条结论标注「知识库依据 / 模型推断」，小模型可执行、结果可审计。
- 未做：LLM 摘要压缩（小模型摘要质量差，16k 场景剪枝已够）、插件框架（不适配 pydantic-ai 栈）。

## 可选的配套小修（独立于模型切换，建议后续单独一轮做）

1. ~~`agent.py` 报告解读指令与 Case 绑定解耦~~（已随本次升级完成）。
2. ~~`uploads/context.py` OCR 预览 1500 → 4000~~（已随本次升级完成）。
3. ~~输入侧 token 预算护栏~~（已由 `context_budget.py` 落地，见上节）。
