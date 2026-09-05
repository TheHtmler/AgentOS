"""Pure-function tests for knowledge hybrid scoring without service dependencies."""

from agent_api.tools.knowledge.tool import score_knowledge_hit


def test_keyword_hit_without_embeddings() -> None:
    score = score_knowledge_hit(
        content="急性失代偿时可出现呕吐嗜睡",
        title="急症识别",
        tags=["isolated_mma"],
        tokens=["失代偿", "呕吐"],
        disease_tags=[],
        query_embedding=None,
        chunk_embedding=None,
    )
    assert score is not None
    assert score >= 6


def test_vector_rescues_synonym_miss() -> None:
    # Same direction vectors → high cosine; no shared keyword tokens.
    query_vec = [1.0, 0.0, 0.0]
    chunk_vec = [0.95, 0.05, 0.0]
    score = score_knowledge_hit(
        content="新生儿筛查 C3 升高需结合血氨与尿有机酸",
        title="NBS 解读",
        tags=["nbs", "isolated_mma"],
        tokens=["代谢危机", "昏迷"],  # not present in content
        disease_tags=[],
        query_embedding=query_vec,
        chunk_embedding=chunk_vec,
    )
    assert score is not None
    assert score >= 2.8  # ~0.28 * 10


def test_low_vector_and_no_keyword_dropped() -> None:
    score = score_knowledge_hit(
        content="日常饮食蛋白控制原则",
        title="饮食教育",
        tags=["diet"],
        tokens=["发热", "嗜睡"],
        disease_tags=[],
        query_embedding=[1.0, 0.0, 0.0],
        chunk_embedding=[0.0, 1.0, 0.0],  # orthogonal → cosine 0
    )
    assert score is None
