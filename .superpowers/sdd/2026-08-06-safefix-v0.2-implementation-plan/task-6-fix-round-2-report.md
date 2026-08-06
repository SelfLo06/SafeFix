# SafeFix v0.2 Task 6 fix round 2/5

## Status

Fixed the remaining HIGH forged candidate-root ownership bypass only.

## Fix

`CandidateWorkspace` registers only sessions it creates, together with its
private token-bound ownership state. `StabilityRunner` now requires the
candidate root to resolve to a live registered workspace and validates that
workspace's marker before evaluation. A forged `.session-owner` marker in an
arbitrary external directory is rejected before the injected runner is called.
Successful workspace cleanup unregisters the session. Existing genuine
`CandidateWorkspace` roots remain accepted.

Added a regression covering both genuine-root acceptance and forged-marker
outside-root rejection. Existing path/symlink checks, cleanup behavior,
pristine per-run copies, classification semantics, and v0.1 `TestRunner`
behavior were left unchanged.

## TDD and verification

- Red: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest
  tests/unit/test_testprep_stability.py::test_stability_rejects_forged_marker_but_accepts_workspace_root -q`
  failed with `DID NOT RAISE` before the ownership binding.
- Green: focused Task 6 tests passed with **17 tests**.
- Related manifest/runner tests passed with **33 tests**.
- Full suite passed with **397 tests**.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m compileall -q src`
  passed.
- `git diff --check` passed.
- `git rev-parse 'v0.1.0^{}'` remains
  `4fc3d6bfd61ad6b4057de66abcf13605af3c2b9c`.

## Reviews

- Specification-compliance review: PASS.
- Code-quality review: PASS.
- No callable subagent-dispatch capability was exposed; separate coordinator
  review passes were recorded in `AGENT_LOG.md`.

## Commit

`d7d4c7dbe2e3f5017df03961910538ed1d9bd2b8` —
`fix: bind stability to live candidate workspaces`.
