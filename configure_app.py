#!/usr/bin/env python3
"""Guided installation preference inspector and configurator for TokenPilot.
TokenPilot 的引导式安装外观偏好检查与配置工具。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable, Sequence

BUNDLE_ID = "local.routingfloat.viewer"
STORAGE_KEY = "TokenPilot.AppearanceSettings.v2"

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "zh-Hans")
SUPPORTED_THEMES: tuple[str, ...] = (
    "cupertino",
    "graphite",
    "instrument",
    "frosted",
    "liquid",
    "vibrant",
)

THEME_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": "cupertino",
        "label": "Cupertino / Cupertino 浅色",
        "summary": "Light pearl surface and classic blue actions",
    },
    {
        "value": "graphite",
        "label": "Quiet Graphite / 静谧石墨",
        "summary": "Warm graphite ledger with calm lilac accents",
    },
    {
        "value": "instrument",
        "label": "Instrument / 精密仪表",
        "summary": "High-contrast monochrome grid & mono counters",
    },
    {
        "value": "frosted",
        "label": "Frosted Glass / 磨砂玻璃",
        "summary": "Translucent macOS HUD blur with cool highlights",
    },
    {
        "value": "liquid",
        "label": "Liquid Glass / 液态玻璃",
        "summary": "Refractive Liquid Glass optic with specular rim",
    },
    {
        "value": "vibrant",
        "label": "Vibrant / 炫彩活力",
        "summary": "Deep indigo-teal gradient with luminous accents",
    },
)

LANGUAGE_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "value": "en",
        "label": "English",
        "summary": "English interface",
    },
    {
        "value": "zh-Hans",
        "label": "简体中文",
        "summary": "Simplified Chinese interface / 简体中文界面",
    },
)


def default_app_path() -> Path:
    return Path(__file__).resolve().parent / "dist" / "TokenPilot.app"


def normalize_explicit_language(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if cleaned == "en":
        return "en"
    if cleaned == "zh-hans":
        return "zh-Hans"
    return None


def normalize_explicit_theme(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if cleaned in SUPPORTED_THEMES:
        return cleaned
    return None


def parse_interactive_language(raw: str) -> str | None:
    cleaned = raw.strip().lower()
    if cleaned in ("1", "en", "english"):
        return "en"
    if cleaned in ("2", "zh-hans", "zh_hans", "zh", "zh-cn", "zh_cn", "zh-sg", "zh_sg", "chinese", "简体中文", "中文"):
        return "zh-Hans"
    return None


def parse_interactive_theme(raw: str) -> str | None:
    cleaned = raw.strip().lower()
    if cleaned in SUPPORTED_THEMES:
        return cleaned
    if cleaned.isdigit():
        idx = int(cleaned) - 1
        if 0 <= idx < len(SUPPORTED_THEMES):
            return SUPPORTED_THEMES[idx]
    return None


def default_question_for_field(field: str) -> dict[str, Any]:
    if field == "language":
        return {
            "field": "language",
            "flag": "--language",
            "prompt": "Choose interface language / 请选择界面语言 (en, zh-Hans)",
            "allowed_values": list(SUPPORTED_LANGUAGES),
            "options": [dict(item) for item in LANGUAGE_OPTIONS],
        }
    if field == "theme":
        return {
            "field": "theme",
            "flag": "--theme",
            "prompt": "Choose appearance theme / 请选择外观主题 (cupertino, graphite, instrument, frosted, liquid, vibrant)",
            "allowed_values": list(SUPPORTED_THEMES),
            "options": [dict(item) for item in THEME_OPTIONS],
        }
    raise ValueError(f"Unknown preference field: {field}")


def resolve_app_paths(app_arg: str | Path | None) -> tuple[Path, Path]:
    target = Path(app_arg).expanduser() if app_arg is not None else default_app_path()
    if target.is_file():
        executable = target
        if (
            target.parent.name == "MacOS"
            and target.parent.parent.name == "Contents"
            and target.parent.parent.parent.suffix == ".app"
        ):
            bundle = target.parent.parent.parent
        else:
            bundle = target
        return bundle, executable

    bundle = target
    executable = bundle / "Contents" / "MacOS" / "TokenPilot"
    return bundle, executable


def sanitize_custom_colors(raw_colors: Any) -> dict[str, dict[str, str]] | None:
    if not isinstance(raw_colors, dict):
        return None
    sanitized: dict[str, dict[str, str]] = {}
    for theme_key, pair in raw_colors.items():
        if theme_key not in SUPPORTED_THEMES or not isinstance(pair, dict):
            continue
        primary = pair.get("primaryHex")
        secondary = pair.get("secondaryHex")
        if isinstance(primary, str) and isinstance(secondary, str):
            sanitized[theme_key] = {
                "primaryHex": primary,
                "secondaryHex": secondary,
            }
    return sanitized


def _emit_json(payload: dict[str, Any], stream: Any) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    stream.flush()


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="configure_app.py",
        description="Inspect or configure TokenPilot installation appearance preferences.",
    )
    parser.add_argument(
        "--app",
        default=None,
        help="Path to built TokenPilot.app bundle (defaults to dist/TokenPilot.app).",
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Print the inferred installation preference proposal JSON without prompting, writing, or launching.",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Explicit interface language override (en or zh-Hans).",
    )
    parser.add_argument(
        "--theme",
        default=None,
        help="Explicit theme override (cupertino, graphite, instrument, frosted, liquid, vibrant).",
    )
    parser.add_argument(
        "--launch",
        action="store_true",
        help="Launch TokenPilot.app via 'open -a' after preferences are configured.",
    )
    return parser


def _parse_json_object(raw_stdout: str) -> dict[str, Any] | None:
    text = (raw_stdout or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _normalize_proposal(
    raw_proposal: dict[str, Any],
    explicit_language: str | None,
    explicit_theme: str | None,
) -> dict[str, Any]:
    raw_sources = raw_proposal.get("sources")
    sources_dict = raw_sources if isinstance(raw_sources, dict) else {}

    language = raw_proposal.get("language")
    language_source = raw_proposal.get("language_source") or sources_dict.get("language") or (
        "saved" if language else "unresolved"
    )
    if explicit_language is not None:
        language = explicit_language
        language_source = "explicit"

    theme = raw_proposal.get("theme")
    theme_source = raw_proposal.get("theme_source") or sources_dict.get("theme") or (
        "saved" if theme else "unresolved"
    )
    if explicit_theme is not None:
        theme = explicit_theme
        theme_source = "explicit"

    if language is not None and language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language in native proposal: {language!r}")
    if theme is not None and theme not in SUPPORTED_THEMES:
        raise ValueError(f"Unsupported theme in native proposal: {theme!r}")

    if language is None:
        language_source = "unresolved"
    if theme is None:
        theme_source = "unresolved"

    unresolved_fields: list[str] = []
    if language is None:
        unresolved_fields.append("language")
    if theme is None:
        unresolved_fields.append("theme")

    raw_questions = raw_proposal.get("questions")
    questions_by_field: dict[str, dict[str, Any]] = {}
    if isinstance(raw_questions, list):
        for item in raw_questions:
            if isinstance(item, dict) and item.get("field") in ("language", "theme"):
                questions_by_field[item["field"]] = item

    questions: list[dict[str, Any]] = []
    for field in unresolved_fields:
        questions.append(questions_by_field.get(field) or default_question_for_field(field))

    needs_input = bool(unresolved_fields)
    return {
        "status": "needs_input" if needs_input else "ready",
        "ok": not needs_input,
        "needs_input": needs_input,
        "language": language,
        "language_source": language_source,
        "theme": theme,
        "theme_source": theme_source,
        "sources": {
            "language": language_source,
            "theme": theme_source,
        },
        "unresolved_fields": unresolved_fields,
        "questions": questions,
        "bundle_id": BUNDLE_ID,
        "storage_key": STORAGE_KEY,
    }


def _prompt_for_missing_field(
    field: str,
    input_fn: Callable[[str], str],
    err_stream: Any,
) -> str:
    if field == "language":
        prompt_header = (
            "Interface language could not be inferred automatically.\n"
            "  [1] en      - English\n"
            "  [2] zh-Hans - 简体中文\n"
        )
        err_stream.write(prompt_header)
        err_stream.flush()
        while True:
            answer = input_fn("Select language (en / zh-Hans): ")
            parsed = parse_interactive_language(answer)
            if parsed is not None:
                return parsed
            err_stream.write("Invalid language choice. Please enter 'en' (1) or 'zh-Hans' (2).\n")
            err_stream.flush()

    if field == "theme":
        lines = ["Appearance theme could not be inferred automatically:"]
        for idx, item in enumerate(THEME_OPTIONS, start=1):
            lines.append(f"  [{idx}] {item['value']:<10} - {item['label']} ({item['summary']})")
        err_stream.write("\n".join(lines) + "\n")
        err_stream.flush()
        while True:
            answer = input_fn(
                "Select theme (cupertino / graphite / instrument / frosted / liquid / vibrant): "
            )
            parsed = parse_interactive_theme(answer)
            if parsed is not None:
                return parsed
            err_stream.write(
                "Invalid theme choice. Please enter one of: cupertino, graphite, instrument, frosted, liquid, vibrant (or 1-6).\n"
            )
            err_stream.flush()

    raise ValueError(f"Unsupported interactive field: {field}")


def _main(
    argv: Sequence[str] | None = None,
    *,
    run_process: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    input_fn: Callable[[str], str] | None = None,
    is_tty: bool | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> int:
    out_stream = stdout if stdout is not None else sys.stdout
    err_stream = stderr if stderr is not None else sys.stderr
    runner = run_process if run_process is not None else subprocess.run
    reader = input_fn if input_fn is not None else input
    tty_available = (
        is_tty
        if is_tty is not None
        else bool(getattr(sys.stdin, "isatty", lambda: False)())
    )

    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except ValueError as exc:
        _emit_json(
            {
                "status": "error",
                "ok": False,
                "needs_input": False,
                "wrote_preferences": False,
                "launched": False,
                "bundle_id": BUNDLE_ID,
                "storage_key": STORAGE_KEY,
                "error": str(exc),
            },
            out_stream,
        )
        return 2

    explicit_language: str | None = None
    if args.language is not None:
        explicit_language = normalize_explicit_language(args.language)
        if explicit_language is None:
            _emit_json(
                {
                    "status": "error",
                    "ok": False,
                    "needs_input": False,
                    "wrote_preferences": False,
                    "launched": False,
                    "bundle_id": BUNDLE_ID,
                    "storage_key": STORAGE_KEY,
                    "error": f"Invalid value for --language: {args.language!r}. Allowed values: en, zh-Hans.",
                },
                out_stream,
            )
            return 2

    explicit_theme: str | None = None
    if args.theme is not None:
        explicit_theme = normalize_explicit_theme(args.theme)
        if explicit_theme is None:
            _emit_json(
                {
                    "status": "error",
                    "ok": False,
                    "needs_input": False,
                    "wrote_preferences": False,
                    "launched": False,
                    "bundle_id": BUNDLE_ID,
                    "storage_key": STORAGE_KEY,
                    "error": (
                        f"Invalid value for --theme: {args.theme!r}. "
                        "Allowed values: cupertino, graphite, instrument, frosted, liquid, vibrant."
                    ),
                },
                out_stream,
            )
            return 2

    bundle_path, executable_path = resolve_app_paths(args.app)
    if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
        message = (
            f"Built TokenPilot app executable not found at '{executable_path}'. "
            "Build the app first by running './app/Build.command'."
        )
        err_stream.write(message + "\n")
        err_stream.flush()
        _emit_json(
            {
                "status": "error",
                "ok": False,
                "needs_input": False,
                "wrote_preferences": False,
                "launched": False,
                "bundle_id": BUNDLE_ID,
                "storage_key": STORAGE_KEY,
                "error": message,
            },
            out_stream,
        )
        return 2

    inspect_cmd: list[str] = [str(executable_path), "--inspect-install-preferences"]
    if explicit_language is not None:
        inspect_cmd.extend(["--language", explicit_language])
    if explicit_theme is not None:
        inspect_cmd.extend(["--theme", explicit_theme])

    inspect_proc = runner(
        inspect_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    raw_inspect = _parse_json_object(inspect_proc.stdout)
    if raw_inspect is None:
        detail = (inspect_proc.stderr or inspect_proc.stdout or "").strip()
        message = "Native preference inspection did not return valid JSON."
        if detail:
            message = f"{message} ({detail})"
        _emit_json(
            {
                "status": "error",
                "ok": False,
                "needs_input": False,
                "wrote_preferences": False,
                "launched": False,
                "bundle_id": BUNDLE_ID,
                "storage_key": STORAGE_KEY,
                "error": message,
            },
            out_stream,
        )
        return inspect_proc.returncode if inspect_proc.returncode not in (0, None) else 2

    if inspect_proc.returncode != 0 or raw_inspect.get("status") not in ("ready", "needs_input"):
        is_needs_input_proposal = (
            inspect_proc.returncode == 2
            and raw_inspect.get("status") == "needs_input"
            and not raw_inspect.get("error")
        )
        if not is_needs_input_proposal:
            error_msg = raw_inspect.get("error") or "Native preference inspection failed."
            _emit_json(
                {
                    "status": "error",
                    "ok": False,
                    "needs_input": False,
                    "wrote_preferences": False,
                    "launched": False,
                    "bundle_id": BUNDLE_ID,
                    "storage_key": STORAGE_KEY,
                    "error": str(error_msg),
                },
                out_stream,
            )
            return inspect_proc.returncode if inspect_proc.returncode != 0 else 2

    try:
        proposal = _normalize_proposal(raw_inspect, explicit_language, explicit_theme)
    except ValueError as exc:
        _emit_json(
            {
                "status": "error",
                "ok": False,
                "needs_input": False,
                "wrote_preferences": False,
                "launched": False,
                "bundle_id": BUNDLE_ID,
                "storage_key": STORAGE_KEY,
                "error": str(exc),
            },
            out_stream,
        )
        return 2

    if args.inspect:
        _emit_json(proposal, out_stream)
        return 2 if proposal["needs_input"] else 0

    resolved_language: str | None = proposal["language"]
    resolved_theme: str | None = proposal["theme"]
    language_source: str = proposal["language_source"]
    theme_source: str = proposal["theme_source"]

    if proposal["needs_input"]:
        if not tty_available:
            _emit_json(proposal, out_stream)
            return 2

        try:
            for field in proposal["unresolved_fields"]:
                choice = _prompt_for_missing_field(field, reader, err_stream)
                if field == "language":
                    resolved_language = choice
                    language_source = "explicit"
                elif field == "theme":
                    resolved_theme = choice
                    theme_source = "explicit"
        except (EOFError, KeyboardInterrupt):
            cancel_msg = "Interactive preference selection cancelled; no preferences were written."
            err_stream.write("\n" + cancel_msg + "\n")
            err_stream.flush()
            _emit_json(
                {
                    "status": "cancelled",
                    "ok": False,
                    "needs_input": True,
                    "wrote_preferences": False,
                    "launched": False,
                    "bundle_id": BUNDLE_ID,
                    "storage_key": STORAGE_KEY,
                    "error": cancel_msg,
                },
                out_stream,
            )
            return 2

    if resolved_language not in SUPPORTED_LANGUAGES or resolved_theme not in SUPPORTED_THEMES:
        _emit_json(
            {
                "status": "needs_input",
                "ok": False,
                "needs_input": True,
                "wrote_preferences": False,
                "launched": False,
                "bundle_id": BUNDLE_ID,
                "storage_key": STORAGE_KEY,
                "error": "Installation preferences remain unresolved.",
            },
            out_stream,
        )
        return 2

    configure_cmd: list[str] = [
        str(executable_path),
        "--configure-install-preferences",
        "--language",
        resolved_language,
        "--theme",
        resolved_theme,
    ]
    configure_proc = runner(
        configure_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    raw_configure = _parse_json_object(configure_proc.stdout)
    if (
        configure_proc.returncode != 0
        or raw_configure is None
        or raw_configure.get("status") != "configured"
        or raw_configure.get("ok") is not True
        or raw_configure.get("language") != resolved_language
        or raw_configure.get("theme") != resolved_theme
    ):
        error_msg = (
            (raw_configure or {}).get("error")
            or (configure_proc.stderr or configure_proc.stdout or "").strip()
            or "Native preference configuration failed."
        )
        _emit_json(
            {
                "status": "error",
                "ok": False,
                "needs_input": bool((raw_configure or {}).get("needs_input", False)),
                "wrote_preferences": False,
                "launched": False,
                "bundle_id": BUNDLE_ID,
                "storage_key": STORAGE_KEY,
                "error": str(error_msg),
            },
            out_stream,
        )
        return configure_proc.returncode if configure_proc.returncode not in (0, None) else 2

    safe_output: dict[str, Any] = {
        "status": "configured",
        "ok": True,
        "needs_input": False,
        "language": resolved_language,
        "language_source": language_source,
        "theme": resolved_theme,
        "theme_source": theme_source,
        "sources": {
            "language": language_source,
            "theme": theme_source,
        },
        "unresolved_fields": [],
        "questions": [],
        "wrote_preferences": bool(raw_configure.get("wrote_preferences", True)),
        "bundle_id": BUNDLE_ID,
        "storage_key": STORAGE_KEY,
        "launched": False,
    }
    custom_colors = sanitize_custom_colors(raw_configure.get("custom_colors"))
    if custom_colors is not None:
        safe_output["custom_colors"] = custom_colors

    if args.launch:
        if run_process is None and sys.platform.startswith("linux"):
            launch_cmd = [str(executable_path), "--daemon"]
        else:
            launch_cmd = ["open", "-a", str(bundle_path)]
        launch_proc = runner(
            launch_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if launch_proc.returncode != 0:
            detail = (launch_proc.stderr or launch_proc.stdout or "").strip()
            msg = f"Failed to launch '{bundle_path}' via open -a."
            if detail:
                msg = f"{msg} ({detail})"
            _emit_json(
                {
                    "status": "error",
                    "ok": False,
                    "needs_input": False,
                    "wrote_preferences": safe_output["wrote_preferences"],
                    "launched": False,
                    "bundle_id": BUNDLE_ID,
                    "storage_key": STORAGE_KEY,
                    "error": msg,
                },
                out_stream,
            )
            return launch_proc.returncode if launch_proc.returncode != 0 else 1
        safe_output["launched"] = True

    _emit_json(safe_output, out_stream)
    return 0


def main(argv=None, *, run_process=None, input_fn=None, is_tty=None, stdout=None, stderr=None):
    """Bound subprocess waits and return structured failures instead of tracebacks."""
    stage = "inspection"
    delegate = run_process if run_process is not None else subprocess.run

    def bounded_run(command, **kwargs):
        nonlocal stage
        stage = ("configuration" if "--configure-install-preferences" in command
                 else "launch" if (command[:2] == ["open", "-a"] or "--daemon" in command) else "inspection")
        kwargs.setdefault("timeout", 20)
        return delegate(command, **kwargs)

    try:
        return _main(argv, run_process=bounded_run, input_fn=input_fn, is_tty=is_tty,
                     stdout=stdout, stderr=stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        message = f"Native preference {stage} could not complete ({type(exc).__name__})."
        if isinstance(exc, subprocess.TimeoutExpired):
            message += " Rebuild the app if it predates the installation-preference commands."
        if stage != "inspection":
            message += " Inspect preferences before retrying; their write state is unknown."
        _emit_json({"status": "error", "ok": False, "needs_input": False,
                    "wrote_preferences": False if stage == "inspection" else None,
                    "launched": False, "bundle_id": BUNDLE_ID, "storage_key": STORAGE_KEY,
                    "error": message}, stdout if stdout is not None else sys.stdout)
        return 2


if __name__ == "__main__":
    sys.exit(main())
