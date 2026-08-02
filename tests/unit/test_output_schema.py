from __future__ import annotations

from orchestrator.codex_client import normalize_output_schema
from orchestrator.schemas import AgentHandoff, AgentPlan


def test_output_schemas_require_every_object_property() -> None:
    for model in (AgentPlan, AgentHandoff):
        original = model.model_json_schema()
        normalized = normalize_output_schema(original)

        assert normalized is not None
        assert normalized is not original
        assert original == model.model_json_schema()
        assert_strict_objects(normalized)


def assert_strict_objects(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            assert value.get("additionalProperties") is False
        properties = value.get("properties")
        if isinstance(properties, dict):
            assert value.get("required") == list(properties)
        for child in value.values():
            assert_strict_objects(child)
    elif isinstance(value, list):
        for child in value:
            assert_strict_objects(child)
