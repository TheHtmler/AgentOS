from agent_api.db.base import Base
from agent_api.db.models import (
    Agent,
    AgentVersion,
    Artifact,
    AuthToken,
    Case,
    CaseFact,
    CaseMembership,
    Interrupt,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    Message,
    Run,
    RunEvent,
    RunMessageHistory,
    Thread,
    User,
    UserAgentDefaultCase,
    UserMemory,
    UserSession,
)


def test_core_model_tables_are_registered() -> None:
    """Ensure Alembic can discover every persistence table."""

    expected_tables = {
        "users",
        "auth_tokens",
        "user_sessions",
        "agents",
        "agent_versions",
        "artifacts",
        "cases",
        "case_memberships",
        "case_facts",
        "user_agent_default_cases",
        "user_memories",
        "knowledge_bases",
        "knowledge_documents",
        "knowledge_chunks",
        "threads",
        "messages",
        "runs",
        "run_events",
        "run_message_histories",
        "interrupts",
    }

    assert {
        User.__tablename__,
        AuthToken.__tablename__,
        UserSession.__tablename__,
        Agent.__tablename__,
        AgentVersion.__tablename__,
        Artifact.__tablename__,
        Case.__tablename__,
        CaseMembership.__tablename__,
        CaseFact.__tablename__,
        UserAgentDefaultCase.__tablename__,
        UserMemory.__tablename__,
        KnowledgeBase.__tablename__,
        KnowledgeDocument.__tablename__,
        KnowledgeChunk.__tablename__,
        Thread.__tablename__,
        Message.__tablename__,
        Run.__tablename__,
        RunEvent.__tablename__,
        RunMessageHistory.__tablename__,
        Interrupt.__tablename__,
    } == expected_tables
    assert set(Base.metadata.tables) == expected_tables
