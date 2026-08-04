from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .config import ConfigError, load_config
from .credentials import CredentialError, CredentialsResolver
from .models import Config, FailureSet, SessionResult, StopReason
from .paths import compute_writable_py_files
from .session_state import SessionState
from .snapshot import SnapshotStore
from .testrunner import TestRunResult, TestRunner


class BaselineRunner(Protocol):
    def run(self) -> TestRunResult:
        """Run the configured pytest baseline."""


TestRunnerFactory = Callable[[Path, list[str]], BaselineRunner]


class SessionRunner:
    """Initialize one repair session through its validated baseline."""

    def __init__(
        self,
        project_root: Path,
        *,
        cli_overrides: dict | None = None,
        credentials: CredentialsResolver | None = None,
        config_loader: Callable[..., Config] = load_config,
        test_runner_factory: TestRunnerFactory = TestRunner,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._cli_overrides = {} if cli_overrides is None else cli_overrides
        self._credentials = credentials or CredentialsResolver()
        self._config_loader = config_loader
        self._test_runner_factory = test_runner_factory
        self.config: Config | None = None
        self.writable_paths: set[Path] = set()
        self.state: SessionState | None = None
        self.snapshot_store: SnapshotStore | None = None

    def initialize(self) -> SessionResult | None:
        """Run INIT and return an early stop, or prepare later phases."""
        try:
            self.config = self._config_loader(
                self.project_root, self._cli_overrides, require_llm=True
            )
            self._credentials.get()
        except (ConfigError, CredentialError):
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)

        self.writable_paths = compute_writable_py_files(
            self.project_root, self.config.allowed_paths, self.config.excluded_paths
        )
        baseline = self._test_runner_factory(
            self.project_root, self.config.pytest_args
        ).run()
        if baseline.exit_code not in {0, 1}:
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)

        self.state = SessionState(FailureSet(baseline.failure_ids))
        if not self.state.F0.ids:
            return SessionResult(stop_reason=StopReason.SUCCESS)
        if not self.writable_paths:
            return SessionResult(stop_reason=StopReason.CONFIG_ERROR)

        self.snapshot_store = SnapshotStore(self.project_root, self.writable_paths)
        return None
