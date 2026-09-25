"""Enforce the clean architecture dependency rule: inner layers never import outer ones."""

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "gradeflow"

AWS_MODULES = {"boto3", "botocore", "aws_lambda_powertools"}

# layer -> top-level modules / gradeflow sub-packages it must never import
FORBIDDEN = {
    "domain": AWS_MODULES
    | {"gradeflow.application", "gradeflow.infrastructure", "gradeflow.entrypoints"},
    "application": AWS_MODULES | {"gradeflow.infrastructure", "gradeflow.entrypoints"},
}


def _imported_modules(source: str) -> set[str]:
    """Return the full dotted names of all absolute imports in a Python source file."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def _violates(module: str, forbidden: set[str]) -> bool:
    return any(module == name or module.startswith(name + ".") for name in forbidden)


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_does_not_import_outer_layers(layer: str) -> None:
    violations = [
        f"{path.relative_to(PACKAGE_ROOT)} imports {module}"
        for path in (PACKAGE_ROOT / layer).rglob("*.py")
        for module in _imported_modules(path.read_text())
        if _violates(module, FORBIDDEN[layer])
    ]
    assert not violations, "Dependency rule broken:\n" + "\n".join(violations)


def test_violation_detection_works() -> None:
    """Sanity check that the guard itself catches a bad import."""
    modules = _imported_modules("import boto3\nfrom gradeflow.infrastructure.s3 import X\n")
    assert _violates("boto3", FORBIDDEN["domain"])
    assert _violates("gradeflow.infrastructure.s3", FORBIDDEN["application"])
    assert modules == {"boto3", "gradeflow.infrastructure.s3"}
