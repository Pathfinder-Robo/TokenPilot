<!-- Security policy, credential controls, and sandbox boundaries for TokenPilot and the Argon worker. -->
<!-- TokenPilot 与 Argon 工作进程的安全策略、凭据控制及沙盒边界文档。 -->
# Security Policy & Controls

## No Full Security Guarantee

TokenPilot and the optional Argon worker implement defense-in-depth boundaries for local usage monitoring and delegated coding tasks. **They do not provide a formal security guarantee or certified multi-tenant isolation.** Users are responsible for reviewing worker-generated diffs, protecting their local machine and API credentials, and evaluating whether external task processing fits their project's policies.

## Credential & Consent Controls

- **External Credential Storage (`0700` / `0600`)**:
  - User API keys are read via a hidden interactive terminal prompt (`python3 install.py --configure-key --authorize-code-export`) and stored outside the repository at `${CODEX_HOME:-~/.codex}/argon/credentials.env` with mode `0600` inside `${CODEX_HOME:-~/.codex}/argon` (`0700`).
  - Never paste API keys into chat sessions, commit them to source control, or pass them via echoed shell pipes.
- **Strict Permission Checks**:
  - `argon_worker.py` and `run_worker.py` refuse to read a credential file if group or other permissions are set (`st_mode & 0o077`).
- **Explicit User Authorization (No Inherited Consent)**:
  - This package ships with no `authorization.json` or `credentials.env`.
  - `run_worker.py` verifies that the current user's `${CODEX_HOME:-~/.codex}/argon/authorization.json` explicitly sets `"necessary_task_code_to_google": true` before sending any task context to Google.
- **Routing Kill Switch (`0%`)**:
  - When `gemini_percent` is set to `0` in `${CODEX_HOME:-~/.codex}/argon/routing.json`, `run_worker.py` exits with `status="routing_disabled"` before making any API call.
- **Viewer Process Isolation**:
  - Both `main.swift` and `bridge.py` strip `GEMINI_API_KEY` from child process environments.

## Worker Workspace & Sandbox Boundaries

- **Workspace Scoping**:
  - File tools (`list_files`, `read_file`, `write_file`, `replace_text`) resolve paths strictly inside the assigned `--workspace`, block private names (`.git`, `.env*`, `.home`, `.tmp`, `__pycache__`) and symlink escapes, and enforce a 1 MB file size cap.
  - Both the Python interpreter (`--python`) and `--result-dir` must reside outside the mutable workspace.
  - API error messages and saved result artifacts scrub the configured API key (`[REDACTED]`).
- **Sandboxed Python Execution (`run_python`)**:
  - When `run_python` is enabled in `implement` mode, `argon_worker.py` requires `/usr/bin/sandbox-exec` on macOS or `bwrap` (bubblewrap) on Linux, denying network access and restricting writes to the assigned workspace.
  - If no supported sandbox backend is available, `run_python` refuses to fall back to unsandboxed execution; use `--file-tools-only` so Codex runs checks independently.

## Reporting Security Issues

Do not report security vulnerabilities or credential exposure in public issues or pull requests.

For this private repository, contact the Pathfinder-Robo repository administrators through an established private organization channel. Before any public release, maintainers should document a dedicated private security reporting channel.
