# Source Preparation and Verification Report

This source-only private repository contains the TokenPilot app, optional Gemini routing setup, and a guided Codex installation workflow.

## Latest verification

- All 69 portable Python tests passed, including appearance inference orchestration, missing-answer handling, cancellation, malformed responses and bounded subprocess failures.
- All 42 isolated native preference tests passed: saved and explicit preference precedence, language/script support, light/dark defaults, partial saved records, read-only inspection, invalid inputs, first-install persistence, and retention of custom palettes.
- Native macOS build succeeded and all 164 existing app self-checks passed. Build outputs were created outside this source tree.
- A read-only inspection of an existing installation returned saved choices without changing them. No current app preferences were configured during verification.
- The source copy is scanned for recognizable secret patterns, personal absolute paths, runtime state and generated artifacts before each upload. Pattern scanning is not a security certification. No credentials, consent records, usage snapshots, worker transcripts, compiled apps or build caches are included.

## Guided preference setup

See INSTALL_IN_CODEX.md. Explicit user choices override saved preferences; valid saved settings survive reinstallation. The installing assistant may use clear conversational preferences, while configure_app.py inspects only local saved settings and macOS language/appearance. It cannot read chat history. Unknown choices are asked before any write. Gemini authorization and API-key entry remain separate.

## Before any public release

Licensing and redistribution rights for the original user-supplied Argon materials remain unconfirmed. No license grant was added. See THIRD_PARTY_NOTICES.md and RELEASE_CHECKLIST.md before making this repository public.
