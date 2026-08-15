# 模型升级：gemma4:e4b → qwen3-vl:8b-instruct 实施步骤

解决「对话僵硬、报告分析说废话」的模型层根因。旧模型 `agentos-gemma4:8k`(4.5B 有效参数、8k 上下文）被超长 system prompt + 工具 schema + 历史 + 图片挤爆，Ollama 静默截断后模型退回啰嗦的默认行为。

选型结论：

- **qwen3-vl:8b-instruct**(Q4_K_M,6.1GB,原生 256k 上下文，vision + tools + 中文强）。
- 用 **instruct** 而非默认 tag(`qwen3-vl:8b` 是 thinking 版，会先输出大段推理痕迹，不适合聊天直答）。
- 不选 qwen3:14b（纯文本，丢报告图直读能力；16GB 也偏紧）;32b 全系 16GB 放不下。
- 要求 Ollama ≥ 0.12.7(qwen3-vl 模板依赖）。

内存预算（16GB Mac mini，需同时跑 Postgres / agent-api / web / ops):

| 项 | 估算 |
| --- | --- |
| 模型权重（Q4_K_M) | 6.1GB |
| KV cache @ num_ctx 16384 | ~2.5GB(约 150KB/千 token) |
| 视觉编码器 + 激活 | ~0.5–1GB |
| 合计 | ~9–10GB，接近 macOS Metal 默认上限（约 10.6GB) |

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

## 可选的配套小修（独立于模型切换，建议后续单独一轮做）

1. `services/agent-api/src/agent_api/agent.py:250` — `REPORT_ANALYSIS_INSTRUCTIONS` 目前只在 `case_context_read` 挂载时注入，未绑 Case 的 Agent 解读报告时缺少输出约束；应改为随 `upload_block` 注入。
2. `services/agent-api/src/agent_api/uploads/context.py:33` — OCR 预览 `preview_chars=1500` 对 16k 上下文过于保守，可提到 4000，减少小模型分段读 `read_artifact` 的失败率。
3. 输入侧仍无 token 预算护栏（只有输出截断检测 `output_limits.py`)，后续可按「历史 > memory > 预览」优先级裁剪。
