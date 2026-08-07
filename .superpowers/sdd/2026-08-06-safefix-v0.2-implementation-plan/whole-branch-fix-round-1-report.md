# Whole-Branch Fix Round 1

## Scope

Addressed both P1 findings in `whole-branch-review.md`:

- production CLI role wiring now creates Repair, configured Test, and configured Review clients from role-scoped keyring entries;
- Review is adapted with `ReviewModelClient` for preparation and final checkpoints;
- approval and explicit high-risk confirmation flow through `SessionRunner` into `SessionSetup`;
- actual v0.2 session state records sanitized role identities and high-risk confirmation;
- legacy existing-only and legacy CLI behavior remains covered.

No keys or raw client credentials enter runner arguments beyond live injected clients; artifact metadata uses the existing sanitization boundary.

## TDD Evidence

- Red: new CLI integration tests failed because only Repair was constructed, confirmation was absent, and non-TTY high-risk execution returned success.
- Green: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/unit/test_cli_v2.py::test_cli_wires_configured_role_clients_and_review_adapter tests/unit/test_cli_v2.py::test_cli_requires_high_risk_confirmation_and_passes_record_to_runner tests/unit/test_cli_v2.py::test_non_tty_run_uses_fail_closed_approval_for_high_risk_work -q` — 3 passed.
- Red: new SessionRunner integration test failed because role identities and setup propagation were absent.
- Green: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/unit/test_runner_init.py::test_v02_runner_passes_role_clients_confirmation_and_audit_identities_to_setup -q` — passed.

## Verification

- Focused/related suites: 84 passed.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests -q` — 591 passed.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m compileall -q src` — passed.
- `git diff --check` — passed.
- Immutable `v0.1.0^{}` — `4fc3d6bfd61ad6b4057de66abcf13605af3c2b9c`.

## Reviews

- Specification-compliance review: PASS. The shipped CLI now reaches configured role clients, preparation review, final review, fail-closed explicit high-risk confirmation, and sanitized state metadata.
- Code-quality review: PASS. The change uses existing factories/adapters, adds no dependency or framework, avoids broad exception handling and speculative fallback, and keeps test seams injected.

## Commits

- Implementation and tests: `8a3722f` (`fix: wire v0.2 role clients through cli`).
- Documentation/log closure: `3075679` (`docs: record whole-branch P1 fix evidence`).
