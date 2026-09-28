import json
from pathlib import Path

import pytest

SCHEMAS = sorted((Path(__file__).resolve().parents[2] / "review_loop" / "schemas").glob("*.json"))


def objects_in(node, path="$"):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield path, node
        for key, child in node.items():
            yield from objects_in(child, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from objects_in(child, f"{path}[{index}]")


@pytest.mark.parametrize("schema_path", SCHEMAS, ids=[p.name for p in SCHEMAS])
def test_every_object_is_strict_so_both_clis_accept_the_same_file(schema_path):
    schema = json.loads(schema_path.read_text())

    assert schema.get("$schema") == "http://json-schema.org/draft-07/schema#"
    for path, node in objects_in(schema):
        assert node.get("additionalProperties") is False, f"{schema_path.name} {path} allows extra properties"
        assert sorted(node.get("required", [])) == sorted(node.get("properties", {})), f"{schema_path.name} {path} has optional properties"


def test_the_five_phase_schemas_exist():
    assert [p.stem for p in SCHEMAS] == ["alignment", "arbitration", "assessment", "fix", "review"]
