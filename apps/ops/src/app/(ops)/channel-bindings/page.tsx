"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  Bell,
  Bot,
  Copy,
  KeyRound,
  Link2,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  UserRound,
} from "lucide-react";

import { PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/skeleton";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/toast";
import { formatTime } from "@/lib/format";
import { OpsFetchError, opsJson } from "@/lib/ops-fetch";

const CHANNEL = "openclaw-weixin";
const STATUS_FILTERS = ["active", "disabled"] as const;

type BindingStatus = (typeof STATUS_FILTERS)[number];

type OpsUser = {
  id: string;
  email: string;
  handle: string | null;
  status: string;
  binding_count: number;
  created_at: string;
  last_login_at: string | null;
};

type ChannelBinding = {
  id: string;
  user_id: string;
  user_email: string;
  user_handle: string | null;
  user_status: string;
  channel: string;
  account_id: string;
  peer_id: string;
  display_name: string;
  status: string;
  receive_notifications: boolean;
  allow_openclaw: boolean;
  allow_agentos: boolean;
  is_default: boolean;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
};

type BindingForm = {
  userId: string;
  userHandle: string;
  accountId: string;
  peerId: string;
  displayName: string;
  receiveNotifications: boolean;
  allowOpenclaw: boolean;
  allowAgentos: boolean;
  isDefault: boolean;
};

const EMPTY_FORM: BindingForm = {
  userId: "",
  userHandle: "",
  accountId: "",
  peerId: "",
  displayName: "",
  receiveNotifications: true,
  allowOpenclaw: false,
  allowAgentos: false,
  isDefault: false,
};

function formFromBinding(binding: ChannelBinding): BindingForm {
  return {
    userId: binding.user_id,
    userHandle: binding.user_handle ?? "",
    accountId: binding.account_id,
    peerId: binding.peer_id,
    displayName: binding.display_name,
    receiveNotifications: binding.receive_notifications,
    allowOpenclaw: binding.allow_openclaw,
    allowAgentos: binding.allow_agentos,
    isDefault: binding.is_default,
  };
}

function userStatusLabel(status: string): string {
  if (status === "active") return "已激活";
  if (status === "invited") return "待激活";
  if (status === "disabled") return "用户已禁用";
  return status;
}

export default function ChannelBindingsPage() {
  const router = useRouter();
  const toast = useToast();
  const [users, setUsers] = useState<OpsUser[]>([]);
  const [bindings, setBindings] = useState<ChannelBinding[]>([]);
  const [statusFilter, setStatusFilter] = useState<BindingStatus>("active");
  const [userEmailDraft, setUserEmailDraft] = useState("");
  const [userEmail, setUserEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<BindingForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteCode, setInviteCode] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const editing =
    editingId === null ? null : (bindings.find((binding) => binding.id === editingId) ?? null);

  const load = useCallback(async () => {
    const params = new URLSearchParams({ status: statusFilter });
    if (userEmail) params.set("user_email", userEmail);
    try {
      const [userBody, bindingBody] = await Promise.all([
        opsJson<{ users: OpsUser[] }>("/api/ops/users"),
        opsJson<{ bindings: ChannelBinding[] }>(`/api/ops/channel-bindings?${params.toString()}`),
      ]);
      setUsers(userBody.users);
      setBindings(bindingBody.bindings);
      setError(null);
    } catch (err) {
      if (err instanceof OpsFetchError && err.status === 401) {
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [router, statusFilter, userEmail]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  function patchForm(patch: Partial<BindingForm>) {
    setForm((current) => ({ ...current, ...patch }));
  }

  function startCreate() {
    const firstAvailableUser = users.find((user) => user.status !== "disabled");
    setEditingId(null);
    setForm({
      ...EMPTY_FORM,
      userId: firstAvailableUser?.id ?? "",
      userHandle: firstAvailableUser?.handle ?? "",
    });
    setInviteCode(null);
    setFormOpen(true);
    setError(null);
  }

  function startEdit(binding: ChannelBinding) {
    setEditingId(binding.id);
    setForm(formFromBinding(binding));
    setInviteCode(null);
    setFormOpen(true);
    setError(null);
  }

  function closeForm() {
    setFormOpen(false);
    setEditingId(null);
    setInviteCode(null);
  }

  async function submitForm(event: FormEvent) {
    event.preventDefault();
    if (!form.userId || !form.accountId.trim() || !form.peerId.trim() || !form.displayName.trim()) {
      setError("用户、OpenClaw account、微信 peer 和名称均不能为空");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (form.userHandle.trim()) {
        await opsJson(`/api/ops/users/${form.userId}`, {
          method: "PATCH",
          body: JSON.stringify({ handle: form.userHandle.trim() }),
        });
      }
      const payload = {
        account_id: form.accountId.trim(),
        peer_id: form.peerId.trim(),
        display_name: form.displayName.trim(),
        receive_notifications: form.receiveNotifications,
        allow_openclaw: form.allowOpenclaw,
        allow_agentos: form.allowAgentos,
        is_default: form.isDefault,
      };
      if (editing) {
        await opsJson(`/api/ops/channel-bindings/${editing.id}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        toast.show("绑定已保存");
      } else {
        await opsJson("/api/ops/channel-bindings", {
          method: "POST",
          body: JSON.stringify({ ...payload, user_id: form.userId, channel: CHANNEL }),
        });
        toast.show("绑定已创建");
      }
      closeForm();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function generateInvite() {
    if (!form.userId) {
      setError("请先选择 AgentOS 用户");
      return;
    }
    setInviteBusy(true);
    setError(null);
    try {
      const result = await opsJson<{ code: string; expires_at: string }>(
        `/api/ops/users/${form.userId}/channel-binding-invites`,
        { method: "POST" },
      );
      setInviteCode(result.code);
      await load();
      toast.show("一次性绑定码已生成");
    } catch (err) {
      setError(err instanceof Error ? err.message : "绑定码生成失败");
    } finally {
      setInviteBusy(false);
    }
  }

  async function toggleStatus(binding: ChannelBinding) {
    setBusyId(binding.id);
    setError(null);
    try {
      await opsJson(`/api/ops/channel-bindings/${binding.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: binding.status === "active" ? "disabled" : "active" }),
      });
      await load();
      toast.show(binding.status === "active" ? "绑定已停用" : "绑定已启用");
    } catch (err) {
      setError(err instanceof Error ? err.message : "状态更新失败");
    } finally {
      setBusyId(null);
    }
  }

  async function removeBinding(binding: ChannelBinding) {
    if (confirmId !== binding.id) {
      setConfirmId(binding.id);
      return;
    }
    setBusyId(binding.id);
    setError(null);
    try {
      await opsJson(`/api/ops/channel-bindings/${binding.id}`, { method: "DELETE" });
      setConfirmId(null);
      await load();
      toast.show("绑定已解除");
    } catch (err) {
      setError(err instanceof Error ? err.message : "解绑失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="stack">
      {toast.node}
      <PageHeader
        title="账号绑定"
        lead="管理 AgentOS 用户与 OpenClaw 微信会话的配对和通知权限。"
        actions={
          <Button type="button" onClick={startCreate} disabled={formOpen}>
            <Plus />
            手工绑定（高级）
          </Button>
        }
      />

      <div className="callout">
        <h2>当前渠道：OpenClaw 微信</h2>
        <p>
          推荐用户在 AgentOS 的“微信通知”页面生成配对码，再在微信发送“绑定
          配对码”。这里的手工绑定仅用于运维排查；
          <code>account_id</code> 和 <code>peer_id</code> 不应要求普通用户自行获取。
        </p>
      </div>

      {formOpen ? (
        <form className="panel stack" onSubmit={(event) => void submitForm(event)}>
          <div className="section-head">
            <h2 className="section-title">{editing ? "编辑绑定" : "新建绑定"}</h2>
            <Button type="button" variant="ghost" size="sm" onClick={closeForm}>
              取消
            </Button>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <label>
              AgentOS 用户
              <select
                value={form.userId}
                disabled={editing !== null}
                onChange={(event) => {
                  const user = users.find((item) => item.id === event.target.value);
                  patchForm({ userId: event.target.value, userHandle: user?.handle ?? "" });
                  setInviteCode(null);
                }}
                required
              >
                <option value="">选择用户</option>
                {users.map((user) => (
                  <option key={user.id} value={user.id} disabled={user.status === "disabled"}>
                    {user.email} · {userStatusLabel(user.status)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              AgentOS 用户名（可选显示名）
              <input
                value={form.userHandle}
                placeholder="可留空"
                onChange={(event) => patchForm({ userHandle: event.target.value })}
              />
            </label>
            <label>
              显示名称
              <input
                value={form.displayName}
                placeholder="例如：张三微信"
                onChange={(event) => patchForm({ displayName: event.target.value })}
                required
              />
            </label>
            <label>
              OpenClaw account_id
              <input
                value={form.accountId}
                placeholder="例如：微信插件显示的 account id"
                onChange={(event) => patchForm({ accountId: event.target.value })}
                required
              />
            </label>
            <label>
              微信 peer_id
              <input
                value={form.peerId}
                placeholder="例如：用户会话 ID"
                onChange={(event) => patchForm({ peerId: event.target.value })}
                required
              />
            </label>
          </div>

          <div className="callout">
            <div className="section-head">
              <div>
                <h2>微信自助绑定码</h2>
                <p>生成后把配对码交给用户；用户在微信发送“绑定 配对码”。</p>
              </div>
              <Button
                type="button"
                variant="outline"
                onClick={() => void generateInvite()}
                disabled={inviteBusy || saving}
              >
                <KeyRound />
                {inviteBusy ? "生成中" : "生成绑定码"}
              </Button>
            </div>
            {inviteCode ? (
              <div className="btn-row">
                <code className="text-lg tracking-[0.18em]">{inviteCode}</code>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    void navigator.clipboard?.writeText(inviteCode);
                    toast.show("绑定码已复制");
                  }}
                >
                  <Copy />
                  复制
                </Button>
              </div>
            ) : null}
          </div>

          <div className="stack">
            <span className="section-title">权限</span>
            <label className="inline-check">
              <input
                type="checkbox"
                checked={form.receiveNotifications}
                onChange={(event) => patchForm({ receiveNotifications: event.target.checked })}
              />
              <Bell className="size-4" />
              接收定时通知
            </label>
            <label className="inline-check">
              <input
                type="checkbox"
                checked={form.allowOpenclaw}
                onChange={(event) => patchForm({ allowOpenclaw: event.target.checked })}
              />
              <Bot className="size-4" />
              允许操作 OpenClaw（入站预留）
            </label>
            <label className="inline-check">
              <input
                type="checkbox"
                checked={form.allowAgentos}
                onChange={(event) => patchForm({ allowAgentos: event.target.checked })}
              />
              <ShieldCheck className="size-4" />
              允许进入 AgentOS（入站预留）
            </label>
            <label className="inline-check">
              <input
                type="checkbox"
                checked={form.isDefault}
                onChange={(event) => patchForm({ isDefault: event.target.checked })}
              />
              <UserRound className="size-4" />
              设为该用户的默认微信收件人
            </label>
          </div>

          {error ? <p className="error">{error}</p> : null}
          <div className="btn-row">
            <Button type="submit" disabled={saving}>
              <Link2 />
              {saving ? "保存中" : "保存绑定"}
            </Button>
            <Button type="button" variant="outline" onClick={closeForm} disabled={saving}>
              取消
            </Button>
          </div>
        </form>
      ) : null}

      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          setUserEmail(userEmailDraft.trim().toLowerCase());
        }}
      >
        <div className="seg" role="tablist" aria-label="绑定状态">
          {STATUS_FILTERS.map((item) => (
            <button
              key={item}
              type="button"
              className={statusFilter === item ? "is-selected" : ""}
              onClick={() => setStatusFilter(item)}
            >
              {item === "active" ? "启用中" : "已停用"}
            </button>
          ))}
        </div>
        <input
          className="search-input"
          value={userEmailDraft}
          placeholder="按 AgentOS 用户邮箱筛选"
          onChange={(event) => setUserEmailDraft(event.target.value)}
        />
        <Button type="submit" variant="outline" size="sm">
          筛选
        </Button>
      </form>

      {error && !formOpen ? <p className="error">{error}</p> : null}
      {loading ? <Skeleton /> : <p className="muted">{bindings.length} 条绑定</p>}

      {!loading && bindings.length > 0 ? (
        <div className="row-list">
          {bindings.map((binding) => (
            <article key={binding.id} className="row row--ops">
              <div className="row__main">
                <div className="row__title">{binding.display_name}</div>
                <div className="row__meta">
                  <span>{binding.user_email}</span>
                  {binding.user_handle ? <span>用户名：{binding.user_handle}</span> : null}
                  <span>{binding.channel}</span>
                  {binding.is_default ? <span className="pill">默认</span> : null}
                  {binding.receive_notifications ? <span>通知</span> : null}
                  {binding.allow_openclaw ? <span>OpenClaw</span> : null}
                  {binding.allow_agentos ? <span>AgentOS</span> : null}
                </div>
                <div className="row__meta">
                  <code>{binding.account_id}</code>
                  <code>{binding.peer_id}</code>
                  <span>{formatTime(binding.updated_at)}</span>
                </div>
              </div>
              <span className={`badge badge--${binding.status}`}>
                {binding.status === "active" ? "启用" : "停用"}
              </span>
              <div className="btn-row">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={busyId === binding.id}
                  onClick={() => startEdit(binding)}
                >
                  <Pencil />
                  编辑
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={busyId === binding.id}
                  onClick={() => void toggleStatus(binding)}
                >
                  {binding.status === "active" ? "停用" : "启用"}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={busyId === binding.id}
                  onClick={() => void removeBinding(binding)}
                  onBlur={() => {
                    if (confirmId === binding.id) setConfirmId(null);
                  }}
                >
                  <Trash2 />
                  {confirmId === binding.id ? "确认解绑" : "解绑"}
                </Button>
              </div>
            </article>
          ))}
        </div>
      ) : null}

      {!loading && bindings.length === 0 ? (
        <div className="empty">当前筛选下没有账号绑定。</div>
      ) : null}
    </div>
  );
}
