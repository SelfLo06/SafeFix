from collections.abc import Callable
import sys


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

    def approve(self, action: object) -> bool:
        if not self._interactive:
            return False
        try:
            answer = self._input("Approve action? [y/N] ")
        except (EOFError, OSError):
            return False
        return answer.strip().lower() in {"y", "yes"}
