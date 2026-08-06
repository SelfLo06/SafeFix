import ast
import re
import sys
import tomllib
from pathlib import Path

from ..paths import normalize_rel_path
from .models import GeneratedTestCandidate, RuleViolation


_ALLOWED_EXCEPTION_NAMES = {
    "AssertionError",
    "EOFError",
    "FileNotFoundError",
    "IndexError",
    "KeyError",
    "LookupError",
    "NotImplementedError",
    "OSError",
    "RuntimeError",
    "StopIteration",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
}
_NONDETERMINISTIC_IMPORTS = {
    "asyncio",
    "datetime",
    "ftplib",
    "http",
    "httpx",
    "random",
    "requests",
    "secrets",
    "smtplib",
    "socket",
    "ssl",
    "subprocess",
    "time",
    "urllib",
    "uuid",
}
_SNAPSHOT_IMPORTS = {"inline_snapshot", "snapshottest", "syrupy"}
_MOCK_CALLS = {
    "Mock",
    "MagicMock",
    "AsyncMock",
    "PropertyMock",
    "patch",
    "patch.object",
    "patch.dict",
    "create_autospec",
}
_WRITE_METHODS = {"write_text", "write_bytes", "unlink", "remove", "rename", "replace"}
_PERFORMANCE_NAMES = {"benchmark", "duration", "elapsed", "latency", "runtime"}


def validate_candidate(
    candidate: GeneratedTestCandidate, project_root: Path
) -> tuple[RuleViolation, ...]:
    """Return deterministic static violations without invoking a model."""
    violations: dict[str, str] = {}

    def add(code: str, message: str) -> None:
        violations.setdefault(code, message)

    if not isinstance(candidate.basis, str) or not candidate.basis.strip():
        add("missing_basis", "candidate basis must explain observable behavior")
    if not candidate.sources:
        add("missing_source_reference", "candidate must cite at least one source")
    if candidate.touched_existing_tests:
        add("existing_test_write", "candidate must not touch existing tests")

    root = project_root.resolve()
    for source in candidate.sources:
        try:
            resolved = normalize_rel_path(root, source)
        except ValueError:
            add("source_path_escape", f"source reference escapes project root: {source}")
            continue
        if not resolved.is_file():
            add("source_not_found", f"source reference does not name a file: {source}")

    if not isinstance(candidate.test_source, str) or not candidate.test_source.strip():
        add("invalid_test_source", "candidate test_source must be non-empty Python")
        return _ordered(violations)
    try:
        tree = ast.parse(candidate.test_source)
    except SyntaxError as exc:
        add("invalid_test_source", f"candidate test_source is not valid Python: {exc.msg}")
        return _ordered(violations)

    if not _contains_test(tree):
        add("missing_test", "candidate source must define a pytest test")
    if _has_private_reference(tree):
        add("private_implementation", "candidate must assert public behavior, not private details")
    if _has_unsupported_exception(tree):
        add("unsupported_exception", "candidate guesses an unsupported exception contract")
    if _has_nondeterminism(tree):
        add("nondeterministic_behavior", "candidate must not depend on network, time, or randomness")
    if _has_performance_threshold(tree):
        add("performance_threshold", "candidate must not assert a performance threshold")
    if _has_complex_snapshot(tree):
        add("complex_snapshot", "candidate must use simple value assertions, not snapshots")
    if _mock_count(tree) > 2:
        add("excessive_mocking", "candidate uses more than two mock operations")
    for module in _undeclared_imports(tree, root):
        add("undeclared_import", f"candidate imports undeclared dependency: {module}")
    if _has_source_write(tree):
        add("non_test_source_edit", "candidate must not write production or existing test files")

    return _ordered(violations)


def _ordered(violations: dict[str, str]) -> tuple[RuleViolation, ...]:
    return tuple(RuleViolation(code, violations[code]) for code in sorted(violations))


def _contains_test(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            return True
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            if any(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name.startswith("test_")
                for child in node.body
            ):
                return True
    return False


def _has_private_reference(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return True
        if isinstance(node, ast.Name) and node.id.startswith("_") and node.id != "_":
            return True
    return False


def _has_unsupported_exception(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "raises" or not node.args:
            continue
        exception_names = [
            item.id
            for item in ast.walk(node.args[0])
            if isinstance(item, ast.Name)
        ]
        if any(name not in _ALLOWED_EXCEPTION_NAMES for name in exception_names):
            return True
    return False


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _has_nondeterminism(tree: ast.AST) -> bool:
    if _import_roots(tree) & _NONDETERMINISTIC_IMPORTS:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"now", "today", "utcnow", "time", "random", "randint", "get"}:
                root = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
                if root in _NONDETERMINISTIC_IMPORTS:
                    return True
    return False


def _has_performance_threshold(tree: ast.AST) -> bool:
    if _import_roots(tree) & {"timeit", "pytest_benchmark", "benchmark"}:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"perf_counter", "process_time", "timeit", "pedantic"}:
                return True
        if isinstance(node, ast.Compare):
            names = {
                child.id.lower()
                for child in ast.walk(node.left)
                if isinstance(child, ast.Name)
            }
            if names & _PERFORMANCE_NAMES:
                return True
    return False


def _has_complex_snapshot(tree: ast.AST) -> bool:
    if _import_roots(tree) & _SNAPSHOT_IMPORTS:
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and "snapshot" in node.id.lower():
            return True
        if isinstance(node, ast.Attribute) and "snapshot" in node.attr.lower():
            return True
    return False


def _mock_count(tree: ast.AST) -> int:
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in _MOCK_CALLS:
            count += 1
        elif isinstance(node.func, ast.Attribute):
            dotted = _dotted_name(node.func)
            if dotted in _MOCK_CALLS or node.func.attr in {"setattr", "setitem", "delattr", "delitem"}:
                count += 1
    return count


def _has_source_write(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in _WRITE_METHODS:
            return True
        if isinstance(node.func, ast.Name) and node.func.id in {"apply_patch", "system", "popen"}:
            return True
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                if re.search(r"[wax+]", node.args[1].value):
                    return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"copy", "move", "rmtree"}:
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "shutil":
                return True
    return False


def _undeclared_imports(tree: ast.AST, project_root: Path) -> tuple[str, ...]:
    allowed = set(getattr(sys, "stdlib_module_names", ()))
    allowed.add("pytest")
    allowed.update(_project_module_roots(project_root))
    allowed.update(_declared_dependencies(project_root))
    return tuple(sorted(module for module in _import_roots(tree) if module not in allowed))


def _project_module_roots(project_root: Path) -> set[str]:
    roots: set[str] = set()
    for path in project_root.rglob("*.py"):
        if any(part in {".git", ".venv", "venv", "__pycache__", "tests"} for part in path.parts):
            continue
        relative = path.relative_to(project_root)
        if relative.parts and relative.parts[0] == "src" and len(relative.parts) > 1:
            roots.add(relative.parts[1].split(".", 1)[0])
        elif relative.parts:
            roots.add(relative.parts[0].split(".", 1)[0])
    return roots


def _declared_dependencies(project_root: Path) -> set[str]:
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return set()
    document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    dependencies = document.get("project", {}).get("dependencies", [])
    result: set[str] = set()
    for dependency in dependencies:
        if isinstance(dependency, str):
            result.add(re.split(r"[<>=!~;\[ ]", dependency, maxsplit=1)[0].lower().replace("-", "_"))
    return result


def _dotted_name(node: ast.Attribute) -> str:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))
