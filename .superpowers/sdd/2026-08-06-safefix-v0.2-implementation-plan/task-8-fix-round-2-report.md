# SafeFix v0.2 Task 8 fix round 2/5 report

## Status

DONE — the remaining HIGH protected candidate-execution boundary is fixed.

## Fixes

1. Static candidate rules reject dynamic evaluation/import, process execution,
   dynamic attribute access, dynamic file paths, absolute/parent path access,
   and standard-library file-open aliases before staging or stability.
2. The service no longer trusts an optional `workspace.run_candidate` override.
   The default path remains the Harness-owned disposable snapshot runner;
   injected runners are retained only as the explicit constructor test seam.
3. The adversarial service regression embeds the absolute original project
   path, asserts rejection before any runner call, and verifies both source and
   existing-test bytes remain unchanged. Valid simple and read-only candidates
   remain covered.

## TDD evidence

- Red: pre-fix focused service/rules command failed 9 tests for the unhandled
  unsafe-rule and workspace-runner behaviors.
- Green: focused service/rules — **87 passed**.
- Related Task 8/manifest/runner suite — **173 passed**.
- Full regression: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest
  tests -q` — **463 passed**.

## Review and verification

- Specification-compliance review: PASS.
- Code-quality review: PASS; no broad exception handling, dependency, fallback
  runner, baseline/F0/Repair/SUCCESS authority, v0.1 tag, or unrelated scope
  was added.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m compileall -q src` —
  passed.
- `git diff --check` — passed.
- Immutable `v0.1.0^{}` remains
  `4fc3d6bfd61ad6b4057de66abcf13605af3c2b9c`.

## Commit

`4823695b84306976c8b29b3197b762ef098f8b33` —
`fix: close Task 8 candidate execution boundary`.
