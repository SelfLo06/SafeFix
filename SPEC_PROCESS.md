# SafeFix — Specification Process Record

> Brainstorming collaboration log for producing `SPEC.md`.
> Course workflow: Superpowers **brainstorming** → (later) **writing-plans**.
> **PLAN and implementation code were explicitly deferred** in this phase.

**Date range:** 2026-08-02
**Method:** Superpowers brainstorming skill — explore context → one question at a time → YAGNI/MVP audit → sectioned design sign-off → write SPEC.

---

## 1. Inputs Read Before Design

| Source | Use |
|--------|-----|
| `docs/course-requirements/AI4SE 期末项目 · 通用要求.md` | Shared deliverables, Superpowers workflow, credentials, distribution, SPEC structure, cold-start, CI notes |
| `docs/course-requirements/AI4SE_Final_Project_A_Coding_Agent_Harness.md` | A3–A6 harness mechanisms, self-implemented loop, mock testability, domain section |
| `docs/COURSE_INDEX.md` | Doc boundaries (`SPEC.md` / `PLAN.md` / `SPEC_PROCESS.md` at repo root) |
| User project brief | Type A harness; SafeFix; Python+pytest; feedback-loop focus; no parasitic frameworks; mockable core |

**Repo state at start:** Empty `SPEC.md` / `PLAN.md` / `SPEC_PROCESS.md` / `README.md` / `AGENT_LOG.md`; course docs only; git initialized.

**Note:** `COURSE_INDEX.md` references English requirement filenames; actual files use Chinese / slightly different names. Content was read from the real paths.

---

## 2. Brainstorming Arc (Summary)

1. **Context exploration** — empty product docs; full course constraints loaded.
2. **Clarifying questions (one at a time)** — problem framing → success metric → workspace/rollback → progress → write scope → tools → delivery → LLM/creds → memory → failure classification → config.
3. **User-driven pause + YAGNI/MVP audit** — shrink non-feedback dimensions; lock non-goals.
4. **Config lock (Q11)** — contracted TOML policy.
5. **Governance/HITL gap fill** — then architecture options.
6. **Sectioned design sign-off** — §§1–5 with iterative corrections.
7. **SPEC + SPEC_PROCESS written**; PLAN deferred.

---

## 3. Key Questions, Answers, and Design Impact

### Q1 — What does “安全 bug” mean?

| Option discussed | Outcome |
|------------------|---------|
| A Security functional defects only | Rejected as v1 scope center |
| B Known vuln patterns + scan | Rejected |
| **C Broad “reliable repair”; safe = agent behavior** | **Adopted** |
| D A + agent safety | Partially absorbed as guardrails, not product theme |

**Impact:** Product is reliable auto-fix with guardrails + test loop; no vuln scanning in v1. One-liner updated accordingly.

### Q2 — Session success target?

| Option | Outcome |
|--------|---------|
| A Full green any pre-existing red | Rejected (scope creep fear mis-stated initially) |
| **B Freeze initial failure set \(F_0\); fix those without new failures** | **Adopted** |
| C Single test only | Rejected as too narrow |
| D Configurable | Deferred |

**Later correction (US-1):** Baseline **all** failed/error ∈ \(F_0\). “Old red tests” are **inside** \(F_0\), not outside. Stability means: do not expand target via **new** failures, model task expansion, or unrelated analysis. No subset selection of \(F_0\) in v1. New failures never join \(F_0\).

### Q3 — Workspace & rollback?

| Option | Outcome |
|--------|---------|
| **A In-place + file snapshots; exclusive dir** | **Adopted** |
| B git worktree/branch | Deferred |
| C Full tree copy | Deferred |
| D A+optional B | Deferred |

### Q4 — Progress / no-progress?

| Option | Outcome |
|--------|---------|
| A Strict failure-set shrink | Basis |
| B Weighted multi-signal | Rejected for v1 complexity |
| C Fail count only | Rejected |
| **D A + configurable N; classify for feedback text not sole stop** | **Adopted**, then refined |

**User refinements locked:**

- Compare against **best checkpoint**, not only previous round (anti-oscillation).
- A **round** = candidate patch applied + **full** test evaluation.
- Read/search do not count as rounds.
- Final progress rule = **strict subset** on \(U\) with \(N=\emptyset\) (see below); **not** lexicographic \((|N|,|U|)\) scoring (that proposal was **rejected** after briefly appearing in a draft §4).

### Q5 — Write allowlist?

| Option | Outcome |
|--------|---------|
| A Convention auto `*.py` exclude tests | Partially |
| B Mandatory explicit allow only | Rejected (too much friction) |
| C Hard excludes + config tighten | Absorbed |
| **D src/-first + C hard excludes** | **Adopted**, later simplified (no complex package discovery) |

**Later lock:** Explicit `allowed_paths` **replaces** derivation; still only `.py` + hard excludes. Repeatable CLI `--allowed-path` / `--excluded-path` (no comma lists).

### Q6 — Tool surface?

| Option | Outcome |
|--------|---------|
| **A Minimal: tests/read/search/patch/finish; no shell** | **Adopted**, then adjusted |
| B + restricted shell | Rejected |
| C Fine-grained write tools | Rejected |
| D str_replace + write_file | Rejected as public surface |

**Later lock:** LLM tools = `read_file`, `list_dir`, `search_code`, `apply_patch`, `finish` only. **`run_tests` not LLM-visible**; `TestRunner` harness-internal (baseline + post-patch only).

### Q7 — UX / deployment?

Initial options included WebUI variants. **User override (course teacher clarification):**

- Pure **CLI**; no WebUI; no cloud server.
- Python **wheel/sdist** + hosted **Release**.
- Open Design N/A.
- Dual CI: GitHub Actions + GitLab **`unit-test`** job.
- WebUI/cloud listed as **non-goals**.

**Adopted in full.**

### Q8 — LLM + credentials?

| Combo | Outcome |
|-------|---------|
| User first said C+1 (multi-backend registry + keyring with `.env` fallback) | Partially; **later superseded by final errata: keyring-only** |

**YAGNI correction (adopted at the time):**

- Only `MockLLMClient` (test inject) + one `OpenAICompatibleClient`. **No** provider plugin system.
- Credentials: **keyring + env read** (**later superseded by final errata: keyring-only**; also no `.env` manager).
- `base_url` / `model` **empty by default**; required for real `run`; no implicit vendor default.

### Q9 — Memory?

| Option | Outcome |
|--------|---------|
| A Session + optional project notes | Basis |
| B Session only | Too weak vs A3 |
| C Vectors | Rejected |
| **D A but cross-session default off** | **Adopted** |

**§5 fix:** Add `ProjectMemoryStore` + `ProjectMemory` entity so `--use-memory` is not a dangling flag. Capped JSON in user data dir; no vectors/SQLite.

### Q10 — Failure classification?

| Option | Outcome |
|--------|---------|
| A Structured pytest parse + labels | Basis |
| B Sets only | Too shallow for stated contribution |
| C + hint templates | Rejected for v1 |
| **D A + hint extension later** | Initially; **hints dropped** in YAGNI |

**Locked:** Built-in JUnit XML; few stable labels; no custom pytest plugin; no hint system.

### Q11 — Configuration?

User chose **contracted D** (strategy-only TOML): fields listed in SPEC §6; CLI overrides; unknown keys error; hard excludes non-liftable; `--use-memory` CLI-only.

**Later additions/fixes:**

- **`max_steps`** required (distinct from `max_rounds`).
- Step = **LLM decision attempt** (increment before call), not “parsed ToolCall”.
- `pytest_args` **allowlist only** (no selection/scope changers).
- Non-interactive / non-TTY → approval deny; **no** `--yes`.
- Fail-fast **two-phase** (writable-empty check only after baseline if \(F_0\) non-empty).
- Credentials status/clear semantics tightened.

---

## 4. YAGNI / MVP Audit (User-Requested Pause)

**Principle:** All six harness dimensions minimum-viable; **only feedback loop deep**.

| Area | Keep | Simplify | Defer |
|------|------|----------|-------|
| Feedback | F0, best, subset progress, rollback, stops, JUnit | Few labels | Multi-framework, plugins, hints |
| Governance | Path DENY, sole write tool | — | Full HITL product initially → then **minimal HITL re-added** for compliance |
| Rollback | File snapshots, best | — | Git, locks, full copy |
| Tools | Five LLM tools | search simple | Shell, run_tests for LLM |
| LLM/creds | Mock + one compatible client; keyring+env *(later superseded by final errata: keyring-only)* | No plugin registry; no `.env` | Multi-provider |
| Memory | Session + JSON + optional project JSON | Caps, no retrieval engine | Vectors, SQLite |
| Paths | src-first + hard exclude + user paths | No package discovery | — |
| Delivery | CLI, wheel/sdist, dual CI | — | WebUI, cloud |
| Config | Small TOML | — | Profiles, YAML rule packs, prompt files |

This audit **explicitly corrected** earlier “multi-backend registry” and “`.env` fallback loader” leanings.

---

## 5. Architecture Choice

| Approach | Verdict |
|----------|---------|
| **1 Layered pipeline + embedded phases** | **Adopted** |
| 2 God-loop single module | Rejected as final structure |
| 3 Full event-sourced reduce bus | Rejected as heavy; light phases kept inside Runner |

---

## 6. Section Sign-Off Log

| Section | Result | Notable amendments |
|---------|--------|-------------------|
| §1 Problem / non-goals | Pass with wording fix | LLM proposes concrete patches; harness owns validation/test/progress/stop; `finish` ≠ SUCCESS |
| §2 User stories | Conditional → pass | US-1 reword; US-5 numeric/HITL semantics; US-6 atomic patch; split US-7/US-8 |
| §3 Config/CLI/creds | Conditional → pass | max_steps definition; pytest allowlist; path semantics; two-phase fail-fast; TTY rules; no implicit model defaults; credential edge cases |
| §4 Loop/tools/feedback/snapshots | Conditional → pass | No LLM `run_tests`; exact replacement patch format; **strict subset** not score key; rounds include final success; valid baseline rules; snapshot only touched files |
| §5 Architecture/NFR/dist/accept | Conditional → pass | `ProjectMemoryStore`; A8/A9; baseline_contents vs best_contents restore; patch txn constraints; Python ≥3.11; exit codes 0–3 |

**Design sign-off:** Complete after §5 five fixes. Brainstorming creative design closed. Next artifact: this process doc + `SPEC.md`. **No PLAN in this phase.**

---

## 7. Suggestions Adopted vs Rejected / Overridden

### Adopted (AI or user)

- Reliable-repair framing (not vuln product).
- \(F_0\) freeze + no auto-expand.
- In-place snapshots; exclusive directory convention.
- Progress vs **best**; strict **subset** chain.
- Minimal tools; no shell.
- Pure CLI + PyPA Release; dual CI.
- Feedback as sole deep dimension.
- Two-level guardrail (permanent DENY + soft HITL).
- Force EVALUATE after successful patch land.
- Harness-only SUCCESS.
- Exact multi-`old_text` non-overlapping pre-image match.
- `ProjectMemory` minimal module for `--use-memory`.
- Exit code table 0/1/2/3.

### Rejected or reversed

| Idea | Why |
|------|-----|
| Security-vuln / scanning product center | User: v1 is reliable fix |
| Git worktree repair isolation | Complexity; A chosen |
| Full-tree sandbox copy | Cost/complexity |
| Provider plugin registry | YAGNI |
| `.env` loader | YAGNI; interim “env read enough” **later superseded by final errata: keyring-only** |
| Complex Python package discovery | YAGNI |
| Hint templates / custom pytest plugin | YAGNI |
| LLM-visible `run_tests` | Duplicate tests & semantic confusion |
| Lexicographic score \(k=(\|N\|,\|U\|)\) | Contradicted locked strict-subset rule; **removed** |
| `--yes` auto-approve | Would bypass HITL in CI |
| Implicit default `base_url`/`model` | Avoid silent vendor lock-in |
| WebUI required by generic course text | Teacher exception for pure CLI + Release |
| Event-bus architecture | Overkill for MVP |
| Claiming OS multi-file atomicity | Replaced with harness transactional protocol |

### AI proposals user tightened

- Step definition: not “parsed ToolCall” → **LLM call attempt**.
- US-1 wording about “other old reds outside F0” → corrected.
- `allowed_paths` “intersection” language → **replace derivation**.
- Snapshot “best map only” incomplete for newly touched files → **baseline_contents + best_contents** restore rule.
- HITL added after an earlier “deny-only” MVP lean, as **minimal** in-loop approval for size thresholds only.

---

## 8. Course Compliance Checklist (Design Level)

| Requirement | SPEC coverage |
|-------------|----------------|
| Problem, users, worth | §1 |
| ≥5 INVEST stories | §3 (US-1…US-8) |
| Functional modules I/O/errors | §§6–7 |
| NFR + credential threat model | §9–10 |
| Architecture + data flow | §5 |
| Data model | §8 |
| Credentials + distribution | §10 |
| Tech choices | §12 |
| Acceptance | §13 + A8/A9 |
| Risks | §14 |
| A.5 domain & mechanisms | §4 |
| Own loop; mockable mechanisms | §§4–5, 13, 15 |
| No high-level agent framework | §5.1 |
| Dual CI / unit-test job | §10.3 |
| WebUI exception documented | §2 non-goals |

**Still future (not this phase):** `PLAN.md`, cold-start second agent, implementation, `AGENT_LOG`, `REFLECTION`, packaging CI artifacts.

---

## 9. Reflection on Brainstorming (for course SPEC_PROCESS)

### What worked well

- **One question at a time** forced prioritization (success metric and “what safe means” early).
- **YAGNI pause** prevented multi-provider / `.env` / WebUI sprawl before architecture.
- **Section sign-off with conditional passes** caught real inconsistencies (F0 wording, step definition, score vs subset, missing memory module, snapshot restore hole).
- Separating **LLM content authority** vs **harness control authority** clarified SUCCESS/`finish` and test ownership.

### What was frustrating / costly

- Course docs disagree on GitHub vs GitLab and WebUI; needed explicit teacher-priority rules in-product.
- Early draft briefly introduced \((|N|,|U|)\) scoring that **conflicted** with an already locked subset rule — process debt from not re-reading prior locks before proposing alternatives.
- HITL oscillated (YAGNI deny-only → compliance minimal HITL); correct outcome but two passes.

### Process metric

- Multiple design iterations (**well above three**) with explicit accept/reject recorded above.
- Final gate: user instructed to generate `SPEC.md` + `SPEC_PROCESS.md` only; **no PLAN, no code**.

---

## 10. Next Steps (Outside This Phase)

1. User reviews `SPEC.md` for final nits.
2. When authorized: Superpowers **writing-plans** → `PLAN.md` (fine tasks, TDD red tests first).
3. Cold-start validation with a **different** agent using only SPEC+PLAN (course §4.5) — after PLAN exists.
4. Implementation via worktrees / subagents / TDD; maintain `AGENT_LOG.md`.

---

## 12. Final Errata Application (Four-point review)

After design sign-off, the following four errata were applied (no new features):

1. **Read/write path separation** — tests are readable/searchable; write-denied for tests only. Ambiguous "Deny secret/hard-excluded reads" replaced.

2. **Credential fallback removal** — v1 uses **only** OS keyring; environment variable fallback removed from US-3, architecture, threat model, credentials section, technology choices, acceptance criteria, and status/clear behavior. (**Final design: keyring-only.**)

3. **Exact JSON contracts** — All five ToolCalls have an **exact JSON contract** (not a formal JSON Schema document): `read_file`, `list_dir`, `search_code`, `apply_patch`, `finish`. LLM must return exactly one JSON object.

4. **Failure_id contract** — "nodeid" (as failure-set identity) replaced with deterministic `failure_id` (stable, parameterized, synthetic support for collection errors). Note: pytest CLI “nodeid” may still appear only as a **forbidden** `pytest_args` selection term.

**Spec self-review:** All changes maintain MVP scope, consistency, and testability. No new functionality introduced.

---

## 13. Consistency cleanup (post-errata residuals)

Final pass before `writing-plans` (no new features):

| Item | Change |
|------|--------|
| Credentials | Removed residual `keyring \| environment` from US-3 acceptance and §6.1 `status`; removed §9.1 “env visible” threat row; §10.1 is keyring-only with no env/`.env`/plaintext fallback |
| Path sets | §6.4 rewritten as explicit **write-denied** vs **read-denied**; tests readable, never `apply_patch` |
| ToolCall paths | All `path` fields are **project-root relative**; no absolute paths; `"."` = root; normalize then no-escape |
| §7.1 | Removed duplicate headings/tool blocks; merged JSON examples with behavior rules; wording “exact JSON contract” |
| §7.4 | Restored \(F_0\), \(F\), \(U=F\cap F_0\), \(N=F\setminus F_0\) as `failure_id` sets |
| §8 | `FailureSet` = `failure_id` set |
| Process history | Prior “keyring + env read” marked **later superseded by final errata: keyring-only** |

---

## 14. Authorization for `writing-plans`

Design phase complete after consistency cleanup. `SPEC.md` and `SPEC_PROCESS.md` are final for planning.

**User condition:** if residuals above are fully cleared → **authorize** Superpowers **writing-plans** to generate `PLAN.md` (no implementation code until further authorization).

---

## 15. SPEC recovery and implementation-traceability correction

On 2026-08-05, the main commit `eefdbf0` was checked with `git show` and
contained a zero-byte `SPEC.md`; the history search identified `c3247dd` as the
final approved brainstorming artifact containing the complete specification and
process record. Earlier implementation-worktree specification-compliance
reviews had therefore been performed against an empty document and cannot serve
as final evidence.

Correction: restore the complete approved `SPEC.md` and this process record from
`c3247dd`, then perform a new item-by-item traceability review against the
current main implementation. The repair is limited to implementation/document
consistency: exit codes, valid JUnit baseline/evaluation, deterministic failure
identity, strict-subset feedback, first-touch snapshots, CLI/path contracts,
bounded opt-in memory, runtime error boundaries, and audit evidence. No product
scope, brainstorming decision, or early draft was reinstated.

The prior empty-SPEC reviews are explicitly superseded. The replacement review
must be independently specification-compliant and code-quality reviewed, with
special attention to duplicated validation, broad exception handling,
speculative fallback behavior, and excessive defensive programming. Final
verification must include `test -s SPEC.md`, the full pytest suite, wheel/sdist
build, `git diff --check`, and clean-status evidence.

---

## 16. v0.2 interactive-console implementation and main integration

The v0.2 implementation evolved from the approved adapter architecture into a
normal-screen terminal console. The `SessionRunner` remains the sole owner of
repair state, frozen-baseline semantics, candidate transactions, guardrails,
approval, rollback, and stop reasons. The TUI only presents semantic events,
collects operator input, and forwards commands at existing safe boundaries.

The delivered interaction adds a baseline-selection preflight (`[tests]`), a
Chinese baseline explanation (`[explain]`), explicit final-review choice
(`[review]`), and explicit `/start` confirmation. Test, Repair, and Review
roles retain independent configuration and environment-only credentials.
Generated tests are validated in isolated session workspaces, may require
operator approval, and freeze the repair baseline without writing into the
target project's test directory.

On 2026-08-09, the user explicitly authorized direct replacement of the root
`main` worktree with the complete current `safefix-v0.2` implementation. This
supersedes preservation of the four pre-existing uncommitted `main` edits and
records an intentional integration decision rather than an accidental reset.
