# SafeFix

SafeFix is a local Python CLI that repairs pytest failures with path
guardrails, bounded feedback, snapshots, and an OpenAI-compatible LLM
endpoint. It is a command-line harness, not a hosted application.

## Requirements and platform limits

- Python 3.11 or newer.
- Network access from the local process to the OpenAI-compatible `base_url`
  configured for the project.

SafeFix has no WebUI and does not provide a cloud service or deployment
endpoint. It supports the local CLI workflow above on platforms with Python
and a compatible terminal when using the interactive console.

## Obtain and install

Obtain a source checkout from the published repository, then install it in a
virtual environment:

```bash
git clone https://github.com/SelfLo06/SafeFix.git safefix
cd safefix
python -m venv .venv
. .venv/bin/activate
python -m pip install .
```

The hosted [v0.2.0 Release](https://github.com/SelfLo06/SafeFix/releases/tag/v0.2.0)
provides the [wheel](https://github.com/SelfLo06/SafeFix/releases/download/v0.2.0/safefix-0.2.0-py3-none-any.whl)
and [source distribution](https://github.com/SelfLo06/SafeFix/releases/download/v0.2.0/safefix-0.2.0.tar.gz).

The activation line above is for POSIX shells. On Windows, use the shell-specific
command below:

Command Prompt:

```bat
.venv\Scripts\activate.bat
```

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

To build release artifacts, install the build frontend and create both a
wheel and a source distribution (sdist):

```bash
python -m pip install build
python -m build --wheel --sdist --outdir dist
```

Install either artifact into an environment:

```bash
python -m pip install dist/*.whl
# Or, in a clean environment:
python -m pip install dist/*.tar.gz
```

## Configure and run

For the simplest interactive start, change to a Python project and run
`safefix` without arguments:

```bash
cd /path/to/project
export SAFEFIX_REPAIR_API_KEY="..."
safefix
```

In a TTY, the lightweight wizard uses the current directory, detects existing
tests, selects standard mode and the TUI, and reuses the normal `run` path.
When `safefix.toml` is missing, it asks for a Repair Model base URL (defaulting
to `https://api.openai.com/v1`) and a required model name, then writes only
the current Repair configuration schema:

```toml
base_url = "..."
model = "..."
```

It does not create Test or Review Model settings and never reuses Repair
settings for those roles. Use `safefix run PATH ...` for scripts, CI, or
explicit advanced options. No-argument startup requires a TTY; redirected or
non-TTY execution remains plain and fail-closed.

Create `safefix.toml` in the project root. `base_url` and `model` are
required for `safefix run`; pytest arguments must use the allowlist described
below.

```toml
base_url = "https://llm.example/v1"
model = "repair-model"
pytest_args = ["-q", "--tb=short"]
```

Set the role-specific API credentials in the current process environment and
run the repair:

```bash
export SAFEFIX_TEST_API_KEY="..."
export SAFEFIX_REPAIR_API_KEY="..."
export SAFEFIX_REVIEW_API_KEY="..."
safefix run .
```

SafeFix reads API credentials from environment variables and does not store
them. It does not load `.env` files and does not provide shared or provider
fallback variables. Missing credentials report only the required role
variable name.

## Terminal presentation

SafeFix installs `prompt_toolkit` and Rich with the normal package installation.
On a capable interactive terminal, `safefix run .` opens the scrollback-first
Guided Repair Console. It renders only safe event summaries and keeps normal
terminal scrollback available while the repair runs. The console accepts
operator controls such as `/pause`, `/resume`, `/stop`, and guidance text;
it never shows credentials, raw model responses, full prompts, or source
content outside SafeFix's existing safe event summaries.

Use `--tui` to request this terminal presentation or `--plain` to force the
structured legacy event output. `--tui` and `--plain` are mutually exclusive.
Non-TTY input or output always uses plain output, including when
`--tui` is present, so logs and CI never start an interactive prompt. Use
`--no-animation` to disable transient progress animation; it changes only
presentation, never configuration, repair decisions, event ordering,
artifacts, stop reasons, or exit codes.

Color and Unicode are disabled for `TERM=dumb`; color is also disabled when
`NO_COLOR` is set. No special terminal font is required. Textual and a WebUI
are not part of SafeFix.

## v0.2 repair options

The `run` command retains the legacy `--base-url` and `--model` options and
also accepts `--generate-tests`, `--baseline-source` (`existing`, `generated`,
or `mixed`), `--acceptance-mode` (`review`, `standard`, or `high-risk`),
`--stability-runs`, and `--max-auto-accepted-failures`. Generated-only mode is
valid only after SafeFix has discovered no existing collected tests. The three
model roles are Repair, Test, and Review; role-specific endpoint/model options
are `--test-base-url`, `--test-model`, `--review-base-url`, and
`--review-model`, with credentials supplied through
`SAFEFIX_TEST_API_KEY`, `SAFEFIX_REPAIR_API_KEY`, and
`SAFEFIX_REVIEW_API_KEY`.

Acceptance and baseline results are recorded in the SafeFix session artifact.
Artifacts preserve the frozen test manifest, generated-test preparation
summary, safe model identities, evaluation and review summaries, counters, and
the final StopReason; they do not contain secrets or raw model output.

All paths supplied to SafeFix tools and path options are project-relative;
absolute paths and paths escaping the project root are rejected. The default
write scope is Python files under `src`; tests and secret-like paths remain
non-writable. `--allowed-path` and `--excluded-path` also accept only
project-relative paths.

Only these pytest arguments are allowed through `pytest_args` or repeated
`--pytest-args`: `-q`, `-v`, `--tb=short`, `--tb=line`, `--tb=no`,
`--disable-warnings`, and `-r` report forms such as `-rA`. Selection and
execution-changing arguments such as `-k`, `-m`, `-x`, and `--collect-only`
are rejected.

The repair-loop defaults are `max_steps = 30`, `max_rounds = 10`, and
`max_no_progress_rounds = 3`. HITL approval is required for changes affecting
`>3 files` or `>80 lines`.

Approval is fail-closed: in a TTY SafeFix prompts for approval; in
non-interactive mode (or with `--non-interactive`) it denies an action requiring
approval, and it never auto-approves it. The default non-interactive policy is
deny; non-interactive mode SafeFix must deny approval-required actions.

Project memory is bounded and opt-in. Library callers must explicitly request
`use_memory=True`; the default context and `safefix run` do not load project
memory. Memory stores summaries only, not credentials or source files.
