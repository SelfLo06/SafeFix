# Whole-Branch Fix Round 2

## Scope

Fixed exactly the two P1 specification findings requested for round 2:

1. The production Repair client now reads `credentials.for_role(ModelRole.REPAIR)`,
   which uses the `safefix-repair` keyring service. No fallback to the legacy
   `safefix` service was added. Existing credential-command compatibility was
   preserved, and CLI test fakes were updated to expose the role-scoped seam.
2. CLI config is resolved first. A resolved `HIGH_RISK` mode is rejected unless
   `args.acceptance_mode` is explicitly `high-risk`. Explicit high-risk mode
   requires a capable interactive TTY and the existing approval confirmation;
   otherwise the CLI returns `CONFIG_ERROR` before constructing the Runner.

Ordinary standard/review modes and existing-only paths were left unchanged.

## TDD Evidence

- Red: role-keyring production CLI test failed with a missing `safefix`
  credential while only `safefix-repair` existed.
- Red: real TOML high-risk test returned success and constructed the Runner.
- Green: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest
  tests/unit/test_cli.py tests/unit/test_cli_v2.py tests/unit/test_credentials.py
  tests/unit/test_openai_client.py -q` — 34 passed.
- Green: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest
  tests/mechanism/test_demo_terminal_fallback.py -q` — 4 passed.

## Verification

- Full suite: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests -q`
  — 592 passed in 8.67s.
- Compile: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m compileall -q src`
  — passed.
- `git diff --check` — passed.
- Dirty plan-local `progress.md` remains unstaged and preserved.
- `SPEC.md`, `PLAN.md`, and tag `v0.1.0` were not modified.

## Reviews

- Specification-compliance review: PASS. Role-scoped Repair credentials,
  explicit CLI-only high-risk opt-in, TTY gate, approval requirement, and
  fail-closed ordering match the approved design and request.
- Code-quality review: PASS. The diff is scoped to the CLI boundary and test
  seams, with no fallback logic, broad catches, duplicated internal
  validation, unnecessary abstraction, or new dependency.
