# SafeFix v0.2 Task 10 review fixes — round 1

## Scope

Resolved the blocking P1/P2 findings in `task-10-review.md` without editing
that review report, changing v0.1 behavior, adding dependencies, or changing
the `v0.1.0` tag.

- `src/safefix/session_setup.py`
  - Defines the explicit v2 runner-factory contract.
  - Routes discovery, formal baseline, and all setup-bound runner creation
    through one adapter that passes and validates exact `target_paths` and
    `allow_empty` values.
  - Rejects a returned runner whose declared scope differs from the requested
    scope; no `TestRunner` identity special case remains in the adapter.
- `src/safefix/runner.py`
  - Uses the same manifest-aware adapter for every post-freeze evaluation.
  - Retains the v0.1 two-argument factory path when no setup seam is selected.
- `tests/unit/test_runner_init.py`
  - Captures exact target paths supplied and used for formal F0 and later
    evaluation.
  - Rejects a scope-ignoring v2 runner.
  - Covers changed and missing frozen manifest files before evaluation, with
    F0 unchanged and no later evaluation runner call.
- `tests/unit/test_session_setup.py`
  - Uses the real `TestPreparationService` and a deterministic client to prove
    the Test Model is closed before the formal baseline starts.

## TDD evidence

- Red: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/unit/test_session_setup.py tests/unit/test_runner_init.py tests/unit/test_runner_evaluate.py -q` — **3 failed, 28 passed**. The new v2 factories required the manifest-aware keyword contract, while the old implementation called them through the legacy two-argument path.
- Green: the same focused command after the adapter — **31 passed**.
- Acceptance additions: the client-closure and changed/missing-manifest cases — **33 passed** in the focused command.
- Related: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/unit/test_runner_*.py tests/unit/test_session_setup.py -q` — **55 passed**.
- Manifest/preparation regression: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/unit/test_test_manifest.py tests/unit/test_testrunner.py tests/unit/test_testprep_service.py tests/unit/test_testprep_stability.py -q` — **52 passed**.
- Full: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests -q` — **530 passed**.
- Static checks: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m compileall -q src` and `git diff --check` — passed.

## Reviews

Specification-compliance review: **PASS**. Formal baseline and post-freeze
evaluation use the complete frozen manifest scope; manifest hash/content
verification remains before evaluation; F0 is created only from the formal
baseline and remains immutable; Test Model closure precedes formal baseline;
legacy two-argument factories remain available without a v2 setup seam; no new
SUCCESS authority or Repair/Review baseline authority was added.

Code-quality review: **PASS**. The adapter is a single explicit boundary
contract, validation is not duplicated in SessionRunner, exceptions remain
specific to setup boundaries, no broad catches/fallbacks/dead production code
or speculative abstractions were added, and tests assert observable scope and
lifecycle behavior rather than call counts alone.

The environment exposed no callable subagent-dispatch capability, so the
implementer, specification-compliance review, and code-quality review were
performed as separate coordinator passes. This is recorded as the only
workflow deviation. The user-specified linked worktree remains externally
managed; no cleanup or integration action was taken.

## Commits and immutable state

- Implementation: `aa60bbc` — `fix: enforce v2 frozen manifest runner scope`.
- Audit/closure documentation commit: to be recorded after this report and
  `AGENT_LOG.md` are committed.
- `v0.1.0^{}` remains
  `4fc3d6bfd61ad6b4057de66abcf13605af3c2b9c`.
