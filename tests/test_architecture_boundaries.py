from __future__ import annotations

import ast
import importlib
from pathlib import Path
import tomllib

from core.app_service import AppService
from infrastructure.contracts import (
    COMPATIBILITY_MODULES,
    PACKAGE_DEPENDENCIES,
    PUBLIC_ENTRY_POINTS,
)


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_PACKAGES = tuple(PACKAGE_DEPENDENCIES)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _production_files(package: str) -> list[Path]:
    compatibility = {name.replace(".", "/") + ".py" for name in COMPATIBILITY_MODULES}
    return [
        path
        for path in (ROOT / package).rglob("*.py")
        if path.relative_to(ROOT).as_posix() not in compatibility
    ]


def test_active_packages_follow_documented_dependency_direction():
    violations: list[str] = []
    for package in ACTIVE_PACKAGES:
        allowed = PACKAGE_DEPENDENCIES[package]
        for path in _production_files(package):
            for imported in _imports(path):
                imported_root = imported.split(".", 1)[0]
                if imported_root in ACTIVE_PACKAGES and imported_root not in allowed:
                    violations.append(
                        f"{path.relative_to(ROOT)} imports disallowed package {imported_root}"
                    )
    assert violations == []


def test_active_packages_do_not_import_compatibility_modules():
    violations: list[str] = []
    for package in ACTIVE_PACKAGES:
        for path in _production_files(package):
            for imported in _imports(path):
                if imported in COMPATIBILITY_MODULES:
                    violations.append(
                        f"{path.relative_to(ROOT)} imports compatibility module {imported}"
                    )
    assert violations == []


def test_public_entry_points_are_importable():
    for target in PUBLIC_ENTRY_POINTS.values():
        module_name, attribute_path = target.split(":", 1)
        value = importlib.import_module(module_name)
        for attribute in attribute_path.split("."):
            value = getattr(value, attribute)
        assert value is not None


def test_project_metadata_pins_runtime_and_development_dependencies():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = list(metadata["project"]["dependencies"])
    dependencies.extend(metadata["project"]["optional-dependencies"]["dev"])
    dependencies.extend(metadata["project"]["optional-dependencies"]["ai"])

    assert dependencies
    assert all("==" in dependency for dependency in dependencies)
    assert metadata["project"]["scripts"]["sqlsense"] == "main:main"


def test_active_app_pipeline_has_no_sql_service_dependency():
    service = AppService()
    assert service.query_pipeline.question_service is None


def test_sql_handoff_rejects_missing_pipeline_contract():
    service = AppService().question_service
    success, message, sql, error = service.generate_from_pipeline(
        question="show records",
        knowledge_base={},
        pipeline_context={},
    )
    assert (success, sql, error) == (False, None, None)
    assert "invalid handoff contract" in message.lower()
