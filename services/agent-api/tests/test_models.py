from agent_api.db.base import Base
from agent_api.db.models import Message, Run, RunEvent, RunMessageHistory, Thread


def test_core_model_tables_are_registered() -> None:
    """Ensure Alembic can discover every persistence table."""

    assert {
        Thread.__tablename__,
        Message.__tablename__,
        Run.__tablename__,
        RunEvent.__tablename__,
        RunMessageHistory.__tablename__,
    } == {
        "threads",
        "messages",
        "runs",
        "run_events",
        "run_message_histories",
    }
    assert set(Base.metadata.tables) == {
        "threads",
        "messages",
        "runs",
        "run_events",
        "run_message_histories",
    }
