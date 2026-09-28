import ast
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "review_loop"

ALLOWED = {
    "engine": {"review_loop.engine", "review_loop.types"},
    "types": {"review_loop.types"},
    "repositories": {"review_loop.repositories", "review_loop.types"},
    "services": {"review_loop.services", "review_loop.engine", "review_loop.types", "review_loop.repositories", "review_loop.config"},
    "adapters": {"review_loop.adapters", "review_loop.types", "review_loop.engine"},
    "config": {"review_loop.config", "review_loop.types"},
}


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_each_layer_imports_only_the_layers_the_plan_allows():
    violations = []
    for layer, allowed in ALLOWED.items():
        for path in (PACKAGE / layer).rglob("*.py"):
            for name in imports_of(path):
                if not name.startswith("review_loop"):
                    continue
                if not any(name == prefix or name.startswith(prefix + ".") for prefix in allowed):
                    violations.append(f"{path.relative_to(PACKAGE)} imports {name}")
    assert violations == []


def test_nothing_imports_the_cli_layer():
    offenders = [str(path.relative_to(PACKAGE)) for path in PACKAGE.rglob("*.py")
                 if "cli" not in path.parts and any(name.startswith("review_loop.cli") for name in imports_of(path))]
    assert offenders == []


def test_engine_modules_touch_no_io_modules():
    forbidden = {"subprocess", "sqlite3", "socket", "urllib", "http", "os", "shutil"}
    offenders = [f"{path.name} imports {name}" for path in (PACKAGE / "engine").glob("*.py")
                 for name in imports_of(path) if name.split(".")[0] in forbidden]
    assert offenders == []
