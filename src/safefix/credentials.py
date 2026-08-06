from __future__ import annotations

from typing import Any

from .models import ModelRole

_DEFAULT_KEYRING = object()

_ROLE_SERVICES = {
    ModelRole.TEST: "safefix-test",
    ModelRole.REPAIR: "safefix-repair",
    ModelRole.REVIEW: "safefix-review",
}


def role_service_name(role: ModelRole) -> str:
    """Return the fixed keyring service for a model role."""
    return _ROLE_SERVICES[ModelRole(role)]


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
        keyring_backend: Any = _DEFAULT_KEYRING,
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
        keyring, keyring_errors = self._dependencies()
        try:
            keyring.set_password(self._service_name, self._username, value)
        except keyring_errors.KeyringError as exc:
            raise CredentialError("cannot store credential in keyring") from exc

    def get(self) -> str:
        value = self._read()
        if value is None:
            raise CredentialNotFoundError(
                f"credential for service {self._service_name!r} is not stored in keyring"
            )
        return value

    def clear(self) -> None:
        keyring, keyring_errors = self._dependencies()
        try:
            keyring.delete_password(self._service_name, self._username)
        except keyring_errors.KeyringError as exc:
            raise CredentialError("cannot clear credential from keyring") from exc

    def _read(self) -> str | None:
        keyring, keyring_errors = self._dependencies()
        try:
            value = keyring.get_password(self._service_name, self._username)
        except keyring_errors.KeyringError as exc:
            raise CredentialError("cannot read credential from keyring") from exc
        if value is not None and (not isinstance(value, str) or not value):
            raise CredentialError("keyring returned an invalid credential")
        return value

    def _dependencies(self) -> tuple[Any, Any]:
        if self._keyring is _DEFAULT_KEYRING:
            import keyring

            self._keyring = keyring
        import keyring.errors as keyring_errors

        return self._keyring, keyring_errors

    def for_role(self, role: ModelRole) -> "CredentialsResolver":
        """Create a resolver for a role while retaining this resolver's backend."""
        return CredentialsResolver(
            self._keyring,
            service_name=role_service_name(role),
            username=self._username,
        )
