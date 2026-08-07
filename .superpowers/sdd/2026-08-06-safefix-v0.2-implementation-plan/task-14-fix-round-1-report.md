# Task 14 Scoped Fix Round 1 Report

Status: DONE

## Scope

Addressed only the two code-quality review coverage findings. The pre-existing
dirty `progress.md` and `PLAN.md` were not modified. No production code,
dependency, configuration, or documentation changed.

## Changes

- `tests/unit/test_cli_v2.py`: split the existing combined test and added a
  capable-TTY default-route regression. It uses the injected `CapturingTui`
  and proves console construction, a non-plain runner sink, and shared command
  queue wiring.
- `tests/unit/test_packaging.py`: added a fresh-process `safefix --help`
  regression that blocks both `prompt_toolkit` and `rich` module families.

No plain subprocess regression was added: an actual plain run requires project
configuration and credentials, while the required help path is offline,
deterministic, and directly guards the lazy-import contract.

## TDD Evidence

Both new tests pass against the existing correct implementation. To prove
their regression value without retaining production changes:

1. Temporarily changed the TTY selection to require `--tui`; the capable-TTY
   test failed because `CapturingTui.created` was false. Restored the original
   selection; the test passed.
2. Temporarily added module-level `import rich`; the blocked-import subprocess
   test failed with `ModuleNotFoundError: No module named 'rich'`. Restored the
   original source; the test passed.

Focused post-restoration verification:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest \
  tests/unit/test_cli_v2.py tests/unit/test_packaging.py -q
# 10 passed
```

Pre-edit baselines were 27 passing Task 14 focused tests and 574 passing full
tests.

## Reviews

Specification-compliance review: PASS. The default capable-TTY TUI contract
and lazy interactive-import help contract are now committed observable tests.

Code-quality review: PASS. Test-only state captures the existing injected
factory contract; the subprocess keeps the real import boundary intact. No
scope expansion or production change was needed.

## Commit

- `6390e92` `test: cover Task 14 presentation regressions`
