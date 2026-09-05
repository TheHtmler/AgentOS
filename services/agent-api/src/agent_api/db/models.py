from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agent_api.db.base import Base


class User(Base):
    """An invited or active AgentOS user; authentication is bound to this record."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "status IN ('invited', 'active', 'disabled')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "handle IS NULL OR length(trim(handle)) > 0",
            name="ck_users_handle",
        ),
        Index(
            "uq_users_handle_ci",
            func.lower(text("handle")),
            unique=True,
            postgresql_where=text("handle IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    # Stable human-readable identifier used by controlled external-channel pairing.
    handle: Mapped[str | None] = mapped_column(String(64))
    # Only an Argon2id password hash is stored; plaintext passwords are never persisted.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'invited'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthToken(Base):
    """A single-use, hashed invitation or magic-link token."""

    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('invite', 'magic_link')",
            name="ck_auth_tokens_purpose",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class UserSession(Base):
    """A revocable browser session represented by a hashed opaque token."""

    __tablename__ = "user_sessions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class UserChannelBinding(Base):
    """Map one AgentOS user to one external channel peer and its permissions."""

    __tablename__ = "user_channel_bindings"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('openclaw-weixin')",
            name="ck_user_channel_bindings_channel",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_user_channel_bindings_status",
        ),
        CheckConstraint(
            "length(trim(account_id)) > 0",
            name="ck_user_channel_bindings_account_id",
        ),
        CheckConstraint(
            "length(trim(peer_id)) > 0",
            name="ck_user_channel_bindings_peer_id",
        ),
        UniqueConstraint(
            "channel",
            "account_id",
            "peer_id",
            name="uq_user_channel_bindings_endpoint",
        ),
        Index(
            "ix_user_channel_bindings_user_channel",
            "user_id",
            "channel",
        ),
        Index(
            "uq_user_channel_bindings_default",
            "user_id",
            "channel",
            unique=True,
            postgresql_where=text("is_default = true"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # The channel account is the OpenClaw bot identity; peer_id is the target chat.
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    peer_id: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'active'"),
        nullable=False,
    )
    receive_notifications: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("true"),
        nullable=False,
    )
    allow_openclaw: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        nullable=False,
    )
    allow_agentos: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        nullable=False,
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        nullable=False,
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChannelBindingFlow(Base):
    """Short-lived state for deterministic external-channel bind/unbind commands."""

    __tablename__ = "channel_binding_flows"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('openclaw-weixin')",
            name="ck_channel_binding_flows_channel",
        ),
        CheckConstraint(
            "action IN ('bind', 'unbind')",
            name="ck_channel_binding_flows_action",
        ),
        CheckConstraint(
            "step IN ("
            "'awaiting_handle', 'awaiting_code', 'awaiting_target', "
            "'awaiting_unbind_confirmation'"
            ")",
            name="ck_channel_binding_flows_step",
        ),
        UniqueConstraint(
            "channel",
            "account_id",
            "peer_id",
            name="uq_channel_binding_flows_endpoint",
        ),
        Index(
            "ix_channel_binding_flows_expires_at",
            "expires_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    peer_id: Mapped[str] = mapped_column(String(512), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    candidate_handle: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChannelBindingInvite(Base):
    """One-time, hashed code issued for a user/channel pairing."""

    __tablename__ = "channel_binding_invites"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('openclaw-weixin')",
            name="ck_channel_binding_invites_channel",
        ),
        Index(
            "ix_channel_binding_invites_user_channel",
            "user_id",
            "channel",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ChannelBindingEvent(Base):
    """Cached response for a control event retried by the external channel."""

    __tablename__ = "channel_binding_events"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('openclaw-weixin')",
            name="ck_channel_binding_events_channel",
        ),
        UniqueConstraint(
            "channel",
            "account_id",
            "peer_id",
            "event_id",
            name="uq_channel_binding_events_endpoint_event",
        ),
        Index(
            "ix_channel_binding_events_created_at",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    peer_id: Mapped[str] = mapped_column(String(512), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    handled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reply: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class OpsSession(Base):
    """Revocable ops-console session (env root subject; not a user_sessions row)."""

    __tablename__ = "ops_sessions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Agent(Base):
    """A selectable general-purpose or vertical agent."""

    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint("kind IN ('general', 'vertical')", name="ck_agents_kind"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_agents_status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        server_default=text("false"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'active'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AgentVersion(Base):
    """An immutable configuration revision for one Agent."""

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
        # Exactly one published revision is selectable for an Agent. Publishing is
        # an application workflow, but this partial index protects it from seed
        # jobs or concurrent admin writes reactivating an older revision.
        Index(
            "uq_agent_versions_one_published",
            "agent_id",
            unique=True,
            postgresql_where=text("is_published = true"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    system_prompt_overlay: Mapped[str] = mapped_column(Text, nullable=False)
    tool_policy_overrides: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    memory_enabled: Mapped[bool] = mapped_column(
        server_default=text("false"),
        nullable=False,
    )
    case_enabled: Mapped[bool] = mapped_column(
        server_default=text("false"),
        nullable=False,
    )
    # NULL = unrestricted (search every active KnowledgeBase, e.g. General);
    # a non-empty list scopes knowledge_search to just those slugs (verticals).
    knowledge_base_slugs: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    # Every Agent revision pins one externally hosted Provider.
    model_provider_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_providers.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    # Runtime tuning knobs; NULL = inherit the env default of the same name.
    memory_recall_top_k: Mapped[int | None] = mapped_column(Integer)
    memory_recall_max_chars: Mapped[int | None] = mapped_column(Integer)
    history_max_runs: Mapped[int | None] = mapped_column(Integer)
    agent_max_requests_per_run: Mapped[int | None] = mapped_column(Integer)
    is_published: Mapped[bool] = mapped_column(
        server_default=text("false"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ModelProvider(Base):
    """One externally hosted OpenAI-compatible chat endpoint.

    ``api_key`` is write-only through the API: responses only expose a masked
    preview.
    """

    __tablename__ = "model_providers"
    __table_args__ = (
        CheckConstraint(
            "api_mode IN ('chat_completions', 'responses')",
            name="ck_model_providers_api_mode",
        ),
        CheckConstraint(
            "reasoning_summary IS NULL OR reasoning_summary IN ('auto', 'concise', 'detailed')",
            name="ck_model_providers_reasoning_summary",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # OpenAI-compatible base URL ending in /v1.
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text)
    default_model: Mapped[str] = mapped_column(String(128), nullable=False)
    # Wire API shape: 'chat_completions' (default) or 'responses' (Codex-class
    # subscription gateways that only serve /responses).
    api_mode: Mapped[str] = mapped_column(
        String(24),
        server_default=text("'chat_completions'"),
        nullable=False,
    )
    # Responses-only readable reasoning summary; NULL keeps opaque reasoning.
    reasoning_summary: Mapped[str | None] = mapped_column(String(16))
    # Drives input budgeting; must match the endpoint model's real window.
    context_window: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL = fall back to settings.model_temperature.
    temperature: Mapped[float | None] = mapped_column(Float)
    max_concurrent_runs: Mapped[int] = mapped_column(Integer, nullable=False)
    supports_vision: Mapped[bool] = mapped_column(
        server_default=text("false"),
        nullable=False,
    )
    # Whether the endpoint's model accepts native tool calls; runs fail fast
    # (409) instead of dying on the provider's raw error mid-stream.
    supports_tools: Mapped[bool] = mapped_column(
        server_default=text("true"),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        server_default=text("true"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Case(Base):
    """A platform-generic subject archive owned by a user (not domain-specific)."""

    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_cases_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'active'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CaseMembership(Base):
    """Grants a user access to one Case with read or write permissions."""

    __tablename__ = "case_memberships"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'editor', 'viewer')",
            name="ck_case_memberships_role",
        ),
        UniqueConstraint("case_id", "user_id", name="uq_case_memberships_case_user"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class CaseFact(Base):
    """A stable fact about one Case (proposed until confirmed)."""

    __tablename__ = "case_facts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'confirmed', 'rejected', 'archived')",
            name="ck_case_facts_status",
        ),
        Index("ix_case_facts_case_status", "case_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    key: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("'{}'"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'proposed'"),
        nullable=False,
    )
    source_thread_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"),
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserAgentDefaultCase(Base):
    """Per-user default Case for one Agent (avoids cross-vertical default clashes)."""

    __tablename__ = "user_agent_default_cases"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "agent_id",
            name="uq_user_agent_default_cases_user_agent",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class KnowledgeBase(Base):
    """A curated public knowledge collection (not patient-private data)."""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_knowledge_bases_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'active'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class KnowledgeDocument(Base):
    """A source document inside one knowledge base."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('official_reference', 'clinical_guideline', 'curated_summary')",
            name="ck_knowledge_documents_source_kind",
        ),
        CheckConstraint(
            "review_status IN ('curated', 'clinically_reviewed', 'withdrawn')",
            name="ck_knowledge_documents_review_status",
        ),
        UniqueConstraint(
            "knowledge_base_id",
            "slug",
            name="uq_knowledge_documents_base_slug",
        ),
        CheckConstraint(
            "import_status IN ('ready', 'processing', 'failed')",
            name="ck_knowledge_documents_import_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_label: Mapped[str | None] = mapped_column(String(256))
    source_kind: Mapped[str] = mapped_column(
        String(32),
        server_default=text("'curated_summary'"),
        nullable=False,
    )
    source_date: Mapped[str | None] = mapped_column(String(32))
    version_label: Mapped[str | None] = mapped_column(String(128))
    review_status: Mapped[str] = mapped_column(
        String(24),
        server_default=text("'curated'"),
        nullable=False,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Async import lifecycle: 'ready' (default) → 'processing' while a background
    # import job runs → 'ready'/'failed'. Old chunks stay searchable during a
    # re-import; the final short transaction swaps them in.
    import_status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'ready'"),
        nullable=False,
    )
    import_error: Mapped[str | None] = mapped_column(Text)
    # Vision pages done/total while processing; NULL when not applicable.
    import_progress_done: Mapped[int | None] = mapped_column(Integer)
    import_progress_total: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class KnowledgeChunk(Base):
    """A tagged, searchable slice of a knowledge document."""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_knowledge_chunks_document_index",
        ),
        Index("ix_knowledge_chunks_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    section_label: Mapped[str | None] = mapped_column(String(256))
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("'{}'"),
        nullable=False,
    )
    # Dense vector as JSON array; null when embedding is off or failed.
    embedding: Mapped[list[float] | None] = mapped_column(JSONB)
    # Model that produced `embedding`; null for rows written before this column
    # existed. Compared against the configured model before use — cosine
    # similarity between vectors from different models is meaningless, not
    # merely inaccurate, and dimension alone won't catch that.
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class KnowledgeDocumentSnapshot(Base):
    """Point-in-time copy of a knowledge document and its chunks before overwrite."""

    __tablename__ = "knowledge_document_snapshots"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_label: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class UserMemory(Base):
    """A user or Case fact scoped to one Agent's memory policy.

    ``profile`` rows are keyed slots (height/weight/…) always injected.
    ``note`` rows are free-text facts retrieved by keyword + embedding hybrid.
    """

    __tablename__ = "user_memories"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="ck_user_memories_status",
        ),
        CheckConstraint(
            "kind IN ('profile', 'note')",
            name="ck_user_memories_kind",
        ),
        Index("ix_user_memories_user_agent_status", "user_id", "agent_id", "status"),
        Index(
            "ix_user_memories_user_agent_kind_status",
            "user_id",
            "agent_id",
            "kind",
            "status",
        ),
        Index(
            "ix_user_memories_user_agent_case_status",
            "user_id",
            "agent_id",
            "case_id",
            "status",
        ),
        Index(
            "uq_user_memories_active_global_profile_key",
            "user_id",
            "agent_id",
            "key",
            unique=True,
            postgresql_where=text(
                "kind = 'profile' AND status = 'active' AND key IS NOT NULL AND case_id IS NULL",
            ),
        ),
        Index(
            "uq_user_memories_active_case_profile_key",
            "user_id",
            "agent_id",
            "case_id",
            "key",
            unique=True,
            postgresql_where=text(
                "kind = 'profile' AND status = 'active' AND key IS NOT NULL "
                "AND case_id IS NOT NULL",
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="SET NULL"),
        index=True,
    )
    source_thread_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"),
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"),
    )
    kind: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'note'"),
        nullable=False,
    )
    # Profile slot id such as height_cm / weight_kg; null for free-text notes.
    key: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        server_default=text("'{}'"),
        nullable=False,
    )
    # Dense vector as JSON array; null when embedding is off or failed.
    embedding: Mapped[list[float] | None] = mapped_column(JSONB)
    # Model that produced `embedding`; null for rows written before this column
    # existed. Compared against the configured model before use — cosine
    # similarity between vectors from different models is meaningless, not
    # merely inaccurate, and dimension alone won't catch that.
    embedding_model: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'active'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Thread(Base):
    """A durable conversation container owned by one authenticated user."""

    __tablename__ = "threads"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # Nullable only for pre-authentication development records; application code never creates
    # an ownerless Thread and never returns legacy ownerless records to a signed-in user.
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id"),
        index=True,
        nullable=False,
    )
    case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="SET NULL"),
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255))
    is_pinned: Mapped[bool] = mapped_column(
        Boolean,
        server_default=text("false"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ScheduledTask(Base):
    """A durable user-owned prompt that can create Runs on a calendar schedule."""

    __tablename__ = "scheduled_tasks"
    __table_args__ = (
        CheckConstraint(
            "schedule_type IN ('once', 'daily', 'weekly', 'monthly')",
            name="ck_scheduled_tasks_schedule_type",
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'completed')",
            name="ck_scheduled_tasks_status",
        ),
        CheckConstraint(
            "last_run_status IS NULL OR last_run_status IN "
            "('queued', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')",
            name="ck_scheduled_tasks_last_run_status",
        ),
        UniqueConstraint("thread_id", name="uq_scheduled_tasks_thread_id"),
        Index("ix_scheduled_tasks_due", "status", "next_run_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="SET NULL"),
        index=True,
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"),
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Local calendar values are kept as JSON so future schedule forms can evolve
    # without changing the UTC dispatch index or losing the original time zone.
    schedule_config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'active'"),
        nullable=False,
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_status: Mapped[str | None] = mapped_column(String(24))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        server_default=text("0"),
        nullable=False,
    )
    result_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    notification_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    notification_channel: Mapped[str | None] = mapped_column(String(64))
    notification_binding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_channel_bindings.id", ondelete="SET NULL"), index=True
    )
    notification_last_status: Mapped[str | None] = mapped_column(String(24))
    notification_last_error_code: Mapped[str | None] = mapped_column(String(64))
    notification_last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScheduledTaskNotification(Base):
    """Durable at-least-once delivery record for one scheduled Run/channel."""

    __tablename__ = "scheduled_task_notifications"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('openclaw-weixin')",
            name="ck_scheduled_task_notifications_channel",
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'retrying', 'delivered', 'skipped', "
            "'failed', 'unknown')",
            name="ck_scheduled_task_notifications_status",
        ),
        UniqueConstraint("run_id", "channel", name="uq_scheduled_task_notifications_run_channel"),
        Index("ix_scheduled_task_notifications_due", "status", "next_attempt_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    delivery_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("scheduled_tasks.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    binding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_channel_bindings.id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    peer_id: Mapped[str] = mapped_column(String(512), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), server_default=sa_text("'pending'"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, server_default=sa_text("0"), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    openclaw_message_id: Mapped[str | None] = mapped_column(String(255))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Message(Base):
    """An ordered user, assistant, system, or tool message within one thread."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="ck_messages_role",
        ),
        UniqueConstraint("thread_id", "seq", name="uq_messages_thread_id_seq"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Run(Base):
    """One agent execution associated with a thread and selected model."""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            (
                "status IN ('queued', 'running', 'waiting_approval', "
                "'completed', 'failed', 'cancelled')"
            ),
            name="ck_runs_status",
        ),
        Index(
            "uq_runs_one_running_per_thread",
            "thread_id",
            unique=True,
            postgresql_where=text("status IN ('running', 'waiting_approval')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Snapshot the Thread's Case scope so downstream events and artifacts retain it.
    case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"),
        index=True,
    )
    scheduled_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scheduled_tasks.id", ondelete="SET NULL"),
        index=True,
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    model_request_count: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunMessageHistory(Base):
    """Pydantic AI message snapshot for a Run (completed or paused for HITL resume)."""

    __tablename__ = "run_message_histories"

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    messages: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Interrupt(Base):
    """A deferred tool call waiting for (or resolved by) human approval."""

    __tablename__ = "interrupts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'timed_out', 'cancelled')",
            name="ck_interrupts_status",
        ),
        UniqueConstraint("run_id", "tool_call_id", name="uq_interrupts_run_tool_call"),
        Index("ix_interrupts_run_id", "run_id"),
        Index(
            "ix_interrupts_pending_expires",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_call_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_args: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class RunEvent(Base):
    """Append-only event emitted by one run; seq enables ordered replay later."""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_events_run_id_seq"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Artifact(Base):
    """Owner-scoped durable content blob (fetch bodies, later uploads/sandbox)."""

    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('fetch_url', 'upload', 'sandbox', 'other')",
            name="ck_artifacts_kind",
        ),
        CheckConstraint("content_chars >= 0", name="ck_artifacts_content_chars"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    case_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="SET NULL"),
        index=True,
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"),
        index=True,
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(
        String(128),
        server_default=text("'text/plain'"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    outline: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PlatformToolPolicy(Base):
    """Ops-managed platform tool policy; rows can only tighten the env baseline."""

    __tablename__ = "platform_tool_policies"
    __table_args__ = (
        CheckConstraint(
            "action IN ('ask', 'deny')",
            name="ck_platform_tool_policies_action",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    tool_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
