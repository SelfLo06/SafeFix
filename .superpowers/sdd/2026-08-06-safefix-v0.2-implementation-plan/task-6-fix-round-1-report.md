# SafeFix v0.2 Task 6 fix round 1/5

## Scope

Fixed all five HIGH findings from `task-6-review.md`:

1. `CandidateWorkspace` rejects symlinked project/workspace/session
   components and requires a per-instance ownership marker before staging or
   cleanup.
2. Cleanup refuses pre-existing, unowned, missing-marker, and containment-
   violating session directories.
3. `StabilityRunner` runs pristine per-run candidate copies and cleans them up,
   so runner mutation cannot affect later runs or the staged source.
4. Stability evaluation requires a marked session-owned candidate root and
   rejects outside, absolute-outside, traversal, and symlinked candidate paths
   before invoking the runner. v0.1 `TestRunner` was not changed.
5. `stability_runs` is bounded to `1..10` at both config loading and stability
   construction; bools and values above the bound are rejected.

Added adversarial tests for symlink escape, ownership collision, mutation
across runs, outside/traversal execution, and upper-bound rejection.

## TDD and verification

- Red: focused Task 6 collection failed before the bounded constant existed.
- Green: focused Task 6 tests — 16 passed.
- Related manifest/runner tests — 32 passed.
- Full suite — 396 passed.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m compileall -q src` —
  passed.
- `git diff --check` — passed.
- `v0.1.0^{}` remains
  `4fc3d6bfd61ad6b4057de66abcf13605af3c2b9c`.

## Reviews

- Specification-compliance review: PASS.
- Code-quality review: PASS.
- No callable subagent-dispatch capability was exposed; separate coordinator
  review passes were recorded in `AGENT_LOG.md`.

Implementation commit hash is recorded after commit in `AGENT_LOG.md`.
