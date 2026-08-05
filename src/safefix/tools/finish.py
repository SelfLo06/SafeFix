from ..models import StopReason


def finish() -> StopReason:
    """Convert a finish tool request into the runner's requested stop reason."""
    return StopReason.REQUESTED
