from pathlib import Path


def _readme_content() -> tuple[str, str]:
    raw_content = Path("README.md").read_text(encoding="utf-8")
    return raw_content, raw_content.lower()


def test_readme_documents_install_and_build() -> None:
    _, content = _readme_content()

    assert "git clone" in content or "source checkout" in content
    assert "python -m pip install ." in content
    assert "python -m build --wheel --sdist" in content
    assert "*.whl" in content
    assert "*.tar.gz" in content


def test_readme_documents_run_and_credentials() -> None:
    _, content = _readme_content()

    assert "safefix run" in content
    assert "safefix credentials set" in content
    assert "safefix credentials status" in content
    assert "safefix credentials clear" in content
    assert "keyring" in content


def test_readme_documents_credential_boundaries() -> None:
    _, content = _readme_content()

    assert "environment variable" in content
    assert ".env" in content
    assert "no fallback" in content or "not supported" in content


def test_readme_documents_platform_and_scope() -> None:
    _, content = _readme_content()

    assert "python 3.11" in content
    assert "macos" in content
    assert "windows" in content
    assert "linux" in content
    assert "webui" in content or "web ui" in content
    assert "cloud" in content


def test_readme_documents_project_relative_paths() -> None:
    _, content = _readme_content()

    assert "project-relative" in content


def test_readme_documents_exact_pytest_allowlist() -> None:
    raw_content, content = _readme_content()

    assert "pytest" in content
    assert "-q" in content
    assert "-v" in content
    assert "--tb=short" in content
    assert "--tb=line" in content
    assert "--tb=no" in content
    assert "--disable-warnings" in content
    assert "-rA" in raw_content


def test_readme_documents_rejected_pytest_arguments() -> None:
    _, content = _readme_content()

    assert "-k" in content
    assert "-m" in content
    assert "-x" in content
    assert "--collect-only" in content


def test_readme_documents_repair_limits() -> None:
    _, content = _readme_content()

    assert "max_steps = 30" in content
    assert "max_rounds = 10" in content
    assert "max_no_progress_rounds = 3" in content
    assert ">3 files" in content
    assert ">80 lines" in content


def test_readme_documents_non_interactive_approval() -> None:
    raw_content, content = _readme_content()

    assert "non-interactive" in content
    assert "deny" in content
    assert "non-interactive mode SafeFix must deny" in raw_content
    assert "never auto-approves" in raw_content


def test_readme_documents_opt_in_summary_memory() -> None:
    raw_content, content = _readme_content()

    assert "use_memory=True" in raw_content
    assert "default context and `safefix run` do not load project" in content
    assert "memory stores summaries only" in content
