# Release Checklist

## 1. Blocking Legal & Provenance Items (UNRESOLVED)

These items are **not** resolved by technical packaging and **must** be completed by the repository owner before any public release or redistribution:

- [ ] **UNRESOLVED — Confirm provenance and redistribution rights for Argon routing/worker/installer files (`install.py`, `routing/`)**: The original Argon setup materials were user-supplied without a `LICENSE` file. Verify authorship and obtain explicit permission to publish and license every included file.
- [ ] **UNRESOLVED — Select and add a root `LICENSE` file**: Only after ownership and third-party rights are confirmed, add the appropriate `LICENSE` file and update `THIRD_PARTY_NOTICES.md`.
- [ ] **Before public release — Document a dedicated private security reporting channel**: `SECURITY.md` currently directs private-repository users to the organization administrators.

## 2. Pre-Release Technical & Privacy Verification (For Root / Maintainer)

- [ ] **Secret & Credential Scan**: Verify no API keys, `credentials.env`, `authorization.json`, `installation.json`, `auth.json`, or session logs appear in the publication tree.
- [ ] **Path & Fixture Hygiene**: Verify no personal filesystem paths (e.g. `/Users/<username>`) or personal social handles/URLs appear in `app/`, `routing/`, `install.py`, `tests/`, or documentation.
- [ ] **Portable Unit Tests**: Run `python3 tests/run_tests.py` in an isolated environment and confirm all unit tests pass without accessing `~/.codex` or live network endpoints.
- [ ] **Staged Native App Build & Self-Checks**: Run `TOKENPILOT_APP_DIR=/tmp/TokenPilot.app ./app/Build.command` and `/tmp/TokenPilot.app/Contents/MacOS/TokenPilot --ui-checks`, confirming `~/.codex` is untouched by the build.
- [ ] **Clean Export Allowlist**: Exclude `work/`, `dist/`, `*.app/`, `.build/`, `.module-cache/`, `__pycache__/`, `runtime.json`, screenshots, and worker result directories when producing the final clean publication folder.
