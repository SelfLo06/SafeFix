# SafeFix Launch Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let `safefix` with no arguments launch a small TTY-first startup wizard while preserving `safefix run PATH ...`.

**Architecture:** Add a presentation-only wizard at the CLI boundary. It detects the current directory, existing pytest tests, and TTY capability, asks only for missing Repair `base_url` and required `model`, writes the existing flat TOML schema, then delegates to the existing run path. Test and Review configuration remains independent and optional.

**Tech Stack:** Python standard library, existing `argparse`, `tomllib`, prompt input, pytest.

## Global Constraints

- No `start` command, new dependency, credential storage, or wizard framework.
- API keys remain `SAFEFIX_TEST_API_KEY`, `SAFEFIX_REPAIR_API_KEY`, and `SAFEFIX_REVIEW_API_KEY`.
- Generated TOML writes only the existing Repair fields `base_url` and `model`; it never aliases Repair configuration to Test or Review.
- TTY defaults to TUI; non-TTY never starts an interactive prompt.
- Existing `safefix run PATH ...` behavior remains unchanged.

### Task 1: CLI Wizard Routing

**Files:**
- Modify: `src/safefix/cli.py`
- Test: `tests/unit/test_cli.py`, `tests/unit/test_cli_v2.py`

- [ ] Add failing tests for no-argument TTY routing, cwd default, non-TTY fail-closed behavior, and delegation to the existing run path.
- [ ] Implement a small wizard function that returns the equivalent run arguments/config inputs and reuses `_run_command`; keep `run` parsing untouched.
- [ ] Run the focused CLI tests and the existing CLI regression tests.

### Task 2: Existing-Schema Configuration Bootstrap

**Files:**
- Modify: `src/safefix/cli.py`
- Test: `tests/unit/test_cli.py` or a focused config test module

- [ ] Add failing tests for the default OpenAI-compatible base URL, required model input, and generated three-role TOML sections without credentials.
- [ ] Implement minimal TOML generation using the existing role sections; never overwrite an existing `safefix.toml`.
- [ ] Verify generated config loads through the existing `load_config` path.

### Task 3: User Documentation and Verification

**Files:**
- Modify: `README.md`
- Test: `tests/unit/test_readme.py`

- [ ] Document `safefix` wizard usage, TTY/non-TTY behavior, and the retained explicit `run` command.
- [ ] Add a small local demo-project recipe or reference without committing secrets or changing production fixtures.
- [ ] Run `python -m pytest tests -q`, `python -m compileall -q src`, and `git diff --check`.
