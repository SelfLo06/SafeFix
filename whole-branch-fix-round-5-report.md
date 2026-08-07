# Whole-Branch Fix Round 5

## Scope

This round closes the two final P1 findings from the whole-branch review.

1. `.safefix` is SafeFix internal state. Central path policy now hard-denies
   it, excluding accepted generated tests from both no-`src` default discovery
   and explicit broad allowed paths. Guardrail denies those repair actions
   before `apply_patch` can write them. `CandidateWorkspace` remains the
   dedicated Harness-owned writer during test preparation.
2. Confirmed high-risk CLI setup now requires Review endpoint/model and obtains
   the Review-role keyring credential before constructing Runner. Runner also
   returns `CONFIG_ERROR` when a directly injected high-risk configuration
   lacks Review configuration or `final_review_client`, including a clean
   baseline that would otherwise return early `SUCCESS`.

Standard-mode final-review behavior and the existing high-risk final human
gate are unchanged.

## TDD Evidence

- Red: the initial focused command failed 6 new assertions covering internal
  path discovery, Guardrail broad-path approval, missing high-risk Review
  configuration, and missing final Review client success.
- Red: the added clean-baseline high-risk regression failed because Runner
  returned `SUCCESS` before reaching final review.
- Green: final focused/related command passed 133 tests.
- Green: full suite passed 602 tests.

## Reviews And Verification

- Specification-compliance review: PASS. The policy is centralized and covers
  default/broad write scopes, controlled internal writes, Review configuration,
  role credential, and both direct-runner success paths.
- Code-quality review: PASS. No new dependency, broad exception handling,
  credential fallback, unnecessary abstraction, duplicate precondition logic,
  dead code, or scope expansion.
- `PYTHONDONTWRITEBYTECODE=1 python -m compileall -q src tests` passed.
- `git diff --check` passed.
- Source/test commit: `3d9c2e0` (`fix: close final whole-branch P1 findings`).

## Preservation

The pre-existing dirty plan-local `progress.md`, root
`whole-branch-fix-round-2-report.md`, and immutable `v0.1.0` tag were not
modified by this fix.
