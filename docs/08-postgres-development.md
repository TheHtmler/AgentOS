# PostgreSQL 开发服务

日期：2026-08-01

状态：首个 schema 已完成

## 目标

为 AgentOS 提供本地开发使用的 PostgreSQL 17 服务，作为后续 Thread、Message、Run 与 RunEvent 的唯一事实来源。

本文件约定开发数据库的启动方式和连接边界。SQLAlchemy 异步引擎、asyncpg、greenlet 与 Alembic 异步环境已完成配置；首个 ORM schema 已迁移至 PostgreSQL。

## 目录与配置

```text
infra/postgres/
  compose.yaml       PostgreSQL 容器定义
  .env.example       可提交的开发变量样例
  .env               本机实际密码，不提交
```

容器使用具名卷 `agentos_postgres_data` 保存数据，不依赖工作目录下的宿主机数据路径。不要在有需要保留的数据时执行 `docker compose down -v`，该命令会删除具名卷。

`services/agent-api/.env` 后续通过 `DATABASE_URL` 访问数据库。URL 仅供后端进程使用，绝不放入 `NEXT_PUBLIC_*` 变量或浏览器代码。

## 开发连接

默认开发连接格式：

```text
postgresql+asyncpg://agentos:<password>@127.0.0.1:5432/agentos
```

端口仅用于本机开发。部署时 PostgreSQL 不向浏览器或公网暴露；Agent API 使用私有网络地址或本机套接字连接。

## 启动与验收

```bash
cp infra/postgres/.env.example infra/postgres/.env
docker compose --env-file infra/postgres/.env -f infra/postgres/compose.yaml up -d
docker compose --env-file infra/postgres/.env -f infra/postgres/compose.yaml ps
docker compose --env-file infra/postgres/.env -f infra/postgres/compose.yaml exec postgres \
  psql -U agentos -d agentos -c 'SELECT current_database(), current_user;'
```

在 Docker daemon 未启动时，先启动 Docker Desktop 或等价的容器运行时。启动成功的完成标准是容器健康检查为 `healthy`，并且 SQL 查询返回数据库名 `agentos` 与用户 `agentos`。

当前开发服务已由项目所有者完成启动和 SQL 连通性验证。

## 首个 Schema

初始迁移版本为 `e40334bd9bc7`，创建四张表：

| 表           | 职责                      | 关键约束                                                                    |
| ------------ | ------------------------- | --------------------------------------------------------------------------- |
| `threads`    | 持久化对话容器            | UUID 主键与创建、更新时间。                                                 |
| `messages`   | Thread 内有序消息         | `(thread_id, seq)` 唯一；角色限定为 `user`、`assistant`、`system`、`tool`。 |
| `runs`       | 单次 Agent 执行           | 关联 Thread；状态限定为 queued、running、completed、failed、cancelled。     |
| `run_events` | Run 的 append-only 事件流 | `(run_id, seq)` 唯一；JSONB payload 用于后续事件重放。                      |

所有子表外键使用 `ON DELETE CASCADE`。当前 schema 不含认证或租户字段；在接入身份模型时，通过新增迁移添加所有权边界，不修改既有迁移。

## 下一步

数据库服务与首个聊天持久化链路已完成。下一步：

1. 提供 Thread 的历史读取接口，并让页面在刷新后恢复消息。
2. 将数据库历史转换为受 token 窗口约束的模型消息上下文。
