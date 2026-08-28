"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  AlarmClock,
  CalendarClock,
  Check,
  ExternalLink,
  Pause,
  Pencil,
  Play,
  PlayCircle,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import type { AgentSummary } from "@/lib/agents";

type ScheduleType = "once" | "daily" | "weekly" | "monthly";

type TaskRun = {
  id: string;
  run_id: string;
  scheduled_for: string | null;
  status: string;
  model_name: string;
  input_tokens: number | null;
  output_tokens: number | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
};

type ScheduledTask = {
  id: string;
  title: string;
  prompt: string;
  agent_id: string;
  agent_name: string;
  case_id: string | null;
  thread_id: string | null;
  schedule_type: ScheduleType;
  run_at: string | null;
  time_of_day: string | null;
  days_of_week: number[] | null;
  day_of_month: number | null;
  timezone: string;
  status: "active" | "paused" | "completed";
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_status: string | null;
  last_error: string | null;
  consecutive_failures: number;
  unread_results: number;
  created_at: string;
  updated_at: string;
  runs: TaskRun[];
};

type TaskForm = {
  title: string;
  prompt: string;
  agentId: string;
  scheduleType: ScheduleType;
  runAt: string;
  timeOfDay: string;
  daysOfWeek: number[];
  dayOfMonth: string;
  timezone: string;
};

type ScheduledTasksPanelProps = {
  agents: AgentSummary[];
  onOpenThread: (threadId: string, agentId?: string) => void;
  onUnreadCountChange?: (count: number) => void;
};

const dayLabels = ["一", "二", "三", "四", "五", "六", "日"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseTasks(value: unknown): ScheduledTask[] | null {
  if (!isRecord(value) || !Array.isArray(value.tasks)) {
    return null;
  }
  return value.tasks.filter((task): task is ScheduledTask => {
    return (
      isRecord(task) &&
      typeof task.id === "string" &&
      typeof task.title === "string" &&
      typeof task.prompt === "string" &&
      typeof task.agent_id === "string" &&
      typeof task.agent_name === "string" &&
      typeof task.schedule_type === "string" &&
      typeof task.timezone === "string" &&
      typeof task.status === "string" &&
      Array.isArray(task.runs)
    );
  });
}

function defaultTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
}

function emptyForm(agentId: string, timezone: string): TaskForm {
  const date = new Date(Date.now() + 60 * 60 * 1_000);
  const localDate = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const part = (type: string) => localDate.find((item) => item.type === type)?.value ?? "00";

  return {
    title: "",
    prompt: "",
    agentId,
    scheduleType: "daily",
    runAt: `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`,
    timeOfDay: "09:00",
    daysOfWeek: [0, 1, 2, 3, 4],
    dayOfMonth: "1",
    timezone,
  };
}

function formFromTask(task: ScheduledTask): TaskForm {
  return {
    title: task.title,
    prompt: task.prompt,
    agentId: task.agent_id,
    scheduleType: task.schedule_type,
    runAt: task.run_at ? task.run_at.slice(0, 16) : "",
    timeOfDay: task.time_of_day ?? "09:00",
    daysOfWeek: task.days_of_week ?? [0],
    dayOfMonth: String(task.day_of_month ?? 1),
    timezone: task.timezone,
  };
}

function formatDate(value: string | null, timezone?: string): string {
  if (!value) return "未安排";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "时间无效";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    ...(timezone ? { timeZone: timezone } : {}),
  }).format(date);
}

function scheduleLabel(task: ScheduledTask): string {
  if (task.schedule_type === "once") return `一次性 · ${formatDate(task.run_at, task.timezone)}`;
  if (task.schedule_type === "daily") return `每天 ${task.time_of_day ?? ""}`;
  if (task.schedule_type === "weekly") {
    const days = (task.days_of_week ?? []).map((day) => `周${dayLabels[day]}`).join("、");
    return `${days} ${task.time_of_day ?? ""}`;
  }
  return `每月 ${task.day_of_month ?? 1} 日 ${task.time_of_day ?? ""}`;
}

function statusLabel(task: ScheduledTask): string {
  if (task.status === "active") return "运行中";
  if (task.status === "paused") return "已暂停";
  return "已完成";
}

function runStatusLabel(status: string): string {
  if (status === "completed") return "成功";
  if (status === "running") return "执行中";
  if (status === "waiting_approval") return "等待审批";
  if (status === "cancelled") return "已取消";
  return "失败";
}

export function ScheduledTasksPanel({
  agents,
  onOpenThread,
  onUnreadCountChange,
}: ScheduledTasksPanelProps) {
  const firstAgentId = agents[0]?.id ?? "";
  const timezone = useMemo(() => defaultTimezone(), []);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [form, setForm] = useState<TaskForm>(() => emptyForm(firstAgentId, timezone));
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ScheduledTask | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? tasks[0] ?? null;

  const loadTasks = useCallback(async () => {
    try {
      const response = await fetch("/api/scheduled-tasks?limit=50", { cache: "no-store" });
      if (!response.ok) throw new Error("无法读取定时任务。");
      const parsed = parseTasks((await response.json()) as unknown);
      if (parsed === null) throw new Error("定时任务格式无效。");
      setTasks(parsed);
      onUnreadCountChange?.(
        parsed.reduce((total, task) => total + Math.max(0, task.unread_results), 0),
      );
      setSelectedTaskId((current) =>
        current && parsed.some((task) => task.id === current) ? current : (parsed[0]?.id ?? null),
      );
      setError(null);
    } catch (caughtError: unknown) {
      setError(caughtError instanceof Error ? caughtError.message : "无法读取定时任务。");
    } finally {
      setLoading(false);
    }
  }, [onUnreadCountChange]);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void loadTasks(), 0);
    const timer = window.setInterval(() => void loadTasks(), 30_000);
    return () => {
      window.clearTimeout(initialLoad);
      window.clearInterval(timer);
    };
  }, [loadTasks]);

  useEffect(() => {
    if (selectedTask === null || selectedTask.unread_results === 0) return;
    void fetch(`/api/scheduled-tasks/${selectedTask.id}/read`, { method: "POST" }).then(
      (response) => {
        if (!response.ok) return;
        setTasks((current) =>
          current.map((task) =>
            task.id === selectedTask.id ? { ...task, unread_results: 0 } : task,
          ),
        );
        onUnreadCountChange?.(
          Math.max(
            0,
            tasks.reduce((total, task) => total + Math.max(0, task.unread_results), 0) -
              selectedTask.unread_results,
          ),
        );
      },
    );
  }, [onUnreadCountChange, selectedTask, tasks]);

  function startCreate() {
    setEditingTaskId(null);
    setForm(emptyForm(firstAgentId, timezone));
    setShowForm(true);
  }

  function startEdit(task: ScheduledTask) {
    setEditingTaskId(task.id);
    setForm(formFromTask(task));
    setShowForm(true);
  }

  async function submitForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const payload = {
      title: form.title,
      prompt: form.prompt,
      agent_id: form.agentId,
      schedule_type: form.scheduleType,
      run_at: form.scheduleType === "once" ? form.runAt : null,
      time_of_day: form.scheduleType === "once" ? null : form.timeOfDay,
      days_of_week: form.scheduleType === "weekly" ? form.daysOfWeek : null,
      day_of_month: form.scheduleType === "monthly" ? Number(form.dayOfMonth) : null,
      timezone: form.timezone,
    };

    try {
      const response = await fetch(
        editingTaskId ? `/api/scheduled-tasks/${editingTaskId}` : "/api/scheduled-tasks",
        {
          method: editingTaskId ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      if (!response.ok) throw new Error("保存定时任务失败，请检查时间和必填项。");
      const saved = (await response.json()) as ScheduledTask;
      setTasks((current) =>
        editingTaskId
          ? current.map((task) => (task.id === saved.id ? saved : task))
          : [saved, ...current],
      );
      setSelectedTaskId(saved.id);
      setShowForm(false);
    } catch (caughtError: unknown) {
      setError(caughtError instanceof Error ? caughtError.message : "保存定时任务失败。");
    } finally {
      setBusy(false);
    }
  }

  async function taskAction(task: ScheduledTask, action: "pause" | "resume" | "run") {
    setBusy(true);
    try {
      const response = await fetch(`/api/scheduled-tasks/${task.id}/${action}`, { method: "POST" });
      if (!response.ok) throw new Error(action === "run" ? "启动执行失败。" : "更新任务状态失败。");
      await loadTasks();
    } catch (caughtError: unknown) {
      setError(caughtError instanceof Error ? caughtError.message : "任务操作失败。");
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (deleteTarget === null) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/scheduled-tasks/${deleteTarget.id}`, { method: "DELETE" });
      if (!response.ok) throw new Error("删除定时任务失败。");
      onUnreadCountChange?.(
        Math.max(
          0,
          tasks.reduce((total, task) => total + Math.max(0, task.unread_results), 0) -
            deleteTarget.unread_results,
        ),
      );
      setTasks((current) => current.filter((task) => task.id !== deleteTarget.id));
      setDeleteTarget(null);
      setSelectedTaskId(null);
    } catch (caughtError: unknown) {
      setError(caughtError instanceof Error ? caughtError.message : "删除定时任务失败。");
    } finally {
      setBusy(false);
    }
  }

  function updateForm<K extends keyof TaskForm>(key: K, value: TaskForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  return (
    <section className="flex h-full min-h-0 flex-col bg-[var(--surface-chat)]">
      <header className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-6 py-5 sm:px-8">
        <div className="flex items-center gap-3">
          <span className="grid size-9 place-items-center border border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent)]">
            <CalendarClock aria-hidden="true" className="size-5" />
          </span>
          <div>
            <h1 className="text-lg font-semibold text-[var(--text)]">定时任务</h1>
            <p className="mt-0.5 text-xs text-[var(--muted)]">{tasks.length} 个任务</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            title="刷新任务"
            aria-label="刷新任务"
            onClick={() => void loadTasks()}
            className="grid size-9 place-items-center border border-[var(--border)] text-[var(--muted)] transition hover:border-[var(--accent-border)] hover:text-[var(--accent)]"
          >
            <RefreshCw aria-hidden="true" className="size-4" />
          </button>
          <button
            type="button"
            onClick={startCreate}
            className="flex items-center gap-2 bg-[var(--accent)] px-3 py-2 text-sm font-semibold text-[var(--send-fg)] transition hover:opacity-90"
          >
            <Plus aria-hidden="true" className="size-4" />
            新建任务
          </button>
        </div>
      </header>

      {error ? (
        <div className="mx-6 mt-4 flex items-center justify-between gap-3 border border-[var(--danger)]/30 bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)] sm:mx-8">
          <span>{error}</span>
          <button
            type="button"
            title="关闭提示"
            aria-label="关闭提示"
            onClick={() => setError(null)}
          >
            <X aria-hidden="true" className="size-4" />
          </button>
        </div>
      ) : null}

      {showForm ? (
        <form
          onSubmit={submitForm}
          className="mx-6 mt-4 border border-[var(--accent-border)] bg-[var(--panel-solid)] p-5 sm:mx-8 sm:p-6"
        >
          <div className="mb-5 flex items-center justify-between">
            <h2 className="font-semibold text-[var(--text)]">
              {editingTaskId ? "编辑任务" : "新建任务"}
            </h2>
            <button
              type="button"
              title="关闭表单"
              aria-label="关闭表单"
              onClick={() => setShowForm(false)}
              className="text-[var(--muted)] hover:text-[var(--text)]"
            >
              <X aria-hidden="true" className="size-5" />
            </button>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <label className="block text-sm text-[var(--text-secondary)]">
              名称
              <input
                required
                maxLength={128}
                value={form.title}
                onChange={(event) => updateForm("title", event.target.value)}
                className="mt-1.5 w-full border border-[var(--border)] bg-[var(--surface-input)] px-3 py-2.5 text-[var(--text)] outline-none focus:border-[var(--accent)]"
                placeholder="例如：每日行业摘要"
              />
            </label>
            <label className="block text-sm text-[var(--text-secondary)]">
              使用助手
              <select
                required
                value={form.agentId}
                disabled={editingTaskId !== null}
                onChange={(event) => updateForm("agentId", event.target.value)}
                className="mt-1.5 w-full border border-[var(--border)] bg-[var(--surface-input)] px-3 py-2.5 text-[var(--text)] outline-none focus:border-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {agents.map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="mt-4 block text-sm text-[var(--text-secondary)]">
            执行指令
            <textarea
              required
              maxLength={4_000}
              rows={4}
              value={form.prompt}
              onChange={(event) => updateForm("prompt", event.target.value)}
              className="mt-1.5 w-full resize-y border border-[var(--border)] bg-[var(--surface-input)] px-3 py-2.5 text-[var(--text)] outline-none focus:border-[var(--accent)]"
              placeholder="让助手在执行时间完成什么？"
            />
          </label>
          <div className="mt-4 grid gap-4 sm:grid-cols-3">
            <label className="block text-sm text-[var(--text-secondary)]">
              重复方式
              <select
                value={form.scheduleType}
                onChange={(event) => updateForm("scheduleType", event.target.value as ScheduleType)}
                className="mt-1.5 w-full border border-[var(--border)] bg-[var(--surface-input)] px-3 py-2.5 text-[var(--text)] outline-none focus:border-[var(--accent)]"
              >
                <option value="once">一次性</option>
                <option value="daily">每天</option>
                <option value="weekly">每周</option>
                <option value="monthly">每月</option>
              </select>
            </label>
            {form.scheduleType === "once" ? (
              <label className="block text-sm text-[var(--text-secondary)] sm:col-span-2">
                执行时间
                <input
                  required
                  type="datetime-local"
                  value={form.runAt}
                  onChange={(event) => updateForm("runAt", event.target.value)}
                  className="mt-1.5 w-full border border-[var(--border)] bg-[var(--surface-input)] px-3 py-2.5 text-[var(--text)] outline-none focus:border-[var(--accent)]"
                />
              </label>
            ) : (
              <label className="block text-sm text-[var(--text-secondary)]">
                执行时间
                <input
                  required
                  type="time"
                  value={form.timeOfDay}
                  onChange={(event) => updateForm("timeOfDay", event.target.value)}
                  className="mt-1.5 w-full border border-[var(--border)] bg-[var(--surface-input)] px-3 py-2.5 text-[var(--text)] outline-none focus:border-[var(--accent)]"
                />
              </label>
            )}
            {form.scheduleType === "monthly" ? (
              <label className="block text-sm text-[var(--text-secondary)]">
                每月日期
                <input
                  required
                  type="number"
                  min={1}
                  max={31}
                  value={form.dayOfMonth}
                  onChange={(event) => updateForm("dayOfMonth", event.target.value)}
                  className="mt-1.5 w-full border border-[var(--border)] bg-[var(--surface-input)] px-3 py-2.5 text-[var(--text)] outline-none focus:border-[var(--accent)]"
                />
              </label>
            ) : null}
          </div>
          {form.scheduleType === "weekly" ? (
            <fieldset className="mt-4">
              <legend className="text-sm text-[var(--text-secondary)]">每周日期</legend>
              <div className="mt-2 flex flex-wrap gap-2">
                {dayLabels.map((label, day) => {
                  const checked = form.daysOfWeek.includes(day);
                  return (
                    <label
                      key={label}
                      className={`flex cursor-pointer items-center gap-1.5 border px-3 py-2 text-sm ${checked ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]" : "border-[var(--border)] text-[var(--muted)]"}`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() =>
                          updateForm(
                            "daysOfWeek",
                            checked
                              ? form.daysOfWeek.filter((item) => item !== day)
                              : [...form.daysOfWeek, day].sort(),
                          )
                        }
                        className="sr-only"
                      />
                      {checked ? <Check aria-hidden="true" className="size-3.5" /> : null}周{label}
                    </label>
                  );
                })}
              </div>
            </fieldset>
          ) : null}
          <label className="mt-4 block max-w-sm text-sm text-[var(--text-secondary)]">
            时区
            <input
              required
              value={form.timezone}
              onChange={(event) => updateForm("timezone", event.target.value)}
              className="mt-1.5 w-full border border-[var(--border)] bg-[var(--surface-input)] px-3 py-2.5 font-mono text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]"
            />
          </label>
          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="border border-[var(--border)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:border-[var(--border-strong)]"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={
                busy ||
                !form.agentId ||
                (form.scheduleType === "weekly" && form.daysOfWeek.length === 0)
              }
              className="flex items-center gap-2 bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[var(--send-fg)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Check aria-hidden="true" className="size-4" />
              保存任务
            </button>
          </div>
        </form>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-0 overflow-hidden lg:grid-cols-[minmax(280px,0.85fr)_minmax(360px,1.15fr)]">
        <div className="min-h-0 overflow-y-auto border-b border-[var(--border)] p-6 sm:p-8 lg:border-r lg:border-b-0">
          {loading ? <p className="text-sm text-[var(--muted)]">加载中...</p> : null}
          {!loading && tasks.length === 0 ? (
            <div className="grid min-h-64 place-items-center border border-dashed border-[var(--border)] px-6 text-center">
              <div>
                <AlarmClock aria-hidden="true" className="mx-auto size-7 text-[var(--muted)]" />
                <p className="mt-3 text-sm text-[var(--text-secondary)]">还没有定时任务</p>
                <button
                  type="button"
                  onClick={startCreate}
                  className="mt-4 text-sm font-semibold text-[var(--accent)]"
                >
                  创建第一个任务
                </button>
              </div>
            </div>
          ) : null}
          <div className="space-y-2">
            {tasks.map((task) => (
              <button
                key={task.id}
                type="button"
                onClick={() => setSelectedTaskId(task.id)}
                className={`block w-full border p-4 text-left transition ${selectedTask?.id === task.id ? "border-[var(--accent-border)] bg-[var(--accent-softer)]" : "border-[var(--border)] bg-[var(--panel-solid)] hover:border-[var(--border-strong)]"}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-[var(--text)]">{task.title}</p>
                    <p className="mt-1 truncate text-xs text-[var(--muted)]">
                      {task.agent_name} · {scheduleLabel(task)}
                    </p>
                  </div>
                  {task.unread_results > 0 ? (
                    <span className="grid min-w-5 place-items-center rounded-full bg-[var(--accent)] px-1.5 py-0.5 text-[11px] font-semibold text-[var(--send-fg)]">
                      {task.unread_results}
                    </span>
                  ) : null}
                </div>
                <div className="mt-3 flex items-center justify-between text-xs">
                  <span
                    className={
                      task.status === "active" ? "text-[var(--success)]" : "text-[var(--muted)]"
                    }
                  >
                    {statusLabel(task)}
                  </span>
                  <span className="text-[var(--muted)]">
                    {task.next_run_at ? formatDate(task.next_run_at, task.timezone) : ""}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 overflow-y-auto p-6 sm:p-8">
          {selectedTask ? (
            <>
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-xl font-semibold text-[var(--text)]">
                      {selectedTask.title}
                    </h2>
                    <span className="border border-[var(--border)] px-2 py-0.5 text-xs text-[var(--muted)]">
                      {statusLabel(selectedTask)}
                    </span>
                  </div>
                  <p className="mt-2 text-sm text-[var(--muted)]">
                    {selectedTask.agent_name} · {scheduleLabel(selectedTask)} ·{" "}
                    {selectedTask.timezone}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  {selectedTask.thread_id ? (
                    <button
                      type="button"
                      title="打开任务会话"
                      aria-label="打开任务会话"
                      onClick={() => onOpenThread(selectedTask.thread_id!, selectedTask.agent_id)}
                      className="grid size-9 place-items-center border border-[var(--border)] text-[var(--muted)] hover:border-[var(--accent-border)] hover:text-[var(--accent)]"
                    >
                      <ExternalLink aria-hidden="true" className="size-4" />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    title="编辑任务"
                    aria-label="编辑任务"
                    onClick={() => startEdit(selectedTask)}
                    className="grid size-9 place-items-center border border-[var(--border)] text-[var(--muted)] hover:border-[var(--accent-border)] hover:text-[var(--accent)]"
                  >
                    <Pencil aria-hidden="true" className="size-4" />
                  </button>
                  <button
                    type="button"
                    title="删除任务"
                    aria-label="删除任务"
                    onClick={() => setDeleteTarget(selectedTask)}
                    className="grid size-9 place-items-center border border-[var(--border)] text-[var(--muted)] hover:border-[var(--danger)] hover:text-[var(--danger)]"
                  >
                    <Trash2 aria-hidden="true" className="size-4" />
                  </button>
                </div>
              </div>
              <div className="mt-6 border border-[var(--border)] bg-[var(--panel-solid)] p-4">
                <p className="text-sm leading-6 whitespace-pre-wrap text-[var(--text-secondary)]">
                  {selectedTask.prompt}
                </p>
              </div>
              <div className="mt-5 flex flex-wrap gap-2">
                {selectedTask.status === "active" ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void taskAction(selectedTask, "pause")}
                    className="flex items-center gap-2 border border-[var(--border)] px-3 py-2 text-sm text-[var(--text-secondary)] hover:border-[var(--accent-border)] hover:text-[var(--accent)] disabled:opacity-50"
                  >
                    <Pause aria-hidden="true" className="size-4" />
                    暂停
                  </button>
                ) : selectedTask.status === "paused" ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void taskAction(selectedTask, "resume")}
                    className="flex items-center gap-2 border border-[var(--accent-border)] px-3 py-2 text-sm text-[var(--accent)] disabled:opacity-50"
                  >
                    <Play aria-hidden="true" className="size-4" />
                    恢复
                  </button>
                ) : null}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void taskAction(selectedTask, "run")}
                  className="flex items-center gap-2 bg-[var(--accent)] px-3 py-2 text-sm font-semibold text-[var(--send-fg)] disabled:opacity-50"
                >
                  <PlayCircle aria-hidden="true" className="size-4" />
                  立即执行
                </button>
              </div>
              {selectedTask.last_error ? (
                <p className="mt-4 border border-[var(--danger)]/30 bg-[var(--danger-soft)] px-3 py-2 text-sm text-[var(--danger)]">
                  {selectedTask.last_error}
                </p>
              ) : null}
              <div className="mt-8">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-[var(--text)]">执行记录</h3>
                  <span className="text-xs text-[var(--muted)]">{selectedTask.runs.length} 次</span>
                </div>
                <div className="mt-3 space-y-2">
                  {selectedTask.runs.length === 0 ? (
                    <p className="border border-dashed border-[var(--border)] px-4 py-6 text-center text-sm text-[var(--muted)]">
                      尚未执行
                    </p>
                  ) : (
                    selectedTask.runs.map((run) => (
                      <div
                        key={run.id}
                        className="border border-[var(--border)] bg-[var(--panel-solid)] px-4 py-3"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-sm text-[var(--text-secondary)]">
                            {formatDate(run.completed_at ?? run.created_at, selectedTask.timezone)}
                          </span>
                          <span
                            className={
                              run.status === "completed"
                                ? "text-xs text-[var(--success)]"
                                : "text-xs text-[var(--danger)]"
                            }
                          >
                            {runStatusLabel(run.status)}
                          </span>
                        </div>
                        {run.error_message ? (
                          <p className="mt-1 text-xs text-[var(--danger)]">{run.error_message}</p>
                        ) : null}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="grid h-full min-h-64 place-items-center text-sm text-[var(--muted)]">
              选择一个任务查看详情
            </div>
          )}
        </div>
      </div>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除定时任务？</AlertDialogTitle>
            <AlertDialogDescription>
              删除后不会删除已经产生的会话和执行记录。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void confirmDelete()}
              className="bg-[var(--danger)] text-white hover:opacity-90"
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
