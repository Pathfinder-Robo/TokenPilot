# TokenPilot

TokenPilot is a native macOS floating panel and menu bar utility for viewing local **Codex** and optional **Google Gemini (Argon worker)** coding usage, account rate-limit windows, trailing 15-minute quota forecasts, experimental public community reset signals, and live task-routing preferences.

TokenPilot can be built and used standalone with Codex alone—installing the optional Gemini routing skill or configuring a Google API key is not required to run the viewer.

---

## Features

- **Read-Only Viewer: Account Quotas vs. Observed Tokens**
  - **Account Quotas (`5-hour` and `Weekly`)**: Queries the user's installed `codex` CLI (`codex app-server` `account/rateLimits/read`) for official percentage usage and reset timestamps on the `300m` (5-hour) and `10080m` (weekly) quota windows. Authentication is handled solely by the local Codex CLI; TokenPilot never reads `auth.json`.
  - **Observed Today Tokens**: Aggregates numerical token usage (`total`, `cached`, `input`, `output`) for the **current local calendar day** from local Codex session logs (`sessions/` and `archived_sessions/`) and locally recorded Gemini worker runs (`argon/usage-cache/`, `argon/runs.jsonl`, `argon/outcomes.jsonl`). Token counts are observational context, not quota units. The collector reads session-log lines locally to locate usage records and retains/displays usage metadata. These raw logs can contain conversation content; legacy Gemini fallback files can also contain task content. The viewer does not upload those logs or read saved API keys.
- **Trailing 15-Minute Quota Forecast**
  - Tracks recent quota percentage samples over a trailing 15-minute window (`argon/viewer/quota-history.json`) to estimate short-term depletion pace assuming unchanged usage.
  - Automatically selects the primary window to highlight: shows **Weekly** when the 5-hour window has more than 95% remaining, otherwise selects the earliest projected exhaustion or active limit.
  - Reports a dedicated **Next reset comes first** state when the scheduled reset precedes projected depletion, and avoids fabricating estimates across idle periods, stale snapshots, or mid-window resets.
- **Experimental Public NextReset Card (`nextreset.ai`)**
  - Fetches public community reset forecast summaries (`24h` / `48h` / `72h` probabilities, announcement state, expected rollout window, and HTTPS signal links) from `https://nextreset.ai/api/forecast`.
  - **Public and experimental**: NextReset data reflects a community tracker, not a personal or official OpenAI account reset schedule. Requests send no account identifiers, cookies, or local usage data.
- **Live Routing Preference Slider (`0–100%`)**
  - Writes `${CODEX_HOME:-~/.codex}/argon/routing.json` (`0600`) to guide how Codex delegates *new* substantive coding tasks when the optional `argon-routing` skill is installed.
  - **Delegation preference, not an automatic proxy or token allocator**: Moving the slider does not proxy API traffic, split token budgets, or interrupt active workers. Setting the slider to `0%` (**GPT only**) causes the bundled worker launcher (`run_worker.py`) to block new Gemini worker runs before any network call.
- **6 Themes, Bilingual UI (EN / 中文), Native Glass & Focus Behavior**
  - **6 Themes**: *Cupertino*, *Quiet Graphite* (default), *Instrument* (high-contrast monochrome grid; custom colors disabled), *Frosted Glass*, *Liquid Glass*, and *Vibrant*, with automatic WCAG contrast enforcement and per-theme custom primary/secondary hex colors.
  - **Bilingual**: Full **English (`en`)** and **Simplified Chinese (`zh-Hans`)** interface localization.
  - **Native macOS SwiftUI/AppKit (Not a Browser Wrapper)**: Built directly with AppKit and SwiftUI targeting **macOS 15.0+**. Native Liquid Glass can change appearance when its window becomes inactive; AppKit supplies the reflections and refraction. When the *Liquid Glass* theme is active on **macOS 26+**, TokenPilot uses a runtime check (`NSClassFromString("NSGlassEffectView")`) to host content inside native `NSGlassEffectView` with a Gaussian text-frost backing field, and automatically falls back to `NSVisualEffectView` on macOS 15.
  - **Non-Activating Focus Behavior**: The floating widget runs as a `.nonactivatingPanel` so dragging the panel, collapsing/expanding, or adjusting the routing slider does not steal focus from your active IDE or terminal. Opening **Customize…** activates a standard key window for color and theme editing.

---

## Repository Layout

```text
.
├── app/
│   ├── Build.command             # Self-contained macOS .app builder (outputs to dist/TokenPilot.app)
│   ├── main.swift                # Floating panel, status bar item, collector IPC, and --ui-checks
│   ├── Appearance.swift          # 6 themes, WCAG contrast engine, EN/zh-Hans localization, Customize UI
│   ├── LiquidGlassContent.swift  # macOS 26+ NSGlassEffectView runtime bridge & macOS 15 fallback
│   ├── GaussianTextFrost.swift   # Weighted 2D Gaussian text-legibility frost field for Liquid Glass
│   ├── NextResetCardView.swift   # SwiftUI card presentation for public NextReset forecast data
│   ├── Icon.swift                # Standalone AppKit iconset generator (asset utility, not app entry)
│   ├── TokenPilot.icns           # App bundle icon
│   ├── TokenPilot.png            # Preview icon asset
│   ├── bridge.py                 # Local usage collector, Codex rateLimits reader, and JSON stdio server
│   ├── forecasting.py            # Trailing 15-minute quota depletion forecasting logic
│   └── nextreset.py              # Public NextReset forecast client and schema validator
├── routing/
│   ├── SKILL.md                  # Optional Argon task-routing skill instructions
│   ├── agents/openai.yaml        # Skill metadata for Codex discovery
│   └── scripts/
│       ├── argon_worker.py       # Sandboxed Google Interactions API coding worker
│       ├── routing_control.py    # Live routing preference reader and numeric run-state publisher
│       └── run_worker.py         # Consent- and routing-aware worker launcher
├── install.py                    # Explicit opt-in installer for the optional Argon routing skill
└── tests/
    ├── run_tests.py              # Portable unittest runner (requires no installed ~/.codex state)
    ├── forecast-fixture.json     # Small synthetic NextReset forecast fixture
    ├── test_bridge.py            # Collector, quota normalization, launcher block, and installer tests
    ├── test_forecasting.py       # 15-minute quota forecast and display-selection tests
    ├── test_nextreset_ai.py      # NextReset JSON/HTML parsing, expiry, and URL validation tests
    ├── test_source_integration.py# Collector NextReset cache and staleness integration tests
    └── test_transport.py         # Worker HTTP IncompleteRead and bounded polling retry tests
```

---

## Requirements

- **Native Viewer (`app/`)**:
  - **macOS 15.0+** (macOS 26+ enables native `NSGlassEffectView` for the *Liquid Glass* theme; macOS 15 uses `NSVisualEffectView`).
  - **Xcode Command Line Tools** (`swiftc`, `codesign`).
  - **Python 3.10+** (standard library only).
  - **Codex CLI** (`codex` available on `PATH`, or specified via `TOKENPILOT_CODEX_BIN`).
- **Optional Gemini Routing (`install.py`, `routing/`)**:
  - **macOS or Linux** with **Python 3.10+**.
  - Execution tool (`run_python`) uses `/usr/bin/sandbox-exec` on macOS or `bwrap` (bubblewrap) on Linux when present; otherwise the worker runs with file tools only (`--file-tools-only`) and delegates command execution to Codex.
  - **Your own Google API key and model access**: The default configured model name is `gemini-4-argon`. This package does not grant model access or imply that `gemini-4-argon` is available to every Google account; you must have access to the configured model or override it with a compatible model via `GEMINI_MODEL`.

---

## Build & Run the Native App

Run `app/Build.command` from the repository root:

```bash
./app/Build.command
```

What `app/Build.command` does:
1. Compiles the 5 app Swift sources (`main.swift`, `Appearance.swift`, `LiquidGlassContent.swift`, `GaussianTextFrost.swift`, `NextResetCardView.swift`) into `dist/TokenPilot.app/Contents/MacOS/TokenPilot` using a local module cache under `.build/module-cache`.
2. Copies `bridge.py`, `forecasting.py`, `nextreset.py`, and `TokenPilot.icns` into `dist/TokenPilot.app/Contents/Resources/`.
3. Generates `dist/TokenPilot.app/Contents/Resources/runtime.json` with `bridge_path: null`, so the app resolves `Contents/Resources/bridge.py` from its current bundle location even after the app is moved, and performs local ad-hoc code signing (`codesign --sign -`).
4. **Does not modify `~/.codex`**: Building the app only writes inside the repository (`dist/` and `.build/`). When you later launch `TokenPilot.app`, the running app reads your runtime `codex_home` (`${CODEX_HOME:-~/.codex}`) to view sessions and store viewer preferences.

> **Note:** `Contents/Resources/runtime.json` inside the built `.app` records machine-specific binary and profile paths (`python`, `codex_binary`, `codex_home`, `bridge_path`) and should not be redistributed.

### Optional Build Overrides

```bash
TOKENPILOT_APP_DIR="/tmp/TokenPilot.app" \
TOKENPILOT_BUILD_DIR="/tmp/tokenpilot-build" \
TOKENPILOT_PYTHON_BIN="$(command -v python3)" \
TOKENPILOT_CODEX_BIN="$(command -v codex)" \
./app/Build.command
```

### Run Built-In UI & Contrast Self-Checks

```bash
./dist/TokenPilot.app/Contents/MacOS/TokenPilot --ui-checks
```

---

## Run Tests

Run the portable unit test suite from a clean checkout (does not read or modify `~/.codex` and makes no live network calls):

```bash
python3 tests/run_tests.py
```

---

## Optional: Install Gemini (Argon) Task Routing

If you want Codex to delegate suitable coding tasks to the external Google worker, run `install.py` from your own interactive terminal.

**Never paste your API key into chat, commit it to a repository, or pipe it on an echoed command line.**

```bash
python3 install.py --configure-key --authorize-code-export
```

To update an existing installation after backing it up:

```bash
python3 install.py --configure-key --authorize-code-export --replace
```

### What `install.py` Modifies (Outside the Repository)

`install.py` requires `--authorize-code-export` (confirming your consent to send necessary task context to Google) and prompts for your Google API key with terminal echo disabled. It writes only under `${CODEX_HOME:-~/.codex}` and `~/.agents`:

1. **Skill Files**: Copies `SKILL.md`, `agents/openai.yaml`, `scripts/argon_worker.py`, `scripts/routing_control.py`, and `scripts/run_worker.py` into `${CODEX_HOME:-~/.codex}/skills/argon-routing/` and creates a discovery symlink at `~/.agents/skills/argon-routing`.
2. **Credentials & Consent (`0700` / `0600`)**: Creates `${CODEX_HOME:-~/.codex}/argon/` (`0700`) and writes `credentials.env` (`0600`), `authorization.json` (`0600`), `installation.json` (`0600`), and `outcomes.jsonl` (`0600`).
3. **Global Instructions**: Inserts or updates a marked `<!-- BEGIN argon-routing --> ... <!-- END argon-routing -->` block in `${CODEX_HOME:-~/.codex}/AGENTS.md` (or `AGENTS.override.md` if non-empty) instructing Codex to consult the installed `SKILL.md` and run `routing_control.py` to read the live `gemini_percent` preference before tasks.
4. **Automatic Backup**: Saves backups of any prior `AGENTS` file, skill directory, and state files under `${CODEX_HOME:-~/.codex}/argon/backups/<uuid>/` (`0700` / `0600`).

---

## Documentation & Release Notices

- [PRIVACY.md](PRIVACY.md) — Local file access, external network requests, and artifact hygiene.
- [SECURITY.md](SECURITY.md) — Credential permissions, consent enforcement, sandbox scope, and limitations.
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — External runtime dependencies and **unconfirmed provenance/licensing notice** for user-supplied Argon materials.
- [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) — Blocking legal/licensing items and pre-release technical verification steps.

## Offline design preview

Open `examples/design-preview.html` in a browser to explore the six themes using sample data. It does not connect to your account or change actual routing settings. Native macOS glass optics are only available in the app.
