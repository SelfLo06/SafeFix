import ast
import re
import sys
import tomllib
from pathlib import Path, PureWindowsPath

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
_NONDETERMINISTIC_CALLS = {"os.urandom", "os.getrandom"}
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
    "unittest.mock.Mock",
    "unittest.mock.MagicMock",
    "unittest.mock.AsyncMock",
    "unittest.mock.PropertyMock",
    "unittest.mock.patch",
    "unittest.mock.patch.object",
    "unittest.mock.patch.dict",
    "unittest.mock.create_autospec",
}
_WRITE_METHODS = {"write_text", "write_bytes", "unlink", "rename", "rmdir"}
_PATH_WRITE_METHODS = {"replace", "touch"}
_PATH_MUTATION_METHODS = _WRITE_METHODS | _PATH_WRITE_METHODS
_PATH_PROBE_METHODS = {
    "read_text",
    "read_bytes",
    "exists",
    "open",
    "glob",
    "rglob",
    "iterdir",
    "is_file",
    "is_dir",
    "stat",
    "lstat",
    "absolute",
    "resolve",
    "expanduser",
    "as_uri",
    "cwd",
    "home",
    "mkdir",
    "chmod",
}
_PATH_METHODS = _PATH_MUTATION_METHODS | _PATH_PROBE_METHODS
_SOURCE_WRITE_CALLS = {
    "codecs.open",
    "io.open",
    "os.fdopen",
    "os.open",
    "os.system",
    "os.popen",
    "os.remove",
    "os.unlink",
    "os.rename",
    "os.replace",
    "shutil.copy",
    "shutil.copy2",
    "shutil.copyfile",
    "shutil.copytree",
    "shutil.move",
    "shutil.rmtree",
}
_PERFORMANCE_NAMES = {"benchmark", "duration", "elapsed", "latency", "runtime"}
_PERFORMANCE_CALL_NAMES = {
    "benchmark",
    "perf_counter",
    "process_time",
    "timeit",
    "pedantic",
}
_TRACKED_CALLABLE_NAMES = (
    _NONDETERMINISTIC_CALLS
    | _MOCK_CALLS
    | _SOURCE_WRITE_CALLS
    | _PERFORMANCE_CALL_NAMES
    | {
        "getattr",
        "builtins.getattr",
        "__import__",
        "import_module",
        "importlib.import_module",
        "builtins.open",
        "pathlib.Path",
        "pathlib.Path.open",
        "pathlib.Path.touch",
    }
)
_DYNAMIC_ATTR_HANDLED_BY_EXISTING_RULES = (
    {name.rsplit(".", 1)[-1] for name in _SOURCE_WRITE_CALLS}
    | {"open", "urandom", "getrandom"}
    | _MOCK_CALLS
    | _PERFORMANCE_CALL_NAMES
)
_DANGEROUS_GETATTR_ATTRIBUTES = {
    name.rsplit(".", 1)[-1] for name in _SOURCE_WRITE_CALLS
} | {
    "open",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
}
_UNSAFE_EXECUTION_CALLS = {
    "eval",
    "builtins.eval",
    "exec",
    "builtins.exec",
    "compile",
    "builtins.compile",
    "__import__",
    "builtins.__import__",
    "import_module",
    "importlib.import_module",
    "globals",
    "locals",
    "vars",
    "inspect.getattr_static",
    "operator.attrgetter",
    "operator.methodcaller",
}
_UNSAFE_PROCESS_PREFIXES = (
    "importlib.",
    "subprocess.",
    "multiprocessing.",
    "os.",
    "pty.",
)
_UNSAFE_FILESYSTEM_PREFIXES = (
    "builtins.open",
    "codecs.",
    "glob.",
    "io.",
    "pathlib.",
    "shutil.",
    "tempfile.",
)
_UNSAFE_IMPORT_ROOTS = {
    "builtins",
    "codecs",
    "ctypes",
    "fcntl",
    "glob",
    "importlib",
    "io",
    "mmap",
    "multiprocessing",
    "msvcrt",
    "os",
    "pathlib",
    "pty",
    "resource",
    "shutil",
    "signal",
    "socket",
    "subprocess",
    "sys",
    "tempfile",
    "winreg",
}
_UNSAFE_API_MESSAGE = "candidate must not use OS, process, or filesystem APIs"
_UNSAFE_PATH_MESSAGE = "candidate must not use absolute or dynamic filesystem paths"


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
    if _has_absolute_path_literal(tree):
        add("unsafe_execution", _UNSAFE_PATH_MESSAGE)
    has_source_write = _has_source_write(tree)
    if (
        _has_unsafe_import(tree)
        and not has_source_write
        and not _has_nondeterministic_call(tree)
    ):
        add("unsafe_execution", _UNSAFE_API_MESSAGE)
    for module in _undeclared_imports(tree, root):
        add("undeclared_import", f"candidate imports undeclared dependency: {module}")
    if has_source_write:
        add("non_test_source_edit", "candidate must not write production or existing test files")
    elif _has_unsafe_file_path(tree):
        add(
            "unsafe_execution",
            "candidate must not access paths outside the Harness-owned candidate project",
        )
    if not has_source_write and _has_unsafe_execution(tree):
        add("unsafe_execution", _UNSAFE_API_MESSAGE)

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
    module_aliases, callable_aliases = _import_bindings(tree)
    callable_aliases = _with_assigned_callable_aliases(
        tree, module_aliases, callable_aliases
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return True
        if isinstance(node, ast.Name) and node.id.startswith("_") and node.id != "_":
            return True
        if (
            isinstance(node, ast.Call)
            and _canonical_callable_name(node.func, module_aliases, callable_aliases)
            in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and node.args[1].value.startswith("_")
        ):
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
    return _has_nondeterministic_call(tree)


def _has_nondeterministic_call(tree: ast.AST) -> bool:
    module_aliases, callable_aliases = _import_bindings(tree)
    module_aliases = _with_assigned_module_aliases(
        tree, module_aliases, callable_aliases
    )
    callable_aliases = _with_assigned_callable_aliases(
        tree, module_aliases, callable_aliases
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callable_name = _canonical_callable_name(
            node.func, module_aliases, callable_aliases
        )
        if callable_name in _NONDETERMINISTIC_CALLS:
            return True
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in {"now", "today", "utcnow", "time", "random", "randint", "get"}:
                root = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
                if root in _NONDETERMINISTIC_IMPORTS:
                    return True
    return False


def _has_unsafe_import(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(_unsafe_import_name(alias.name) for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _unsafe_import_name(node.module):
                return True
    return False


def _unsafe_import_name(name: str) -> bool:
    return name.split(".", 1)[0] in _UNSAFE_IMPORT_ROOTS


def _has_performance_threshold(tree: ast.AST) -> bool:
    if _import_roots(tree) & {"timeit", "pytest_benchmark", "benchmark"}:
        return True
    module_aliases, callable_aliases = _import_bindings(tree)
    module_aliases = _with_assigned_module_aliases(
        tree, module_aliases, callable_aliases
    )
    callable_aliases = _with_assigned_callable_aliases(
        tree, module_aliases, callable_aliases
    )
    performance_aliases = _performance_aliases(
        tree, module_aliases
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callable_name = _canonical_callable_name(
                node.func, module_aliases, callable_aliases
            )
            if (
                callable_name in _PERFORMANCE_CALL_NAMES
                or callable_name.rsplit(".", 1)[-1] in _PERFORMANCE_CALL_NAMES
            ):
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr in _PERFORMANCE_CALL_NAMES:
                return True
        if isinstance(node, ast.Compare):
            operands = (node.left, *node.comparators)
            names = {
                performance_aliases.get(child.id, child.id).lower()
                for operand in operands
                for child in ast.walk(operand)
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
    module_aliases, callable_aliases = _import_bindings(tree)
    module_aliases = _with_assigned_module_aliases(
        tree, module_aliases, callable_aliases
    )
    callable_aliases = _with_assigned_callable_aliases(
        tree, module_aliases, callable_aliases
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callable_name = _canonical_callable_name(
            node.func, module_aliases, callable_aliases
        )
        if (
            callable_name in _MOCK_CALLS
            or callable_name.rsplit(".", 1)[-1] in _MOCK_CALLS
        ):
            count += 1
        elif isinstance(node.func, ast.Attribute):
            dotted = _dotted_name(node.func)
            if dotted in _MOCK_CALLS or node.func.attr in {"setattr", "setitem", "delattr", "delitem"}:
                count += 1
    return count


def _has_source_write(tree: ast.AST) -> bool:
    module_aliases, callable_aliases = _import_bindings(tree)
    module_aliases = _with_assigned_module_aliases(
        tree, module_aliases, callable_aliases
    )
    callable_aliases = _with_assigned_callable_aliases(
        tree, module_aliases, callable_aliases
    )
    path_names = _path_variable_names(tree, module_aliases, callable_aliases)
    path_method_aliases = _path_method_aliases(
        tree, module_aliases, callable_aliases, path_names
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _dangerous_getattr_member(node, module_aliases, callable_aliases):
            return True
        callable_name = _canonical_callable_name(
            node.func, module_aliases, callable_aliases
        )
        path_method = (
            path_method_aliases.get(node.func.id)
            if isinstance(node.func, ast.Name)
            else None
        )
        if path_method is not None and path_method[1] in _PATH_MUTATION_METHODS:
            return True
        if path_method is not None:
            callable_name = f"pathlib.Path.{path_method[1]}"
        if callable_name in {"apply_patch", "system", "popen"}:
            return True
        if callable_name in _SOURCE_WRITE_CALLS:
            return True
        if callable_name == "pathlib.Path.touch":
            return True
        if callable_name in {"builtins.open", "pathlib.Path.open"}:
            alias_kind = path_method[0] if path_method is not None else None
            if _write_mode(
                node,
                path_class_receiver=alias_kind == "class"
                or (
                    isinstance(node.func, ast.Attribute)
                    and _is_path_class_receiver(
                        node.func.value, module_aliases, callable_aliases
                    )
                ),
                bound_path_method=alias_kind == "bound",
            ):
                return True
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            if _write_mode(node):
                return True
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in _WRITE_METHODS:
                return True
            if node.func.attr in _PATH_WRITE_METHODS:
                if node.func.attr == "touch" or _is_path_receiver(
                    node.func.value, module_aliases, callable_aliases, path_names
                ):
                    return True
            if node.func.attr == "open" and _write_mode(
                node,
                path_class_receiver=_is_path_class_receiver(
                    node.func.value, module_aliases, callable_aliases
                ),
            ):
                return True
    return False


def _has_unsafe_execution(tree: ast.AST) -> bool:
    module_aliases, callable_aliases = _import_bindings(tree)
    module_aliases = _with_assigned_module_aliases(
        tree, module_aliases, callable_aliases
    )
    callable_aliases = _with_assigned_callable_aliases(
        tree, module_aliases, callable_aliases
    )
    path_names = _path_variable_names(tree, module_aliases, callable_aliases)
    path_method_aliases = _path_method_aliases(
        tree, module_aliases, callable_aliases, path_names
    )
    string_names = _string_variable_names(tree)
    if _has_unsafe_callable_reference(tree, module_aliases, callable_aliases):
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            path_method = path_method_aliases.get(node.func.id)
            if path_method is not None and path_method[1] in _PATH_PROBE_METHODS:
                return True
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in _PATH_PROBE_METHODS:
                return True
            if node.func.attr == "replace" and not _is_string_expression(
                node.func.value, string_names
            ):
                return True
        callable_name = _canonical_callable_name(
            node.func, module_aliases, callable_aliases
        )
        if callable_name in _UNSAFE_EXECUTION_CALLS:
            return True
        if callable_name in {"os.urandom", "os.getrandom"}:
            continue
        if _is_unsafe_callable_name(callable_name):
            return True
        if callable_name in {"getattr", "builtins.getattr"}:
            member = _getattr_member(node)
            if member is None:
                return True
            if member.startswith("_"):
                continue
            if member in _DYNAMIC_ATTR_HANDLED_BY_EXISTING_RULES:
                continue
            return True
    return False


def _has_unsafe_callable_reference(
    tree: ast.AST,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            value = node
        elif isinstance(node, ast.Return) and isinstance(
            node.value, (ast.Attribute, ast.Name)
        ):
            value = node.value
        elif isinstance(node, ast.Name) and node.id in callable_aliases:
            value = node
        else:
            continue
        callable_name = _canonical_callable_name(
            value, module_aliases, callable_aliases
        )
        if callable_name in _NONDETERMINISTIC_CALLS:
            continue
        if _is_unsafe_callable_name(callable_name):
            return True
    return False


def _is_unsafe_callable_name(callable_name: str) -> bool:
    if callable_name in _UNSAFE_EXECUTION_CALLS:
        return True
    if any(
        callable_name == prefix[:-1] or callable_name.startswith(prefix)
        for prefix in _UNSAFE_PROCESS_PREFIXES
    ):
        return True
    return any(
        callable_name == prefix or callable_name.startswith(prefix)
        for prefix in _UNSAFE_FILESYSTEM_PREFIXES
    )


def _has_absolute_path_literal(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        value = node.value
        if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
            return True
    return False


def _has_unsafe_file_path(tree: ast.AST) -> bool:
    module_aliases, callable_aliases = _import_bindings(tree)
    module_aliases = _with_assigned_module_aliases(
        tree, module_aliases, callable_aliases
    )
    callable_aliases = _with_assigned_callable_aliases(
        tree, module_aliases, callable_aliases
    )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callable_name = _canonical_callable_name(
            node.func, module_aliases, callable_aliases
        )
        if callable_name == "builtins.open" and node.args:
            if _path_expression_escapes(node.args[0], module_aliases, callable_aliases):
                return True
        elif callable_name == "pathlib.Path.open" and isinstance(node.func, ast.Attribute):
            path_expression = node.func.value
            if _is_path_class_receiver(
                node.func.value, module_aliases, callable_aliases
            ):
                path_expression = node.args[0] if node.args else node.func.value
            if _path_expression_escapes(
                path_expression, module_aliases, callable_aliases
            ):
                return True
    return False


def _path_expression_escapes(
    node: ast.AST,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        path = Path(node.value)
        return path.is_absolute() or ".." in path.parts
    if isinstance(node, ast.Call):
        if (
            _canonical_callable_name(node.func, module_aliases, callable_aliases)
            == "pathlib.Path"
            and node.args
        ):
            return _path_expression_escapes(
                node.args[0], module_aliases, callable_aliases
            )
    return True


def _dangerous_getattr_member(
    node: ast.Call,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> bool:
    callable_name = _canonical_callable_name(node.func, module_aliases, callable_aliases)
    if callable_name not in {"getattr", "builtins.getattr"}:
        return False
    member = _getattr_member(node)
    if member is None:
        return False
    return member in _DANGEROUS_GETATTR_ATTRIBUTES


def _getattr_member(node: ast.Call) -> str | None:
    if len(node.args) < 2:
        return None
    member = node.args[1]
    if isinstance(member, ast.Constant) and isinstance(member.value, str):
        return member.value
    return None


def _import_bindings(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    module_aliases: dict[str, str] = {}
    callable_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    module_aliases[alias.asname] = alias.name
                else:
                    module_aliases[alias.name.split(".", 1)[0]] = alias.name.split(
                        ".", 1
                    )[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                bound_name = alias.asname or alias.name
                callable_aliases[bound_name] = f"{node.module}.{alias.name}"
    return module_aliases, callable_aliases


def _canonical_callable_name(
    node: ast.AST,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> str:
    if isinstance(node, ast.Name):
        if node.id in module_aliases:
            return module_aliases[node.id]
        if node.id == "open":
            return "builtins.open"
        return callable_aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Call):
            base_name = _canonical_callable_name(
                node.value, module_aliases, callable_aliases
            )
            if base_name:
                return f"{base_name}.{node.attr}"
        parts = _dotted_name_parts(node)
        if parts and parts[0] in module_aliases:
            parts = module_aliases[parts[0]].split(".") + parts[1:]
        elif parts and parts[0] in callable_aliases:
            parts = callable_aliases[parts[0]].split(".") + parts[1:]
        return ".".join(parts)
    if isinstance(node, ast.Call):
        dynamic_name = _dynamic_callable_name(
            node, module_aliases, callable_aliases
        )
        if dynamic_name:
            return dynamic_name
        callable_name = _canonical_callable_name(
            node.func, module_aliases, callable_aliases
        )
        if callable_name in {"__import__", "import_module", "importlib.import_module"}:
            if node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    return node.args[0].value
        return callable_name
    return ""


def _dynamic_callable_name(
    node: ast.Call,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> str | None:
    if _canonical_callable_name(node.func, module_aliases, callable_aliases) not in {
        "getattr",
        "builtins.getattr",
    }:
        return None
    if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
        return None
    member = node.args[1].value
    if not isinstance(member, str):
        return None
    base = node.args[0]
    if isinstance(base, ast.Call):
        base_callable = _canonical_callable_name(
            base.func, module_aliases, callable_aliases
        )
        if base_callable == "__import__" and base.args:
            if isinstance(base.args[0], ast.Constant) and isinstance(base.args[0].value, str):
                return f"{base.args[0].value}.{member}"
        if base_callable in {"import_module", "importlib.import_module"} and base.args:
            if isinstance(base.args[0], ast.Constant) and isinstance(base.args[0].value, str):
                return f"{base.args[0].value}.{member}"
    base_name = _canonical_callable_name(base, module_aliases, callable_aliases)
    if base_name:
        return f"{base_name}.{member}"
    return None


def _with_assigned_callable_aliases(
    tree: ast.AST,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> dict[str, str]:
    aliases = dict(callable_aliases)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
            value = node.value
        else:
            continue
        value_name = _canonical_callable_name(value, module_aliases, aliases) if value else ""
        if value_name == "pathlib.Path" and isinstance(value, ast.Call):
            continue
        if not value_name or (
            value_name not in _TRACKED_CALLABLE_NAMES
            and value_name not in _UNSAFE_EXECUTION_CALLS
            and not any(
                value_name.startswith(prefix) for prefix in _UNSAFE_PROCESS_PREFIXES
            )
        ):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = value_name
    return aliases


def _performance_aliases(
    tree: ast.AST,
    module_aliases: dict[str, str],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
            value = node.value
        else:
            continue
        value_name = _canonical_callable_name(value, module_aliases, aliases) if value else ""
        if value_name.lower() not in _PERFORMANCE_NAMES:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = value_name
    return aliases


def _with_assigned_module_aliases(
    tree: ast.AST,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> dict[str, str]:
    aliases = dict(module_aliases)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Name) and node.value.id in aliases:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases[target.id] = aliases[node.value.id]
            continue
        if not isinstance(node.value, ast.Call):
            continue
        importer = _canonical_callable_name(
            node.value.func, aliases, callable_aliases
        )
        if importer not in {"__import__", "import_module", "importlib.import_module"}:
            continue
        if not node.value.args:
            continue
        module = node.value.args[0]
        if not isinstance(module, ast.Constant) or not isinstance(module.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = module.value
    return aliases


def _path_variable_names(
    tree: ast.AST,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        constructor = _canonical_callable_name(
            node.value.func, module_aliases, callable_aliases
        )
        if constructor != "pathlib.Path":
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _is_path_class_receiver(
    node: ast.AST,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
) -> bool:
    return isinstance(node, (ast.Name, ast.Attribute)) and _canonical_callable_name(
        node, module_aliases, callable_aliases
    ) == "pathlib.Path"


def _is_path_receiver(
    node: ast.AST,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
    path_names: set[str],
) -> bool:
    if isinstance(node, ast.Name) and node.id in path_names:
        return True
    return _canonical_callable_name(node, module_aliases, callable_aliases) == "pathlib.Path"


def _path_method_aliases(
    tree: ast.AST,
    module_aliases: dict[str, str],
    callable_aliases: dict[str, str],
    path_names: set[str],
) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = (node.target,)
            value = node.value
        else:
            continue
        if not isinstance(value, ast.Attribute) or value.attr not in _PATH_METHODS:
            continue
        if value.attr == "replace" and _is_string_expression(
            value.value, _string_variable_names(tree)
        ):
            continue
        if _is_path_class_receiver(value.value, module_aliases, callable_aliases):
            kind = "class"
        elif _is_path_receiver(
            value.value, module_aliases, callable_aliases, path_names
        ):
            kind = "bound"
        else:
            kind = "bound"
        for target in targets:
            if isinstance(target, ast.Name):
                aliases[target.id] = (kind, value.attr)
    return aliases


def _string_variable_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _is_string_expression(node: ast.AST, string_names: set[str]) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    ) or (isinstance(node, ast.JoinedStr)) or (
        isinstance(node, ast.Name) and node.id in string_names
    )


def _write_mode(
    node: ast.Call,
    *,
    path_class_receiver: bool = False,
    bound_path_method: bool = False,
) -> bool:
    if bound_path_method:
        mode_index = 0
    elif isinstance(node.func, ast.Name):
        mode_index = 1
    elif path_class_receiver:
        mode_index = 1
    else:
        mode_index = 0
    mode_node: ast.AST | None = node.args[mode_index] if len(node.args) > mode_index else None
    for keyword in node.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
            break
    if mode_node is None:
        return False
    if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
        return True
    return bool(re.search(r"[wax+]", mode_node.value))


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
    return ".".join(_dotted_name_parts(node))


def _dotted_name_parts(node: ast.Attribute) -> list[str]:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return list(reversed(parts))
