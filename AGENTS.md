# SafeFix Agent Engineering Rules

## 1. Mandatory workflow

This repository is developed under the AI4SE course workflow.

Before acting, identify and use the applicable Superpowers skill. Required workflow priority:

1. `using-git-worktrees` before isolated implementation work;
2. `subagent-driven-development` for PLAN execution;
3. `test-driven-development` for every behavior change;
4. `requesting-code-review` after implementation tasks;
5. `receiving-code-review` before applying review feedback;
6. `verification-before-completion` before any completion claim;
7. `finishing-a-development-branch` when implementation is complete.

Do not skip a required skill merely because a task appears small.

Record skill usage, red/green commands, review results, deviations, and commit hashes in `AGENT_LOG.md`.

## 2. Sources of truth

Implementation must conform to:

1. Course requirements;
2. `SPEC.md`;
3. `PLAN.md`;
4. This file;
5. The current task.
6. Local implementation preferences.

If content conflicts, follow the priority order above and record the reason for
any deviation in `AGENT_LOG.md`. Do not silently expand product scope or
reinterpret locked design decisions.

## 3. Software engineering principles

Prefer simple, explicit, maintainable code.

Apply:

* KISS;
* YAGNI;
* single responsibility;
* clear module boundaries;
* explicit data contracts;
* dependency injection only where required for testability;
* small functions with meaningful names;
* deterministic behavior for Harness mechanisms;
* standard-library solutions when they are sufficient;
* straightforward code over clever abstractions.

Implement only what the current SPEC and PLAN require.

Do not add extension points, plugin systems, generic frameworks, configuration options, fallback paths, or abstraction layers for hypothetical future needs.

## 4. Avoid excessive defensive programming

SafeFix must defend untrusted system boundaries, but must not repeatedly defend trusted internal code against impossible states.

### Defend at these boundaries

Perform explicit validation where data enters from:

* CLI arguments;
* `safefix.toml`;
* LLM responses;
* project-relative paths;
* filesystem operations;
* pytest and JUnit reports;
* keyring;
* HTTP transport;
* interactive approval input.

At these boundaries:

* validate once;
* return or raise a specific error;
* fail fast;
* preserve the original cause where useful;
* do not continue with invented defaults.

### Trust validated internal invariants

After boundary validation:

* pass typed domain objects internally;
* do not revalidate the same condition in every layer;
* do not add branches for states made impossible by constructors or parsers;
* do not wrap every internal call in `try/except`;
* do not catch broad `Exception` unless at a deliberate top-level error boundary;
* do not swallow exceptions;
* do not return empty values merely to avoid an error;
* do not add silent fallback behavior not required by the SPEC;
* do not introduce nullable or optional states without a real use case;
* do not duplicate Guardrail, ConfigLoader, or ActionParser checks inside unrelated modules.

The preferred model is:

> Validate once at the trust boundary, establish an invariant, and rely on that invariant internally.

## 5. Error-handling rules

Use errors to represent actual failures, not normal control flow.

* Catch only exceptions the current layer can meaningfully handle.
* Otherwise allow the error to propagate to the designated boundary.
* Preserve deterministic StopReason mapping in `SessionRunner` or CLI boundaries.
* Do not retry except where the SPEC explicitly requires bounded LLM transport retries.
* Do not add fallback credential sources.
* Do not convert infrastructure errors into successful or empty results.
* Do not add “best effort” behavior where the SPEC requires rejection or stopping.

## 6. Testing rules

Strict TDD is mandatory:

1. write the smallest failing test;
2. run it and confirm the expected failure;
3. implement the minimum code required;
4. run the focused test;
5. run relevant regression tests;
6. refactor only while green.

Tests should verify observable contracts, not duplicate implementation details.

Avoid:

* testing private helpers without a contract reason;
* one test per trivial line;
* excessive mocking of pure internal code;
* giant fixtures for small behavior;
* tests for hypothetical states excluded by validated invariants;
* adding production branches solely to satisfy mocks.

Use fakes or injected boundaries for LLM, HTTP, keyring, filesystem failures, and approval behavior.

## 7. Scope discipline

Do not introduce:

* generic shell execution;
* provider registries;
* `.env` support;
* WebUI or cloud services;
* custom pytest plugins;
* vector databases;
* SQLite memory;
* Git-based repair isolation;
* file creation/deletion/rename support;
* automatic approval;
* additional public tools;
* speculative recovery systems.

A useful improvement outside the active task must be recorded as a future note, not implemented opportunistically.

## 8. Review requirements

Each execution unit requires two distinct reviews:

1. specification-compliance review;
2. code-quality review.

Code-quality review must explicitly check:

* unnecessary abstraction;
* duplicated validation;
* broad exception handling;
* speculative fallback logic;
* excessive defensive branches;
* dead code;
* scope expansion;
* tests coupled to implementation rather than behavior.

A task is complete only when:

* focused tests pass;
* relevant regression tests pass;
* both reviews pass;
* `AGENT_LOG.md` is updated;
* verification evidence is recorded.
