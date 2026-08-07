# Task 14 Implementer Report

Status: DONE

## Scope

Implemented SafeFix v0.2 Task 14 in the supplied linked worktree.

Changed implementation and documentation:

- `src/safefix/cli.py`
- `README.md`
- `tests/unit/test_cli.py`
- `tests/unit/test_cli_v2.py`
- `tests/unit/test_readme.py`
- `AGENT_LOG.md`

The pre-existing dirty
`.superpowers/sdd/2026-08-06-safefix-v0.2-implementation-plan/progress.md`
was preserved and was neither modified nor staged. `PLAN.md` was not changed.

## Behavior Delivered

- `run` now accepts mutually exclusive `--tui` and `--plain` flags plus
  `--no-animation`.
- A capable stdin/stdout TTY selects the injected guided console by default.
  `--plain` always retains the existing `print` event sink. A non-TTY run
  remains plain even with `--tui`, and it uses fail-closed approval.
- The CLI owns presentation selection and creates exactly one
  `OperatorCommandQueue` for a TUI session. The console is created through the
  injected `tui_factory(command_queue, controller_factory, capabilities,
  no_animation)` boundary; CLI tests use fakes and never start a real
  prompt_toolkit/Rich loop.
- `--no-animation` is passed only to the presentation adapter/capabilities.
  It is omitted from Config overrides and does not change Runner, event,
  artifact, StopReason, or exit-code behavior.
- All v0.2 Config CLI fields are exposed without disturbing legacy
  `--base-url`/`--model`, credential role commands, or plain output. The false
  `--generate-tests` default does not overwrite TOML configuration.
- README coverage documents installation dependencies, the scrollback console,
  terminal fallbacks and controls, no-secret policy, roles, generated-only
  restriction, acceptance modes, artifact semantics, package artifacts, and
  the absence of Textual/WebUI and a special-font requirement.

## TDD Evidence

Initial red run:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest \
  tests/unit/test_cli.py tests/unit/test_cli_v2.py tests/unit/test_readme.py -q
```

Outcome: expected 5 failures. The parser rejected the new v0.2 options,
`main()` lacked `tty_detector` and `tui_factory`, and the README did not yet
document console presentation.

High-risk non-TTY red run:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest \
  tests/unit/test_cli_v2.py::test_non_tty_run_uses_fail_closed_approval_for_high_risk_work -q
```

Outcome: expected 1 failure; the CLI invoked the interactive approval factory
for a non-TTY run.

Green and regression evidence:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest \
  tests/unit/test_cli.py tests/unit/test_cli_v2.py tests/unit/test_readme.py \
  tests/unit/test_packaging.py -q
# 27 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests -q
# 574 passed

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m compileall -q src
git diff --check
```

All green commands exited successfully. A separate `safefix --help` probe with
both `prompt_toolkit` and Rich blocked from import also exited 0 and printed
usage, confirming that the interactive dependencies are not loaded for help.

## Reviews

Specification-compliance review: PASS. The CLI is the sole presentation-policy
owner: TTY defaults, `--plain`, non-TTY dominance, TUI queue construction, and
no-animation routing all occur before the existing Runner boundary. Config
validation stays in `load_config`; the Runner remains authoritative for test
discovery, generated-only conflict handling, events, artifacts, and stop
reasons. Non-interactive and non-TTY approvals are fail-closed.

Code-quality review: PASS. Interactive dependencies are imported inside the
selected production TUI factory, so plain output and help avoid their import.
The change adds no Runner/config/artifact/event abstraction, duplicated
validation, broad exception handling, speculative fallback, or new dependency.
Tests assert observable routing and approval behavior using injected fakes.

The environment did not expose a callable subagent-dispatch or model-selection
interface, so the requested fresh implementer and two review passes were
performed directly in the Task 14 scope and recorded in `AGENT_LOG.md`.

## Commits

- `4a7e27a` `feat: expose v0.2 console presentation controls`
