import { ChatWorkspace } from "@/components/chat/chat-workspace";

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

      <ChatWorkspace />
    </main>
  );
}
