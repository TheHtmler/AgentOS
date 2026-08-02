# 聊天持久化边界

日期：2026-08-01

状态：已完成

## 目标

将当前临时的文本流转化为可审计的事实记录，但不在 SSE 整个生命周期内占用一个数据库连接或事务。

## 事实与事务

```text
短事务 A
  创建或锁定 Thread
  写入 user Message
  创建 running Run
  写入 run_started Event
  提交

流式执行
  每个 text_delta：短事务写入一个 RunEvent，然后发送 SSE

短事务 B
  写入 assistant Message
  将 Run 标记为 completed
  写入 run_completed Event
  提交
```

模型错误和客户端取消使用各自的短事务把 Run 标记为 `failed` 或 `cancelled`，并写入终止事件。取消异常仍须向上传播，不能被数据库清理逻辑吞掉。

## 顺序与并发

- 创建新 Message 前锁定指定 Thread 行，确保同一 Thread 的 `messages.seq` 不重复。
- 同一 Run 的事件由单流执行器顺序写入，`(run_id, seq)` 唯一约束作为最终保护。
- 每次写入只持有连接到提交完成；模型生成期间不持有数据库事务。

## 已完成

- SSE 路由在打开响应前创建或锁定 Thread，写入用户消息、running Run 与 `run_started` 事件。
- 每个文本增量在发送给浏览器前，以独立短事务写入 `text_delta` 事件。
- 正常完成会原子写入助手消息、`completed` 状态与 `run_completed` 事件。
- 模型错误和浏览器取消会分别写入 `failed` 或 `cancelled` 终态，避免 Run 永远停留在 `running`。
- 响应通过 `X-AgentOS-Thread-ID` 返回当前 Thread ID；路由测试使用真实 PostgreSQL，并在结束时删除测试数据。

## 认证后的边界

- 每个新 Thread 在创建短事务中写入当前 `user_id`；既有 Thread 在锁定前先按 `user_id` 过滤。
- Thread 历史、Run 详情和 AG-UI 也按相同 owner 过滤，不属于当前用户时返回 `404`。
- 流式生成期间仍不持有数据库事务；所有权只影响初始 Thread 查询与创建，不改变后续 delta 和终态的短事务结构。

完整认证行为见 [11-authentication.md](11-authentication.md)。
