from __future__ import annotations

from typing import Any

import keyring
import keyring.errors as keyring_errors


class CredentialError(RuntimeError):
    """Raised when the keyring cannot complete a credential operation."""


class CredentialNotFoundError(CredentialError):
    """Raised when no credential is stored in the keyring."""


class CredentialValueError(CredentialError, ValueError):
    """Raised when a credential value is invalid."""


class CredentialsResolver:
    """Read and manage the SafeFix API credential using only a keyring."""

    def __init__(
        self,
        keyring_backend: Any = keyring,
        *,
        service_name: str = "safefix",
        username: str = "api_key",
    ) -> None:
        self._keyring = keyring_backend
        self._service_name = service_name
        self._username = username

    def status(self) -> bool:
        return self._read() is not None

    def set(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise CredentialValueError("credential must be a non-empty string")
        try:
            self._keyring.set_password(self._service_name, self._username, value)
        except keyring_errors.KeyringError as exc:
            raise CredentialError("cannot store credential in keyring") from exc

    def get(self) -> str:
        value = self._read()
        if value is None:
            raise CredentialNotFoundError("credential is not stored in keyring")
        return value

    def clear(self) -> None:
        try:
            self._keyring.delete_password(self._service_name, self._username)
        except keyring_errors.KeyringError as exc:
            raise CredentialError("cannot clear credential from keyring") from exc

    def _read(self) -> str | None:
        try:
            value = self._keyring.get_password(self._service_name, self._username)
        except keyring_errors.KeyringError as exc:
            raise CredentialError("cannot read credential from keyring") from exc
        if value is not None and (not isinstance(value, str) or not value):
            raise CredentialError("keyring returned an invalid credential")
        return value
