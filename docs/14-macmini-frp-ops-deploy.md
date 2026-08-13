# Mac mini + FRP：Ops 控制台部署

与产品站 `https://agentos.lemonbabycare.cn` 同一拓扑：Next 跑在 Mac mini，经 `frpc` 打到云服务器，宝塔 Nginx 终止 HTTPS。

| 站点 | 域名 | Mac mini 端口 | launchd |
| --- | --- | --- | --- |
| 产品 Web | `agentos.lemonbabycare.cn` | `3000` | `com.local.agentos-web` |
| Ops | `ops-agentos.lemonbabycare.cn` | `3001` | `com.local.agentos-ops` |

```text
浏览器
  -> 宝塔 Nginx (HTTPS, ops-agentos.lemonbabycare.cn)
  -> frps 本机端口 (例: 13001)
  -> FRP
  -> Mac mini frpc -> 127.0.0.1:3001 (apps/ops)
  -> BFF -> 127.0.0.1:8000 (agent-api)
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

## 3. Agent API（Mac mini）

`services/agent-api/.env` 需有：

```env
OPS_ROOT_USERNAME=admin
OPS_ROOT_PASSWORD_HASH=...   # pwdlib Argon2id，勿用明文
OPS_SESSION_TTL_HOURS=12
```

生成哈希：

```bash
uv run --directory services/agent-api python -c \
  "from pwdlib import PasswordHash; print(PasswordHash.recommended().hash('你的强密码'))"
```

然后：

```bash
cd /path/to/AgentOS && git pull
uv run --directory services/agent-api alembic upgrade head
# 按你平时方式重启 agent-api（确保 OPS_* 已加载）
```

## 4. Ops Next + launchd（Mac mini）

首次：

```bash
cd /path/to/AgentOS
git pull
pnpm --filter ops install
# apps/ops/.env.local
# AGENT_API_BASE_URL=http://127.0.0.1:8000
pnpm --filter ops build

# 从 example 安装 plist（替换 __AGENTOS_ROOT__ / __NODE_BIN__）
cp infra/launchd/com.local.agentos-ops.plist.example \
  ~/Library/LaunchAgents/com.local.agentos-ops.plist
# 编辑占位符后：
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.local.agentos-ops.plist
# 或旧系统：launchctl load ~/Library/LaunchAgents/com.local.agentos-ops.plist
```

日常更新（对齐 web 习惯）：

```bash
cd /path/to/AgentOS
git pull
pnpm --filter ops install
pnpm --filter ops build
launchctl kickstart -k gui/$(id -u)/com.local.agentos-ops
```

本地冒烟：

```bash
curl -sS --noproxy '*' http://127.0.0.1:3001/login | head
```

公网：打开 `https://ops-agentos.lemonbabycare.cn/login`，用 ops root 登录。

## 5. 注意

- Cookie：`ops_session` 只挂在 ops 子域；与 `agentos.lemonbabycare.cn` 的用户 Cookie 隔离。
- `NODE_ENV=production` 时 BFF 会给 Cookie 加 `Secure`（需 HTTPS）。
- 不要把 ops 反代到产品站同端口；必须独立 `3001` + 独立域名。
- 生产请改掉开发用密码；`.env` / `.env.local` 不入库。
