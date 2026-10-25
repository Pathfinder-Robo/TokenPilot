# Privacy Notice

TokenPilot is designed to inspect only numerical usage metadata locally while making explicit, narrowly scoped network requests when running.

## 1. Local Data Accessed by the Viewer (`app/bridge.py`)

- **Codex Session Logs (`${CODEX_HOME:-~/.codex}/sessions/` and `archived_sessions/`)**:
  - `bridge.py` tails `*.jsonl` files and filters for lines containing `"token_usage_record"` or `"token_count"`.
  - Only timestamps, response IDs, numerical token counts (`total`, `input`, `cached`, `output`), and rate-limit window percentages are retained in memory.
  - Raw session lines are read locally before filtering and matching records are parsed as JSON. Conversation text is not retained or displayed by the viewer, but the source files can contain it. The viewer does not upload those source logs.
- **Gemini Worker Numeric Summaries (`${CODEX_HOME:-~/.codex}/argon/`)**:
  - Prefers numeric usage summaries from `argon/usage-cache/*.json`. Legacy fallbacks parse `runs.jsonl`, `outcomes.jsonl`, `interaction-*.json`, or `result.json` locally to extract usage; those legacy files may also contain task content.
- **Local Viewer State**:
  - Writes routing preference to `argon/routing.json` (`0600`) and viewer caches/state to `argon/viewer/quota-history.json`, `argon/viewer/nextreset-cache.json`, and `argon/viewer/ui-state.json`.
- **No Credential File Reads in Viewer**:
  - The viewer never reads Codex `auth.json` or `${CODEX_HOME:-~/.codex}/argon/credentials.env`, and strips `GEMINI_API_KEY` from child processes.

## 2. Outgoing Requests

- **Codex Account Quotas (Local CLI Subprocess)**:
  - `bridge.py` spawns a short-lived `codex app-server` subprocess and sends `account/rateLimits/read` to read 5-hour and weekly quota percentages.
  - Authentication is performed by the user's installed `codex` CLI using its existing sign-in state.
- **Public NextReset Summary (`nextreset.ai`)**:
  - `bridge.py` periodically performs an unauthenticated `GET https://nextreset.ai/api/forecast` request (`User-Agent: TokenPilot/2.2 (public reset summary)`, 1 MB response cap).
  - No account identifiers, cookies, credentials, or local usage data are sent.
  - Passing `--offline` to `bridge.py` disables both quota and NextReset background refreshes.
- **Optional Google Gemini (Argon) Worker (`routing/scripts/`)**:
  - Never called by the viewer or during `install.py`.
  - The supported `run_worker.py` launcher permits invocation only when the user has explicitly run `install.py --authorize-code-export`, configured their own `GEMINI_API_KEY`, and left `gemini_percent > 0`.
  - When launched via `run_worker.py`, sends the assigned task brief, relevant workspace source snippets, and tool results to `https://generativelanguage.googleapis.com/v1beta/interactions`.

## 3. Keeping Local Artifacts Private

- Credentials (`credentials.env`), consent records (`authorization.json`, `installation.json`), session logs, run registries (`runs.jsonl`, `outcomes.jsonl`, `usage-cache/`), worker result directories (`interaction-*.json`, `result.json`, `tools.json`, `changes.patch`, `final.txt`), `work/` directories, build outputs (`dist/`, `*.app`, `runtime.json`), and local screenshots must remain private and outside published source control.
