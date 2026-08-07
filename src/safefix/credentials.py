from __future__ import annotations

import os
from collections.abc import Mapping

from .models import ModelRole, ROLE_API_KEY_ENV


class CredentialError(RuntimeError):
    """Raised when a role credential is unavailable."""


class CredentialNotFoundError(CredentialError):
    """Raised when a role environment variable is missing."""


class CredentialsResolver:
    """Read one role credential from the current process environment."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        env_name: str = "SAFEFIX_REPAIR_API_KEY",
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._env_name = env_name

    def status(self) -> bool:
        return bool(self._read())

    def get(self) -> str:
        value = self._read()
        if value is None:
            raise CredentialNotFoundError(f"missing {self._env_name}")
        return value

    def for_role(self, role: ModelRole) -> "CredentialsResolver":
        """Create a resolver for one role's explicit environment variable."""
        return CredentialsResolver(
            self._environ,
            env_name=ROLE_API_KEY_ENV[ModelRole(role)],
        )

    def _read(self) -> str | None:
        value = self._environ.get(self._env_name)
        return value if value else None
