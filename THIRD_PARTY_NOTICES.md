# Third-Party Notices & Provenance Status

## IMPORTANT: Unconfirmed Provenance & Redistribution Rights

- **Argon Routing / Worker / Installer Components (`install.py`, `routing/`)**:
  - The original Argon routing setup, worker scripts, and installer in this workspace were adapted from user-supplied local materials that did **not** include a `LICENSE` file or explicit copyright/redistribution grant.
  - **Rights are currently UNCONFIRMED.**
  - **No license grant (such as MIT, Apache-2.0, or BSD) and no copyright ownership claim are made in this package.**
  - Do **not** add a `LICENSE` file or publish/redistribute this package publicly until authorship, provenance, and redistribution rights for all included files are confirmed and authorized by the rightful owner(s).

## External Runtime & Service Dependencies (Not Bundled)

1. **Apple macOS System Frameworks (`AppKit`, `SwiftUI`, `CoreGraphics`, `QuartzCore`, `Combine`)**
   - Linked from the host macOS SDK when running `app/Build.command` on macOS 15.0+.
2. **Python Standard Library (Python 3.10+)**
   - Used by `app/bridge.py`, `app/forecasting.py`, `app/nextreset.py`, `install.py`, `routing/scripts/*.py`, and `tests/*.py`. No third-party PyPI packages are bundled or required.
3. **OpenAI Codex CLI (`codex`)**
   - Invoked locally by `app/bridge.py` (`codex app-server`) from the user's host installation to read account rate limits.
4. **Google Generative Language Interactions API (`https://generativelanguage.googleapis.com/v1beta/interactions`)**
   - Called over HTTPS by `routing/scripts/argon_worker.py` only when configured by the user with their own API key and explicit consent.
5. **NextReset Public Forecast API (`https://nextreset.ai/api/forecast`)**
   - Polled read-only by `app/nextreset.py` for experimental community reset probabilities.
   - `tests/forecast-fixture.json` is a minimal synthetic test fixture matching the expected JSON schema (`example.invalid` / `x.com/example/status/123`) and contains no third-party social posts or personal data.
