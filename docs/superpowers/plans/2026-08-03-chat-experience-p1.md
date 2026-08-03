# Chat Experience P1 Implementation Plan

> **For agentic workers:** Implement soft-delete and rename for Threads per the chat experience redesign spec.

**Goal:** Users can rename and soft-delete conversations from the sidebar; deleted threads disappear from list/history/continue.

**Architecture:** Add `threads.deleted_at`; filter all user-facing reads; `PATCH`/`DELETE` on `/v1/threads/{id}` with Next.js proxies; conversation list gains rename/delete actions.

**Tech Stack:** FastAPI, Alembic/SQLAlchemy, Next.js route handlers, existing conversation-list UI.
