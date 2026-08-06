from __future__ import annotations

import pytest
import keyring.errors as keyring_errors

from safefix.credentials import (
    CredentialError,
    CredentialNotFoundError,
    CredentialValueError,
    CredentialsResolver,
    role_service_name,
)
from safefix.models import ModelRole


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
            raise keyring_errors.KeyringError("backend unavailable")

    credentials = CredentialsResolver(BrokenKeyring())

    with pytest.raises(CredentialError, match="cannot read credential"):
        credentials.status()


def test_set_keyring_failure_raises_credential_error() -> None:
    class BrokenKeyring(FakeKeyring):
        def set_password(self, service: str, username: str, password: str) -> None:
            raise keyring_errors.KeyringError("backend unavailable")

    credentials = CredentialsResolver(BrokenKeyring())

    with pytest.raises(CredentialError, match="cannot store credential"):
        credentials.set("test-api-key")


def test_clear_keyring_failure_raises_credential_error() -> None:
    class BrokenKeyring(FakeKeyring):
        def delete_password(self, service: str, username: str) -> None:
            raise keyring_errors.KeyringError("backend unavailable")

    credentials = CredentialsResolver(BrokenKeyring())

    with pytest.raises(CredentialError, match="cannot clear credential"):
        credentials.clear()


def test_programming_errors_are_not_wrapped_as_credential_errors() -> None:
    class BrokenKeyring(FakeKeyring):
        def get_password(self, service: str, username: str) -> str | None:
            raise RuntimeError("programming error")

    credentials = CredentialsResolver(BrokenKeyring())

    with pytest.raises(RuntimeError, match="programming error"):
        credentials.status()


def test_role_credentials_are_isolated() -> None:
    keyring = FakeKeyring()
    CredentialsResolver(keyring, service_name="safefix-test").set("test-key")
    CredentialsResolver(keyring, service_name="safefix-repair").set("repair-key")
    CredentialsResolver(keyring, service_name="safefix-review").set("review-key")

    assert role_service_name(ModelRole.TEST) == "safefix-test"
    assert role_service_name(ModelRole.REPAIR) == "safefix-repair"
    assert role_service_name(ModelRole.REVIEW) == "safefix-review"
    assert CredentialsResolver(keyring, service_name=role_service_name(ModelRole.TEST)).get() == "test-key"
    assert CredentialsResolver(keyring, service_name=role_service_name(ModelRole.REPAIR)).get() == "repair-key"
    assert CredentialsResolver(keyring, service_name=role_service_name(ModelRole.REVIEW)).get() == "review-key"


@pytest.mark.parametrize("role", list(ModelRole))
def test_missing_role_credential_is_specific_and_has_no_environment_fallback(
    role: ModelRole, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SAFEFIX_API_KEY", "environment-secret")
    credentials = CredentialsResolver(FakeKeyring(), service_name=role_service_name(role))

    with pytest.raises(CredentialNotFoundError, match=role_service_name(role)):
        credentials.get()


def test_legacy_repair_service_name_remains_compatible() -> None:
    keyring = FakeKeyring()
    credentials = CredentialsResolver(keyring, service_name="safefix")
    credentials.set("legacy-repair-key")

    assert credentials.get() == "legacy-repair-key"
