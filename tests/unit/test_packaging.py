import subprocess
import sys
import tomllib
from pathlib import Path


def test_pyproject_declares_package_and_cli() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    metadata = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    build_system = metadata["build-system"]
    project = metadata["project"]
    setuptools = metadata["tool"]["setuptools"]

    assert project["requires-python"] == ">=3.11"
    assert build_system == {
        "requires": ["setuptools>=68"],
        "build-backend": "setuptools.build_meta",
    }
    assert setuptools["package-dir"] == {"": "src"}
    assert setuptools["packages"]["find"]["where"] == ["src"]
    assert project["scripts"] == {"safefix": "safefix.cli:main"}
    assert project["dependencies"] == [
        "keyring>=25",
        "prompt_toolkit>=3.0.43,<4",
        "rich>=13.7.1,<15",
    ]
    assert project["name"] == "safefix"
    assert project["version"] == "0.2.0"
    assert project["description"] == "A coding-agent harness for repairing pytest failures"


def test_cli_help_works_when_keyring_is_unavailable() -> None:
    source_root = Path(__file__).parents[2] / "src"
    bootstrap = """
import importlib.abc
import runpy
import sys


class BlockKeyring(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "keyring" or fullname.startswith("keyring."):
            raise ModuleNotFoundError("No module named 'keyring'")
        return None


sys.meta_path.insert(0, BlockKeyring())
sys.argv = ["safefix", "--help"]
runpy.run_module("safefix", run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        env={"PYTHONPATH": str(source_root), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_cli_help_works_when_interactive_libraries_are_unavailable() -> None:
    source_root = Path(__file__).parents[2] / "src"
    bootstrap = """
import importlib.abc
import runpy
import sys


class BlockInteractiveLibraries(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"prompt_toolkit", "rich"} or fullname.startswith(
            ("prompt_toolkit.", "rich.")
        ):
            raise ModuleNotFoundError(f"No module named '{fullname}'")
        return None


sys.meta_path.insert(0, BlockInteractiveLibraries())
sys.argv = ["safefix", "--help"]
runpy.run_module("safefix", run_name="__main__")
"""
    result = subprocess.run(
        [sys.executable, "-c", bootstrap],
        env={"PYTHONPATH": str(source_root), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
