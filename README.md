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
# Replace REPOSITORY_URL with the published repository URL.
git clone REPOSITORY_URL safefix
cd safefix
python -m venv .venv
. .venv/bin/activate
python -m pip install .
```

The activation line above is for POSIX shells; on Windows use
`.venv\\Scripts\\activate` in Command Prompt or PowerShell.

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
safefix credentials set 'paste-your-provider-key-here'
safefix credentials status
safefix run --project-root .
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
`--pytest-arg`: `-q`, `-v`, `--tb=short`, `--tb=line`, `--tb=no`,
`--disable-warnings`, and `-r` report forms such as `-rA`. Selection and
execution-changing arguments such as `-k`, `-m`, `-x`, and `--collect-only`
are rejected.

Approval is fail-closed: in non-interactive mode SafeFix must deny an action
requiring approval, and it never auto-approves it. The CLI does not enable an
interactive approval prompt.

Project memory is bounded and opt-in. Library callers must explicitly request
`use_memory=True`; the default context and `safefix run` do not load project
memory. Memory stores summaries only, not credentials or source files.
