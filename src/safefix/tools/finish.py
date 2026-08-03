from ..models import StopReason


def finish(reason: str | None = None) -> StopReason:
    """Convert a finish tool request into the runner's requested stop reason."""
    return StopReason.REQUESTED
