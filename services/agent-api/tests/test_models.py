from agent_api.db.base import Base
from agent_api.db.models import (
    AuthToken,
    Message,
    Run,
    RunEvent,
    RunMessageHistory,
    Thread,
    User,
    UserSession,
)


def test_core_model_tables_are_registered() -> None:
    """Ensure Alembic can discover every persistence table."""

    expected_tables = {
        "users",
        "auth_tokens",
        "user_sessions",
        "threads",
        "messages",
        "runs",
        "run_events",
        "run_message_histories",
    }

    assert {
        User.__tablename__,
        AuthToken.__tablename__,
        UserSession.__tablename__,
        Thread.__tablename__,
        Message.__tablename__,
        Run.__tablename__,
        RunEvent.__tablename__,
        RunMessageHistory.__tablename__,
    } == expected_tables
    assert set(Base.metadata.tables) == expected_tables
