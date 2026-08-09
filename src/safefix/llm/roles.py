from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from ..credentials import CredentialsResolver
from ..models import ModelRoleConfig
from .base import HTTPTransport, LLMClient, LLMResponseError
from .openai_compatible import DEFAULT_MODEL_TEMPERATURE, DEFAULT_MODEL_TIMEOUT_SECONDS, OpenAICompatibleClient


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


class CurlHTTPTransport:
    """Production HTTP transport that is reliable with the configured proxy."""

    def post(
        self, url: str, headers: dict[str, str], json_body: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        payload = json.dumps(json_body, separators=(",", ":"))
        request_file = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        )
        request_path = request_file.name
        try:
            request_file.write(payload)
            request_file.flush()
            request_file.close()
            config = self._config(url, headers, request_path)
            try:
                completed = subprocess.run(
                    [
                        "curl",
                        "--disable",
                        "--silent",
                        "--show-error",
                        "--fail",
                        "--max-time",
                        str(timeout),
                        "--write-out",
                        "\n%{http_code}",
                        "--config",
                        "-",
                    ],
                    input=config,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise OSError("request timed out") from exc
        finally:
            request_file.close()
            Path(request_path).unlink(missing_ok=True)

        body, separator, status = completed.stdout.rpartition("\n")
        if not separator or not status.isdigit():
            raise OSError("curl returned no HTTP status")
        if status == "000":
            if completed.returncode == 28:
                raise OSError("request timed out")
            raise OSError("curl request failed")
        if not 200 <= int(status) < 300:
            raise OSError(f"HTTP Error {status}")
        if completed.returncode != 0:
            raise OSError("curl request failed")
        try:
            response = json.loads(body)
        except ValueError as exc:
            raise LLMResponseError("invalid JSON response") from exc
        if not isinstance(response, dict):
            raise LLMResponseError("invalid JSON response")
        return response

    @staticmethod
    def _config(url: str, headers: dict[str, str], request_path: str) -> str:
        def quote(value: str) -> str:
            if "\r" in value or "\n" in value:
                raise OSError("invalid curl configuration value")
            return value.replace("\\", "\\\\").replace('"', '\\"')

        lines = [f'url = "{quote(url)}"', 'request = "POST"']
        lines.extend(f'header = "{quote(f"{name}: {value}")}"' for name, value in headers.items())
        lines.append(f'data-binary = "@{quote(request_path)}"')
        return "\n".join(lines) + "\n"


class ModelClientFactory:
    """Construct an OpenAI-compatible client from one role's environment key."""

    def __init__(
        self,
        transport: HTTPTransport | None = None,
        timeout: float = DEFAULT_MODEL_TIMEOUT_SECONDS,
        temperature: float = DEFAULT_MODEL_TEMPERATURE,
    ) -> None:
        self._transport = transport if transport is not None else UrllibHTTPTransport()
        self._timeout = timeout
        self._temperature = temperature

    def create(self, role_config: ModelRoleConfig, environ: Any) -> LLMClient:
        api_key = CredentialsResolver(environ, env_name=role_config.credential_env).get()
        return OpenAICompatibleClient(
            base_url=role_config.base_url,
            model=role_config.model,
            api_key=api_key,
            transport=self._transport,
            timeout=self._timeout,
            temperature=self._temperature,
        )
