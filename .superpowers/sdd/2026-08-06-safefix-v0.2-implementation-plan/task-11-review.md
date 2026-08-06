# SafeFix v0.2 Task 11 independent review

## Scope and review basis

Reviewed the supplied worktree:

`/home/selflo/MyCodes/summer-ai/safefix/.worktrees/safefix-v0.2`

Reviewed the repository instructions, `SPEC.md`, `PLAN.md`, the approved v0.2
design and implementation plan, Task 11 brief and implementer report, prior
Task 10 scoped re-review, and the supplied package
`review-02b2ba3..466f711.diff` (implementation commit `ba54550` plus its
documentation commits). No production or test source was edited and no commit
was created by this review; only this review report was written.

## Verdict

- Specification compliance: **FAIL — blocking re-review required**.
- Code quality: **FAIL — blocking re-review required**.
- Overall Task 11 verdict: **FAIL**.

The repair/evaluation invariants are preserved, but the operator-control
contract is incomplete: `/pause` and `/resume` do not implement a paused
state, and `/status` does not emit a current snapshot.

## Specification-compliance review

### P1 — HIGH: `/pause` and `/resume` are observed no-ops

The v0.2 design requires `/pause` to prevent the next Repair Model decision and
enter `PAUSED`, with `/resume` returning to `READY` (design lines 264–272).
`SessionRunner._consume_ready_commands()` instead handles both commands and
`/status` with the same branch:

```text
src/safefix/runner.py:441-442
elif command.kind in {"pause", "resume", "status"}:
    self._emit_control(command.kind, {"status": "observed"})
```

No paused flag or phase transition is recorded. `_ready_stop_reason()` then
returns `None` and the main loop immediately increments the step and calls
`_complete()` (`src/safefix/runner.py:204-222`). Consequently a queued
`/pause` cannot prevent the next LLM call, and `/resume` cannot restore a
paused controller because no pause state exists. This violates the explicit
operator semantics even though guidance and `/stop` are consumed at a READY
boundary.

Required resolution:

- Add an explicit paused controller state/phase at the READY boundary.
- While paused, do not call the Repair Model or dispatch a new action.
- Consume `/resume` to return to READY; keep `/stop` safe and terminal from
  the paused state.
- Define and test the interaction with pending approval, including that
  `/approve` and `/deny` remain scoped to the pending action.
- Add tests for commands queued during a fake blocked LLM, patch, and pytest
  operation to prove they are deferred until the next safe boundary, then
  test `/pause` actually gates the next LLM decision.

### P2 — MEDIUM: `/status` does not emit the required current snapshot

The design requires `/status` to emit the current snapshot without changing
state (design lines 270–272). The implementation emits only
`{"command": "status", "status": "observed"}` through `_emit_control`
(`src/safefix/runner.py:441-442`); it does not include phase, step/round/
no-progress counters, current unresolved failures, best checkpoint, or
pending approval state. A consumer therefore cannot render the requested
status from the typed event.

Required resolution:

- Build a bounded, redacted status payload from harness-owned state.
- Emit it as a typed control/status event without changing counters,
  guidance, F0, manifest, current action, or approval state.
- Add an observable test asserting the snapshot fields and state
  immutability.

## Verified passing requirements

The following Task 11 and previously locked v0.2 invariants were checked and
remain conforming:

- **Harness authority and frozen evaluation:** post-patch evaluation verifies
  the frozen manifest and constructs the runner with exactly its manifest
  paths (`src/safefix/runner.py:274-283`, `:353-362`; enforced by
  `runner_for()`); the prior Task 10 scoped re-review also covers formal
  baseline scope, post-freeze mutation rejection, Test Model closure, and
  legacy factory compatibility.
- **Progress and rollback:** valid landed patches increment rounds, use
  `FeedbackEngine`, update the best checkpoint only for better/success, and
  restore best for same/worse/incomparable outcomes. New failure IDs are
  compared against immutable F0 and are not promoted into it. Existing
  `tests/unit/test_runner_evaluate.py` covers better, same, worse/new failure,
  success, infrastructure failure, and checkpoint restoration.
- **Model authority boundaries:** Repair actions still pass through
  `ActionParser`, Guardrail, and the existing dispatch boundary. The Repair
  Model has no test/manifest/F0 mutation path. Review/Test Model code does not
  acquire SUCCESS or baseline authority; `finish` remains `REQUESTED`.
- **Atomic operator timing:** guidance is drained only at the READY path
  before `_complete()`; queue submission during LLM/application/evaluation
  does not interrupt the synchronous operation. `/stop` is consumed at the
  next safe boundary, `_finalize()` restores best and writes the artifact, and
  the result is `OPERATOR_STOP`, not `SUCCESS`.
- **Approval:** queued large-patch approval becomes pending, waits without
  applying/evaluating, and `approve_pending()`/`deny_pending()` resolve it.
  Ordinary non-interactive approval remains fail-closed. `/approve` and
  `/deny` without pending approval produce typed ignored events.
- **Event/audit safety:** typed events are constructed as `SessionEvent` and
  payloads pass the existing recursive redaction/bounding boundary. Guidance
  is bounded before entering state/context. The callable event sink adapter
  remains compatible with v0.1 callers.
- **v0.1 regression:** the existing runner/CLI behavior remains green in the
  full suite, including callable event output, `finish -> REQUESTED`,
  non-interactive approval denial, and stop/exit mappings.

## Code-quality review

### Findings affecting quality and coverage

The implementation is small and uses specific exception handling; no new
dependency, shell execution, filesystem feature, provider registry, broad
`except Exception`, silent success fallback, or duplicated manifest authority
was found. The approval wait uses an explicit event and the queue remains
unable to dispatch tools directly.

However, the required operator controls are represented by a shared
“observed” branch rather than explicit stateful behavior, and the new
Task 11 tests cover guidance, stop, pending approval, and F0 regression but do
not cover `/pause`, `/resume`, or status payload semantics. This is a
load-bearing test gap because the current 42-test focused suite and 536-test
regression suite both pass while the design-required pause/status behavior is
absent. Code-quality review therefore cannot pass until the controls are
implemented explicitly and covered by observable tests.

The typed sink path is redaction-safe, but currently maps runner text events to
`Phase.READY`/`kind="control"` (`src/safefix/runner.py:495-514`). Re-review
should confirm this intentional compatibility representation remains adequate
for the eventual TUI/audit consumer; status and pause events must at minimum
carry the correct typed control payloads.

## Verification evidence

Fresh checks run in the specified worktree:

- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/unit/test_runner_operator.py tests/unit/test_approval.py tests/unit/test_runner_dispatch.py tests/unit/test_runner_evaluate.py tests/unit/test_runner_limits.py -q`
  — **42 passed**.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests -q`
  — **536 passed**.
- `git diff --check 02b2ba3..HEAD --` — passed.
- Worktree was clean before writing this report.
- Scoped commits reviewed: `ba54550`, `945b987`, `466f711`.

Passing tests do not override the two specification findings above; they
demonstrate regression stability, not completion of the missing operator
semantics.

## Re-review requirements

Do not close Task 11 until the implementer:

1. Implements a real READY/PAUSED controller transition, with `/pause`
   blocking the next Repair Model decision and `/resume` returning to READY.
2. Defines safe behavior for pause/resume/status while approval is pending;
   `/stop` must still restore best, write the artifact, and return
   `OPERATOR_STOP`.
3. Emits a bounded redacted status snapshot as a typed event without mutating
   harness state.
4. Adds load-bearing tests for pause gating, resume, status snapshot/state
   immutability, command ordering, pending approval, and controls queued during
   each atomic LLM/patch/pytest boundary.
5. Re-runs the focused Task 11 tests, all relevant runner/setup regressions,
   the full `tests` suite, `compileall`, and `git diff --check`.
6. Performs a fresh independent specification-compliance and code-quality
   review, then updates the implementer/review records with the new evidence
   and re-review result.
