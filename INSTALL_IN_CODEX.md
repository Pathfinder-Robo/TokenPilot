<!-- Guided installation instructions and preference precedence rules for TokenPilot in Codex. -->
<!-- TokenPilot 在 Codex 中的引导式安装说明与外观偏好优先级规则文档。 -->
# Guided Installation in Codex (`INSTALL_IN_CODEX.md`)

Use this guide when asking **Codex** to build, configure, and optionally launch **TokenPilot** on macOS.

---

## Copyable Prompt for Codex

Copy and paste the block below into your Codex session (optionally adding explicit preferences such as *"use Liquid Glass"* or *"use Simplified Chinese"*):

```text
Please install TokenPilot from https://github.com/Pathfinder-Robo/TokenPilot using my existing GitHub access. If it is not checked out, clone it into a new TokenPilot folder without overwriting an existing checkout. Follow INSTALL_IN_CODEX.md:
1. Build the native macOS app with ./app/Build.command.
2. Inspect saved/system appearance preferences with python3 configure_app.py --inspect.
3. Resolve language (en | zh-Hans) and theme (cupertino | graphite | instrument | frosted | liquid | vibrant) using the precedence rules in INSTALL_IN_CODEX.md:
   - My explicit choices in this request override saved settings.
   - Valid saved settings survive reinstallation and override weak chat clues (do not overwrite a saved UI language just because we are chatting in another language).
   - Clear conversational preferences can fill fields without saved choices; otherwise use supported macOS defaults from the inspect proposal.
   - If strong cues conflict or any field remains unresolved, ask me only for the unresolved choice(s) before writing preferences.
4. Apply the resolved configuration with python3 configure_app.py (pass --language and/or --theme if needed, and --launch if I asked to open the app).
5. Do not run install.py or configure Google Gemini (Argon) routing unless I separately ask for it; Gemini setup requires my own interactive terminal for explicit --authorize-code-export consent and hidden-echo API-key entry.
```

---

## Assistant Workflow & Precedence Rules

### 1. Build the Native App First

`configure_app.py` requires an existing built `TokenPilot.app` bundle (default `dist/TokenPilot.app`) and never triggers an implicit build, skill installation, or network request.

```bash
./app/Build.command
```

### 2. Inspect Local Saved & System Preferences (Read-Only)

Run the read-only inspector:

```bash
python3 configure_app.py --inspect
```

What `--inspect` (and the underlying native `--inspect-install-preferences` flag) does:
- Reads only local `UserDefaults` for bundle identifier `local.routingfloat.viewer` (`TokenPilot.AppearanceSettings.v2`), macOS `Locale.preferredLanguages`, and `NSApp.effectiveAppearance.bestMatch(from: [.aqua, .darkAqua])`.
- Performs **no writes**, opens no windows, starts no collector backend, and makes no network or credential reads.
- **Note on chat context**: Neither `configure_app.py` nor the native `TokenPilot` binary can read Codex chat history or session logs during installation. Any conversational preference inference is performed by the installing Codex assistant when deciding which CLI flags (`--language`, `--theme`) to pass.

### 3. Resolve `language` and `theme` Without Redundant Prompts

Evaluate `language` (`en` or `zh-Hans`) and `theme` (`cupertino`, `graphite`, `instrument`, `frosted`, `liquid`, `vibrant`) independently using this strict order:

1. **Current Explicit User Request Wins (`explicit`)**:
   - If the user explicitly asks for a specific theme or language in their current installation prompt (for example, *"install with Liquid Glass and Simplified Chinese"* or *"use the vibrant theme"*), pass `--theme liquid --language zh-Hans` or `--theme vibrant` to `configure_app.py`.
   - Explicit CLI flags override both saved preferences and macOS system defaults while preserving any saved custom hex accent colors.
2. **Valid Saved Preferences Survive (`saved`)**:
   - If `configure_app.py --inspect` reports `language_source: "saved"` or `theme_source: "saved"`, keep that saved setting unless the user explicitly asked to change it.
   - **Mere chat language must never overwrite a saved UI preference**: If a user already has `"language": "en"` saved in `UserDefaults` (`source: "saved"`) and asks in Chinese to rebuild or update the app without requesting a UI language change, keep `"en"`. Likewise, saved themes such as `liquid`, `vibrant`, `frosted`, or `instrument` remain untouched when no explicit theme override is given.
   - If one saved field is corrupt, missing, or unknown while the other is valid, the valid saved field is preserved and only the corrupt/missing field falls back independently.
3. **Clear Conversational Preferences vs. Supported System Defaults (`system`)**:
   - For fields that are **not** already saved:
     - If the user expressed a clear conversational preference for language or theme, pass it via `--language` / `--theme`.
     - If strong cues conflict (for example, the user gives contradictory instructions about which theme or language to use), ask a brief clarifying question instead of guessing.
     - Otherwise, accept the reliable macOS system inference reported by `--inspect`:
       - **System Language (`Locale.preferredLanguages`)**: First clearly supported preferred language wins: `en` / `en-*` -> `en`; `zh-Hans`, `zh-CN`, `zh-SG`, or generic `zh` -> `zh-Hans`. Traditional Chinese (`zh-Hant`, `zh-TW`, `zh-HK`, `zh-MO`) is **never** silently mapped to Simplified Chinese (`zh-Hans`); the helper checks later preferred languages and leaves `language` unresolved if no supported language is present.
       - **System Appearance (`NSApp.effectiveAppearance`)**: Dark Aqua (`darkAqua`) -> `graphite`; Light Aqua (`aqua`) -> `cupertino`. This infers only light/dark matching default surfaces—never a preference for glass (`frosted`, `liquid`) or `vibrant` themes.
4. **Ask Only What Remains Unresolved (`needs_input`)**:
   - When both fields resolve cleanly, do **not** ask the user to confirm before configuring.
   - When `--inspect` (or a non-TTY run of `configure_app.py`) exits with code `2` and `"needs_input": true`, inspect `unresolved_fields` / `questions` and ask the user **only** for the missing field(s) (`language`, `theme`, or both). Never invent silent fallback defaults for unresolved fields or partially write a proposal.

### 4. Apply Preferences (and Optionally Launch)

- When saved/system inferences are complete and no override is needed:

```bash
python3 configure_app.py
```

- When passing explicit or newly answered choices (and optionally launching `TokenPilot.app` via `open -a`):

```bash
python3 configure_app.py --language zh-Hans --theme liquid --launch
```

- If the resolved `language` and `theme` already match the valid saved record in `UserDefaults`, `configure_app.py` avoids rewriting preferences gratuitously (`"wrote_preferences": false`).
- **Direct App Launches**: Launching `TokenPilot.app` directly in Finder or via `open` without running `configure_app.py` (or `--configure-install-preferences`) uses `AppearanceStore`'s standard startup defaults if no preferences have been persisted yet.

---

## Optional Gemini (Argon) Task Routing Is Separate

Appearance inference and `configure_app.py` **never** install the Gemini routing skill, **never** grant or imply code-export authorization, and **never** read, prompt for, or store a Google API key.

If the user separately wants optional Google Gemini (`argon-routing`) task delegation:
- They must run `install.py` themselves in their own interactive terminal so they can explicitly pass `--authorize-code-export` and enter `GEMINI_API_KEY` at the hidden-echo prompt:

```bash
python3 install.py --configure-key --authorize-code-export
```

- Never ask the user to paste `GEMINI_API_KEY` into chat or pass it on a command line.
