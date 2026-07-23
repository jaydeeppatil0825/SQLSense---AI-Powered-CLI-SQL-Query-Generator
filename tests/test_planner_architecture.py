import ast
import importlib
from pathlib import Path


PLANNER_ROOT = Path("query_pipeline/planner")
ORCHESTRATOR_MODULE = "query_pipeline.query_planner"


def _imports_query_planner(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == ORCHESTRATOR_MODULE:
                    hits.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in {ORCHESTRATOR_MODULE, "query_pipeline"}:
                imported = {alias.name for alias in node.names}
                if module == ORCHESTRATOR_MODULE or "query_planner" in imported:
                    hits.append((node.lineno, module))
    return hits


def test_planner_resolvers_do_not_import_query_planner():
    resolver_files = [
        path
        for path in PLANNER_ROOT.glob("*.py")
        if path.name
        not in {
            "__init__.py",
            "legacy_planner.py",
        }
    ]
    offenders = {
        str(path): _imports_query_planner(path)
        for path in resolver_files
        if _imports_query_planner(path)
    }

    assert offenders == {}


def test_neutral_query_predicates_do_not_import_orchestrator():
    assert _imports_query_planner(PLANNER_ROOT / "query_predicates.py") == []


def test_planner_modules_import_independently():
    modules = [
        "query_pipeline.query_planner",
        "query_pipeline.planner.join_resolver",
        "query_pipeline.planner.phase7_bfs_join_resolver",
        "query_pipeline.planner.filter_resolver",
        "query_pipeline.planner.role_resolver",
        "query_pipeline.planner.ranking_resolver",
        "query_pipeline.planner.contract_builder",
        "query_pipeline.planner.query_predicates",
    ]

    for module_name in modules:
        importlib.import_module(module_name)
