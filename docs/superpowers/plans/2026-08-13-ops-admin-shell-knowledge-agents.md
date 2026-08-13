# Ops Admin Shell + Knowledge Depth + Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Ship ops console Phase-1 admin: shell/dashboard, knowledge detail + metadata PATCH + snapshot payload, Agent list/patch — all via `/v1/ops/*`.

**Architecture:** Extend FastAPI ops routers; Next `apps/ops` gets `OpsShell` layout, BFF proxies, and pages for overview/knowledge/agents plus MCP/Skills/Sessions placeholders.

**Tech Stack:** FastAPI, SQLAlchemy async, Next.js App Router, pytest, TypeScript.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-13-ops-admin-shell-knowledge-agents-design.md`
- Ops auth only (`ops_session`); no user cookie
- No chunk edit, restore, create agent, version edits
- Commit on `main` + push; update progress docs

---

## File map

| Path | Responsibility |
| --- | --- |
| `api/ops_stats.py` | `GET /v1/ops/stats` |
| `api/ops_knowledge.py` | GET detail, expanded PATCH, GET snapshot by id |
| `api/ops_agents.py` | GET/PATCH agents |
| `main.py` | include routers |
| `tests/test_ops_*.py` | cover new endpoints |
| `apps/ops/src/components/ops-shell.tsx` | nav + drawer |
| `apps/ops/src/app/(ops)/**` | gated pages |
| `apps/ops/src/app/api/ops/**` | BFF |
| docs | progress + spec status |

---

### Task 1: Backend APIs + tests

- [x] Stats endpoint
- [x] Knowledge GET detail + PATCH fields + snapshot GET
- [x] Agents GET/PATCH (default uniqueness, cannot disable sole default)
- [x] pytest green
- [x] Commit

### Task 2: Ops shell + dashboard + placeholders

- [x] `OpsShell`, `(ops)` layout auth gate
- [x] Dashboard + placeholder pages + CSS
- [x] BFF `/api/ops/stats`
- [x] Commit

### Task 3: Knowledge UI

- [x] List → detail links + status filter
- [x] Detail edit form + chunks + snapshot preview
- [x] BFF routes
- [x] Commit

### Task 4: Agents UI

- [x] Agents page + BFF
- [x] Commit

### Task 5: Docs + verify

- [x] Spec → 已实现; progress/roadmap
- [x] `pnpm --filter ops build` + ops pytest
- [x] Commit + push
