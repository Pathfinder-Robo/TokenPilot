<!-- Privacy notice detailing local file access, preference storage, and network requests in TokenPilot. -->
<!-- 详细说明 TokenPilot 本地文件访问、偏好存储及网络请求的隐私声明文档。 -->
# Privacy Notice

TokenPilot processes usage metadata and appearance preferences locally, with the explicitly described network requests below.

## 1. Local Data Accessed by the Viewer (`app/bridge.py`)

- **Codex Session Logs (`${CODEX_HOME:-~/.codex}/sessions/` and `archived_sessions/`)**:
  - `bridge.py` tails `*.jsonl` files and filters for lines containing `"token_usage_record"` or `"token_count"`.
  - Only timestamps, response IDs, numerical token counts (`total`, `input`, `cached`, `output`), and rate-limit window percentages are retained in memory.
  - Raw session lines are read locally before filtering and matching records are parsed as JSON. Conversation text is not retained or displayed by the viewer, but the source files can contain it. The viewer does not upload those source logs.
- **Gemini Worker Numeric Summaries (`${CODEX_HOME:-~/.codex}/argon/`)**:
  - Prefers numeric usage summaries from `argon/usage-cache/*.json`. Legacy fallbacks parse `runs.jsonl`, `outcomes.jsonl`, `interaction-*.json`, or `result.json` locally to extract usage; those legacy files may also contain task content.
- **Local Viewer State**:
  - Writes routing preference to `argon/routing.json` (`0600`) and viewer caches/state to `argon/viewer/quota-history.json`, `argon/viewer/nextreset-cache.json`, and `argon/viewer/ui-state.json`.
- **Guided Appearance Preference Inference (`app/InstallationPreferences.swift`, `configure_app.py`)**:
  - **Local Appearance & OS Reads**: Reads only the app's local `UserDefaults` entry (`TokenPilot.AppearanceSettings.v2` under bundle identifier `local.routingfloat.viewer`), macOS `Locale.preferredLanguages`, and `NSApp.effectiveAppearance` (`aqua` / `darkAqua`).
  - **Read-Only Inspection (`--inspect-install-preferences`, `configure_app.py --inspect`)**: Emits a JSON proposal and exits without writing preferences, creating windows, starting `bridge.py`, making network requests, or reading credentials.
  - **Scoped Writes Only on Configure (`--configure-install-preferences`, `configure_app.py`)**: Writes only the resolved appearance record (`theme`, `language`, and preserved `customColors`) to `UserDefaults` (`TokenPilot.AppearanceSettings.v2`) after all choices are validated and resolved, and skips rewriting when the resolved choices already match the saved record.
  - **No Chat, Session Log, or Credential Access**: Neither `configure_app.py` nor `InstallationPreferences.swift` reads chat transcripts, Codex session logs, `auth.json`, or `credentials.env`, nor do they invoke `install.py` or grant Google code-export consent. Launching `TokenPilot.app` directly (bypassing `configure_app.py` or `--configure-install-preferences`) uses standard `AppearanceStore` startup defaults unless preferences were already saved.
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
