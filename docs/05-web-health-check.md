# Web 健康检查边界

日期：2026-08-01

状态：已完成

## 目标

替换 Next.js 默认页面，显示 Agent API 的实时可用状态，并建立前端到后端的第一个可部署边界。

## 路由设计

```text
浏览器
  -> GET /api/health                 Next.js 同域 Route Handler
  -> GET {AGENT_API_BASE_URL}/health FastAPI Agent API
```

浏览器不直接访问 FastAPI 的端口或地址。`AGENT_API_BASE_URL` 只由 Next.js 服务端读取，因此本地开发使用 `http://127.0.0.1:8000`，部署时可以替换为 FRP 后的私有入口，不需要修改浏览器代码。

当前响应契约：

| 场景                     | HTTP 状态 | 响应体                        |
| ------------------------ | --------- | ----------------------------- |
| Agent API 正常           | 200       | `{ "status": "ok" }`          |
| 上游返回格式非法或非成功 | 502       | `{ "status": "unavailable" }` |
| 上游无法连接或超时       | 503       | `{ "status": "unavailable" }` |

## 安全边界

- 不使用 `NEXT_PUBLIC_AGENT_API_BASE_URL`；该变量会暴露给浏览器。
- 不开放 FastAPI CORS。前端先经 Next.js 同域代理访问后端。
- 路由只代理固定的健康检查地址，不接受浏览器提交的任意上游 URL。
- 上游请求禁用缓存并设置 3 秒超时。

## 页面状态

页面只展示三种状态：`checking`、`ready`、`unavailable`。用户可以手动刷新状态；页面加载和每 30 秒自动执行一次检查。

本轮不实现聊天输入、身份认证、数据库、模型调用、Agent 列表或 Sandbox。

## 实现记录

已完成：

- 添加 `apps/web/.env.example`，为本地 Agent API 指定服务端环境变量。
- 添加 `GET /api/health` Route Handler，使用固定上游地址、禁用缓存、3 秒超时和响应格式校验。
- 验证 `curl --noproxy '*' http://127.0.0.1:3000/api/health` 返回 `{ "status": "ok" }`。
- 用 `HealthStatus` 客户端组件替换默认页面，展示检查中、运行正常和不可用状态，支持手动刷新和 30 秒轮询。
- 验证前端 ESLint 和生产构建通过，路由表包含动态的 `/api/health`。

代码在网络访问、上游响应校验和轮询位置保留了定向注释，说明其部署和状态边界。
