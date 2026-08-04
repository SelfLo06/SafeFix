from pathlib import Path


def test_readme_documents_install_run_credentials_and_limits() -> None:
    content = Path("README.md").read_text(encoding="utf-8").lower()

    assert "git clone" in content or "source checkout" in content
    assert "python -m pip install ." in content
    assert "python -m build --wheel --sdist" in content
    assert "*.whl" in content
    assert "*.tar.gz" in content
    assert "safefix run" in content
    assert "safefix credentials set" in content
    assert "safefix credentials status" in content
    assert "safefix credentials clear" in content
    assert "keyring" in content
    assert "environment variable" in content
    assert ".env" in content
    assert "no fallback" in content or "not supported" in content
    assert "python 3.11" in content
    assert "macos" in content
    assert "windows" in content
    assert "linux" in content
    assert "project-relative" in content
    assert "pytest" in content
    assert "non-interactive" in content
    assert "deny" in content
    assert "webui" in content or "web ui" in content
    assert "cloud" in content
