from safefix.approval import ApprovalProvider


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
