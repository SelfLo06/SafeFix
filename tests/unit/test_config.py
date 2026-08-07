from pathlib import Path

import pytest

from safefix.config import ConfigError, load_config
from safefix.credentials import ROLE_API_KEY_ENV
from safefix.models import AcceptanceMode, BaselineSource, ModelRole


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


def test_v02_config_defaults(tmp_path: Path):
    config = load_config(tmp_path, {})

    assert config.generate_tests is False
    assert config.baseline_source is BaselineSource.EXISTING
    assert config.acceptance_mode is AcceptanceMode.STANDARD
    assert config.stability_runs == 3
    assert config.max_auto_accepted_failures == 3
    assert config.test_base_url == ""
    assert config.test_model == ""
    assert config.review_base_url == ""
    assert config.review_model == ""


def test_generated_only_is_a_valid_config_value(tmp_path: Path):
    config = load_config(
        tmp_path,
        {"generate_tests": True, "baseline_source": "generated"},
    )

    assert config.baseline_source is BaselineSource.GENERATED


def test_role_configs_match_authoritative_credential_environment(tmp_path: Path):
    config = load_config(
        tmp_path,
        {
            "base_url": "https://repair.example/v1",
            "model": "repair-model",
            "test_base_url": "https://test.example/v1",
            "test_model": "test-model",
            "review_base_url": "https://review.example/v1",
            "review_model": "review-model",
        },
    )

    expected_endpoints = {
        ModelRole.REPAIR: ("https://repair.example/v1", "repair-model"),
        ModelRole.TEST: ("https://test.example/v1", "test-model"),
        ModelRole.REVIEW: ("https://review.example/v1", "review-model"),
    }

    for role, endpoint in expected_endpoints.items():
        role_config = config.role_config(role)

        assert (role_config.base_url, role_config.model) == endpoint
        assert role_config.credential_env == ROLE_API_KEY_ENV[role]


@pytest.mark.parametrize("key", ["stability_runs", "max_auto_accepted_failures"])
@pytest.mark.parametrize("value", [0, -1, True])
def test_v02_numeric_bounds_are_positive(tmp_path: Path, key: str, value: object):
    with pytest.raises(ConfigError):
        load_config(tmp_path, {key: value})


def test_invalid_acceptance_mode_is_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="acceptance_mode"):
        load_config(tmp_path, {"acceptance_mode": "unsafe"})


def test_duplicate_role_endpoint_model_is_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="same base_url.*model"):
        load_config(
            tmp_path,
            {
                "base_url": "https://same/v1",
                "model": "m",
                "review_base_url": "https://same/v1",
                "review_model": "m",
            },
            require_llm=True,
        )


def test_duplicate_role_effective_endpoint_model_is_rejected(tmp_path: Path):
    with pytest.raises(ConfigError, match="same base_url.*model"):
        load_config(
            tmp_path,
            {
                "base_url": "https://same/v1",
                "model": "m",
                "review_base_url": "https://same/v1/",
                "review_model": "m",
            },
            require_llm=True,
        )


def test_same_endpoint_with_different_models_is_allowed(tmp_path: Path):
    config = load_config(
        tmp_path,
        {
            "base_url": "https://same/v1",
            "model": "repair-model",
            "review_base_url": "https://same/v1",
            "review_model": "review-model",
        },
        require_llm=True,
    )

    assert config.review_model == "review-model"


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


def test_invalid_toml_cannot_be_masked_by_cli_override(tmp_path: Path):
    (tmp_path / "safefix.toml").write_text('max_steps = "not-an-integer"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, {"max_steps": 9})


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


@pytest.mark.parametrize("toml_value", ['"30"', "true", "1.5", "[30]"])
def test_config_type_validation(tmp_path: Path, toml_value: str):
    (tmp_path / "safefix.toml").write_text(f"max_steps = {toml_value}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, {})


def test_cli_type_validation_rejects_boolean_max_steps(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(tmp_path, {"max_steps": True})


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


def test_cli_excluded_paths_are_additive_to_toml(tmp_path: Path):
    (tmp_path / "safefix.toml").write_text(
        'excluded_paths = ["src/generated.py"]\n', encoding="utf-8"
    )

    config = load_config(tmp_path, {"excluded_paths": ["src/vendor.py"]})

    assert config.excluded_paths == ["src/generated.py", "src/vendor.py"]


@pytest.mark.parametrize(
    "arg", ["-k", "-m", "-x", "--maxfail=1", "--collect-only", "-r", "tests/test_app.py"]
)
def test_disallowed_pytest_args_rejected(tmp_path: Path, arg: str):
    with pytest.raises(ConfigError):
        load_config(tmp_path, {"pytest_args": [arg]})
