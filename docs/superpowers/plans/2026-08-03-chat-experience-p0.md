# Chat Experience P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship step timeline (multi Thinking + tools), assistant Markdown, message timestamps, and Run duration on the AG-UI chat panel.

**Architecture:** Replace single `reasoning` + parallel `toolCalls` with an ordered `timelineSteps` list anchored to the current user message. Render assistant content via sanitized GFM Markdown. Keep `created_at` on display messages; after run finish, load Run `started_at`/`completed_at` for duration. Thinking is live-only (no history persistence).

**Tech Stack:** Next.js web app, `@ag-ui/client`, `react-markdown`, `remark-gfm`, `rehype-sanitize`, existing Run inspector API proxy.

## Global Constraints

- Production path is AG-UI only for this slice.
- Do not persist Thinking to `run_events` in P0.
- User messages stay plain text; Thinking/Tool are not Markdown.
- Prefer CSS variables for new styles so P2 can retheme.
- No Chat SDK replacement; no per-step duration.

---

### Task 1: Timeline step state in chat-panel

**Files:**
- Modify: `apps/web/src/components/chat/chat-panel.tsx`
- Modify: `apps/web/src/components/chat/tool-call-card.tsx` (export types if needed)
- Create (optional): `apps/web/src/components/chat/thinking-step-card.tsx`

**Interfaces:**
- Produces: `TimelineStep` union (`thinking` | `tool`) ordered array; helpers to upsert/toggle

- [x] Replace `reasoning` / `toolCalls` with `timelineSteps` + `anchorUserMessageId`
- [x] On Reasoning Start: append new thinking step; Content/End update that step
- [x] On Tool events: append/update tool steps in the same array (arrival order)
- [x] Render steps after the matching user message, before assistant
- [x] Clear steps on new send; history load leaves steps empty (tools still from `tool_calls`)
- [x] Keep history tool cards: merge historical `tool_calls` into display as tool-only steps keyed by `afterMessageId` (separate from live `timelineSteps`, or unified `historyToolSteps`)

**Verify:** Manual or component logic — two reasoning starts create two cards with a tool between when events arrive in that order.

---

### Task 2: Assistant Markdown

**Files:**
- Modify: `apps/web/package.json` (deps)
- Create: `apps/web/src/components/chat/assistant-markdown.tsx`
- Modify: `apps/web/src/components/chat/chat-panel.tsx`
- Modify: `apps/web/src/app/globals.css`

- [x] `pnpm --filter web add react-markdown remark-gfm rehype-sanitize`
- [x] Render assistant bubbles through `AssistantMarkdown`
- [x] Style `.agentos-md` for headings, lists, links, code/pre
- [x] `pnpm --filter web exec tsc --noEmit`

---

### Task 3: Timestamps + Run duration

**Files:**
- Modify: `apps/web/src/components/chat/chat-panel.tsx`
- Create (optional): `apps/web/src/lib/format-time.ts`
- Check: `apps/web/src/components/run/run-inspector.tsx` / run API route for fetch pattern

- [x] Extend `ChatMessage` with `createdAt: string`
- [x] `parseThreadHistory` keeps `created_at`
- [x] Live user message: `new Date().toISOString()`; assistant: set on run complete
- [x] Format display: today `HH:mm`, else `M/D HH:mm`
- [x] After run finishes with `runId`, fetch run detail; show `用时 Xs` on that assistant message
- [x] Cancel/fail without `completed_at`: omit or show `已中断`

**Verify:** `pnpm --filter web exec tsc --noEmit` and `pnpm lint:web` (or eslint on touched files).

---

### Task 4: Docs + commit

**Files:**
- Modify: `docs/implementation-progress.md`
- Modify: `docs/README.md` (link plan if needed)

- [x] Note P0 progress
- [x] Commit on `feat/chat-experience-p0`
