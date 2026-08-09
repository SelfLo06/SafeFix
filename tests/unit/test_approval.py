import threading

from safefix.approval import ApprovalProvider, DeferredApprovalProvider


def test_non_interactive_approval_denies():
    provider = ApprovalProvider(interactive=False, input_fn=lambda _: "yes")

    assert provider.approve(object()) is False


def test_injected_input_can_approve():
    provider = ApprovalProvider(interactive=True, input_fn=lambda _: "yes")

    assert provider.approve(object()) is True


def test_injected_input_denies_non_confirmation():
    provider = ApprovalProvider(interactive=True, input_fn=lambda _: "no")

    assert provider.approve(object()) is False


def test_input_failure_fails_closed():
    def fail(_: str) -> str:
        raise EOFError

    provider = ApprovalProvider(interactive=True, input_fn=fail)

    assert provider.approve(object()) is False


def test_default_interactive_mode_follows_stdin_tty(monkeypatch):
    monkeypatch.setattr("safefix.approval.sys.stdin.isatty", lambda: True)
    provider = ApprovalProvider(input_fn=lambda _: "yes")

    assert provider.approve(object()) is True


def test_pending_approval_can_be_resolved_without_interactive_prompt():
    provider = ApprovalProvider(interactive=False)

    provider.request("patch")

    assert provider.pending is True
    assert provider.approve_pending() is True
    assert provider.pending is False
    assert provider.deny_pending() is False


def test_deferred_approval_waits_for_explicit_resolution_without_reading_stdin():
    provider = DeferredApprovalProvider()
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(provider.approve("candidate")))

    worker.start()
    assert provider.wait_until_pending(timeout=0.5)
    assert provider.pending is True
    assert provider.approve_pending() is True
    worker.join(timeout=0.5)

    assert result == [True]


def test_deferred_approval_can_be_denied():
    provider = DeferredApprovalProvider()
    result: list[bool] = []
    worker = threading.Thread(target=lambda: result.append(provider.approve("candidate")))

    worker.start()
    assert provider.wait_until_pending(timeout=0.5)
    assert provider.deny_pending() is True
    worker.join(timeout=0.5)

    assert result == [False]
