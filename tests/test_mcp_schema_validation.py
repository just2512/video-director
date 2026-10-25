from video_director.mcp_schema import validate_json_schema


def test_schema_validator_checks_required_fields_and_additional_properties():
    schema = {
        "type": "object",
        "properties": {"project_id": {"type": "string", "minLength": 1}},
        "required": ["project_id"],
        "additionalProperties": False,
    }
    assert "project_id" in validate_json_schema({}, schema)
    assert "unexpected" in validate_json_schema({"project_id": "demo", "unexpected": 1}, schema)
    assert validate_json_schema({"project_id": "demo"}, schema) is None


def test_schema_validator_checks_numeric_bounds_enums_and_finite_numbers():
    number_schema = {"type": "number", "minimum": 0.05, "maximum": 1}
    assert "type" in validate_json_schema("wide", number_schema)
    assert "at most 1" in validate_json_schema(2, number_schema)
    assert "finite" in validate_json_schema(float("inf"), number_schema)
    assert validate_json_schema(0.5, number_schema) is None
    assert "one of" in validate_json_schema("unknown", {"type": "string", "enum": ["a", "b"]})


def test_schema_validator_checks_nested_one_of_and_any_of_contracts():
    schema = {
        "type": "object",
        "properties": {
            "script": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "object", "additionalProperties": True},
                ]
            },
            "payload": {
                "type": "object",
                "anyOf": [
                    {"required": ["brief"]},
                    {"required": ["script"]},
                ],
            },
        },
        "additionalProperties": False,
    }
    assert validate_json_schema({"script": "hello", "payload": {"brief": "x"}}, schema) is None
    assert "exactly one" in validate_json_schema({"script": 7, "payload": {"brief": "x"}}, schema)
    assert "at least one" in validate_json_schema({"payload": {}}, schema)


def test_schema_validator_checks_array_item_types_and_combined_bounds():
    schema = {
        "type": "array",
        "minItems": 1,
        "maxItems": 3,
        "items": {"type": "number", "minimum": 0},
    }
    assert "1–3" in validate_json_schema([], schema)
    assert "1–3" in validate_json_schema([1, 2, 3, 4], schema)
    assert "[0]" in validate_json_schema([True], schema)
    assert validate_json_schema([0.1, 1, 2], schema) is None
