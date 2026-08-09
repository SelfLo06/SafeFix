from collections.abc import Callable
import sys
import threading


class ApprovalProvider:
    """Human approval boundary with a fail-closed non-interactive mode."""

    def __init__(
        self,
        *,
        interactive: bool | None = None,
        input_fn: Callable[[str], str] = input,
    ) -> None:
        self._interactive = sys.stdin.isatty() if interactive is None else interactive
        self._input = input_fn
        self._pending_action: object | None = None

    @property
    def pending(self) -> bool:
        return self._pending_action is not None

    def request(self, action: object) -> None:
        if self.pending:
            raise RuntimeError("an approval is already pending")
        self._pending_action = action

    def approve_pending(self) -> bool:
        if not self.pending:
            return False
        self._pending_action = None
        return True

    def deny_pending(self) -> bool:
        if not self.pending:
            return False
        self._pending_action = None
        return True

    def approve(self, action: object) -> bool:
        if not self._interactive:
            return False
        try:
            answer = self._input("Approve action? [y/N] ")
        except (EOFError, OSError):
            return False
        return answer.strip().lower() in {"y", "yes"}


class DeferredApprovalProvider(ApprovalProvider):
    """Approval boundary resolved by TUI commands instead of a worker stdin read."""

    def __init__(self) -> None:
        super().__init__(interactive=False)
        self._resolution: bool | None = None
        self._pending_event = threading.Event()
        self._resolved_event = threading.Event()

    def begin(self, action: object) -> None:
        self.request(action)
        self._resolution = None
        self._resolved_event.clear()
        self._pending_event.set()

    def approve(self, action: object) -> bool:
        if not self.pending:
            self.begin(action)
        self._resolved_event.wait()
        return self._resolution is True

    def approve_pending(self) -> bool:
        if not self.pending:
            return False
        super().approve_pending()
        self._resolution = True
        self._pending_event.clear()
        self._resolved_event.set()
        return True

    def deny_pending(self) -> bool:
        if not self.pending:
            return False
        super().deny_pending()
        self._resolution = False
        self._pending_event.clear()
        self._resolved_event.set()
        return True

    def wait_until_pending(self, timeout: float) -> bool:
        return self._pending_event.wait(timeout)
