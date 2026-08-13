export function ComingSoon({ title }: { title: string }) {
  return (
    <div className="stack">
      <div>
        <h1 className="page-title">{title}</h1>
        <p className="muted">后续竖切 · 本页暂未开放配置</p>
      </div>
      <section className="panel">
        <p style={{ margin: 0 }}>
          当前第一期聚焦知识库审核与 Agent 基础管理。MCP / Skills / Sessions 将在后续迭代接入。
        </p>
      </section>
    </div>
  );
}
