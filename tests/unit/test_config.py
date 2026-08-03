from pathlib import Path

import pytest

from safefix.config import ConfigError, load_config


def test_config_defaults(tmp_path: Path):
    config = load_config(tmp_path, {})
    assert config.max_steps == 30
    assert config.max_rounds == 10
    assert config.max_no_progress_rounds == 3


def test_config_fields_exist(tmp_path: Path):
    config = load_config(tmp_path, {})
    assert hasattr(config, "allowed_paths")
    assert hasattr(config, "excluded_paths")
    assert hasattr(config, "pytest_args")
    assert hasattr(config, "base_url")
    assert hasattr(config, "model")


def test_require_llm_needs_base_url_model(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(tmp_path, {}, require_llm=True)


def test_unknown_key_rejected(tmp_path: Path):
    (tmp_path / "safefix.toml").write_text("max_steps = 5\nfoo = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, {})


def test_cli_overrides_toml(tmp_path: Path):
    (tmp_path / "safefix.toml").write_text("max_steps = 5\n", encoding="utf-8")
    config = load_config(tmp_path, {"max_steps": 9})
    assert config.max_steps == 9


def test_forbidden_pytest_args(tmp_path: Path):
    (tmp_path / "safefix.toml").write_text('pytest_args = ["-k", "test"]\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, {})


def test_allowlisted_pytest_args_ok(tmp_path: Path):
    (tmp_path / "safefix.toml").write_text(
        'pytest_args = ["-q", "--tb=short", "-rA"]\n', encoding="utf-8"
    )
    config = load_config(tmp_path, {})
    assert config.pytest_args == ["-q", "--tb=short", "-rA"]


def test_malformed_toml_rejected(tmp_path: Path):
    (tmp_path / "safefix.toml").write_text("max_steps = [\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, {})


@pytest.mark.parametrize("key", ["api_key", "api_token", "password", "secret"])
def test_secret_key_rejected(tmp_path: Path, key: str):
    (tmp_path / "safefix.toml").write_text(f'{key} = "do-not-store-secrets"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, {})


@pytest.mark.parametrize("value", ["30", True, 1.5, [30]])
def test_config_type_validation(tmp_path: Path, value):
    (tmp_path / "safefix.toml").write_text(f"max_steps = {value!r}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, {})


@pytest.mark.parametrize("key", ["max_steps", "max_rounds", "max_no_progress_rounds"])
@pytest.mark.parametrize("value", [0, -1])
def test_config_numeric_bounds_rejected(tmp_path: Path, key: str, value: int):
    (tmp_path / "safefix.toml").write_text(f"{key} = {value}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, {})


def test_cli_values_take_precedence_for_all_fields(tmp_path: Path):
    (tmp_path / "safefix.toml").write_text(
        'max_steps = 5\nallowed_paths = ["src"]\nbase_url = "toml-url"\n',
        encoding="utf-8",
    )
    config = load_config(
        tmp_path,
        {"max_steps": 9, "allowed_paths": ["lib"], "base_url": "cli-url"},
    )
    assert config.max_steps == 9
    assert config.allowed_paths == ["lib"]
    assert config.base_url == "cli-url"


@pytest.mark.parametrize(
    "arg", ["-k", "-m", "-x", "--maxfail=1", "--collect-only", "-r", "tests/test_app.py"]
)
def test_disallowed_pytest_args_rejected(tmp_path: Path, arg: str):
    with pytest.raises(ConfigError):
        load_config(tmp_path, {"pytest_args": [arg]})
