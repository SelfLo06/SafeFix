from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

from ..credentials import CredentialsResolver
from ..models import ModelRoleConfig
from .base import HTTPTransport, LLMClient, LLMResponseError
from .openai_compatible import OpenAICompatibleClient


class UrllibHTTPTransport:
    """Production HTTP transport for OpenAI-compatible completion requests."""

    def post(
        self, url: str, headers: dict[str, str], json_body: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        request = Request(
            url,
            data=json.dumps(json_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except ValueError as exc:
            raise LLMResponseError("invalid JSON response") from exc


class ModelClientFactory:
    """Construct an OpenAI-compatible client from one role's environment key."""

    def __init__(self, transport: HTTPTransport | None = None, timeout: float = 30) -> None:
        self._transport = transport if transport is not None else UrllibHTTPTransport()
        self._timeout = timeout

    def create(self, role_config: ModelRoleConfig, environ: Any) -> LLMClient:
        api_key = CredentialsResolver(environ, env_name=role_config.credential_env).get()
        return OpenAICompatibleClient(
            base_url=role_config.base_url,
            model=role_config.model,
            api_key=api_key,
            transport=self._transport,
            timeout=self._timeout,
        )
