# SafeFix Implementation Plan (Finalized)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement SafeFix v1 — a self-owned Python CLI coding-agent harness that repairs pytest failures under path guardrails, strict-subset feedback progress, file snapshots, keyring credentials, and MockLLM-deterministic tests (per `SPEC.md`).

**Architecture:** Single-process layered pipeline with an **embedded phase state machine** in `SessionRunner` (no event bus, no LangChain/AutoGen/etc.):

```
CLI (run | credentials)
  → ConfigLoader + CredentialsResolver (keyring only)
  → SessionRunner (phases)
       INIT → READY → DISPATCH → EVALUATE → STOP
```

**Tech Stack:** Python ≥ 3.11, stdlib `tomllib`, `pytest`, `keyring`, HTTP client for OpenAI-compatible API, packaging (wheel/sdist), GitHub Actions + GitLab CI (`unit-test`).

## Global Constraints

- Source of truth: root `SPEC.md` (final errata + consistency cleanup applied).
- Own agent loop; no high-level agent frameworks.
- Core mechanisms unit-testable with injected `MockLLMClient` (no network, no real API key).
- Real CLI credentials: **OS keyring only** (no env / `.env` / plaintext fallback).
- LLM tools only: `read_file`, `list_dir`, `search_code`, `apply_patch`, `finish`.
- Paths in ToolCalls are **project-root relative**; `"."` = root; no absolute paths; normalize then no escape.
- Tests are **readable**, never writable via `apply_patch`.
- Progress: strict subset on `failure_id` sets; SUCCESS iff \(F = \emptyset\) on valid suite.
- TDD: red → green → refactor for every task; commit after each green task.
- Package layout under `src/safefix/`; tests under `tests/`.
- Defaults: `max_steps=30`, `max_rounds=10`, `max_no_progress_rounds=3`; HITL `>3` files or `>80` lines.
- Absolute paths in ToolCalls: `PARSE_ERROR` (locked).
- Every implementation, specification-review, and code-quality-review subagent must first read and obey root `AGENTS.md`.
- Engineering discipline follows boundary defense plus trust of validated internal invariants; validate once at untrusted boundaries and do not add repeated internal defensive branches.
- Code-quality review must explicitly check excessive defensive programming, including duplicated validation, broad exception handling, invented fallback behavior, and branches for impossible states.

## File Structure

```text
pyproject.toml
README.md
.github/workflows/ci.yml
.gitlab-ci.yml
src/safefix/
  __init__.py
  __main__.py
  cli.py
  config.py
  credentials.py
  models.py
  patch_preflight.py
  paths.py
  llm/
    base.py
    mock.py
    openai_compatible.py
  parse.py
  guardrail.py
  approval.py
  tools/
    read_file.py
    list_dir.py
    search_code.py
    apply_patch.py
    finish.py
    dispatch.py
  testrunner.py
  junit.py
  feedback.py
  snapshot.py
  session_state.py
  memory.py
  artifacts.py
  context.py
  runner.py
tests/
  unit/…
  integration/…
  fixtures/projects/…          # tiny pytest projects for loop demos
  mechanism/…                  # A.6 demos
```

**Dependency order:** models → config/paths → credentials → parse → guardrail/approval → tools/snapshot → testrunner/junit/feedback → llm → context/memory/artifacts → runner → cli → packaging/CI → mechanism demos.

---

### Task 0: Establish repository-level engineering rules

**Files:**
- Create: `AGENTS.md`
- Modify: `PLAN.md`, `AGENT_LOG.md`

**Dependencies:** None. This is a non-implementation prerequisite and must not modify `SPEC.md`, product scope, or implementation code.

- [x] **Step 1: Create the rules file.** Copy the approved SafeFix Agent Engineering Rules into root `AGENTS.md`, including source-of-truth priority, mandatory Superpowers workflow, boundary-defense/internal-invariant discipline, strict TDD, scope limits, and two-review requirements.
- [x] **Step 2: Verify the rules file content.** Run `test -f AGENTS.md && rg -n "Mandatory workflow|Sources of truth|Avoid excessive defensive programming|Strict TDD|Review requirements|AGENT_LOG.md" AGENTS.md`. Expected: exit 0 with every required section and logging rule found.
- [x] **Step 3: Update the plan gates.** Add the AGENTS.md read requirement, boundary-defense/internal-invariant rule, and excessive-defensive-programming review check to Global Constraints. Add the exact subagent prompt requirement “先阅读 SPEC.md、PLAN.md、AGENTS.md” and state that an AGENTS.md violation cannot pass code-quality review in the execution handoff.
- [x] **Step 4: Review and log Task 0.** Review AGENTS.md line-by-line against the approved user text; review PLAN.md for the three new gates; confirm `git diff --check -- AGENTS.md PLAN.md AGENT_LOG.md`; append the creation, verification command, review result, skill usage, no-deviation statement, and pending commit subject to AGENT_LOG.md.
- [x] **Step 5: Commit the prerequisite separately.** Run `git add AGENTS.md PLAN.md AGENT_LOG.md && git commit -m "docs: add repository agent engineering rules"`. Expected: one commit containing only Task 0 documentation/rule changes. Do not start Task 1 in the same execution unit.

---

### Task 1: Project scaffold + models

**Files:**
- Create: `pyproject.toml`, `src/safefix/__init__.py`, `src/safefix/models.py`
- Test: `tests/unit/test_models.py`

**Interfaces:**
- Produces: dataclasses/enums `StopReason`, `GuardDecision`, `ToolName`, `Change`, `ToolCall`, `FailureSet`, `Feedback`, `Config`, `SessionResult`

**Red tests (models only; no ConfigLoader behavior)**

```python
# tests/unit/test_models.py
from safefix.models import (
    StopReason, GuardDecision, ToolName, Change, ToolCall,
    FailureSet, Feedback, Config, SessionResult,
)

def test_stop_reason_values():
    assert {item.name for item in StopReason} == {
        "SUCCESS", "REQUESTED", "MAX_STEPS", "MAX_ROUNDS",
        "NO_PROGRESS", "ERROR", "CONFIG_ERROR",
    }

def test_tool_call_apply_patch_roundtrip():
    change = Change("src/app.py", "return 1", "return 2")
    call = ToolCall(tool=ToolName.APPLY_PATCH, changes=(change,))
    assert call.changes[0] == change

def test_failure_set_ids():
    failures = FailureSet(frozenset({"case-a", "case-b"}))
    assert failures.ids == frozenset({"case-a", "case-b"})

def test_config_defaults():
    config = Config()
    assert config.max_steps == 30
    assert config.max_rounds == 10
    assert config.max_no_progress_rounds == 3

def test_config_fields_exist():
    config = Config()
    assert hasattr(config, "allowed_paths")
    assert hasattr(config, "excluded_paths")
    assert hasattr(config, "pytest_args")
    assert hasattr(config, "base_url")
    assert hasattr(config, "model")
```

**Minimal implementation + commit** `feat: scaffold package and core models`

---

### Task 2: ConfigLoader (`safefix.toml` + CLI merge)

**Files:**
- Create: `src/safefix/config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: `Config` from `models.py`
- Produces: `load_config(project_root: Path, cli_overrides: dict) -> Config`
  Raises `ConfigError` on unknown keys, bad types, disallowed `pytest_args`, empty required `base_url`/`model` when `require_llm=True`.

**Red tests** (secrets, numeric bounds, allowlist positives/negatives, malformed TOML,
plus test_require_llm_needs_base_url_model, test_unknown_key_rejected,
test_cli_overrides_toml, test_forbidden_pytest_args, and
test_allowlisted_pytest_args_ok moved from Task 1)

**Minimal implementation + commit** `feat: config loader with allowlisted pytest_args`

---

### Task 3: Path policy (read-denied / write-denied / writable set)

**Files:**
- Create: `src/safefix/paths.py`
- Test: `tests/unit/test_paths.py`

**Red tests** (tests readable, root escape denied, venv/.git/.env denied, src default writable, allowed_paths replace semantics)

**Minimal implementation + commit** `feat: read/write path policies`

---

### Task 4: Credentials (keyring-only)

**Files:**
- Create: `src/safefix/credentials.py`
- Test: `tests/unit/test_credentials.py`

**Red tests** (status, set, get, clear, no env fallback)

**Minimal implementation + commit** `feat: keyring-only credentials`

---

### Task 5: ActionParser (exact JSON contract, one ToolCall)

**Files:**
- Create: `src/safefix/parse.py`
- Test: `tests/unit/test_parse.py`

**Red tests** (absolute path, array, unknown field, multiple actions, finish)

**Minimal implementation + commit** `feat: strict ToolCall JSON parser`

---

### Task 6: Guardrail + ApprovalProvider

**Files:**
- Create: `src/safefix/guardrail.py`, `src/safefix/approval.py`
- Test: `tests/unit/test_guardrail.py`, `tests/unit/test_approval.py`

**Red tests** (permanent DENY test edit, >3 files or >80 lines → REQUIRE_APPROVAL, stub deny, non-interactive deny, boundary 3/80)

**Minimal implementation + commit** `feat: guardrail and approval providers`

---

### Task 7a: SnapshotStore

**Files:**
- Create: `src/safefix/snapshot.py`
- Test: `tests/unit/test_snapshot.py`

**Red tests** (baseline_contents, best_contents, restore, pre-apply snapshot, atomic restore on fail)

**Minimal implementation + commit** `feat: snapshot store`

---

### Task 7b: apply_patch tool

**Files:**
- Create: `src/safefix/tools/apply_patch.py`
- Test: `tests/unit/test_apply_patch.py`

**Red tests** (exact match, non-overlapping, pre-check restore, transactional temp-file)

**Minimal implementation + commit** `feat: apply_patch transactional tool`

---

### Task 8: read_file / list_dir / search_code + dispatch

**Files:**
- Create: `src/safefix/tools/read_file.py`, `list_dir.py`, `search_code.py`, `finish.py`, `dispatch.py`
- Test: `tests/unit/test_read_tools.py`, `tests/unit/test_dispatch.py`

**Red tests** (test readable, root escape denied, search finds string, finish request)

**Minimal implementation + commit** `feat: read tools and dispatcher`

---

### Task 9: TestRunner + JUnit `failure_id` parser

**Files:** Create src/safefix/testrunner.py and src/safefix/junit.py; test
tests/unit/test_junit.py and tests/unit/test_testrunner.py; fixtures under
tests/fixtures/junit/.

Required identity tests, written before implementation:

    test_failure_id_stable_between_baseline_and_later_report
    test_parameterized_instances_have_distinct_stable_ids
    test_failed_error_status_change_preserves_failure_id
    test_collection_error_gets_deterministic_synthetic_id

Identity is locked as classname + "::" + name, preserving parameter suffixes;
failed/error is metadata only. Collection errors use
collection_error::<suite>::<sha256(normalized_message)[:16]>.
First red command: python -m pytest tests/unit/test_junit.py tests/unit/test_testrunner.py -q.
Expected: collection fails because safefix.junit and safefix.testrunner are absent.
Minimal implementation: parse valid JUnit, preserve parameterized names, emit
deterministic synthetic IDs, and execute python -m pytest with shell=False.
Green command: python -m pytest tests/unit/test_junit.py tests/unit/test_testrunner.py -q.
Expected: all selected tests pass. Commit:
git add src/safefix/junit.py src/safefix/testrunner.py tests/unit/test_junit.py tests/unit/test_testrunner.py tests/fixtures/junit AGENT_LOG.md
git commit -m "feat: pytest runner and stable failure ids"
Prerequisites: Tasks 1 and 2.

---

### Task 10: FeedbackEngine (strict subset)

**Files:**
- Create: `src/safefix/feedback.py`
- Test: `tests/unit/test_feedback.py`

**Red tests** (better/same/worse/success/incomparable)

**Minimal implementation + commit** `feat: strict-subset feedback engine`

---

### Task 11: LLM clients (Mock + OpenAI-compatible)

**Files:** Create src/safefix/llm/base.py, src/safefix/llm/mock.py, and
src/safefix/llm/openai_compatible.py; test
tests/unit/test_mock_llm.py and tests/unit/test_openai_client.py.

OpenAICompatibleClient must use an injectable HTTP transport/client exposing
post(url, headers, json_body, timeout). Unit tests use only FakeTransport, which
records the request and returns a fixed response. They do not access network
sockets, a real HTTP client, or real credentials.
First red command: python -m pytest tests/unit/test_mock_llm.py tests/unit/test_openai_client.py -q.
Required tests: scripted response, deterministic exhaustion, fake transport
request shape, and transport-error mapping. Expected red result: missing LLM
modules. Minimal implementation: define the protocol, implement MockLLM, send
the OpenAI-compatible request through injected transport, extract assistant
content, and map bounded errors.
Green command: python -m pytest tests/unit/test_mock_llm.py tests/unit/test_openai_client.py tests/unit/test_parse.py -q.
Expected: all selected tests pass and FakeTransport records every request.
Commit: git add src/safefix/llm tests/unit/test_mock_llm.py tests/unit/test_openai_client.py AGENT_LOG.md
git commit -m "feat: mock and injectable OpenAI-compatible clients"
Prerequisites: Tasks 1 and 5.

---

### Task 12: SessionState, artifacts, ProjectMemoryStore, ContextBuilder

Task 12 is four independently dispatched execution units. Each unit has the
following exact red-green-review-commit cycle.

#### Task 12a: SessionState

Files: create src/safefix/session_state.py and tests/unit/test_session_state.py.
Dependencies: Tasks 1, 9, and 10.

1. First write test_session_state_defaults,
   test_session_state_records_tool_and_guard_events, and
   test_session_state_updates_best_checkpoint. The tests assert F0 is immutable,
   counters start at zero, and event lists are capped.
2. Run python -m pytest tests/unit/test_session_state.py -q.
   Expected: collection fails because safefix.session_state is missing.
3. Implement the state dataclass, counter methods, immutable F0, U_best,
   bounded recent events, and patch fingerprints. Do not load project memory.
4. Run python -m pytest tests/unit/test_session_state.py tests/unit/test_feedback.py -q.
   Expected: all selected tests pass.
5. Specification review checks the state fields against SPEC 7.7; quality review
   checks mutation boundaries and cap constants. Append both reviews, then run
   git add src/safefix/session_state.py tests/unit/test_session_state.py AGENT_LOG.md
   and git commit -m "feat: add live session state".

#### Task 12b: ArtifactWriter

Files: create src/safefix/artifacts.py and tests/unit/test_artifacts.py.
Dependencies: Task 12a.

1. First write test_artifact_contains_counters_and_failure_diffs,
   test_artifact_redacts_secret_values, and test_artifact_written_for_stop_result.
2. Run python -m pytest tests/unit/test_artifacts.py -q.
   Expected: collection fails because safefix.artifacts is missing.
3. Implement human-readable JSON output containing counters, stop reason,
   failure-set diffs, and guard events. Redact keys, source bodies, full
   tracebacks, and transcripts.
4. Run python -m pytest tests/unit/test_artifacts.py tests/unit/test_session_state.py -q.
   Expected: all selected tests pass.
5. Review artifact schema and redaction, append both review results, then run
   git add src/safefix/artifacts.py tests/unit/test_artifacts.py AGENT_LOG.md
   and git commit -m "feat: write redacted session artifacts".

#### Task 12c: ProjectMemoryStore

Files: create src/safefix/memory.py and tests/unit/test_memory.py.
Dependencies: Task 12b.

1. First write test_memory_not_loaded_by_default,
   test_use_memory_loads_capped_slice, test_project_memory_isolation, and
   test_memory_has_no_keys_or_source.
2. Run python -m pytest tests/unit/test_memory.py -q.
   Expected: collection fails because safefix.memory is missing.
3. Implement per-project JSON under the user data directory, deterministic
   project IDs, fixed caps, update behavior, and explicit opt-in loading.
4. Run python -m pytest tests/unit/test_memory.py tests/unit/test_artifacts.py -q.
   Expected: all selected tests pass.
5. Review storage location, project isolation, caps, and secret exclusion;
   append both reviews, then run
   git add src/safefix/memory.py tests/unit/test_memory.py AGENT_LOG.md
   and git commit -m "feat: add capped opt-in project memory".

#### Task 12d: ContextBuilder

Files: create src/safefix/context.py and tests/unit/test_context.py.
Dependencies: Tasks 8, 11, and 12a-12c.

1. First write test_context_without_memory_has_no_project_slice,
   test_context_with_memory_includes_capped_slice, and
   test_context_contains_failure_and_tool_feedback.
2. Run python -m pytest tests/unit/test_context.py -q.
   Expected: collection fails because safefix.context is missing.
3. Implement a bounded structured context containing current failures, best
   summary, recent tool/guard feedback, and the optional memory slice. Never
   include credentials or full source.
4. Run python -m pytest tests/unit/test_context.py tests/unit/test_memory.py tests/unit/test_session_state.py -q.
   Expected: all selected tests pass.
5. Review context size and secret/source exclusion, append both reviews, then
   run git add src/safefix/context.py tests/unit/test_context.py AGENT_LOG.md
   and git commit -m "feat: build bounded repair context".

### Task 13: SessionRunner (phase state machine) (split for manageable TDD)

Treat 13a, 13b, 13c, and 13d as separate dispatch units. Each receives a new
implementation subagent, then a specification review and a code-quality review;
no later unit starts before the prior unit commits.

#### Task 13a: INIT, baseline, and early stops

Files: create src/safefix/runner.py and tests/unit/test_runner_init.py.
Dependencies: Tasks 2, 3, 4, 9, 10, and 12.

1. First write test_valid_baseline_freezes_f0,
   test_valid_empty_baseline_stops_success,
   test_invalid_baseline_stops_config_error, and
   test_nonempty_f0_with_empty_writable_set_stops_config_error.
2. Run python -m pytest tests/unit/test_runner_init.py -q.
   Expected: collection fails because SessionRunner is missing.
3. Implement INIT fail-fast, credential/config/path resolution, valid baseline
   rules, F0 freeze, best checkpoint initialization, and the two-phase empty
   writable-set check.
4. Run python -m pytest tests/unit/test_runner_init.py tests/unit/test_config.py tests/unit/test_testrunner.py -q.
   Expected: all selected tests pass.
5. Review phase ordering and valid-baseline semantics; append both reviews, then
   run git add src/safefix/runner.py tests/unit/test_runner_init.py AGENT_LOG.md
   and git commit -m "feat: add runner init and baseline stops".

#### Task 13b: READY and DISPATCH for non-patch actions

Files: modify src/safefix/runner.py and create tests/unit/test_runner_dispatch.py.
Dependencies: Tasks 6, 8, and 13a.

1. First write test_read_tool_returns_to_ready,
   test_list_and_search_tools_return_to_ready, test_finish_stops_requested,
   and test_denied_patch_returns_feedback_without_round.
2. Run python -m pytest tests/unit/test_runner_dispatch.py -q.
   Expected: failures because READY/DISPATCH behavior is not implemented.
3. Implement step increment before each LLM call, one parsed ToolCall,
   guard/approval routing, non-patch dispatch back to READY, and finish as
   REQUESTED only.
4. Run python -m pytest tests/unit/test_runner_dispatch.py tests/unit/test_dispatch.py tests/unit/test_guardrail.py -q.
   Expected: all selected tests pass.
5. Review step accounting and finish semantics; append both reviews, then run
   git add src/safefix/runner.py tests/unit/test_runner_dispatch.py AGENT_LOG.md
   and git commit -m "feat: dispatch runner tool actions".

#### Task 13c: EVALUATE, feedback, rollback, and success

Files: modify src/safefix/runner.py and create tests/unit/test_runner_evaluate.py.
Dependencies: Tasks 7a, 7b, 10, and 13b.

1. First write test_better_patch_updates_best,
   test_same_patch_restores_best_and_increments_no_progress,
   test_worse_or_new_failure_restores_best,
   test_successful_patch_counts_round, and
   test_post_patch_infra_error_restores_and_stops_error.
2. Run python -m pytest tests/unit/test_runner_evaluate.py -q.
   Expected: failures because patch landing and EVALUATE are incomplete.
3. Implement mandatory full-suite evaluation after a successful land,
   immediate round increment for valid reports, strict-subset best updates,
   restore for same/worse, and restore-plus-ERROR for infrastructure failure.
4. Run python -m pytest tests/unit/test_runner_evaluate.py tests/unit/test_feedback.py tests/unit/test_snapshot.py -q.
   Expected: all selected tests pass.
5. Review success authority and rollback invariants; append both reviews, then
   run git add src/safefix/runner.py tests/unit/test_runner_evaluate.py AGENT_LOG.md
   and git commit -m "feat: evaluate patches with rollback and progress".

#### Task 13d: Limits, parse errors, retries, and finalization

Files: modify src/safefix/runner.py and create tests/unit/test_runner_limits.py.
Dependencies: Task 13c.

1. First write test_parse_error_consumes_step_not_round, test_max_steps_stop,
   test_max_rounds_stop, test_no_progress_stop, test_transport_retry_then_error,
   and test_stop_restores_all_touched_files_and_writes_artifact.
2. Run python -m pytest tests/unit/test_runner_limits.py -q.
   Expected: failures for missing limit, retry, and finalization branches.
3. Implement finite transport retries, stop checks at READY, parse-error
   feedback, exact step/round accounting, no-progress stopping, final best
   restoration, artifact writing, and SessionResult construction.
4. Run python -m pytest tests/unit/test_runner_*.py tests/unit/test_artifacts.py tests/unit/test_context.py -q.
   Expected: all runner, artifact, and context tests pass.
5. Review stop precedence, counter definitions, and final tree restoration;
   append both reviews, then run
   git add src/safefix/runner.py tests/unit/test_runner_limits.py AGENT_LOG.md
   and git commit -m "feat: enforce runner limits and finalization".



---

### Task 14: CLI (`run` + credentials) + exit codes

Files: create src/safefix/cli.py and src/safefix/__main__.py; test
tests/unit/test_cli.py.
Dependencies: Tasks 2, 4, and 13.

1. First write test_run_command_passes_config_overrides,
   test_credentials_set_status_clear, test_noninteractive_approval_denies,
   and test_exit_code_mapping_for_all_stop_reasons. Use injected keyring,
   SessionRunner, and approval boundaries; do not use a real key or network.
2. Run python -m pytest tests/unit/test_cli.py -q.
   Expected: collection fails because safefix.cli and safefix.__main__ are absent.
3. Implement argparse entrypoints for exactly run and credentials
   set/status/clear, reject raw API-key options, pass CLI overrides to
   ConfigLoader, select the production client for run, and map StopReason to
   exit codes 0/1/2/3.
4. Run python -m pytest tests/unit/test_cli.py tests/unit/test_config.py tests/unit/test_credentials.py tests/unit/test_runner_limits.py -q.
   Expected: all selected tests pass.
5. Run a specification-compliance review and a separate code-quality review;
   append both to AGENT_LOG.md, then commit:
   git add src/safefix/cli.py src/safefix/__main__.py tests/unit/test_cli.py AGENT_LOG.md
   git commit -m "feat: CLI entrypoints and exit codes".

**Files:**
- Create: `src/safefix/cli.py`, `src/safefix/__main__.py`
- Test: `tests/unit/test_cli.py`

---

### Task 15: Packaging metadata, README, dual CI

Treat 15a, 15b, and 15c as separate dispatch units with a fresh subagent,
specification review, quality review, AGENT_LOG entry, and commit per unit.

#### Task 15a: Build metadata and install smoke test

Files: modify pyproject.toml and create tests/unit/test_packaging.py.
Dependencies: Tasks 1 and 14.

1. First write test_pyproject_declares_package_and_cli. It must assert Python
   >=3.11, build-system metadata, src package discovery, the safefix console
   script, runtime dependencies, and project metadata.
2. Run python -m pytest tests/unit/test_packaging.py -q.
   Expected: assertion failure naming the first missing packaging field.
3. Add exact build metadata and the console entry point. Create a disposable
   build environment and install the build frontend with:
   BUILD_ENV=$(mktemp -d /tmp/safefix-build-venv.XXXXXX)
   python -m venv "$BUILD_ENV"
   "$BUILD_ENV/bin/python" -m pip install build
   "$BUILD_ENV/bin/python" -m build --wheel --sdist --outdir /tmp/safefix-dist
   Expected: wheel and sdist files are created under /tmp/safefix-dist.
4. Install the wheel into the same fresh environment and smoke-test the CLI:
   "$BUILD_ENV/bin/python" -m pip install --no-deps /tmp/safefix-dist/*.whl
   "$BUILD_ENV/bin/python" -m safefix --help
   Expected: help output and exit 0.
5. Run python -m pytest tests/unit/test_packaging.py tests/unit/test_cli.py -q;
   expected all selected tests pass. Review metadata and install reproducibility,
   then commit with
   git add pyproject.toml tests/unit/test_packaging.py AGENT_LOG.md
   and git commit -m "chore: finalize package metadata and CLI entrypoint".

#### Task 15b: README reproduction guide

Files: create README.md and tests/unit/test_readme.py.
Dependencies: Task 15a.

1. First write test_readme_documents_install_run_credentials_and_limits. Assert
   obtain/install, wheel/sdist, safefix run, keyring setup, no environment or
   .env fallback, platform limits, and no-WebUI/cloud scope.
2. Run python -m pytest tests/unit/test_readme.py -q.
   Expected: the test is collected and its assertion fails because README.md is absent.
3. Write the reproduction guide with valid install/run commands, credentials
   workflow, use-memory note, non-interactive approval behavior, and Release
   artifact instructions.
4. Run python -m pytest tests/unit/test_readme.py tests/unit/test_packaging.py -q.
   Expected: all selected tests pass.
5. Review every command against the actual CLI, append both reviews, then run
   git add README.md tests/unit/test_readme.py AGENT_LOG.md
   and git commit -m "docs: add installation and usage guide".

#### Task 15c: GitHub Actions and GitLab unit-test job

Files: create .github/workflows/ci.yml, .gitlab-ci.yml, and
tests/unit/test_ci_config.py.
Dependencies: Task 15a.

1. First write test_github_workflow_runs_unit_tests and
   test_gitlab_has_unit_test_job. Parse the files and assert Python setup,
   dependency installation, pytest execution, and exact GitLab job key unit-test.
2. Run python -m pytest tests/unit/test_ci_config.py -q.
   Expected: the test is collected and its assertion fails because the CI files are absent.
3. Add push-triggered GitHub Actions and a GitLab unit-test job that installs
   package/test dependencies and runs python -m pytest.
4. Run python -m pytest tests/unit/test_ci_config.py tests/unit/test_packaging.py -q.
   Expected: all selected tests pass; validate YAML syntax if a YAML parser is
   available.
5. Review CI secret-free behavior, append both reviews, then run
   git add .github/workflows/ci.yml .gitlab-ci.yml tests/unit/test_ci_config.py AGENT_LOG.md
   and git commit -m "ci: add GitHub and GitLab test pipelines".


---

### Task 16: Mechanism demos (A.6)

Files: create tests/mechanism/test_demo_deny.py,
tests/mechanism/test_demo_feedback_changes_action.py, and
tests/mechanism/test_demo_progress_rollback.py; add only the smallest fixed
fixtures under tests/fixtures/projects/.
Dependencies: Tasks 6, 8, 10, and 13.

1. First write test_demo_test_edit_is_permanently_denied,
   test_feedback_changes_the_next_scripted_action, and
   test_better_same_worse_and_no_progress_are_deterministic. Use only injected
   MockLLM, fake approval, and local pytest fixtures.
2. Run python -m pytest tests/mechanism -q.
   Expected: tests are collected and fail because the mechanism fixtures and
   final SessionRunner wiring are not implemented.
3. Implement the smallest fixed project fixtures and scripted responses needed
   to demonstrate permanent DENY, feedback-driven next action, strict-subset
   progress, rollback, and no-progress stopping. Do not add shell execution,
   network access, or public mock mode.
4. Run python -m pytest tests/mechanism -q && python -m pytest tests -q.
   Expected: all mechanism tests and the full test suite pass.
5. Run a specification-compliance review and a separate code-quality review;
   append both to AGENT_LOG.md, then commit:
   git add tests/mechanism tests/fixtures/projects AGENT_LOG.md
   git commit -m "test: demonstrate SafeFix mechanisms offline".

**Files:**
- Create: `tests/mechanism/test_demo_deny.py`, `test_demo_feedback_changes_action.py`, `test_demo_progress_rollback.py`

---

## Execution status

The original task execution records remain historical evidence. The current
main branch is under a SPEC recovery/traceability correction and must not be
described as complete until the replacement reviews and final verification
listed below pass.

| Tasks | Status | Evidence |
|-------|--------|----------|
| 0–8 | historical implementation | superseded for final SPEC review |
| 9–11 | historical implementation | superseded for final SPEC review |
| 12a–12d | historical implementation | superseded for final SPEC review |
| 13a–13d | historical implementation | superseded for final SPEC review |
| 14 | historical implementation | superseded for final SPEC review |
| 15a–15c | historical implementation | superseded for final SPEC review |
| 16 | historical mechanism evidence | superseded for final SPEC review |

Integration evidence: branch `safefix-task-15` was merged into `main` as
`15ee2f2` on 2026-08-05. That historical result is not final evidence because
the merged tree was reviewed while `SPEC.md` was empty. The current repair has
focused tests green; replacement full verification is pending.

## Spec coverage checklist (self-review)

| SPEC area | Task(s) |
|-----------|---------|
| Models / stop reasons | 1 |
| Config TOML/CLI / pytest_args | 2, 14 |
| Path read/write policies | 3, 6, 8 |
| Credentials keyring-only | 4, 14 |
| ToolCall JSON contract | 5 |
| Guardrail + HITL | 6 |
| Snapshots + apply_patch txn | 7a/7b |
| Read tools | 8 |
| TestRunner + failure_id | 9 |
| Feedback subset rules | 10 |
| LLM Mock + OpenAI-compatible | 11 |
| Memory / artifacts / context | 12 |
| Main loop | 13 |
| CLI + exit codes | 14 |
| Distro + dual CI + README | 15 |
| A.6 demos / A1–A9 | 13, 16 |

**Parallelism:** Tasks 2–4 after 1 can partially parallelize; Task 8 after 3+5; Task 14 after 2+4+13.

---

## Execution handoff — selected

The formal execution method is **Subagent-Driven**. This choice is complete;
the plan must not ask again between planning and implementation.

At implementation time, dispatch one fresh implementation subagent for every
execution unit, including 12a-12d, 13a-13d, and 15a-15c. Before each commit:

1. Run a specification-compliance review against SPEC.md.
2. Run a separate code-quality review.
3. Require both reviews to pass.
4. Append red/green commands, review findings, commit hash, and any blocker to
   AGENT_LOG.md.
5. Every implementation, specification-review, and code-quality-review
   subagent prompt must begin with: “先阅读 SPEC.md、PLAN.md、AGENTS.md”.
6. Any implementation or review finding that violates AGENTS.md blocks the
   task; the code-quality review must reject it until corrected and logged.

Do not cold-start or repeat brainstorming. Task 0 is a non-implementation
prerequisite; after its dedicated commit, begin Task 1.

## SPEC recovery traceability repair (status-only record)

- [x] Restore the final approved SPEC and SPEC_PROCESS artifacts from commit
  `c3247dd` after confirming `eefdbf0` contained an empty SPEC.
- [x] Record that prior empty-SPEC compliance reviews are superseded.
- [x] Repair implementation contracts without adding product scope: exit-code
  mapping, valid test reports, strict-subset feedback, first-touch snapshots,
  deterministic path/CLI boundaries, bounded memory context, and runtime
  error handling.
- [x] Complete fresh specification-compliance review, code-quality review, and
  final integration verification on main. Repository evidence is recorded in
  `AGENT_LOG.md`; hosted Release evidence is now provided by the published
  `v0.1.0` wheel/sdist Release and README links.

This section is a traceability correction only; it does not redesign Tasks 1–16
or reopen brainstorming.
