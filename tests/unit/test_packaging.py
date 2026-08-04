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
    assert project["dependencies"] == ["keyring>=25"]
    assert project["name"] == "safefix"
    assert project["version"] == "0.1.0"
    assert project["description"] == "A coding-agent harness for repairing pytest failures"
