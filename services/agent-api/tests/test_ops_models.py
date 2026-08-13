from agent_api.db.models import KnowledgeDocumentSnapshot, OpsSession


def test_ops_models_are_mapped() -> None:
    assert OpsSession.__tablename__ == "ops_sessions"
    assert KnowledgeDocumentSnapshot.__tablename__ == "knowledge_document_snapshots"
