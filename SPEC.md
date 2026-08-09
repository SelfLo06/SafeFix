# SafeFix — Product & System Specification

> **Status:** Final specification; implementation traceability under repair
> **Date:** 2026-08-02
> **Project type:** AI4SE Final Project · A · Coding Agent Harness
> **Process record:** `SPEC_PROCESS.md`
> **Implementation plan:** root `PLAN.md` (finalized; execution and traceability records in `AGENT_LOG.md`)

---

## 1. Problem Statement

### 1.1 Problem

Small and medium **Python + pytest** projects often have failing tests that are tedious to fix by hand. Off-the-shelf coding agents are powerful, but typically:

- Bind the agent loop and governance to an external framework, making mechanisms hard to test offline without a real LLM.
- May edit tests, write outside the intended tree, or expand the task indefinitely.
- Lack a **code-defined** notion of repair progress and stop conditions grounded in the test failure set.

**SafeFix** is a self-implemented **Coding Agent Harness**: tools, path governance, test-feedback loop, and rollback are **deterministic code**. The LLM proposes candidate repairs under constraints; **whether a repair succeeded is decided only by full pytest results**.

### 1.2 Product one-liner

> An automatic bug-fix agent for small/medium **Python + pytest** projects, with **safety guardrails** and a **test-feedback closed loop** (local CLI).

### 1.3 What “safe” means

In SafeFix v1, **“safe” means constrained repair behavior**, not vulnerability scanning:

- Fix ordinary functional defects exposed by existing pytest failures.
- Only modify source within an allowed write set.
- By default, never modify test files.
- Dangerous writes are intercepted by **code** guardrails.
- Regressions roll back to the best checkpoint.
- Stop on consecutive lack of progress or hard limits.
- Success requires confirmation by a **full**, valid test run.

Security-themed bugs may appear later as fixtures; they are **out of v1 product scope**. SafeFix does **not** perform vulnerability scanning.

### 1.4 Target users

- Maintainers of small/medium pytest projects who want bounded automatic repair on a local tree.
- Course reviewers who must verify harness mechanisms offline (MockLLM) and reproduce install via Release artifacts.

### 1.5 Responsibility split (LLM vs Harness)

| Role | Responsibility |
|------|----------------|
| **LLM** | Analyze failures; choose *what to read / list / search / change*; propose **candidate source patches** via `apply_patch`. |
| **ActionParser** | Parse each model response into **exactly one** `ToolCall` (or parse failure). |
| **Guardrail** | `ALLOW` / `DENY` / `REQUIRE_APPROVAL`. |
| **ApprovalProvider** | Minimal in-loop HITL for soft-risk patches only. |
| **`apply_patch`** | **Sole** source-write entry point. |
| **Harness** | Deterministic validation, approval gating, disk write, **automatic full pytest**, progress judgment, rollback, **exclusive** SUCCESS and stop authority. |
| **`finish`** | Model may **request** stop only; it **cannot** declare SUCCESS. |

**Harness does not invent business fix code.** Patch *content* comes from the LLM; harness evaluates and constrains it.

---

## 2. Non-Goals (v1)

- WebUI, cloud deployment, server mode, Open Design.
- General-purpose shell or arbitrary command execution.
- Multi-language targets or non-pytest test runners.
- Automatic vulnerability scanning.
- Provider plugin registry; custom prompt/skill packs; config profiles/inheritance.
- `.env` file loading/management.
- Vector memory, SQLite, complex retrieval.
- Custom pytest plugins; fix-hint template systems.
- Complex multi-party approval workflows (async tickets, roles).
- Git worktree isolation for repair; whole-tree copy sandboxes.
- File locks / cross-process recovery; concurrent multi-agent writers on one tree.
- Creating, deleting, moving, or renaming files (patches only replace text in existing `.py` files).
- Public CLI “mock mode” (MockLLM is **test injection only**).
- Auto-approve flags (`-y` / `--yes`) that bypass HITL.

**Delivery note:** Per course clarification for pure CLI projects, **hosted Release links** (wheel/sdist) satisfy distribution; WebUI is not required.

---

## 3. User Stories

### US-1 — Bounded repair of the frozen failure set

**As** a developer maintaining a pytest project,
**I want** `safefix run .` to attempt repairs within step/round limits against the **failure set \(F_0\) frozen at session start**,
**so that** the repair target stays stable and does not expand due to new failures during repair, model-driven task expansion, or unrelated code analysis.

**Acceptance**

- Baseline run freezes \(F_0\) = all **failed/error** `failure_id`s from a **valid** baseline (see §7.5).
- Every baseline failed/error belongs to \(F_0\).
- New failures are used only for **regression** judgment; they are **never** merged into \(F_0\).
- v1 does **not** support selecting a subset of \(F_0\) to fix.
- SUCCESS iff current failed/error set \(F = \emptyset\) after a valid full suite evaluation (and baseline itself was valid).
- If \(F_0 = \emptyset\) after a **valid** baseline → SUCCESS (nothing to fix).

### US-2 — Declarative harness policy without embedding secrets

**As** a user,
**I want** project-root `safefix.toml` plus CLI overrides for rounds/steps, paths, safe pytest display args, and model endpoint,
**so that** runs are repeatable and reviewable without putting API keys in the repo.

**Acceptance**

- Only the locked config fields (§6); unknown keys fail at startup.
- Priority: **CLI > TOML > built-in defaults**.
- API keys never in TOML.
- `pytest_args` restricted to an allowlist that cannot change test selection/scope.

### US-3 — Credential safety

**As** a user,
**I want** to supply role-specific API keys through the current process environment,
**so that** keys are not committed, printed, or written to plaintext files by SafeFix.

**Acceptance**

- Real CLI reads only `SAFEFIX_TEST_API_KEY`, `SAFEFIX_REPAIR_API_KEY`, and
  `SAFEFIX_REVIEW_API_KEY` from the current process environment.
- SafeFix has no API-key command-line argument, `.env` loading, plaintext
  credential file, or fallback credential source.
- Diagnostic output identifies a missing variable by name only; it never
  prints key material.
- Unit tests / MockLLM paths never require a real key or network.

### US-4 — Hard deny of dangerous writes

**As** a repo owner,
**I want** permanent, non-overridable rejection of edits to tests, escapes from project root, `.git`/venv/credential files, path traversal, and illegal patches,
**so that** safety is enforced in code.

**Acceptance**

- `Guardrail` returns `DENY`; no disk write; cannot be approved away.
- Deterministic unit tests with constructed `ToolCall`s; no LLM required.

### US-5 — Minimal HITL for soft-risk patches

**As** a user,
**I want** large patches (too many files or lines) to require interactive approval,
**so that** I retain a veto without a full approval workflow product.

**Acceptance**

- Thresholds are **code constants**, not LLM judgments: files **> 3** or added+removed lines **> 80** → `REQUIRE_APPROVAL` (exactly 3 or 80 does **not** trigger).
- Stats computed **before** write; permanent deny rules take precedence.
- Stub `ApprovalProvider`: deny → no write; allow → normal apply + EVALUATE.
- Non-interactive mode (flag or non-TTY stdin) **defaults to deny**; no auto-approve option.
- Deny / approval-reject consume a **step**, not a **round**.

### US-6 — Feedback loop: progress, rollback, stop

**As** a user and course reviewer,
**I want** every landed candidate patch evaluated by a full suite, regressions restored to best, and hard stops on no-progress / limits,
**so that** the tree does not oscillate into a worse state and mechanisms are demonstrable.

**Acceptance**

- After successful `apply_patch` land, harness **always** runs full pytest (LLM has no `run_tests` tool).
- Progress uses **strict subset** rules on failure sets (§7.4); same/worse → rollback to best and `no_progress++`.
- A candidate `apply_patch` is **atomically evaluated**; v1 does **not** keep “no improvement but preparatory” intermediate trees. Multi-dependent edits must appear in **one** candidate patch.
- `finish` → `REQUESTED` only; SUCCESS only by harness when \(F = \emptyset\).

### US-7 — Offline verification of harness mechanisms

**As** a course reviewer or developer,
**I want** to verify the main loop, guardrails, feedback, and rollback with MockLLM and fixed fixtures without real LLM/API,
**so that** behavior is proven as deterministic code.

**Acceptance**

- Mock proposes editing a test file → deterministic `DENY`.
- Mock changes next action after injected tool/feedback failure (scripted).
- better / same / worse, rollback, no-progress stop are deterministic.
- No real API key read; no network.

### US-8 — Install and reproduce CLI delivery

**As** a reproducer of the course submission,
**I want** to install SafeFix from a hosted Release (wheel/sdist) and follow the README,
**so that** I can run it on a fresh machine.

**Acceptance**

- Release provides wheel and sdist.
- README: obtain, install, run, credential setup, platform limits; no WebUI/cloud required.
- GitHub Actions runs tests on every push.

---

## 4. Domain & Mechanism Design (A.5)

### 4.1 Domain mapping (coding agent)

| Mechanism class | SafeFix v1 encoding |
|-----------------|---------------------|
| **Actions / tools** | `read_file`, `list_dir`, `search_code`, `apply_patch`, `finish`; internal `TestRunner` not LLM-visible |
| **Objective feedback** | Full `python -m pytest` + JUnit XML → `FailureSet`; progress/stop in code |
| **Dangerous actions** | Path/patch guardrails; permanent DENY vs soft HITL |
| **Memory** | `SessionState` + JSON artifacts; optional `ProjectMemoryStore` via `--use-memory` |

### 4.2 Depth policy (A.4-D)

| Dimension | Depth |
|-----------|--------|
| Decision (loop, context, single ToolCall) | Minimum viable |
| Tools | Minimum viable (five + internal TestRunner) |
| Memory | Minimum viable (`SessionState`, artifacts, light project memory) |
| Governance | Minimum + small HITL (enough for compliance) |
| **Feedback loop** | **Primary contribution (deep)** |
| Configuration | Minimum viable (`safefix.toml` + CLI) |

**Primary contribution:** test-feedback closed loop — failure classification (small stable labels), progress against best checkpoint (strict subset), stop conditions, regression rollback.

### 4.3 “Mechanism is code” criterion (A.4-C)

Removing the real LLM, every core mechanism remains unit-testable with MockLLM / direct calls: tool dispatch, guardrail, approval gating, JUnit parse, progress compare, snapshot restore, config validation, stop reasons.

---

## 5. System Architecture

### 5.1 Recommended shape

**Single-process layered pipeline** with an **embedded phase state machine** inside `SessionRunner` (no general event bus).

```
CLI
 ├── credentials (set | status | clear)
 └── run
      → ConfigLoader
      → CredentialsResolver (role-specific environment variables)
      → SessionRunner (phases)
           ├── ContextBuilder
           ├── LLMClient
           │     ├── OpenAICompatibleClient  (production)
           │     └── MockLLMClient           (tests only, injected)
           ├── ActionParser
           ├── Guardrail
           ├── ApprovalProvider (CLI interactive | test stub)
           ├── Tools: read_file, list_dir, search_code, apply_patch, finish
           ├── TestRunner          (harness-only)
           ├── FeedbackEngine
           ├── SnapshotStore
           ├── SessionState
           ├── ProjectMemoryStore  (optional load on --use-memory)
           └── ArtifactWriter
```

**Dependency rule:** Tools, Feedback, Guardrail, Snapshot, Config do **not** depend on LLM. Runner orchestrates.

**Implementation boundary (A.4):** Own the agent loop. Do **not** build on LangChain `AgentExecutor`, AutoGen, CrewAI, LlamaIndex agents, or a host coding-agent SDK runner. Allowed: HTTP client, TOML, pytest as the *target* runner, stdlib.

### 5.2 Phase state machine

```
INIT
  Phase-A fail-fast (path, TOML, pytest_args, base_url, model, credentials)
  Compute write policy (may be empty)
  baseline via TestRunner (must be valid — §7.5)
  invalid baseline → STOP(CONFIG_ERROR | ERROR)
  F0 ← all failed/error `failure_id`s
  F0 empty → STOP(SUCCESS)
  F0 non-empty and writable set empty → STOP(CONFIG_ERROR)
  best ← baseline; U_best ← F0
  → READY

READY
  if steps >= max_steps → STOP(MAX_STEPS)
  if rounds >= max_rounds → STOP(MAX_ROUNDS)
  if no_progress >= max_no_progress_rounds → STOP(NO_PROGRESS)
  steps += 1
  call LLM (bounded retries on transport/service errors → else STOP(ERROR))
  parse exactly one ToolCall or PARSE_ERROR → feedback → READY
  → DISPATCH

DISPATCH
  Guardrail → DENY | REQUIRE_APPROVAL | ALLOW
  DENY or approval reject → structured feedback → READY
  read_file | list_dir | search_code → ToolResult → READY
  finish → STOP(REQUESTED)
  apply_patch all-or-nothing land → EVALUATE

EVALUATE  (harness auto TestRunner; not optional)
  infra failure (no usable report, etc.)
    → restore unvalidated patch to best → STOP(ERROR)
  valid report → rounds += 1   # includes eventual successful patch
  if F == ∅ → update best; STOP(SUCCESS)
  compare to best (strict subset rules)
    better → update best & U_best; no_progress = 0
    same | worse → restore best; no_progress += 1
  → READY

STOP
  ensure all session-touched files match best
  if SessionState was initialized, write session JSON artifact; pre-init CONFIG_ERROR/usage errors print the terminal error and exit code without requiring an artifact
  exit with mapped code (§11)
```

### 5.3 Loop invariants

1. Each LLM response yields at most one `ToolCall`.
2. Successful `apply_patch` land **always** triggers full-suite EVALUATE.
3. `rounds` increments **immediately** after a valid post-patch evaluation report is obtained, **then** SUCCESS/better/same/worse is decided (so the final successful patch counts as a round).
4. better updates best; same/worse restore best and increment `no_progress`.
5. Test infrastructure failure after a landed patch → restore → `STOP(ERROR)`.
6. SUCCESS only if harness sees \(F = \emptyset\) on a valid suite result.
7. `finish` never yields SUCCESS.
8. `max_steps`, `max_rounds`, and no-progress threshold **must** stop the session.
9. Candidate patches are atomically evaluated; no kept “preparatory but non-improving” disk state.

### 5.4 Step vs round

| Concept | Definition |
|---------|------------|
| **Step** | One **LLM decision attempt**: before call, if `steps >= max_steps` stop; else `steps += 1` then call. Consumed whether output is valid, unparsable, denied, approval-rejected, or `finish`. |
| **Round** | One candidate patch **successfully landed** and **valid full-suite evaluation** completed. |

Harness-automatic baseline, post-patch tests, rollback, and artifact I/O **do not** consume steps.
Parse failure, DENY, approval reject, write failure **do not** increment rounds.

LLM network/service errors: **fixed finite retries** in code; still failing → `STOP(ERROR)` (no infinite re-entry as a substitute for retries).

---

## 6. Configuration & CLI

### 6.1 Commands

| Command | Purpose |
|---------|---------|
| `safefix run <project_path> [options]` | One repair session (exclusive use of project dir by convention) |
| `safefix credentials status` | Lists accepted role-specific environment variable names; no secrets |

No other v1 subcommands.

### 6.2 `safefix.toml`

- Location: **project root** of the target repo.
- Missing file → built-in safe defaults for policy fields; `base_url` / `model` still required for real `run`.
- Priority: **CLI > TOML > defaults**.
- Unknown keys → startup error.
- API keys **forbidden** in TOML (explicit reject of common secret key names recommended).

**Allowed fields only:**

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `max_steps` | int | `30` | LLM decision attempts |
| `max_rounds` | int | `10` | Landed+evaluated candidate patches |
| `max_no_progress_rounds` | int | `3` | Consecutive non-improving evaluated patches |
| `allowed_paths` | string[] | (derived) | See §6.4 |
| `excluded_paths` | string[] | `[]` | **Add-only** excludes |
| `pytest_args` | string[] | `[]` | Display-only allowlist |
| `base_url` | string | `""` | **Required** for real run (TOML or CLI) |
| `model` | string | `""` | **Required** for real run (TOML or CLI) |

`--use-memory` is **CLI-only**, not a TOML field.

Example:

```toml
max_steps = 30
max_rounds = 10
max_no_progress_rounds = 3
allowed_paths = ["src"]
excluded_paths = ["src/generated"]
pytest_args = ["-q"]
base_url = "https://api.example.com/v1"
model = "example-model"
```

### 6.3 CLI overrides for `run`

| Option | Meaning |
|--------|---------|
| `--max-steps` | Override |
| `--max-rounds` | Override |
| `--max-no-progress-rounds` | Override |
| `--allowed-path` | **Repeatable**; not comma-separated |
| `--excluded-path` | **Repeatable**; add-only excludes |
| `--pytest-args` | Extra display args (still allowlisted) |
| `--base-url` / `--model` | LLM endpoint |
| `--use-memory` | Load project memory into context |
| `--non-interactive` | All `REQUIRE_APPROVAL` → deny |

If stdin is **not a TTY**, behave as non-interactive even without the flag.
**No** `-y` / `--yes` / auto-approve.

No CLI flag accepts a raw API key (avoid shell history).

### 6.4 Path policies (read vs write)

Path rules are **separate** for read tools and write (`apply_patch`). Config cannot lift hard denies.

**write-denied** (never `apply_patch`; permanent DENY):

- Test sources: `tests/` tree, `test_*.py`, `*_test.py` (exact matching fixed by implementation tests).
- Paths outside project root / traversal escapes.
- `.git/` (and git internals).
- Virtual environments (common venv/dirs).
- Caches (e.g. `__pycache__/`).
- Credential/secret files (e.g. `.env`, `*.pem`, similar patterns).

**read-denied** (never `read_file` / `list_dir` / `search_code`):

- Paths outside project root / traversal escapes.
- `.git` internals.
- Virtual environments.
- Caches.
- Credential/secret files.

**Tests are readable:** test source **may** be used by `read_file`, `list_dir`, and `search_code`, but is **always** forbidden for `apply_patch`.

**Writable derivation** (after applying write-denied + `excluded_paths`):

- If `allowed_paths` **omitted**:
  - If `src/` exists → writable candidates = `src/**/*.py` minus write-denied and `excluded_paths`.
  - Else → project `*.py` minus write-denied and `excluded_paths`.
- If `allowed_paths` **provided**:
  - User paths **replace** auto-derivation.
  - Each path must resolve **inside** project root (no escape) after normalization.
  - Still only existing Python sources; still fully subject to write-denied; cannot include tests, creds, git, venv.

### 6.5 `pytest_args` allowlist

Fixed runner:

```text
python -m pytest <allowed display args> --junitxml=<temp report path>
```

Argument vector + `shell=False`. Never arbitrary shell.

**Allowed examples:** `-q`, `-v`, `--tb=short`, `--tb=line`, `--tb=no`, `--disable-warnings`, safe `-r` report options.

**Forbidden (non-exhaustive; unknown → reject):** positional test paths/nodeids; `-k`, `-m`; `-x`, `--maxfail`; `--lf`, `--ff`; `--collect-only`; `-p`; `-c`, `--rootdir`, `--confcutdir`; `--ignore` and other collection-scope changers.

### 6.6 Fail-fast phases

**Before baseline**

- Project path missing or not a directory.
- TOML parse / unknown keys / type / numeric bounds.
- Path escape or explicit paths that hard-collide unsafely.
- Disallowed `pytest_args`.
- Missing `base_url`, `model`, or usable credentials (real run).

**After baseline**

- If \(F_0\) non-empty but final writable set empty → `CONFIG_ERROR`.
- If \(F_0\) empty after **valid** baseline → SUCCESS without requiring writable files.

---

## 7. Functional Spec: Tools, Guardrails, Feedback, Snapshots

### 7.1 Tools (LLM-visible)

**Exact JSON contract (not a formal JSON Schema document):** the LLM response body must be **exactly one** JSON object. Reject as `PARSE_ERROR`: non-JSON, unknown fields, missing required fields, arrays of calls, multiple actions, or extra top-level keys beyond the chosen tool contract.

**Path convention (all tools that take `path`):**

- Paths are **project-root relative** only (e.g. `src/foo.py`, `tests/test_a.py`).
- **Absolute paths are rejected as `PARSE_ERROR`** at the parser boundary.
- `"."` means the project root.
- Harness normalizes (`..` segments, separators) then verifies the result stays inside the project root; escape → deny.

#### `read_file`

```json
{
  "tool": "read_file",
  "path": "src/example.py"
}
```

- Required: `tool`, `path`.
- Returns file text or structured error.
- Subject to **read-denied** (§6.4). Test sources are **allowed**.

#### `list_dir`

```json
{
  "tool": "list_dir",
  "path": "src"
}
```

- Required: `tool`, `path` (use `"."` for project root).
- Returns a bounded directory listing.
- Subject to **read-denied**. Test directories are **allowed**.

#### `search_code`

```json
{
  "tool": "search_code",
  "query": "keyword",
  "path": "."
}
```

- Required: `tool`, `query`, `path` (search root; `"."` = project root).
- Returns limited filename or simple text matches (no external search service, no semantic index).
- Subject to **read-denied**. May search test sources.

#### `apply_patch`

```json
{
  "tool": "apply_patch",
  "changes": [
    {
      "path": "src/example.py",
      "old_text": "return a - b",
      "new_text": "return a + b"
    }
  ]
}
```

- Required: `tool`, non-empty `changes[]`; each change requires `path`, `old_text`, `new_text`.
- **Sole write entry**; subject to **write-denied** (tests never writable).
- Multiple files / multiple replacements allowed (may trigger HITL).
- Only **modify existing** `.py` files; **no** create/delete/move/rename.
- Each `old_text` must match **exactly once** in the **pre-patch** file content; else entire patch fails.
- Multiple `old_text` on the **same** file: all matched against **pre-modification** text; match spans **must not overlap**.
- All changes: parse → path checks → match checks **before any write**; any pre-check failure → **no** files written.

**Transactional semantics (harness-level, not OS cross-file atomicity):**

1. Pre-validate all changes.
2. Generate full target contents; write via **temp files** then replace.
3. If replacing any file fails mid-way: immediately restore already-replaced files from **this round’s** pre-apply snapshot, then `STOP(ERROR)`.
4. SPEC “all-or-nothing” means this harness protocol, **not** a claim of multi-file OS atomic commit.

After **successful** land → harness **always** runs TestRunner (EVALUATE).

#### `finish`

```json
{
  "tool": "finish",
  "reason": "model requested stop"
}
```

- Required: `tool`. Optional: `reason` (string for logs).
- → `STOP(REQUESTED)` only; never SUCCESS.

### 7.2 TestRunner (harness-internal)

Invoked only for:

1. Session baseline
2. After every successful `apply_patch` land

Not exposed to the LLM (avoids duplicate tests, wasted steps, and dual semantics).

### 7.3 Guardrail

Outcomes: `ALLOW` | `DENY` | `REQUIRE_APPROVAL`.

**Permanent DENY (not approvable)**

- Modify tests.
- Escape project root / path traversal.
- Modify `.git`, virtualenvs, credential files.
- Illegal or unparsable patches; failed uniqueness/overlap pre-checks.
- Other non-tool or non-allowed writes if ever parsed.

**REQUIRE_APPROVAL** (only `apply_patch` that already passed permanent checks)

- Distinct files in patch **> 3**, or
- Total added lines + deleted lines **> 80**
  (counts before land; exactly 3 files or 80 lines → no approval).

Constants are **built-in** (not TOML). Permanent DENY wins over approval.

**ApprovalProvider**

- Interactive TTY: prompt `y/N`.
- Non-interactive / non-TTY: deny.
- Tests inject allow/deny stubs.
- No auto-approve CLI flag.

### 7.4 FeedbackEngine

**Inputs:** JUnit XML (preferred built-in `--junitxml`), baseline \(F_0\), best’s \(U_{best}\).

**Deterministic `failure_id` contract**

- `failure_id` is stable across baseline and later evaluations.
- Includes parameterization identity.
- Supports deterministic synthetic IDs for collection errors.
- Normal testcase: `classname::name` (with the report's parameterized name suffix).
- Collection error: `collection_error::<suite>::<sha256(normalized_message)[:16]>`, where whitespace is collapsed and trimmed before hashing.
- A failed/error status change does not change the identity.

**Sets (all are sets of `failure_id`)**

- \(F_0\): baseline failed/error `failure_id` set (immutable for the session).
- \(F\): current failed/error `failure_id` set.
- \(U = F \cap F_0\): still-unfixed initial failures.
- \(N = F \setminus F_0\): new failures (regression signal only; never merged into \(F_0\)).
- \(U_{best}\): unresolved initial failures at best (\(U\) at best checkpoint).

**Labels (small, rule-based):** at least report-level `failed` vs `error`; optional coarse exception-derived tags (e.g. assert / import / other) for LLM summaries. **No** hint-template system.

**Judgments**

| Result | Condition |
|--------|-----------|
| SUCCESS | \(F = \emptyset\) (on a valid suite result) |
| better | \(N = \emptyset\) and \(U \subsetneq U_{best}\) |
| same | \(N = \emptyset\) and \(U = U_{best}\) |
| worse | \(N \neq \emptyset\), or \(U \supsetneq U_{best}\), or \(U\) **incomparable** to \(U_{best}\) |

Best’s unresolved set only shrinks along a **strict subset chain**.

New failures never join \(F_0\).

### 7.5 Valid baseline / valid evaluation

A test run is **valid** only if:

1. pytest process starts normally;
2. JUnit report is present and parseable;
3. **At least one** test was collected.

Otherwise baseline → `CONFIG_ERROR` or `ERROR`, **never** SUCCESS.
Non-zero pytest exit **with** a valid failure report is **normal** red tests, not infrastructure error.
Post-patch infra failure → restore unvalidated patch → `STOP(ERROR)`.

### 7.6 SnapshotStore

No full-repo copy.

| Map | Meaning |
|-----|---------|
| `baseline_contents` | Original content of every file **first touched** this session |
| `best_contents` | Contents of files that differ from baseline **at best** (session-modified set at best) |

**Restore to best**

- Path in `best_contents` → write best content.
- Path touched but **not** in `best_contents` → write **baseline** content.

Also keep per-round pre-apply snapshot sufficient to undo a failed mid-land apply.

- First touch: stash baseline text.
- better: refresh `best_contents` from current.
- same/worse/ERROR: restore as above.
- STOP: all session-touched files match best.

No directory tree/permission/Git/cross-process features. v1 has no create/delete so only text restore is required.

### 7.7 Memory

**SessionState (always):** \(F_0\), best summary / \(U_{best}\), steps, rounds, `no_progress`, recent tool and guard events, optional patch fingerprints.

**ArtifactWriter:** structured session JSON every run (human-readable, demo, review); redact secrets.

**ProjectMemoryStore (minimal cross-session)**

```text
ProjectMemory
- project_id
- last_session_summary
- recent_unsuccessful_patch_fingerprints
- updated_at
```

- Stored as **per-project JSON under the user data directory**.
- **Not** loaded by default.
- Loaded only with `--use-memory`; `ContextBuilder` injects a **fixed small** structured slice.
- No full source, no API keys, no full tracebacks, no full transcripts.
- Lists capped to fixed maxima.
- Projects isolated by `project_id`.
- Not a knowledge base / vector store.

---

## 8. Data Model (summary)

| Entity | Role |
|--------|------|
| `Config` | Validated policy + endpoint fields |
| `ToolCall` / `Change` | Single action; patch replacements |
| `GuardDecision` | ALLOW / DENY / REQUIRE_APPROVAL + reason |
| `FailureSet` | `failure_id` set; U, N derived |
| `Feedback` | Comparison outcome + summaries/labels |
| `Checkpoint` maps | `baseline_contents`, `best_contents`, `U_best` |
| `SessionState` | Live loop memory |
| `ProjectMemory` | Optional cross-session JSON |
| `SessionResult` | `StopReason`, counters, artifact path |
| `StopReason` | `SUCCESS`, `REQUESTED`, `MAX_STEPS`, `MAX_ROUNDS`, `NO_PROGRESS`, `ERROR`, `CONFIG_ERROR` |

---

## 9. Non-Functional Requirements

### 9.1 Security & credentials threat model

| Threat | Mitigation |
|--------|------------|
| Key in git / TOML / logs | Forbidden in TOML; status never prints key; log redaction |
| Key in shell history | No key CLI flags; users must avoid placing exports in shell-history files |
| Process environment exposure | Role-specific variables are read only by the current process; SafeFix does not persist them |
| Model tries unsafe edits | Code guardrails; sole write tool; no shell |
| CI exfiltration | Tests use Mock; no real keys in CI secrets required for unit-test job |

### 9.2 Observability

- Terminal stream of phase-significant events (denies, approvals, round outcomes, stop).
- Session JSON: counters, failure-set diffs, guard events; desensitized.

### 9.3 Performance

- Aimed at small/medium repos; hard caps via `max_steps` / `max_rounds`.
- No strict latency SLA in v1.

### 9.4 Testability

- Core mechanisms offline-deterministic under MockLLM.
- Mechanism demo aligns with course A.6 (deny; feedback changes next action; deep feedback behaviors).

### 9.5 Usability

- Pure local CLI.
- Illegal configuration fails fast with clear errors.
- Exclusive directory access is a **documented convention**, not a lock service.

---

## 10. Credentials & Distribution

### 10.1 Credentials

- **Only production source:** role-specific environment variables in the current process.
- No raw-key CLI flag, `.env` loading, or plaintext credential-file support.
- `credentials status`: lists variable names only; it does not inspect or display values.
- MockLLM / unit tests remain credential-free.

### 10.2 Distribution

- **Python package:** wheel + sdist on hosted **Release**.
- **Runtime:** **Python ≥ 3.11** (stdlib `tomllib`).
- README must document: obtain, install, run, secure key setup, platform limits.
- **Non-goals:** WebUI, cloud deploy URL.

### 10.3 CI

- **GitHub Actions:** run test suite on push.
- GitHub Actions is the sole CI system and runs the full test suite on every push.

---

## 11. Exit Codes

| Code | Meaning |
|------|---------|
| `0` | `SUCCESS` (including valid baseline with nothing to fix) |
| `1` | Incomplete repair: `REQUESTED`, `MAX_STEPS`, `MAX_ROUNDS`, `NO_PROGRESS` |
| `2` | Configuration / usage error (`CONFIG_ERROR` and similar) |
| `3` | Runtime `ERROR` (LLM after retries, pytest infra, filesystem mid-apply failure, etc.) |

Detailed `StopReason` always appears in terminal summary and session JSON.

---

## 12. Technology Choices

| Choice | Selection | Rationale |
|--------|-----------|-----------|
| Language | Python ≥ 3.11 | Same ecosystem as targets; `tomllib` |
| Packaging | wheel + sdist | Course Release distribution |
| Dev tests | pytest | Consistency with domain |
| LLM | One `OpenAICompatibleClient` + injectable `MockLLMClient` | Thin HTTP; no provider plugin system |
| Secrets | Role-specific process environment variables | No persistence, raw-key CLI flag, or `.env` support. |
| Config | TOML (`safefix.toml`) | Small surface, stdlib parse |
| UI | CLI only | Teacher-allowed for pure CLI + Release |

---

## 13. Acceptance Criteria (traceability)

| ID | Criterion |
|----|-----------|
| **A1** | Self-implemented main loop + five LLM tools + internal TestRunner; no high-level agent framework hosts the loop |
| **A2** | Mock: permanent DENY on test edits; injected failure feedback changes next scripted action; better/same/worse, rollback, no-progress stop offline |
| **A3** | \(F_0\) frozen; new failures not added to \(F_0\); SUCCESS ⇔ \(F = \emptyset\) on valid suite; subset progress rules |
| **A4** | Exact-replacement patches; all-or-nothing harness semantics; no create/delete/rename |
| **A5** | Config/CLI/credentials/`pytest_args` allowlist behaviors per §6 |
| **A6** | GitHub Actions; Release wheel+sdist; README completeness |
| **A7** | Mechanism unit tests: no network, no real API key |
| **A8** | HITL: >3 files or >80 lines → REQUIRE_APPROVAL; stub deny/allow; permanent deny not bypassable; non-interactive default deny |
| **A9** | Session JSON always; default no project memory load; `--use-memory` loads capped per-project JSON; isolation; no keys/full source in memory; mock-testable |

---

## 14. Risks & Open Implementation Notes

| Risk / boundary | Handling |
|-----------------|----------|
| Strict subset progress is conservative | May mark “trade A for B inside \(F_0\)” as worse/incomparable; accepted for v1 clarity |
| Atomic multi-edit requirement | LLM must put dependent edits in one `apply_patch` |
| No shell | Cannot install deps or change environment—source-only fixes |
| Exclusive directory | Convention only; external concurrent edits undefined |
| Exact `old_text` brittleness | Model must supply unique context; mismatch fails whole patch |
| Cross-file “atomicity” | Harness protocol (pre-check, temp files, restore), not OS guarantee |
| Real LLM quality | Out of unit-test scope; harness guarantees bounds and loop |
| HITL thresholds fixed | May need later tuning; not configurable in v1 |

No open **product-scope** decisions remain for v1 MVP; remaining work is implementation detail under this SPEC.

---

## 15. Mechanism Demonstration Requirements (A.6)

Submit deterministic demos (tests and/or scripts) under MockLLM:

1. **Governance:** dangerous/permanent-deny action intercepted.
2. **Feedback:** injected failure causes a **different** next tool call.
3. **Focus dimension:** strict-subset progress and/or same/worse rollback and/or no-progress stop.

---

## 16. Document Control

| Doc | Role |
|-----|------|
| `SPEC.md` | This specification (source of truth for build) |
| `SPEC_PROCESS.md` | Brainstorming process, decisions, rejections |
| `PLAN.md` | Finalized implementation plan and execution handoff |
| `AGENT_LOG.md` | Implementation, review, verification, and traceability evidence |
| `docs/decision-records/` | Project design-decision records |

Course material was consulted during design but is not shipped in the final repository.
