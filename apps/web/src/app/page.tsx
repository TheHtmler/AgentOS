import { ChatPanel } from "@/components/chat/chat-panel";
import { HealthStatus } from "@/components/system/health-status";

const runtimeItems = [
  { label: "执行入口", value: "FastAPI Agent API" },
  { label: "模型路由", value: "Ollama · gemma4:e4b" },
  { label: "会话存储", value: "PostgreSQL Thread" },
];

export default function Home() {
  return (
    <main className="min-h-screen bg-zinc-100">
      <header className="border-b border-zinc-200 bg-white">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5">
          <div>
            <p className="text-lg font-semibold text-zinc-950">AgentOS</p>
            <p className="text-xs text-zinc-500">Runtime control plane</p>
          </div>
          <p className="text-sm text-zinc-500">本地开发环境</p>
        </div>
      </header>

      <div className="mx-auto grid max-w-6xl gap-5 px-5 py-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <section className="min-w-0">
          <ChatPanel />
        </section>

        <aside className="space-y-5">
          <HealthStatus />

          <section className="border border-zinc-200 bg-white p-5">
            <p className="text-sm font-medium text-zinc-500">当前配置</p>
            <dl className="mt-4 divide-y divide-zinc-100">
              {runtimeItems.map((item) => (
                <div key={item.label} className="py-3 first:pt-0 last:pb-0">
                  <dt className="text-xs text-zinc-500">{item.label}</dt>
                  <dd className="mt-1 text-sm font-medium text-zinc-800">{item.value}</dd>
                </div>
              ))}
            </dl>
          </section>
        </aside>
      </div>
    </main>
  );
}
