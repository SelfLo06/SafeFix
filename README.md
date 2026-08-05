# SafeFix

SafeFix is a local Python CLI that repairs pytest failures with path
guardrails, bounded feedback, snapshots, and an OpenAI-compatible LLM
endpoint. It is a command-line harness, not a hosted application.

## Requirements and platform limits

- Python 3.11 or newer.
- A configured OS keyring backend: macOS Keychain, Windows Credential
  Manager, or Linux Secret Service. The backend is platform-dependent and
  must be available to the local Python process.
- Network access from the local process to the OpenAI-compatible `base_url`
  configured for the project.

SafeFix has no WebUI and does not provide a cloud service or deployment
endpoint. It supports the local CLI workflow above; platform support depends
on Python and the operating-system keyring backend.

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

The hosted [v0.1.0 Release](https://github.com/SelfLo06/SafeFix/releases/tag/v0.1.0)
provides the [wheel](https://github.com/SelfLo06/SafeFix/releases/download/v0.1.0/safefix-0.1.0-py3-none-any.whl)
and [source distribution](https://github.com/SelfLo06/SafeFix/releases/download/v0.1.0/safefix-0.1.0.tar.gz).

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

Create `safefix.toml` in the project root. `base_url` and `model` are
required for `safefix run`; pytest arguments must use the allowlist described
below.

```toml
base_url = "https://llm.example/v1"
model = "repair-model"
pytest_args = ["-q", "--tb=short"]
```

Store the provider credential in the OS keyring, check its status, run the
repair, and clear it when it is no longer needed:

```bash
safefix credentials set    # prompts without echo
safefix credentials status
safefix run .
safefix credentials clear
```

Credentials are keyring-only. SafeFix does not read API keys from environment variables,
does not load `.env` files, and has no fallback. Do not commit `safefix.toml` if it contains sensitive
endpoint details.

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
