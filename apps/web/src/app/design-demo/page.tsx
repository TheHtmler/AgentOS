"use client";

import { useEffect, useState, type FormEvent } from "react";

type Message = {
  id: number;
  role: "assistant" | "user";
  time: string;
  content: string;
};

const initialMessages: Message[] = [
  {
    id: 1,
    role: "user",
    time: "10:42",
    content: "根据最近一次生长记录，帮我看一下需要重点关注什么。",
  },
  {
    id: 2,
    role: "assistant",
    time: "10:43",
    content:
      "这次记录最值得先看两件事：身高增长速度偏慢，需要和过去 3-6 个月的同口径数据对照；体重趋势相对稳定，暂时不建议只根据一次测量下结论。",
  },
];

const conversations = [
  { title: "生长评估 · 2026年8月", meta: "刚刚", active: true, dot: "live" },
  { title: "MMA 饮食记录整理", meta: "昨天", active: false, dot: "" },
  { title: "门诊报告复盘", meta: "8月13日", active: false, dot: "" },
  { title: "康复训练计划", meta: "8月09日", active: false, dot: "" },
];

function IconButton({
  label,
  children,
  onClick,
  active = false,
}: {
  label: string;
  children: React.ReactNode;
  onClick?: () => void;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      className={active ? "demo-icon-button is-active" : "demo-icon-button"}
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function SourcePanel({ onClose }: { onClose: () => void }) {
  return (
    <aside className="demo-context-panel">
      <div className="demo-panel-heading">
        <div>
          <span className="demo-kicker">资料范围</span>
          <h2>本轮资料</h2>
        </div>
        <IconButton label="关闭资料面板" onClick={onClose}>
          ×
        </IconButton>
      </div>

      <section className="demo-context-section">
        <div className="demo-section-label">
          <span>当前资料</span>
          <span className="demo-status-dot">已绑定</span>
        </div>
        <div className="demo-case-card">
          <div className="demo-case-mark">M</div>
          <div>
            <strong>生长与代谢管理</strong>
            <span>最近更新 · 4 分钟前</span>
          </div>
          <span className="demo-chevron">›</span>
        </div>
      </section>

      <section className="demo-context-section">
        <div className="demo-section-label">
          <span>已引用资料</span>
          <span>3</span>
        </div>
        <div className="demo-source-list">
          <div className="demo-source-item">
            <span className="demo-source-type pdf">PDF</span>
            <div>
              <strong>2026-08-16 生长记录</strong>
              <span>第 1 页 · 文档识别 + 图片分析</span>
            </div>
            <span className="demo-source-check">✓</span>
          </div>
          <div className="demo-source-item">
            <span className="demo-source-type kb">KB</span>
            <div>
              <strong>生长监测与趋势判断</strong>
              <span>知识库 · 临床审核</span>
            </div>
            <span className="demo-source-check">✓</span>
          </div>
          <div className="demo-source-item">
            <span className="demo-source-type note">NOTE</span>
            <div>
              <strong>家庭记录 · 7月</strong>
              <span>资料事实 · 用户确认</span>
            </div>
            <span className="demo-source-check">✓</span>
          </div>
        </div>
      </section>

      <section className="demo-context-section demo-runtime-section">
        <div className="demo-section-label">
          <span>助手状态</span>
          <span className="demo-live-label">
            <i /> 已就绪
          </span>
        </div>
        <dl className="demo-runtime-list">
          <div>
            <dt>助手</dt>
            <dd>通用助手</dd>
          </div>
          <div>
            <dt>模型</dt>
            <dd>Qwen3-VL · Local</dd>
          </div>
          <div>
            <dt>上下文</dt>
            <dd>8.4k / 16k</dd>
          </div>
        </dl>
      </section>

      <button type="button" className="demo-outline-action">
        查看处理详情 <span>↗</span>
      </button>
    </aside>
  );
}

function MessageBody({ message }: { message: Message }) {
  if (message.role === "user") {
    return <p>{message.content}</p>;
  }

  return (
    <>
      <p>{message.content}</p>
      <div className="demo-metric-grid">
        <div className="demo-metric">
          <span>身高趋势</span>
          <strong className="warning">需对照</strong>
          <small>单次记录不足以判断</small>
        </div>
        <div className="demo-metric">
          <span>体重趋势</span>
          <strong className="stable">相对稳定</strong>
          <small>建议继续按同一条件记录</small>
        </div>
      </div>
      <p className="demo-follow-up">
        如果你愿意，我可以继续把过去 3 次记录按时间顺序整理成一张趋势表。
      </p>
    </>
  );
}

export default function DesignDemoPage() {
  const [messages, setMessages] = useState(initialMessages);
  const [draft, setDraft] = useState("");
  const [showContext, setShowContext] = useState(true);
  const [showMobileNav, setShowMobileNav] = useState(false);
  const [darkMode, setDarkMode] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 1199px)");
    const syncContextVisibility = () => setShowContext(!media.matches);

    syncContextVisibility();
    media.addEventListener("change", syncContextVisibility);
    return () => media.removeEventListener("change", syncContextVisibility);
  }, []);

  function submitMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = draft.trim();
    if (!value) return;

    setMessages((current) => [
      ...current,
      { id: Date.now(), role: "user", time: "现在", content: value },
    ]);
    setDraft("");
  }

  return (
    <main className={darkMode ? "agentos-design-demo is-dark" : "agentos-design-demo"}>
      <div className="demo-mobile-topbar">
        <IconButton label="打开会话导航" onClick={() => setShowMobileNav(true)}>
          ☰
        </IconButton>
        <div className="demo-mobile-title">
          <span>资料 / 01</span>
          <strong>生长评估</strong>
        </div>
        <IconButton
          label="打开资料面板"
          onClick={() => setShowContext((current) => !current)}
          active={showContext}
        >
          ◫
        </IconButton>
      </div>

      <div className="demo-shell">
        <aside className={showMobileNav ? "demo-sidebar is-mobile-open" : "demo-sidebar"}>
          <div className="demo-brand-row">
            <div className="demo-brand-mark">A</div>
            <div>
              <strong>AgentOS</strong>
              <span>personal workspace</span>
            </div>
            <IconButton label="关闭会话导航" onClick={() => setShowMobileNav(false)}>
              ×
            </IconButton>
          </div>

          <button type="button" className="demo-new-button">
            <span>＋</span> 新建会话 <kbd>⌘ K</kbd>
          </button>

          <div className="demo-nav-section">
            <div className="demo-nav-label">
              <span>工作区</span>
              <IconButton label="工作区更多操作">•••</IconButton>
            </div>
            <button type="button" className="demo-nav-item is-selected">
              <span className="demo-nav-symbol">⌂</span>
              <span>全部会话</span>
              <span className="demo-nav-count">12</span>
            </button>
            <button type="button" className="demo-nav-item">
              <span className="demo-nav-symbol">◇</span>
              <span>资料档案</span>
              <span className="demo-nav-count">4</span>
            </button>
          </div>

          <div className="demo-conversation-section">
            <div className="demo-nav-label">
              <span>最近会话</span>
              <button type="button" className="demo-text-action">
                搜索
              </button>
            </div>
            <div className="demo-conversation-list">
              {conversations.map((conversation) => (
                <button
                  type="button"
                  className={
                    conversation.active ? "demo-conversation is-active" : "demo-conversation"
                  }
                  key={conversation.title}
                >
                  <span className={`demo-conversation-dot ${conversation.dot}`} />
                  <span className="demo-conversation-copy">
                    <strong>{conversation.title}</strong>
                    <small>{conversation.meta}</small>
                  </span>
                  {conversation.active ? <span className="demo-conversation-more">•••</span> : null}
                </button>
              ))}
            </div>
          </div>

          <div className="demo-sidebar-footer">
            <button type="button" className="demo-account-button">
              <span className="demo-avatar">R</span>
              <span>
                <strong>Randy</strong>
                <small>本地账户</small>
              </span>
              <span className="demo-chevron">⌄</span>
            </button>
            <div className="demo-footer-actions">
              <IconButton label="切换主题" onClick={() => setDarkMode((current) => !current)}>
                {darkMode ? "☼" : "◐"}
              </IconButton>
              <IconButton label="设置">⚙</IconButton>
            </div>
          </div>
        </aside>

        {showMobileNav ? (
          <button
            type="button"
            className="demo-mobile-backdrop"
            aria-label="关闭会话导航"
            onClick={() => setShowMobileNav(false)}
          />
        ) : null}

        <section className="demo-chat-column">
          <header className="demo-chat-header">
            <div className="demo-thread-heading">
              <span className="demo-kicker">资料 / 生长与代谢管理</span>
              <div className="demo-title-line">
                <h1>生长评估 · 2026年8月</h1>
                <span className="demo-saved-pill">
                  <i /> 已保存
                </span>
              </div>
            </div>
            <div className="demo-header-actions">
              <button
                type="button"
                className="demo-header-button"
                onClick={() => setShowContext((current) => !current)}
              >
                <span>◫</span> 资料
              </button>
              <IconButton label="会话更多操作">•••</IconButton>
            </div>
          </header>

          <div className="demo-message-scroll">
            <div className="demo-message-inner">
              <div className="demo-date-divider">
                <span>今天 · 2026年8月16日</span>
              </div>
              {messages.map((message) => (
                <article className={`demo-message-row ${message.role}`} key={message.id}>
                  {message.role === "assistant" ? (
                    <div className="demo-assistant-mark">A</div>
                  ) : null}
                  <div className={`demo-message ${message.role}`}>
                    <div className="demo-message-meta">
                      <strong>{message.role === "assistant" ? "助手" : "你"}</strong>
                      <span>{message.time}</span>
                    </div>
                    <MessageBody message={message} />
                    {message.role === "assistant" ? (
                      <div className="demo-message-actions">
                        <button type="button">复制</button>
                        <button type="button">有帮助</button>
                        <button type="button">重新生成</button>
                      </div>
                    ) : null}
                  </div>
                </article>
              ))}

              <div className="demo-process-row">
                <span className="demo-process-icon">✦</span>
                <div>
                  <strong>已完成 2 个步骤</strong>
                  <span>读取记录 · 检索知识库</span>
                </div>
                <button type="button">展开</button>
              </div>
            </div>
          </div>

          <div className="demo-composer-wrap">
            <div className="demo-suggestion-row">
              <button type="button" onClick={() => setDraft("把过去 3 次记录整理成趋势表")}>
                整理趋势表
              </button>
              <button type="button" onClick={() => setDraft("这次记录和上次相比有什么变化？")}>
                对比上次记录
              </button>
              <button type="button" onClick={() => setDraft("列出需要和医生确认的问题")}>
                生成就诊问题
              </button>
            </div>
            <form className="demo-composer" onSubmit={submitMessage}>
              <div className="demo-composer-topline">
                <button type="button" className="demo-mode-button">
                  <span>✦</span> 通用助手 <span className="demo-chevron">⌄</span>
                </button>
                <span className="demo-composer-hint">Enter 发送 · Shift + Enter 换行</span>
              </div>
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="继续描述你的问题..."
                rows={1}
                aria-label="消息输入框"
              />
              <div className="demo-composer-bottomline">
                <div className="demo-composer-tools">
                  <IconButton label="添加附件">＋</IconButton>
                  <span className="demo-attachment-chip">
                    <span>PDF</span> 生长记录.pdf{" "}
                    <button type="button" aria-label="移除附件">
                      ×
                    </button>
                  </span>
                </div>
                <button
                  type="submit"
                  className="demo-send-button"
                  aria-label="发送消息"
                  title="发送消息"
                >
                  ↑
                </button>
              </div>
            </form>
            <p className="demo-disclaimer">
              助手可能会出错。重要信息请结合原始报告和专业意见确认。
            </p>
          </div>
        </section>

        {showContext ? <SourcePanel onClose={() => setShowContext(false)} /> : null}
      </div>
    </main>
  );
}
