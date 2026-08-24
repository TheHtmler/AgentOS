"""Tool detail schemas stay available for every builtin registration."""

from pydantic_ai import Tool

from agent_api.tools.contracts import get_output_schema
from agent_api.tools.registry import iter_builtin_specs


def test_builtin_input_schemas_are_generated_from_registered_handlers() -> None:
    for spec in iter_builtin_specs():
        schema = Tool(spec.handler, name=spec.name).function_schema.json_schema
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties"), dict)


def test_builtin_output_contracts_are_json_objects() -> None:
    for spec in iter_builtin_specs():
        schema = get_output_schema(spec.name)
        assert schema["type"] == "object"
        assert isinstance(schema.get("properties"), dict)


def test_output_schema_is_returned_as_a_copy() -> None:
    first = get_output_schema("web_search")
    first["properties"] = {}

    second = get_output_schema("web_search")
    properties = second.get("properties")
    assert isinstance(properties, dict)
    assert "results" in properties
