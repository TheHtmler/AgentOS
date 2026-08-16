# Mac mini + FRP：Ops 控制台部署

与产品站 `https://agentos.lemonbabycare.cn` 同一拓扑：Next 跑在 Mac mini，经 `frpc` 打到云服务器，宝塔 Nginx 终止 HTTPS。

| 站点     | 域名                           | Mac mini 端口 | launchd                 |
| -------- | ------------------------------ | ------------- | ----------------------- |
| 产品 Web | `agentos.lemonbabycare.cn`     | `3000`        | `com.local.agentos-web` |
| Ops      | `ops-agentos.lemonbabycare.cn` | `3001`        | `com.local.agentos-ops` |

```text
浏览器
  -> 宝塔 Nginx (HTTPS, ops-agentos.lemonbabycare.cn)
  -> frps 本机端口 (例: 13001)
  -> FRP
  -> Mac mini frpc -> 127.0.0.1:3001 (apps/ops)
  -> BFF -> 127.0.0.1:8100 (agent-api;8000 端口归本机 OCR 服务)
```

仓库模板：

- `infra/launchd/com.local.agentos-ops.plist.example`
- `infra/frpc/frpc.agentos.example.toml`
- `infra/nginx/ops-agentos.lemonbabycare.cn.conf.example`

## 1. 宝塔（云服务器）

1. DNS：`ops-agentos` → 云服务器公网 IP（与 `agentos` 相同）。
2. 宝塔 → 网站 → 添加站点：`ops-agentos.lemonbabycare.cn`（纯静态/PHP 均可，随后改反代）。
3. 申请 Let’s Encrypt SSL，强制 HTTPS。
4. 配置反代到 **frps 在本机监听的端口**（须与 frpc `remotePort` 一致，示例 `13001`）。可参考 `infra/nginx/ops-agentos.lemonbabycare.cn.conf.example`。
5. 若 frps 有 `allowPorts`，把该 `remotePort` 加进白名单并重启 frps。

## 2. frpc（Mac mini）

在现有 `frpc.toml` 增加 ops 代理（示例见 `infra/frpc/frpc.agentos.example.toml`）：

```toml
[[proxies]]
name = "agentos-ops"
type = "tcp"
localIP = "127.0.0.1"
localPort = 3001
remotePort = 13001
```

重启 frpc 后，在云服务器上确认 `13001`（或你选的端口）在听。

## 3. 一键部署（推荐）

Mac mini 仓库根目录：

```bash
# 首次安装 launchd（api / web / ops；会替换占位符并启动）
./scripts/install-launchd.sh

# 日常：pull + uv sync + migrate + build web/ops + kickstart
./scripts/macmini-deploy.sh

# 只更某一侧
./scripts/macmini-deploy.sh api
./scripts/macmini-deploy.sh web ops
./scripts/macmini-deploy.sh --no-pull ops
```

默认 launchd 名：`com.local.agentos-api` / `com.local.agentos-web` / `com.local.agentos-ops`。  
若你本机 API 的 Label 不同，可：`AGENTOS_API_LABEL=你的label ./scripts/macmini-deploy.sh api`。

## 4. Agent API 环境（Mac mini）

`services/agent-api/.env` 最简单这样写：

```env
OPS_ROOT_USERNAME=admin
OPS_ROOT_PASSWORD=你的简单密码
OPS_SESSION_TTL_HOURS=12
```

（可选）也可用 `OPS_ROOT_PASSWORD_HASH`（Argon2id）；两者都设时以 hash 为准。  
`apps/web/.env.local` / `apps/ops/.env.local` 建议：`AGENT_API_BASE_URL=http://127.0.0.1:8100`(agent-api 已让出 8000 给本机 OCR 服务;OCR_BASE_URL 指向 `http://127.0.0.1:8000`)。

本地冒烟：

```bash
curl -sS --noproxy '*' http://127.0.0.1:8100/health
curl -sS --noproxy '*' -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/
curl -sS --noproxy '*' -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3001/login
```

公网：`https://agentos.lemonbabycare.cn` / `https://ops-agentos.lemonbabycare.cn/login`。

## 5. 注意

- Cookie：`ops_session` 只挂在 ops 子域；与 `agentos.lemonbabycare.cn` 的用户 Cookie 隔离。
- `NODE_ENV=production` 时 BFF 会给 Cookie 加 `Secure`（需 HTTPS）。
- 不要把 ops 反代到产品站同端口；必须独立 `3001` + 独立域名。
- 生产请改掉开发用密码；`.env` / `.env.local` 不入库。
