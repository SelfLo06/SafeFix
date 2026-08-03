from __future__ import annotations

import pytest

from safefix.credentials import (
    CredentialError,
    CredentialNotFoundError,
    CredentialValueError,
    CredentialsResolver,
)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.values[(service, username)]


def test_status_set_get_and_clear_use_keyring() -> None:
    keyring = FakeKeyring()
    credentials = CredentialsResolver(keyring, service_name="safefix")

    assert credentials.status() is False
    credentials.set("test-api-key")
    assert credentials.status() is True
    assert credentials.get() == "test-api-key"

    credentials.clear()
    assert credentials.status() is False


def test_get_missing_credential_is_specific_error() -> None:
    credentials = CredentialsResolver(FakeKeyring())

    with pytest.raises(CredentialNotFoundError):
        credentials.get()


def test_missing_credential_does_not_fall_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAFEFIX_API_KEY", "environment-secret")
    credentials = CredentialsResolver(FakeKeyring())

    assert credentials.status() is False
    with pytest.raises(CredentialNotFoundError):
        credentials.get()


def test_empty_credential_is_rejected() -> None:
    credentials = CredentialsResolver(FakeKeyring())

    with pytest.raises(CredentialValueError):
        credentials.set("")


def test_keyring_failure_is_not_swallowed() -> None:
    class BrokenKeyring(FakeKeyring):
        def get_password(self, service: str, username: str) -> str | None:
            raise RuntimeError("backend unavailable")

    credentials = CredentialsResolver(BrokenKeyring())

    with pytest.raises(CredentialError, match="cannot read credential"):
        credentials.status()
