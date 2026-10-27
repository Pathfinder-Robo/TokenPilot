#!/usr/bin/env python3
"""Linux & Cross-Platform Native App Runtime, Preferences Engine, and Live UI Server for TokenPilot.
TokenPilot 的 Linux 与跨平台原生应用运行时、偏好推断引擎及实时界面服务。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable
import urllib.parse
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from datetime import timezone as _tz

    def ZoneInfo(_name: str):  # type: ignore[misc]
        return _tz.utc

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import bridge  # noqa: E402

BUNDLE_ID = "local.routingfloat.viewer"
STORAGE_KEY = "TokenPilot.AppearanceSettings.v2"
DEFAULT_PORT = 43123

SUPPORTED_LANGUAGES: tuple[str, ...] = ("en", "zh-Hans")
SUPPORTED_THEMES: tuple[str, ...] = (
    "cupertino",
    "graphite",
    "instrument",
    "frosted",
    "liquid",
    "vibrant",
)

THEME_ALIASES: dict[str, str] = {
    "cupertino": "cupertino",
    "a": "cupertino",
    "light": "cupertino",
    "pearl": "cupertino",
    "graphite": "graphite",
    "quiet-graphite": "graphite",
    "quiet_graphite": "graphite",
    "b": "graphite",
    "instrument": "instrument",
    "d": "instrument",
    "mono": "instrument",
    "monochrome": "instrument",
    "frosted": "frosted",
    "frosted-glass": "frosted",
    "frosted_glass": "frosted",
    "apple-frosted-glass": "frosted",
    "liquid": "liquid",
    "liquid-glass": "liquid",
    "liquid_glass": "liquid",
    "apple-liquid-glass": "liquid",
    "vibrant": "vibrant",
    "colorful": "vibrant",
}

DEFAULT_THEME = "graphite"
DEFAULT_LANGUAGE = "en"

DEFAULT_COLOR_PAIRS: dict[str, dict[str, str]] = {
    "cupertino": {"primaryHex": "#007AFF", "secondaryHex": "#30B0C7"},
    "graphite": {"primaryHex": "#8E8CD8", "secondaryHex": "#636182"},
    "instrument": {"primaryHex": "#EDEDED", "secondaryHex": "#6E6E76"},
    "frosted": {"primaryHex": "#3898FF", "secondaryHex": "#4FD1C5"},
    "liquid": {"primaryHex": "#0068E6", "secondaryHex": "#6C63FF"},
    "vibrant": {"primaryHex": "#22D3EE", "secondaryHex": "#F472B6"},
}

THEME_SPECS: dict[str, dict[str, Any]] = {
    "cupertino": {
        "isDark": False,
        "supportsCustomColors": True,
        "usesGlass": False,
        "cornerRadius": 16,
        "surfaceHex": "#FAFAFC",
        "insetHex": "#EFEFF4",
        "borderHex": "#DEDEE5",
        "trackHex": "#D6D6DF",
        "primaryTextHex": "#242428",
        "secondaryTextHex": "#595963",
        "enName": "Cupertino",
        "zhName": "Cupertino 浅色",
        "enSummary": "Light pearl surface and classic blue actions",
        "zhSummary": "浅色珠光面板与经典蓝青交互",
    },
    "graphite": {
        "isDark": True,
        "supportsCustomColors": True,
        "usesGlass": False,
        "cornerRadius": 12,
        "surfaceHex": "#242427",
        "insetHex": "#2E2E33",
        "borderHex": "#39393F",
        "trackHex": "#45454D",
        "primaryTextHex": "#EDEDF0",
        "secondaryTextHex": "#B7B7C0",
        "enName": "Quiet Graphite",
        "zhName": "静谧石墨",
        "enSummary": "Warm graphite ledger with calm lilac accents",
        "zhSummary": "温润石墨账本布局与柔和丁香紫",
    },
    "instrument": {
        "isDark": True,
        "supportsCustomColors": False,
        "usesGlass": False,
        "cornerRadius": 8,
        "surfaceHex": "#111113",
        "insetHex": "#1E1E21",
        "borderHex": "#333337",
        "trackHex": "#414144",
        "primaryTextHex": "#EDEDED",
        "secondaryTextHex": "#B3B3B8",
        "enName": "Instrument",
        "zhName": "精密仪表",
        "enSummary": "High-contrast monochrome grid & mono counters",
        "zhSummary": "高对比单色开放网格与等宽计数",
    },
    "frosted": {
        "isDark": True,
        "supportsCustomColors": True,
        "usesGlass": True,
        "cornerRadius": 16,
        "surfaceHex": "#1D222B",
        "insetHex": "#262C37",
        "borderHex": "#465062",
        "trackHex": "#3B4454",
        "primaryTextHex": "#F2F5FA",
        "secondaryTextHex": "#B4BECC",
        "enName": "Frosted Glass",
        "zhName": "磨砂玻璃",
        "enSummary": "Translucent macOS HUD blur with cool highlights",
        "zhSummary": "macOS 原生磨砂半透明材质与冷色高亮",
    },
    "liquid": {
        "isDark": False,
        "supportsCustomColors": True,
        "usesGlass": True,
        "cornerRadius": 18,
        "surfaceHex": "#EEF3FA",
        "insetHex": "#E2E9F4",
        "borderHex": "#C5D1E3",
        "trackHex": "#C9D4E5",
        "primaryTextHex": "#151A23",
        "secondaryTextHex": "#495364",
        "enName": "Liquid Glass",
        "zhName": "液态玻璃",
        "enSummary": "Refractive Liquid Glass optic with specular rim",
        "zhSummary": "原生液态玻璃折射质感与高光边缘",
    },
    "vibrant": {
        "isDark": True,
        "supportsCustomColors": True,
        "usesGlass": False,
        "cornerRadius": 16,
        "surfaceHex": "#1E223A",
        "insetHex": "#272C48",
        "borderHex": "#424B74",
        "trackHex": "#3B4368",
        "primaryTextHex": "#F5F7FF",
        "secondaryTextHex": "#C0C7E3",
        "enName": "Vibrant",
        "zhName": "炫彩活力",
        "enSummary": "Deep indigo-teal gradient with luminous accents",
        "zhSummary": "深邃靛青渐变面板与鲜明活力强调色",
    },
}


# MARK: - RGB & WCAG Contrast Math


def normalize_hex(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) != 6:
        return None
    for ch in s:
        if ch not in "0123456789ABCDEFabcdef":
            return None
    return "#" + s.upper()


def hex_to_rgb(hex_str: str) -> tuple[float, float, float]:
    norm = normalize_hex(hex_str)
    if norm is None:
        return (0.5, 0.5, 0.5)
    val = int(norm[1:], 16)
    return (((val >> 16) & 0xFF) / 255.0, ((val >> 8) & 0xFF) / 255.0, (val & 0xFF) / 255.0)


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r = min(max(int(round(rgb[0] * 255.0)), 0), 255)
    g = min(max(int(round(rgb[1] * 255.0)), 0), 255)
    b = min(max(int(round(rgb[2] * 255.0)), 0), 255)
    return f"#{r:02X}{g:02X}{b:02X}"


def mix_rgb(a: tuple[float, float, float], b: tuple[float, float, float], fraction: float) -> tuple[float, float, float]:
    t = min(max(fraction, 0.0), 1.0)
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )


def _channel_luminance(c: float) -> float:
    v = min(max(c, 0.0), 1.0)
    return (v / 12.92) if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[float, float, float]) -> float:
    return 0.2126 * _channel_luminance(rgb[0]) + 0.7152 * _channel_luminance(rgb[1]) + 0.0722 * _channel_luminance(rgb[2])


def contrast_ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    l1 = max(relative_luminance(a), relative_luminance(b))
    l2 = min(relative_luminance(a), relative_luminance(b))
    return (l1 + 0.05) / (l2 + 0.05)


def min_contrast_ratio(candidate: tuple[float, float, float], backgrounds: list[tuple[float, float, float]]) -> float:
    if not backgrounds:
        return 21.0
    return min(contrast_ratio(candidate, bg) for bg in backgrounds)


def readable_accent_text(
    accent: tuple[float, float, float],
    backgrounds: list[tuple[float, float, float]],
    is_dark_surface: bool,
    minimum_contrast: float = 4.5,
) -> tuple[float, float, float]:
    safety_target = max(minimum_contrast, 4.55)
    if min_contrast_ratio(accent, backgrounds) >= safety_target:
        return accent
    target_pole = (1.0, 1.0, 1.0) if is_dark_surface else (0.0, 0.0, 0.0)
    low = 0.0
    high = 1.0
    best = target_pole
    for _ in range(24):
        mid = (low + high) * 0.5
        candidate = mix_rgb(accent, target_pole, mid)
        if min_contrast_ratio(candidate, backgrounds) >= safety_target:
            best = candidate
            high = mid
        else:
            low = mid
    return best


def resolve_palette(theme: str, color_pair: dict[str, str], material_kind: str = "solid") -> dict[str, Any]:
    spec = THEME_SPECS.get(theme, THEME_SPECS[DEFAULT_THEME])
    fallback = DEFAULT_COLOR_PAIRS.get(theme, DEFAULT_COLOR_PAIRS[DEFAULT_THEME])
    effective_pair = color_pair if spec["supportsCustomColors"] else fallback
    raw_primary_hex = normalize_hex(effective_pair.get("primaryHex")) or fallback["primaryHex"]
    raw_secondary_hex = normalize_hex(effective_pair.get("secondaryHex")) or fallback["secondaryHex"]
    raw_primary = hex_to_rgb(raw_primary_hex)
    raw_secondary = hex_to_rgb(raw_secondary_hex)

    is_dark = bool(spec["isDark"])
    surface_rgb = hex_to_rgb(spec["surfaceHex"])
    inset_rgb = hex_to_rgb(spec["insetHex"])
    border_rgb = hex_to_rgb(spec["borderHex"])
    track_rgb = hex_to_rgb(spec["trackHex"])
    primary_text_rgb = hex_to_rgb(spec["primaryTextHex"])
    secondary_text_rgb = hex_to_rgb(spec["secondaryTextHex"])

    gradient_stops: list[tuple[float, float, float]] = []
    if theme == "vibrant":
        gradient_stops = [
            mix_rgb(hex_to_rgb("#181B34"), raw_primary, 0.10),
            mix_rgb(hex_to_rgb("#17253B"), raw_secondary, 0.10),
            mix_rgb(hex_to_rgb("#221A36"), raw_secondary, 0.12),
        ]

    check_surfaces = [surface_rgb, inset_rgb, *gradient_stops]
    readable_primary = readable_accent_text(raw_primary, check_surfaces, is_dark, 4.5)
    readable_secondary = readable_accent_text(raw_secondary, check_surfaces, is_dark, 4.5)

    return {
        "theme": theme,
        "isDark": is_dark,
        "materialKind": material_kind,
        "surfaceRGB": surface_rgb,
        "insetRGB": inset_rgb,
        "borderRGB": border_rgb,
        "trackRGB": track_rgb,
        "primaryTextRGB": primary_text_rgb,
        "secondaryTextRGB": secondary_text_rgb,
        "rawPrimaryRGB": raw_primary,
        "rawSecondaryRGB": raw_secondary,
        "readablePrimaryTextRGB": readable_primary,
        "readableSecondaryTextRGB": readable_secondary,
        "gradientStops": gradient_stops,
    }


# MARK: - Theme & Language Resolution + Pure Inference Logic


def resolve_theme(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    return THEME_ALIASES.get(cleaned)


def resolve_language(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    if cleaned == "en" or cleaned.startswith("en-") or cleaned.startswith("en_") or cleaned == "english":
        return "en"
    if cleaned == "zh" or cleaned.startswith("zh-") or cleaned.startswith("zh_") or cleaned == "chinese" or "中文" in cleaned:
        return "zh-Hans"
    return None


def parse_explicit_language(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().lower()
    if cleaned == "en":
        return "en"
    if cleaned == "zh-hans":
        return "zh-Hans"
    return None


def parse_explicit_theme(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().lower()
    return cleaned if cleaned in SUPPORTED_THEMES else None


def parse_supported_language_tag(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    base_tag = trimmed.split(".", 1)[0].split("@", 1)[0]
    if base_tag in ("C", "POSIX"):
        return "en"
    normalized = base_tag.replace("_", "-").lower()
    parts = normalized.split("-")
    if not parts or not parts[0] or "" in parts:
        return None
    primary = parts[0]
    if primary == "en":
        return "en"
    if primary == "zh":
        if len(parts) == 1:
            return "zh-Hans"
        if "hant" in parts:
            return None
        if len(parts) > 1 and parts[1] == "hans":
            return "zh-Hans"
        if any(region in parts for region in ("tw", "hk", "mo")):
            return None
        second = parts[1]
        if second in ("hans", "cn", "sg"):
            return "zh-Hans"
        return None
    return None


def parse_saved_language(raw: str | None) -> str | None:
    exact = parse_explicit_language(raw)
    if exact is not None:
        return exact
    return parse_supported_language_tag(raw)


def parse_saved_theme(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if cleaned.lower() in SUPPORTED_THEMES:
        return cleaned.lower()
    return resolve_theme(cleaned)


def infer_system_language(preferred_languages: list[str]) -> str | None:
    for tag in preferred_languages:
        supported = parse_supported_language_tag(tag)
        if supported is not None:
            return supported
    return None


def infer_system_theme(is_dark: bool | None) -> str | None:
    if is_dark is None:
        return None
    return "graphite" if is_dark else "cupertino"


def language_question() -> dict[str, Any]:
    return {
        "field": "language",
        "flag": "--language",
        "prompt": "Choose interface language / 请选择界面语言 (en, zh-Hans)",
        "allowed_values": list(SUPPORTED_LANGUAGES),
        "options": [
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
        ],
    }


def theme_question() -> dict[str, Any]:
    options = []
    for theme_id in SUPPORTED_THEMES:
        spec = THEME_SPECS[theme_id]
        options.append(
            {
                "value": theme_id,
                "label": f"{spec['enName']} / {spec['zhName']}",
                "summary": spec["enSummary"],
            }
        )
    return {
        "field": "theme",
        "flag": "--theme",
        "prompt": "Choose appearance theme / 请选择外观主题 (cupertino, graphite, instrument, frosted, liquid, vibrant)",
        "allowed_values": list(SUPPORTED_THEMES),
        "options": options,
    }


def infer_installation_preferences(
    *,
    saved_language: str | None = None,
    saved_theme: str | None = None,
    preferred_languages: list[str] | None = None,
    is_dark: bool | None = None,
    explicit_language: str | None = None,
    explicit_theme: str | None = None,
) -> dict[str, Any]:
    langs = preferred_languages if preferred_languages is not None else []

    if explicit_language is not None:
        resolved_language: str | None = explicit_language
        language_source = "explicit"
    elif (saved_lang := parse_saved_language(saved_language)) is not None:
        resolved_language = saved_lang
        language_source = "saved"
    elif (sys_lang := infer_system_language(langs)) is not None:
        resolved_language = sys_lang
        language_source = "system"
    else:
        resolved_language = None
        language_source = "unresolved"

    if explicit_theme is not None:
        resolved_theme: str | None = explicit_theme
        theme_source = "explicit"
    elif (saved_thm := parse_saved_theme(saved_theme)) is not None:
        resolved_theme = saved_thm
        theme_source = "saved"
    elif (sys_thm := infer_system_theme(is_dark)) is not None:
        resolved_theme = sys_thm
        theme_source = "system"
    else:
        resolved_theme = None
        theme_source = "unresolved"

    unresolved_fields: list[str] = []
    questions: list[dict[str, Any]] = []
    if resolved_language is None:
        unresolved_fields.append("language")
        questions.append(language_question())
    if resolved_theme is None:
        unresolved_fields.append("theme")
        questions.append(theme_question())

    needs_input = bool(unresolved_fields)
    return {
        "status": "needs_input" if needs_input else "ready",
        "ok": not needs_input,
        "needs_input": needs_input,
        "language": resolved_language,
        "language_source": language_source,
        "theme": resolved_theme,
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


# MARK: - Linux System Environment Detection & Preferences Persistence


def detect_linux_preferred_languages(env: dict[str, str] | None = None) -> list[str]:
    environ = env if env is not None else os.environ
    override = environ.get("TOKENPILOT_PREFERRED_LANGUAGES")
    if override is not None:
        return [item.strip() for item in override.split(",") if item.strip()]

    candidates: list[str] = []
    language_list = environ.get("LANGUAGE", "")
    if language_list:
        for item in language_list.split(":"):
            if item.strip():
                candidates.append(item.strip())
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = environ.get(key, "").strip()
        if val:
            candidates.append(val)
    if not candidates:
        candidates.append("en-US")
    return candidates


def detect_linux_system_is_dark(env: dict[str, str] | None = None) -> bool | None:
    environ = env if env is not None else os.environ
    override = environ.get("TOKENPILOT_SYSTEM_APPEARANCE")
    if override is not None:
        cleaned = override.strip().lower()
        if cleaned in ("dark", "darkaqua", "true", "1"):
            return True
        if cleaned in ("light", "aqua", "false", "0"):
            return False
        if cleaned in ("none", "unknown", "unresolved", ""):
            return None

    gtk_theme = environ.get("GTK_THEME", "").strip().lower()
    if gtk_theme:
        if "dark" in gtk_theme:
            return True
        if "light" in gtk_theme:
            return False

    if shutil.which("gsettings"):
        try:
            proc = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if proc.returncode == 0:
                out = proc.stdout.strip().strip("'").lower()
                if "prefer-dark" in out:
                    return True
                if "prefer-light" in out:
                    return False
                if "default" in out:
                    return True
        except (OSError, subprocess.SubprocessError):
            pass

    return True


def default_preferences_path(env: dict[str, str] | None = None) -> Path:
    environ = env if env is not None else os.environ
    override = environ.get("TOKENPILOT_PREFERENCES_FILE")
    if override:
        return Path(override).expanduser().resolve()
    codex_home = Path(environ.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser().resolve()
    return codex_home / "argon" / "viewer" / "appearance-settings.json"


class AppearanceStore:
    """Persistent appearance store for Linux matching macOS AppearanceStore semantics."""

    def __init__(self, prefs_path: Path | None = None, persists_changes: bool = True) -> None:
        self.prefs_path = prefs_path
        self.persists_changes = persists_changes
        self.theme: str = DEFAULT_THEME
        self.language: str = DEFAULT_LANGUAGE
        self.custom_colors: dict[str, dict[str, str]] = {
            t: dict(DEFAULT_COLOR_PAIRS[t]) for t in SUPPORTED_THEMES
        }
        self._load()

    def _load(self) -> None:
        if self.prefs_path is None or not self.prefs_path.is_file():
            return
        try:
            raw = json.loads(self.prefs_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        valid_theme = resolve_theme(raw.get("theme"))
        if valid_theme:
            self.theme = valid_theme
        valid_lang = resolve_language(raw.get("language"))
        if valid_lang:
            self.language = valid_lang
        raw_colors = raw.get("customColors")
        if isinstance(raw_colors, dict):
            for raw_key, pair in raw_colors.items():
                key_theme = resolve_theme(raw_key)
                if not key_theme or not isinstance(pair, dict):
                    continue
                if not THEME_SPECS[key_theme]["supportsCustomColors"]:
                    self.custom_colors[key_theme] = dict(DEFAULT_COLOR_PAIRS[key_theme])
                    continue
                default_pair = DEFAULT_COLOR_PAIRS[key_theme]
                p = normalize_hex(pair.get("primaryHex")) or default_pair["primaryHex"]
                s = normalize_hex(pair.get("secondaryHex")) or default_pair["secondaryHex"]
                self.custom_colors[key_theme] = {"primaryHex": p, "secondaryHex": s}

    def color_pair(self, target_theme: str | None = None) -> dict[str, str]:
        t = target_theme or self.theme
        if not THEME_SPECS.get(t, THEME_SPECS[DEFAULT_THEME])["supportsCustomColors"]:
            return dict(DEFAULT_COLOR_PAIRS[t])
        return dict(self.custom_colors.get(t, DEFAULT_COLOR_PAIRS[t]))

    def set_primary_hex(self, raw_hex: str, target_theme: str | None = None) -> bool:
        t = target_theme or self.theme
        if not THEME_SPECS.get(t, THEME_SPECS[DEFAULT_THEME])["supportsCustomColors"]:
            return False
        valid_hex = normalize_hex(raw_hex)
        if valid_hex is None:
            return False
        pair = self.color_pair(t)
        if pair["primaryHex"] == valid_hex:
            return True
        pair["primaryHex"] = valid_hex
        self.custom_colors[t] = pair
        self.persist_if_needed()
        return True

    def set_secondary_hex(self, raw_hex: str, target_theme: str | None = None) -> bool:
        t = target_theme or self.theme
        if not THEME_SPECS.get(t, THEME_SPECS[DEFAULT_THEME])["supportsCustomColors"]:
            return False
        valid_hex = normalize_hex(raw_hex)
        if valid_hex is None:
            return False
        pair = self.color_pair(t)
        if pair["secondaryHex"] == valid_hex:
            return True
        pair["secondaryHex"] = valid_hex
        self.custom_colors[t] = pair
        self.persist_if_needed()
        return True

    def reset_colors_for_current_theme(self) -> None:
        if not THEME_SPECS.get(self.theme, THEME_SPECS[DEFAULT_THEME])["supportsCustomColors"]:
            return
        self.custom_colors[self.theme] = dict(DEFAULT_COLOR_PAIRS[self.theme])
        self.persist_if_needed()

    def persist_if_needed(self) -> None:
        if not self.persists_changes or self.prefs_path is None:
            return
        payload = {
            "theme": self.theme,
            "language": self.language,
            "customColors": {t: self.color_pair(t) for t in SUPPORTED_THEMES},
        }
        bridge.atomic_json(self.prefs_path, payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme,
            "language": self.language,
            "customColors": {t: self.color_pair(t) for t in SUPPORTED_THEMES},
        }


def read_saved_appearance_snapshot(prefs_path: Path | None) -> dict[str, Any]:
    if prefs_path is None or not prefs_path.is_file():
        return {
            "language": None,
            "theme": None,
            "customColors": {},
            "hasCompleteCanonicalRecord": False,
        }
    try:
        decoded = json.loads(prefs_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {
            "language": None,
            "theme": None,
            "customColors": {},
            "hasCompleteCanonicalRecord": False,
        }
    if not isinstance(decoded, dict):
        return {
            "language": None,
            "theme": None,
            "customColors": {},
            "hasCompleteCanonicalRecord": False,
        }
    colors: dict[str, dict[str, str]] = {}
    raw_colors = decoded.get("customColors")
    if isinstance(raw_colors, dict):
        for raw_key, pair in raw_colors.items():
            key_theme = resolve_theme(raw_key)
            if not key_theme or not THEME_SPECS[key_theme]["supportsCustomColors"] or not isinstance(pair, dict):
                continue
            def_pair = DEFAULT_COLOR_PAIRS[key_theme]
            p = normalize_hex(pair.get("primaryHex")) or def_pair["primaryHex"]
            s = normalize_hex(pair.get("secondaryHex")) or def_pair["secondaryHex"]
            colors[key_theme] = {"primaryHex": p, "secondaryHex": s}

    raw_lang = decoded.get("language") if isinstance(decoded.get("language"), str) else None
    raw_theme = decoded.get("theme") if isinstance(decoded.get("theme"), str) else None
    valid_lang = parse_saved_language(raw_lang)
    valid_theme = parse_saved_theme(raw_theme)
    is_canonical = (
        valid_lang is not None
        and valid_theme is not None
        and raw_lang == valid_lang
        and raw_theme == valid_theme
        and isinstance(raw_colors, dict)
    )
    return {
        "language": raw_lang,
        "theme": raw_theme,
        "customColors": colors,
        "hasCompleteCanonicalRecord": is_canonical,
    }


def _parse_installation_command_arguments(arguments: list[str]) -> tuple[str, str | None, str | None, str | None]:
    inspect_flag = "--inspect-install-preferences"
    configure_flag = "--configure-install-preferences"

    touches_inspect = any(a == inspect_flag or a.startswith(inspect_flag + "=") for a in arguments)
    touches_configure = any(a == configure_flag or a.startswith(configure_flag + "=") for a in arguments)
    if not touches_inspect and not touches_configure:
        return ("not_applicable", None, None, None)

    tokens = list(arguments)
    if tokens and not tokens[0].startswith("-"):
        tokens.pop(0)

    mode: str | None = None
    explicit_language: str | None = None
    explicit_theme: str | None = None

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token == inspect_flag:
            if mode is not None and mode != "inspect":
                return ("invalid", None, None, f"Cannot combine {inspect_flag} and {configure_flag}.")
            mode = "inspect"
            idx += 1
        elif token == configure_flag:
            if mode is not None and mode != "configure":
                return ("invalid", None, None, f"Cannot combine {inspect_flag} and {configure_flag}.")
            mode = "configure"
            idx += 1
        elif token == "--language":
            if idx + 1 >= len(tokens) or tokens[idx + 1].startswith("-"):
                return ("invalid", None, None, "Missing value for --language. Allowed values: en, zh-Hans.")
            raw_val = tokens[idx + 1]
            parsed = parse_explicit_language(raw_val)
            if parsed is None:
                return ("invalid", None, None, f"Invalid value for --language: '{raw_val}'. Allowed values: en, zh-Hans.")
            if explicit_language is not None and explicit_language != parsed:
                return ("invalid", None, None, "Conflicting values provided for --language.")
            explicit_language = parsed
            idx += 2
        elif token == "--theme":
            if idx + 1 >= len(tokens) or tokens[idx + 1].startswith("-"):
                return (
                    "invalid",
                    None,
                    None,
                    "Missing value for --theme. Allowed values: cupertino, graphite, instrument, frosted, liquid, vibrant.",
                )
            raw_val = tokens[idx + 1]
            parsed = parse_explicit_theme(raw_val)
            if parsed is None:
                return (
                    "invalid",
                    None,
                    None,
                    f"Invalid value for --theme: '{raw_val}'. Allowed values: cupertino, graphite, instrument, frosted, liquid, vibrant.",
                )
            if explicit_theme is not None and explicit_theme != parsed:
                return ("invalid", None, None, "Conflicting values provided for --theme.")
            explicit_theme = parsed
            idx += 2
        elif token.startswith("--language="):
            raw_val = token[len("--language="):]
            parsed = parse_explicit_language(raw_val)
            if parsed is None:
                return ("invalid", None, None, f"Invalid value for --language: '{raw_val}'. Allowed values: en, zh-Hans.")
            if explicit_language is not None and explicit_language != parsed:
                return ("invalid", None, None, "Conflicting values provided for --language.")
            explicit_language = parsed
            idx += 1
        elif token.startswith("--theme="):
            raw_val = token[len("--theme="):]
            parsed = parse_explicit_theme(raw_val)
            if parsed is None:
                return (
                    "invalid",
                    None,
                    None,
                    f"Invalid value for --theme: '{raw_val}'. Allowed values: cupertino, graphite, instrument, frosted, liquid, vibrant.",
                )
            if explicit_theme is not None and explicit_theme != parsed:
                return ("invalid", None, None, "Conflicting values provided for --theme.")
            explicit_theme = parsed
            idx += 1
        else:
            return ("invalid", None, None, f"Unknown or unsupported argument: '{token}'.")

    if mode is None:
        return ("not_applicable", None, None, None)
    return ("valid", mode, explicit_language, explicit_theme)


def handle_installation_preference_command(
    arguments: list[str],
    *,
    appearance: AppearanceStore | None = None,
    prefs_path: Path | None = None,
    preferred_languages: list[str] | None = None,
    is_dark: bool | None = None,
    output: Callable[[str], None] = print,
) -> int | None:
    status, mode, explicit_lang, explicit_theme_or_err = _parse_installation_command_arguments(arguments)
    if status == "not_applicable":
        return None

    if status == "invalid":
        error_proposal = {
            "status": "error",
            "ok": False,
            "needs_input": False,
            "language": None,
            "language_source": "unresolved",
            "theme": None,
            "theme_source": "unresolved",
            "sources": {"language": "unresolved", "theme": "unresolved"},
            "unresolved_fields": [],
            "questions": [],
            "wrote_preferences": False,
            "bundle_id": BUNDLE_ID,
            "storage_key": STORAGE_KEY,
            "error": explicit_theme_or_err,
        }
        output(json.dumps(error_proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 2

    resolved_prefs_path = prefs_path if prefs_path is not None else (
        appearance.prefs_path if appearance is not None else default_preferences_path()
    )
    store = appearance if appearance is not None else AppearanceStore(prefs_path=resolved_prefs_path, persists_changes=True)
    langs = preferred_languages if preferred_languages is not None else detect_linux_preferred_languages()
    dark = is_dark if is_dark is not None else detect_linux_system_is_dark()

    saved_snapshot = read_saved_appearance_snapshot(resolved_prefs_path)
    proposal = infer_installation_preferences(
        saved_language=saved_snapshot["language"],
        saved_theme=saved_snapshot["theme"],
        preferred_languages=langs,
        is_dark=dark,
        explicit_language=explicit_lang,
        explicit_theme=explicit_theme_or_err,
    )

    if mode == "inspect":
        output(json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 0

    if (
        proposal["needs_input"]
        or proposal["language"] not in SUPPORTED_LANGUAGES
        or proposal["theme"] not in SUPPORTED_THEMES
    ):
        proposal["status"] = "needs_input"
        proposal["ok"] = False
        proposal["wrote_preferences"] = False
        proposal["error"] = "Unresolved installation preferences require explicit --language and/or --theme."
        output(json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 2

    resolved_language: str = proposal["language"]
    resolved_theme: str = proposal["theme"]
    saved_lang = parse_saved_language(saved_snapshot["language"])
    saved_theme = parse_saved_theme(saved_snapshot["theme"])
    already_persisted_and_matching = (
        saved_snapshot["hasCompleteCanonicalRecord"]
        and saved_lang == resolved_language
        and saved_theme == resolved_theme
        and store.language == resolved_language
        and store.theme == resolved_theme
    )

    if already_persisted_and_matching:
        proposal["wrote_preferences"] = False
    else:
        for theme_key, pair in saved_snapshot["customColors"].items():
            if THEME_SPECS.get(theme_key, {}).get("supportsCustomColors"):
                if store.color_pair(theme_key) == DEFAULT_COLOR_PAIRS[theme_key]:
                    store.set_primary_hex(pair["primaryHex"], theme_key)
                    store.set_secondary_hex(pair["secondaryHex"], theme_key)
        store.language = resolved_language
        store.theme = resolved_theme
        store.persist_if_needed()
        proposal["wrote_preferences"] = True

    proposal["status"] = "configured"
    proposal["ok"] = True
    proposal["needs_input"] = False
    proposal["custom_colors"] = {t: store.color_pair(t) for t in SUPPORTED_THEMES}
    output(json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


# MARK: - Built-In UI & Preference Self-Checks (--ui-checks)


def select_collapsed_quota(snapshot: dict[str, Any], language: str = "en") -> dict[str, Any]:
    limits = snapshot.get("limits") or {}
    five = limits.get("five_hour")
    weekly = limits.get("weekly")
    forecast = snapshot.get("forecast") or {}
    reset_comes_first = forecast.get("status") == "reset_before_projection"

    chosen_is_five: bool | None = None
    if five and weekly:
        if 100.0 - five["used_percent"] > 95.0:
            chosen_is_five = False
        elif forecast.get("display_window"):
            chosen_is_five = forecast["display_window"] == "five_hour"
        else:
            a = (forecast.get("five_hour") or {}).get("exhaustion_at")
            b = (forecast.get("weekly") or {}).get("exhaustion_at")
            if a is not None and b is not None:
                chosen_is_five = a <= b
            elif a is not None:
                chosen_is_five = True
            elif b is not None:
                chosen_is_five = False
            else:
                chosen_is_five = five["used_percent"] >= weekly["used_percent"]
    elif five:
        chosen_is_five = True
    elif weekly:
        chosen_is_five = False

    if chosen_is_five is None:
        return {
            "label": "配额" if language == "zh-Hans" else "Quota",
            "percentText": "—",
            "available": False,
        }

    limit = five if chosen_is_five else weekly
    remaining = min(max(100.0 - limit["used_percent"], 0.0), 100.0)
    percent_text = f"{int(round(remaining))}%"
    is_weekly = not chosen_is_five
    short_prefix = ("周" if is_weekly else "5小时") if language == "zh-Hans" else ("Wk" if is_weekly else "5h")
    base_title = ("周配额" if is_weekly else "5 小时") if language == "zh-Hans" else ("Weekly" if is_weekly else "5-hour")

    label = base_title
    if reset_comes_first:
        next_is_weekly = forecast.get("next_reset_window") == "weekly"
        next_prefix = ("周" if next_is_weekly else "5小时") if language == "zh-Hans" else ("Wk" if next_is_weekly else "5h")
        if language == "zh-Hans":
            label = f"{short_prefix}先重置" if next_prefix == short_prefix else f"{short_prefix} · {next_prefix}先重置"
        else:
            label = f"{short_prefix} reset first" if next_prefix == short_prefix else f"{short_prefix} · {next_prefix} reset first"

    return {
        "label": label,
        "percentText": percent_text,
        "available": True,
    }


def run_self_checks() -> bool:
    passed = 0
    failed = 0

    def check(condition: bool, label: str) -> None:
        nonlocal passed, failed
        if condition:
            passed += 1
        else:
            failed += 1
            sys.stderr.write(f"FAIL: {label}\n")

    # 1. Theme resolution & defaults
    check(len(SUPPORTED_THEMES) == 6, "Exactly 6 themes exist")
    check(DEFAULT_THEME == "graphite", "Default theme is graphite")
    check(resolve_theme("c") is None and resolve_theme("focus") is None, "Removed Focus/C theme does not resolve")
    check(
        resolve_theme("a") == "cupertino"
        and resolve_theme("b") == "graphite"
        and resolve_theme("d") == "instrument",
        "Theme aliases resolve",
    )

    # 2. Isolated persistence & per-theme color isolation
    with tempfile.TemporaryDirectory(prefix="tokenpilot-checks-") as tmp_dir:
        prefs_file = Path(tmp_dir) / "appearance.json"
        store1 = AppearanceStore(prefs_path=prefs_file, persists_changes=True)
        check(store1.theme == "graphite" and store1.language == "en", "Fresh store uses graphite + english")
        check(store1.set_primary_hex("#123456", "graphite"), "Graphite accepts valid 6-digit hex")
        check(not store1.set_primary_hex("#12345678", "graphite"), "Rejects 8-digit hex with alpha")
        store1.theme = "cupertino"
        check(store1.set_primary_hex("#FF3366", "cupertino"), "Cupertino accepts custom primary")
        store1.language = "zh-Hans"
        store1.persist_if_needed()

        store2 = AppearanceStore(prefs_path=prefs_file, persists_changes=True)
        check(store2.theme == "cupertino", "Persisted theme restored")
        check(store2.language == "zh-Hans", "Persisted language restored")
        check(store2.color_pair("graphite")["primaryHex"] == "#123456", "Graphite custom color survived theme switch")
        check(store2.color_pair("cupertino")["primaryHex"] == "#FF3366", "Cupertino custom color persisted")

        store2.reset_colors_for_current_theme()
        check(store2.color_pair("cupertino") == DEFAULT_COLOR_PAIRS["cupertino"], "Reset restored current theme default")
        check(store2.color_pair("graphite")["primaryHex"] == "#123456", "Reset did not wipe Graphite custom color")
        check(store2.language == "zh-Hans", "Reset did not alter language")

        store2.theme = "instrument"
        check(not THEME_SPECS["instrument"]["supportsCustomColors"], "Instrument disables custom colors")
        check(not store2.set_primary_hex("#FF0000"), "Instrument ignores set_primary_hex")

    # 3. Contrast verification across all 6 themes and extreme custom colors
    test_hexes = ("#000000", "#FFFFFF", "#FFFF00", "#0000FF", "#FF0055", "#22D3EE")
    for theme in SUPPORTED_THEMES:
        for hex_val in test_hexes:
            pair = {"primaryHex": hex_val, "secondaryHex": hex_val}
            pal = resolve_palette(theme, pair, "solid")
            surfaces = [pal["surfaceRGB"], pal["insetRGB"], *pal["gradientStops"]]
            check(min_contrast_ratio(pal["primaryTextRGB"], surfaces) >= 4.5, f"{theme} primaryText contrast >= 4.5")
            check(min_contrast_ratio(pal["secondaryTextRGB"], surfaces) >= 4.5, f"{theme} secondaryText contrast >= 4.5")
            check(
                min_contrast_ratio(pal["readablePrimaryTextRGB"], surfaces) >= 4.5,
                f"{theme} readablePrimary ({hex_val}) contrast >= 4.5",
            )
            check(
                min_contrast_ratio(pal["readableSecondaryTextRGB"], surfaces) >= 4.5,
                f"{theme} readableSecondary ({hex_val}) contrast >= 4.5",
            )

    # 4. Quota selection & Reset-first semantics
    test_snapshot = {
        "timestamp": "2026-10-24T15:33:00Z",
        "codex": {"total_tokens": 51_200_000, "cached_tokens": 50_100_000, "available": True},
        "gemini": {
            "total_tokens": 148_000,
            "cached_tokens": 51_800,
            "available": True,
            "status": "completed",
            "status_message": "Gemini worker completed",
        },
        "routing": {"gemini_percent": 70},
        "limits": {
            "five_hour": {"used_percent": 3.0, "resets_at": 1_792_860_000},
            "weekly": {"used_percent": 20.0, "resets_at": 1_793_100_000},
        },
        "warnings": [],
        "forecast": {
            "status": "reset_before_projection",
            "headline": "Next reset comes first",
            "detail": "5-hour resets first · weekly quota remains",
            "exhaustion_at": None,
            "tokens_per_minute": 145_000,
            "five_hour": {
                "status": "reset_before_projection",
                "message": "Next reset comes first",
                "exhaustion_at": 1_792_900_000,
            },
            "weekly": {
                "status": "reset_before_projection",
                "message": "Next reset comes first",
                "exhaustion_at": 1_793_200_000,
            },
            "display_window": "five_hour",
            "selection_reason": "5-hour has >95% remaining; showing weekly",
            "next_reset_window": "five_hour",
        },
        "nextreset": None,
    }
    sel_en = select_collapsed_quota(test_snapshot, "en")
    check(sel_en["percentText"] == "80%", "5-hour >95% remaining selects Weekly (80%)")
    check("Wk" in sel_en["label"] and "reset first" in sel_en["label"], "English reset-first collapsed label")
    sel_zh = select_collapsed_quota(test_snapshot, "zh-Hans")
    check("周" in sel_zh["label"] and "先重置" in sel_zh["label"], "Chinese reset-first collapsed label")

    print(f"TokenPilot UI checks: {passed} passed, {failed} failed")
    return failed == 0


# MARK: - Live Dashboard HTML UI & HTTP Server


LIVE_UI_SCRIPT = r"""
(function() {
  // 1. Custom Rich Hover Tooltip Card (macOS-style hover card on any widget element with [title])
  const hoverCard = document.createElement('div');
  hoverCard.id = 'tp-hover-card';
  hoverCard.setAttribute('role', 'tooltip');
  hoverCard.style.cssText = 'position:fixed;z-index:99999;pointer-events:none;max-width:320px;padding:10px 13px;border-radius:10px;background:rgba(24,26,32,0.96);color:#f2f5fa;border:1px solid rgba(255,255,255,0.16);box-shadow:0 12px 28px rgba(0,0,0,0.35);font-size:11.5px;line-height:1.45;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);opacity:0;transform:translateY(4px);transition:opacity 0.12s ease,transform 0.12s ease;display:none;white-space:pre-line;';
  document.body.appendChild(hoverCard);

  let activeHoverEl = null;
  document.addEventListener('mouseover', e => {
    const target = e.target.closest('[title], [data-tp-tooltip]');
    if (!target) return;
    if (target.hasAttribute('title')) {
      const raw = target.getAttribute('title');
      if (raw) {
        target.setAttribute('data-tp-tooltip', raw);
        target.removeAttribute('title');
      }
    }
    const tip = target.getAttribute('data-tp-tooltip');
    if (!tip) return;
    activeHoverEl = target;
    hoverCard.textContent = tip;
    hoverCard.style.display = 'block';
    requestAnimationFrame(() => {
      hoverCard.style.opacity = '1';
      hoverCard.style.transform = 'translateY(0)';
    });
  });

  document.addEventListener('mousemove', e => {
    if (!activeHoverEl || hoverCard.style.display === 'none') return;
    const pad = 14;
    let x = e.clientX + pad;
    let y = e.clientY + pad;
    const rect = hoverCard.getBoundingClientRect();
    if (x + rect.width > window.innerWidth - 12) x = Math.max(12, e.clientX - rect.width - pad);
    if (y + rect.height > window.innerHeight - 12) y = Math.max(12, e.clientY - rect.height - pad);
    hoverCard.style.left = x + 'px';
    hoverCard.style.top = y + 'px';
  });

  document.addEventListener('mouseout', e => {
    if (activeHoverEl && !activeHoverEl.contains(e.relatedTarget)) {
      activeHoverEl = null;
      hoverCard.style.opacity = '0';
      hoverCard.style.transform = 'translateY(4px)';
      setTimeout(() => {
        if (!activeHoverEl) hoverCard.style.display = 'none';
      }, 120);
    }
  });

  // 2. Floating Hover Card Overlay Mode ("Float Card Mode" vs "All 6 Themes Studio")
  let floatingDock = document.createElement('div');
  floatingDock.id = 'tp-floating-hover-dock';
  floatingDock.style.cssText = 'position:fixed;top:18px;right:22px;z-index:9990;filter:drop-shadow(0 16px 36px rgba(0,0,0,0.34));transition:transform 0.15s ease;';
  document.body.appendChild(floatingDock);

  let liveBanner = document.createElement('div');
  liveBanner.id = 'tokenpilot-live-bar';
  liveBanner.style.cssText = 'max-width:848px;margin:0 auto 20px;padding:12px 18px;border-radius:12px;background:#1e293b;color:#f8fafc;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;font-size:13px;box-shadow:0 4px 12px rgba(0,0,0,0.12);';
  liveBanner.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;">
      <span id="tp-live-dot" style="width:8px;height:8px;border-radius:50%;background:#28bc6d;display:inline-block;"></span>
      <strong id="tp-live-title">TokenPilot Live Linux Runtime</strong>
      <span id="tp-live-status" style="color:#94a3b8;font-size:12px;">Native GTK Floating Hover Card + Web Studio Active</span>
    </div>
    <div style="display:flex;align-items:center;gap:10px;font-size:12px;flex-wrap:wrap;">
      <span id="tp-live-summary" style="color:#cbd5e1;font-family:var(--mono);"></span>
      <button type="button" id="tp-toggle-float-btn" style="border:1px solid #38bdf8;background:#0284c7;color:#ffffff;border-radius:6px;padding:4px 10px;font-size:12px;font-weight:500;cursor:pointer;">Pin Floating Hover Card</button>
      <button type="button" id="tp-live-refresh-btn" style="border:1px solid #475569;background:#334155;color:#f8fafc;border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer;">Refresh Collector</button>
    </div>
  `;
  const headerEl = document.querySelector('.board-header');
  if (headerEl && headerEl.parentNode) {
    headerEl.parentNode.insertBefore(liveBanner, headerEl.nextSibling);
  }

  let floatPinned = false;
  let floatCollapsed = false;

  function renderFloatingHoverCard() {
    if (!floatPinned) {
      floatingDock.innerHTML = '';
      floatingDock.style.display = 'none';
      return;
    }
    floatingDock.style.display = 'block';
    const themeId = (typeof state !== 'undefined' && state.activeTheme) ? state.activeTheme : 'graphite';
    const selector = `.widget[data-theme="${themeId}"][data-mode="${floatCollapsed ? 'collapsed' : 'expanded'}"]`;
    const sourceWidget = document.querySelector(selector);
    if (!sourceWidget) return;
    const stageClone = sourceWidget.closest('.widget-stage').cloneNode(true);
    floatingDock.innerHTML = '';
    const dragBar = document.createElement('div');
    dragBar.style.cssText = 'display:flex;justify-content:space-between;align-items:center;padding:4px 10px;margin-bottom:4px;border-radius:8px;background:rgba(15,23,42,0.88);color:#e2e8f0;font-size:11px;cursor:move;user-select:none;';
    dragBar.innerHTML = `<span>⠿ Floating Hover Card (${themeId})</span><div style="display:flex;gap:6px;"><button type="button" id="tp-float-mode-btn" style="border:0;background:transparent;color:#93c5fd;cursor:pointer;font-size:11px;">${floatCollapsed ? 'Expand' : 'Collapse'}</button><button type="button" id="tp-float-close-btn" style="border:0;background:transparent;color:#fca5a5;cursor:pointer;font-size:11px;">✕</button></div>`;
    floatingDock.appendChild(dragBar);
    floatingDock.appendChild(stageClone);

    let dragging = false, startX = 0, startY = 0, origLeft = 0, origTop = 0;
    dragBar.addEventListener('mousedown', ev => {
      if (ev.target.tagName === 'BUTTON') return;
      dragging = true;
      const r = floatingDock.getBoundingClientRect();
      floatingDock.style.right = 'auto';
      floatingDock.style.left = r.left + 'px';
      floatingDock.style.top = r.top + 'px';
      startX = ev.clientX;
      startY = ev.clientY;
      origLeft = r.left;
      origTop = r.top;
    });
    window.addEventListener('mousemove', ev => {
      if (!dragging) return;
      floatingDock.style.left = Math.max(8, origLeft + (ev.clientX - startX)) + 'px';
      floatingDock.style.top = Math.max(8, origTop + (ev.clientY - startY)) + 'px';
    });
    window.addEventListener('mouseup', () => { dragging = false; });

    const modeBtn = floatingDock.querySelector('#tp-float-mode-btn');
    if (modeBtn) modeBtn.addEventListener('click', () => { floatCollapsed = !floatCollapsed; renderFloatingHoverCard(); });
    const closeBtn = floatingDock.querySelector('#tp-float-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', () => { floatPinned = false; renderFloatingHoverCard(); });
  }

  const toggleFloatBtn = document.getElementById('tp-toggle-float-btn');
  if (toggleFloatBtn) {
    toggleFloatBtn.addEventListener('click', () => {
      floatPinned = !floatPinned;
      renderFloatingHoverCard();
    });
  }

  let latestSnapshot = null;

  function compactNum(val) {
    const n = Number(val) || 0;
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
    return String(Math.round(n));
  }

  function applyLiveSnapshot(snap) {
    if (!snap || typeof snap !== 'object') return;
    latestSnapshot = snap;
    const isZh = (typeof state !== 'undefined' && state.language === 'zh-Hans');
    const subEl = document.getElementById('page-subtitle');
    if (subEl) {
      subEl.textContent = isZh
        ? `Linux 实时运行面板 · 本地目录 ${snap.day || ''} (${snap.timezone || 'UTC'})`
        : `Live Linux App · Local Telemetry (${snap.day || ''} · ${snap.timezone || 'UTC'})`;
    }

    const titleEl = document.getElementById('tp-live-title');
    const statusEl = document.getElementById('tp-live-status');
    const summaryEl = document.getElementById('tp-live-summary');
    const refreshBtn = document.getElementById('tp-live-refresh-btn');
    if (titleEl) titleEl.textContent = isZh ? 'TokenPilot Linux 实时服务' : 'TokenPilot Live Linux Runtime';
    if (refreshBtn) refreshBtn.textContent = isZh ? '立即刷新' : 'Refresh Now';

    const codexAvail = snap.codex && snap.codex.available;
    const geminiAvail = snap.gemini && snap.gemini.available;
    const codexTotal = codexAvail ? compactNum(snap.codex.total_tokens) : '0';
    const geminiTotal = geminiAvail ? compactNum(snap.gemini.total_tokens) : '0';
    const pct = (snap.routing && typeof snap.routing.gemini_percent === 'number') ? snap.routing.gemini_percent : 75;

    if (statusEl) {
      const limErr = snap.limits && snap.limits.error;
      statusEl.textContent = limErr
        ? (isZh ? `本地收集器已连接 · ${limErr}` : `Local collector active · ${limErr}`)
        : (isZh ? '本地收集器已连接 · 实时同步中' : 'Local collector active · live sync');
    }
    if (summaryEl) {
      summaryEl.textContent = `Codex: ${codexTotal} | Gemini: ${geminiTotal} | Routing: ${pct}%`;
    }

    if (typeof state !== 'undefined' && state.routingPercent !== pct) {
      state.routingPercent = pct;
      document.querySelectorAll('.concept-col').forEach(col => {
        if (typeof syncRouting === 'function') syncRouting(col, pct);
      });
    }
  }

  async function fetchLiveState() {
    try {
      const res = await fetch('/api/state', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (data.appearance && typeof state !== 'undefined') {
        let needsRender = false;
        if (data.appearance.language && data.appearance.language !== state.language) {
          state.language = data.appearance.language;
          needsRender = true;
        }
        if (data.appearance.theme && data.appearance.theme !== state.activeTheme) {
          state.activeTheme = data.appearance.theme;
          needsRender = true;
        }
        if (data.appearance.customColors && typeof data.appearance.customColors === 'object') {
          Object.keys(data.appearance.customColors).forEach(k => {
            const pair = data.appearance.customColors[k];
            if (pair && state.customColors && state.customColors[k]) {
              const p = pair.primaryHex || pair.primary;
              const s = pair.secondaryHex || pair.secondary;
              if (p && s && (state.customColors[k].primary !== p || state.customColors[k].secondary !== s)) {
                state.customColors[k] = { primary: p, secondary: s };
                needsRender = true;
              }
            }
          });
        }
        if (needsRender && typeof renderCustomizer === 'function' && typeof renderBoard === 'function') {
          renderCustomizer();
          renderBoard();
          if (typeof renderFloatingHoverCard === 'function') renderFloatingHoverCard();
        }
      }
      if (data.snapshot) {
        applyLiveSnapshot(data.snapshot);
        if (typeof renderFloatingHoverCard === 'function') renderFloatingHoverCard();
      }
    } catch (_) {}
  }

  async function postJSON(url, payload) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res.ok) {
        const data = await res.json();
        if (data.snapshot) applyLiveSnapshot(data.snapshot);
      }
    } catch (_) {}
  }

  document.addEventListener('change', e => {
    if (e.target && (e.target.matches('.routing-slider') || e.target.matches('[data-role="preset"]'))) {
      const val = Math.max(0, Math.min(100, Math.round(Number(e.target.value) || 0)));
      postJSON('/api/routing', { gemini_percent: val });
    }
    if (e.target && (e.target.id === 'primary-color-input' || e.target.id === 'secondary-color-input')) {
      if (typeof state !== 'undefined') {
        postJSON('/api/appearance', {
          theme: state.activeTheme,
          language: state.language,
          customColors: state.customColors
        });
      }
    }
  });

  document.addEventListener('click', e => {
    const langBtn = e.target.closest('[data-lang-btn]');
    const tileBtn = e.target.closest('[data-select-theme]');
    const resetBtn = e.target.closest('#reset-theme-colors');
    if (langBtn || tileBtn || resetBtn) {
      setTimeout(() => {
        if (typeof state !== 'undefined') {
          postJSON('/api/appearance', {
            theme: state.activeTheme,
            language: state.language,
            customColors: state.customColors
          });
          if (latestSnapshot) applyLiveSnapshot(latestSnapshot);
        }
      }, 30);
    }
    if (e.target && (e.target.id === 'tp-live-refresh-btn' || e.target.closest('[data-action="refresh"]'))) {
      postJSON('/api/refresh', {});
    }
  });

  fetchLiveState();
  setInterval(fetchLiveState, 5000);
})();
"""


def load_dashboard_html() -> str:
    candidates = [
        APP_DIR.parent / "examples" / "design-preview.html",
        APP_DIR.parent.parent / "examples" / "design-preview.html",
        Path(__file__).resolve().parent.parent / "examples" / "design-preview.html",
    ]
    for candidate in candidates:
        if candidate.is_file():
            raw = candidate.read_text(encoding="utf-8")
            return raw.replace("</body>", f"<script>{LIVE_UI_SCRIPT}</script>\n</body>")
    return "<!DOCTYPE html><html><body><h1>TokenPilot</h1></body></html>"


def load_runtime_config() -> dict[str, Any]:
    runtime_path = APP_DIR / "runtime.json"
    if runtime_path.is_file():
        try:
            data = json.loads(runtime_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {
        "python": sys.executable,
        "codex_binary": shutil.which("codex") or "",
        "codex_home": os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"),
        "bridge_path": None,
    }


def serve_ui(
    port: int = DEFAULT_PORT,
    host: str = "127.0.0.1",
    offline: bool = False,
    open_browser: bool = False,
    start_collapsed: bool = False,
    enable_gtk_panel: bool = True,
) -> int:
    runtime = load_runtime_config()
    codex_home = Path(os.environ.get("CODEX_HOME") or runtime.get("codex_home") or str(Path.home() / ".codex")).expanduser()
    codex_bin = os.environ.get("TOKENPILOT_CODEX_BIN") or runtime.get("codex_binary") or shutil.which("codex")
    tz_name = os.environ.get("TZ") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    collector = bridge.Bridge(codex_home, codex_bin, tz, offline=offline)
    prefs_path = default_preferences_path()
    appearance = AppearanceStore(prefs_path=prefs_path, persists_changes=True)
    html_content = load_dashboard_html()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
            body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path in ("/", "/index.html"):
                body = html_content.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/state":
                with lock:
                    appearance._load()
                    snap = collector.snapshot(refresh=not offline)
                    app_dict = appearance.to_dict()
                self._send_json({"ok": True, "snapshot": snap, "appearance": app_dict})
                return
            if parsed.path == "/api/health":
                self._send_json({"ok": True, "status": "running", "platform": sys.platform})
                return
            self._send_json({"ok": False, "error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(min(length, 65536)) if length > 0 else b"{}"
            try:
                body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"ok": False, "error": "Invalid JSON"}, status=HTTPStatus.BAD_REQUEST)
                return

            if parsed.path == "/api/routing":
                pct = body.get("gemini_percent")
                if type(pct) is not int or not (0 <= pct <= 100):
                    self._send_json(
                        {"ok": False, "error": "Routing preference must be an integer from 0 to 100"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    with lock:
                        bridge.set_routing(codex_home, pct)
                        snap = collector.snapshot(refresh=False)
                    self._send_json({"ok": True, "snapshot": snap})
                except Exception as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return

            if parsed.path == "/api/appearance":
                with lock:
                    if isinstance(body.get("theme"), str):
                        resolved_t = resolve_theme(body["theme"])
                        if resolved_t:
                            appearance.theme = resolved_t
                    if isinstance(body.get("language"), str):
                        resolved_l = resolve_language(body["language"])
                        if resolved_l:
                            appearance.language = resolved_l
                    raw_colors = body.get("customColors")
                    if isinstance(raw_colors, dict):
                        for theme_k, pair in raw_colors.items():
                            rk = resolve_theme(theme_k)
                            if rk and isinstance(pair, dict):
                                p = pair.get("primaryHex") or pair.get("primary")
                                s = pair.get("secondaryHex") or pair.get("secondary")
                                if isinstance(p, str):
                                    appearance.set_primary_hex(p, rk)
                                if isinstance(s, str):
                                    appearance.set_secondary_hex(s, rk)
                    appearance.persist_if_needed()
                    app_dict = appearance.to_dict()
                self._send_json({"ok": True, "appearance": app_dict})
                return

            if parsed.path == "/api/refresh":
                with lock:
                    if not offline:
                        collector.refresh_limits(force=True)
                        collector.refresh_nextreset(force=True)
                    snap = collector.snapshot(refresh=False)
                self._send_json({"ok": True, "snapshot": snap})
                return

            self._send_json({"ok": False, "error": "Unknown endpoint"}, status=HTTPStatus.NOT_FOUND)

    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"TokenPilot Linux server listening on {url}", flush=True)

    # Record UI state for parity with macOS recordPanelState
    try:
        ui_state_path = codex_home / "argon" / "viewer" / "ui-state.json"
        bridge.atomic_json(
            ui_state_path,
            {
                "action": "show",
                "visible": True,
                "collapsed": False,
                "width": 360,
                "height": 540,
                "theme": appearance.theme,
                "language": appearance.language,
                "url": url,
                "platform": sys.platform,
            },
        )
    except Exception:
        pass

    if enable_gtk_panel:
        try:
            import linux_panel

            gtk_started = linux_panel.launch_gtk_floating_panel(
                collector=collector,
                appearance=appearance,
                codex_home=codex_home,
                web_url=url,
                start_collapsed=start_collapsed,
            )
            if gtk_started:
                print("TokenPilot native GTK3 floating hover card panel & tray indicator started.", flush=True)
        except Exception as exc:
            sys.stderr.write(f"Note: GTK floating panel unavailable ({exc}); using web UI.\n")

    if open_browser:
        _launch_linux_window(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _launch_linux_window(url: str) -> None:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    for browser in ("google-chrome", "chromium", "chromium-browser"):
        binary = shutil.which(browser)
        if binary:
            try:
                subprocess.Popen(
                    [binary, f"--app={url}", "--new-window", "--window-size=920,760"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return
            except OSError:
                pass
    xdg = shutil.which("xdg-open")
    if xdg:
        try:
            subprocess.Popen(
                [xdg, url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv if argv is None else argv)

    # 1. Check installation preference commands first (--inspect-install-preferences / --configure-install-preferences)
    pref_code = handle_installation_preference_command(args_list)
    if pref_code is not None:
        return pref_code

    # 2. Check --ui-checks
    if "--ui-checks" in args_list[1:]:
        ok = run_self_checks()
        return 0 if ok else 1

    # 3. Parse standard runtime flags
    parser = argparse.ArgumentParser(prog="TokenPilot", description="TokenPilot Linux & Cross-Platform App")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TOKENPILOT_PORT", DEFAULT_PORT)))
    parser.add_argument("--host", default=os.environ.get("TOKENPILOT_HOST", "127.0.0.1"))
    parser.add_argument("--offline", action="store_true", help="Disable live quota and NextReset network refreshes")
    parser.add_argument("--collapsed", action="store_true", help="Start in collapsed mode")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a desktop browser window automatically")
    parser.add_argument("--no-panel", action="store_true", help="Disable the native GTK floating hover card panel")
    parser.add_argument("--daemon", action="store_true", help="Spawn the TokenPilot server in the background and exit")
    parsed, _ = parser.parse_known_args(args_list[1:])

    if parsed.daemon:
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--port",
            str(parsed.port),
            "--host",
            parsed.host,
        ]
        if parsed.offline:
            cmd.append("--offline")
        if parsed.collapsed:
            cmd.append("--collapsed")
        if parsed.no_browser:
            cmd.append("--no-browser")
        if parsed.no_panel:
            cmd.append("--no-panel")
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return 0

    return serve_ui(
        port=parsed.port,
        host=parsed.host,
        offline=parsed.offline,
        open_browser=not parsed.no_browser,
        start_collapsed=parsed.collapsed,
        enable_gtk_panel=not parsed.no_panel,
    )


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.exit(main())
