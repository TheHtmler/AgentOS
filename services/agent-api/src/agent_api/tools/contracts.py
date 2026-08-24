"""Documented JSON payload contracts for built-in tool results."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy


def _object(properties: Mapping[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }


def _array(item_schema: Mapping[str, object]) -> dict[str, object]:
    return {"type": "array", "items": item_schema}


_STRING = {"type": "string"}
_NUMBER = {"type": "number"}
_BOOLEAN = {"type": "boolean"}
_ERROR_FIELDS = {"error": _STRING, "code": _STRING}


_OUTPUT_SCHEMAS: dict[str, dict[str, object]] = {
    "web_search": _object(
        {
            "provider": _STRING,
            "query": _STRING,
            "results": _array(
                _object(
                    {
                        "title": _STRING,
                        "url": _STRING,
                        "snippet": _STRING,
                        "published_at": {"type": ["string", "null"]},
                    },
                ),
            ),
            **_ERROR_FIELDS,
        },
    ),
    "fetch_url": _object(
        {
            "provider": _STRING,
            "url": _STRING,
            "title": _STRING,
            "outline": _STRING,
            "text": _STRING,
            "truncated": _BOOLEAN,
            "total_chars": {"type": "integer"},
            "artifact_id": {"type": ["string", "null"]},
            "next_offset": {"type": ["integer", "null"]},
            "hint": _STRING,
            **_ERROR_FIELDS,
        },
    ),
    "read_artifact": _object(
        {
            "artifact_id": _STRING,
            "title": _STRING,
            "source_url": {"type": ["string", "null"]},
            "offset": {"type": "integer"},
            "text": _STRING,
            "truncated": _BOOLEAN,
            "total_chars": {"type": "integer"},
            "next_offset": {"type": ["integer", "null"]},
            **_ERROR_FIELDS,
        },
    ),
    "growth_assess": _object(
        {
            "standard": _STRING,
            "standard_version": _STRING,
            "source_url": _STRING,
            "sex": _STRING,
            "age_months": _NUMBER,
            "age_days": {"type": ["number", "null"]},
            "height_cm": {"type": ["number", "null"]},
            "weight_kg": {"type": ["number", "null"]},
            "indicators": _array(
                _object(
                    {
                        "indicator": _STRING,
                        "z_score": _NUMBER,
                        "percentile": _NUMBER,
                    },
                ),
            ),
            "warnings": _array(_STRING),
            "errors": _array(_STRING),
            "note": _STRING,
            **_ERROR_FIELDS,
        },
    ),
    "time_diff": _object(
        {
            "ok": _BOOLEAN,
            "start": _STRING,
            "end": _STRING,
            "timezone": _STRING,
            "delta": _object(
                {
                    "days": _NUMBER,
                    "hours": _NUMBER,
                    "minutes": _NUMBER,
                    "months": {"type": "integer"},
                    "years": {"type": "integer"},
                },
            ),
            "error_code": _STRING,
            "message": _STRING,
        },
    ),
    "calculate": _object(
        {
            "ok": _BOOLEAN,
            "expression": _STRING,
            "result": {"type": ["integer", "number"]},
            "result_type": _STRING,
            "error_code": _STRING,
            "message": _STRING,
        },
    ),
    "knowledge_search": _object(
        {
            "query": _STRING,
            "disease_tags": _array(_STRING),
            "count": {"type": "integer"},
            "results": _array(
                _object(
                    {
                        "chunk_id": _STRING,
                        "title": _STRING,
                        "content": _STRING,
                        "tags": _array(_STRING),
                        "source_url": {"type": ["string", "null"]},
                        "source_label": {"type": ["string", "null"]},
                        "source_kind": _STRING,
                        "source_date": {"type": ["string", "null"]},
                        "version_label": {"type": ["string", "null"]},
                        "score": _NUMBER,
                    },
                ),
            ),
            "embedding_used": _BOOLEAN,
            "note": _STRING,
            **_ERROR_FIELDS,
        },
    ),
    "case_context_read": _object(
        {
            "case_id": _STRING,
            "current_count": {"type": "integer"},
            "current": _array(
                _object(
                    {
                        "key": _STRING,
                        "content": _STRING,
                        "tags": _array(_STRING),
                        "status": _STRING,
                        "recorded_at": _STRING,
                    },
                ),
            ),
            "history_count": {"type": "integer"},
            "history": _array(
                _object(
                    {
                        "key": _STRING,
                        "content": _STRING,
                        "tags": _array(_STRING),
                        "status": _STRING,
                        "recorded_at": _STRING,
                    },
                ),
            ),
            "note": _STRING,
            **_ERROR_FIELDS,
        },
    ),
    "case_attribution_confirm": _object(
        {
            "written": {"type": "integer"},
            "case_id": _STRING,
            "status": _STRING,
            **_ERROR_FIELDS,
        },
    ),
    "case_slot_collect": _object(
        {
            "written": {"type": "integer"},
            "keys": _array(_STRING),
            "missing": _array(_STRING),
            "fields": _array(
                _object(
                    {
                        "key": _STRING,
                        "label": _STRING,
                        "unit": _STRING,
                        "reason": _STRING,
                    },
                ),
            ),
            "case_id": _STRING,
            "status": _STRING,
            **_ERROR_FIELDS,
        },
    ),
    "sandbox_exec": _object(
        {
            "ok": _BOOLEAN,
            "exit_code": {"type": ["integer", "null"]},
            "timed_out": _BOOLEAN,
            "stdout": _STRING,
            "stderr": _STRING,
            "output_preview": _STRING,
            "output_truncated": _BOOLEAN,
            "output_artifact_id": {"type": ["string", "null"]},
            "duration_ms": {"type": "integer"},
            "files": _array(
                _object(
                    {
                        "path": _STRING,
                        "size": {"type": "integer"},
                        "mime_type": _STRING,
                    },
                ),
            ),
            "error": _STRING,
            "code": _STRING,
        },
    ),
}

_DEFAULT_OUTPUT_SCHEMA = _object(
    {
        "result": {"description": "JSON-decoded tool result"},
        **_ERROR_FIELDS,
    },
)

_OUTPUT_DESCRIPTIONS: dict[str, str] = {
    "web_search": "搜索结果数组；失败时返回 error。",
    "fetch_url": "网页正文和截断/Artifact 信息；失败时返回 error。",
    "read_artifact": "Artifact 的分页文本窗口；失败时返回 error。",
    "growth_assess": "生长指标、百分位、警告和错误列表。",
    "time_diff": "签名时间差 delta，或 error_code/message。",
    "calculate": "计算结果 result，或 error_code/message。",
    "knowledge_search": "策展知识切片及来源字段；失败时返回 error。",
    "case_context_read": "当前和历史 Case facts；失败时返回 error。",
    "case_attribution_confirm": "HITL 批准后的写入数量和状态。",
    "case_slot_collect": "HITL 表单写入的字段、缺失字段和状态。",
    "sandbox_exec": "命令退出状态、stdout/stderr、生成文件元数据和可选 Artifact 引用。",
}


def get_output_schema(name: str) -> dict[str, object]:
    """Return a copy so API serialization cannot mutate the registry contract."""

    return deepcopy(_OUTPUT_SCHEMAS.get(name, _DEFAULT_OUTPUT_SCHEMA))


def get_output_description(name: str) -> str:
    return _OUTPUT_DESCRIPTIONS.get(name, "工具返回 JSON 对象；具体字段由工具实现决定。")
