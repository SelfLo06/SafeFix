from pathlib import Path
import re
import tomllib

from .models import Config


class ConfigError(ValueError):
    """Raised when project or CLI configuration is invalid."""


_FIELDS = {
    "max_steps",
    "max_rounds",
    "max_no_progress_rounds",
    "allowed_paths",
    "excluded_paths",
    "pytest_args",
    "base_url",
    "model",
}
_INTEGER_FIELDS = {"max_steps", "max_rounds", "max_no_progress_rounds"}
_LIST_FIELDS = {"allowed_paths", "excluded_paths", "pytest_args"}
_PYTEST_FLAGS = {"-q", "-v", "--tb=short", "--tb=line", "--tb=no", "--disable-warnings"}
_REPORT_ARG = re.compile(r"-r[faAFeExXnNoOpPiIsSxX]+")


def load_config(
    project_root: Path, cli_overrides: dict, *, require_llm: bool = False
) -> Config:
    """Load project TOML and merge validated CLI overrides over it."""
    config_path = project_root / "safefix.toml"
    values: dict = {}
    if config_path.exists():
        try:
            with config_path.open("rb") as stream:
                values.update(tomllib.load(stream))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {config_path}") from exc
        except OSError as exc:
            raise ConfigError(f"cannot read {config_path}") from exc

    _validate_keys(values)
    _validate_values(values)
    _validate_keys(cli_overrides)
    cli_values = {key: value for key, value in cli_overrides.items() if value is not None}
    _validate_values(cli_values)
    if "excluded_paths" in values and "excluded_paths" in cli_values:
        cli_values["excluded_paths"] = [
            *values["excluded_paths"],
            *cli_values["excluded_paths"],
        ]
    values.update(cli_values)

    if require_llm and (not values.get("base_url", "").strip() or not values.get("model", "").strip()):
        raise ConfigError("base_url and model are required when require_llm is true")
    return Config(**values)


def _validate_keys(values: dict) -> None:
    unknown = set(values) - _FIELDS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigError(f"unknown configuration key(s): {names}")


def _validate_values(values: dict) -> None:
    for key, value in values.items():
        if key in _INTEGER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ConfigError(f"{key} must be a positive integer")
        elif key in _LIST_FIELDS:
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ConfigError(f"{key} must be a list of strings")
            if key == "pytest_args":
                _validate_pytest_args(value)
        elif key in {"base_url", "model"} and not isinstance(value, str):
            raise ConfigError(f"{key} must be a string")


def _validate_pytest_args(args: list[str]) -> None:
    for arg in args:
        if arg not in _PYTEST_FLAGS and not _REPORT_ARG.fullmatch(arg):
            raise ConfigError(f"disallowed pytest argument: {arg}")
