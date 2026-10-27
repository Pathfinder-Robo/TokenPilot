#!/usr/bin/env python3
"""Native GTK3 Floating Hover Card Panel, System Tray Indicator, and Customizer Window for TokenPilot on Linux.
TokenPilot 在 Linux 上的原生 GTK3 悬浮卡片面板、系统托盘图标与外观自定义窗口。
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import bridge  # noqa: E402
import linux_app  # noqa: E402


def compact_tokens(value: float | int | None) -> str:
    val = float(value or 0.0)
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val / 1_000:.1f}K".replace(".0K", "K")
    return f"{val:.0f}"


class Localizer:
    def __init__(self, language: str = "en") -> None:
        self.language = "zh-Hans" if language == "zh-Hans" else "en"

    def L(self, en: str, zh: str) -> str:
        return zh if self.language == "zh-Hans" else en

    def window_title(self, is_weekly: bool) -> str:
        return self.L("Weekly", "周配额") if is_weekly else self.L("5-hour", "5 小时")

    def window_short_prefix(self, is_weekly: bool) -> str:
        return self.L("Wk", "周") if is_weekly else self.L("5h", "5小时")

    def format_epoch_hm(self, epoch_val: float | int | None) -> str:
        if not epoch_val or epoch_val <= 0:
            return ""
        dt = datetime.fromtimestamp(float(epoch_val))
        now = datetime.now()
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        return dt.strftime("%a %H:%M")

    def format_epoch_week(self, epoch_val: float | int | None) -> str:
        if not epoch_val or epoch_val <= 0:
            return ""
        dt = datetime.fromtimestamp(float(epoch_val))
        return dt.strftime("%a %H:%M")

    def format_reset_label(self, resets_at: float | int | None, is_weekly: bool, include_verb: bool = True) -> str:
        if not resets_at or resets_at <= 0:
            return ""
        formatted = self.format_epoch_week(resets_at) if is_weekly else self.format_epoch_hm(resets_at)
        if not formatted:
            return ""
        if not include_verb:
            return formatted
        return self.L(f"resets {formatted}", f"{formatted} 重置")


def build_widget_tooltip_and_strings(snapshot: dict[str, Any] | None, language: str = "en") -> dict[str, Any]:
    loc = Localizer(language)
    snap = snapshot or {}
    codex = snap.get("codex") or {}
    gemini = snap.get("gemini") or {}
    limits = snap.get("limits") or {}
    forecast = snap.get("forecast") or {}
    nextreset = snap.get("nextreset") or {}
    routing = snap.get("routing") or {"gemini_percent": 75}

    codex_avail = bool(codex.get("available"))
    gemini_avail = bool(gemini.get("available"))
    codex_val = compact_tokens(codex.get("total_tokens")) if codex_avail else "—"
    gemini_val = compact_tokens(gemini.get("total_tokens")) if gemini_avail else "—"
    codex_cache = (
        loc.L(f"cached {compact_tokens(codex.get('cached_tokens'))}", f"缓存 {compact_tokens(codex.get('cached_tokens'))}")
        if codex_avail
        else loc.L("No local session logs today", "今日暂无本地会话记录")
    )
    gemini_cache = (
        loc.L(f"cached {compact_tokens(gemini.get('cached_tokens'))}", f"缓存 {compact_tokens(gemini.get('cached_tokens'))}")
        if gemini_avail
        else loc.L("cached —", "缓存 —")
    )

    gemini_status_raw = str(gemini.get("status_message") or gemini.get("status") or "")
    gemini_alert = (not gemini_avail) or ("error" in gemini_status_raw.lower()) or ("fail" in gemini_status_raw.lower())
    if language == "zh-Hans":
        low = gemini_status_raw.lower()
        if "no tracked" in low or "idle" in low:
            gemini_status_text = "今日暂无已追踪的 Gemini 运行"
        elif "completed" in low:
            gemini_status_text = "Gemini 工作进程已完成"
        elif "running" in low:
            gemini_status_text = "Gemini 工作进程运行中"
        elif "fail" in low or "error" in low:
            gemini_status_text = "Gemini 请求失败 · 暂无更新用量"
        else:
            gemini_status_text = gemini_status_raw or "今日暂无已追踪的 Gemini 运行"
    else:
        gemini_status_text = gemini_status_raw or "No tracked Gemini runs today"

    # Quotas (5-hour and Weekly)
    five = limits.get("five_hour")
    weekly = limits.get("weekly")

    def quota_info(limit_obj: dict[str, Any] | None, f_win: dict[str, Any] | None, is_weekly: bool) -> dict[str, Any]:
        title = loc.window_title(is_weekly)
        avail = isinstance(limit_obj, dict) and isinstance(limit_obj.get("used_percent"), (int, float))
        used = float(limit_obj["used_percent"]) if avail else 0.0
        rem = min(max(100.0 - used, 0.0), 100.0) if avail else 0.0
        frac = rem / 100.0 if avail else 0.0
        left_str = loc.L(f"{int(round(rem))}% left", f"剩余 {int(round(rem))}%") if avail else loc.L("Unavailable", "不可用")
        reset_verb = loc.format_reset_label(limit_obj.get("resets_at") if avail else None, is_weekly, include_verb=True)
        reset_short = loc.format_reset_label(limit_obj.get("resets_at") if avail else None, is_weekly, include_verb=False)

        tip_parts = []
        if avail:
            tip_parts.append(
                loc.L(
                    f"{title}: {int(round(rem))}% remaining ({int(round(used))}% used)",
                    f"{title}：剩余 {int(round(rem))}%（已用 {int(round(used))}%）",
                )
            )
            if reset_short:
                tip_parts.append(loc.L(f"Resets {reset_short}", f"{reset_short} 重置"))
        else:
            err = limits.get("error") or loc.L("Quota data unavailable", "配额数据不可用")
            tip_parts.append(f"{title} · {err}")
        if f_win and f_win.get("message"):
            tip_parts.append(str(f_win["message"]))
        return {
            "title": title,
            "available": avail,
            "remaining": rem,
            "fraction": frac,
            "left_str": left_str,
            "reset_verb": reset_verb,
            "reset_short": reset_short,
            "tooltip": " · ".join(tip_parts),
        }

    five_info = quota_info(five, forecast.get("five_hour"), is_weekly=False)
    weekly_info = quota_info(weekly, forecast.get("weekly"), is_weekly=True)

    # Forecast row
    f_status = str(forecast.get("status") or "unavailable")
    reset_first = f_status == "reset_before_projection"
    f_headline_raw = str(forecast.get("headline") or loc.L("Waiting for quota data", "等待配额数据"))
    f_detail_raw = str(forecast.get("detail") or "")
    if language == "zh-Hans":
        if reset_first:
            f_primary = "下次重置早于耗尽"
        elif f_status == "learning":
            f_primary = "正在学习 15 分钟配额趋势"
        elif f_status == "unavailable":
            f_primary = "等待配额数据"
        elif f_status == "steady":
            f_primary = "近期无明显配额消耗"
        elif f_status == "projected":
            f_primary = "预计配额耗尽时间"
        else:
            f_primary = "等待最新配额数据…"

        if "5-hour resets first" in f_detail_raw.lower():
            f_secondary = "5 小时窗口先重置 · 周配额继续计入"
        elif "weekly resets first" in f_detail_raw.lower():
            f_secondary = "周配额窗口先重置 · 5 小时配额继续计入"
        elif "15m trend" in f_detail_raw.lower():
            f_secondary = "近 15 分钟趋势 · 含缓存 · 假设速率不变"
        else:
            f_secondary = f_detail_raw
    else:
        f_primary = f_headline_raw
        f_secondary = f_detail_raw

    tpm = float(forecast.get("tokens_per_minute") or 0.0)
    burn_badge = loc.L(f"{compact_tokens(tpm)} tok/min", f"{compact_tokens(tpm)} tok/分钟") if tpm > 0 else ""

    # NextReset card
    nr_p24 = nextreset.get("probability_24h")
    nr_p48 = nextreset.get("probability_48h")
    nr_p72 = nextreset.get("probability_72h")

    def fmt_prob(v: Any) -> str:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0.0 <= float(v) <= 1.0:
            return f"{int(round(float(v) * 100.0))}%"
        return "—"

    nr_announced = str(nextreset.get("announcement_state") or "").lower() == "announced"
    nr_avail = any(
        nextreset.get(k) is not None
        for k in ("probability_24h", "probability_48h", "probability_72h", "expected_by", "source_as_of", "latest_report_at")
    ) or nr_announced

    if not nr_avail:
        nr_status = loc.L("Forecast unavailable", "预测暂不可用")
        nr_badge = loc.L("Unavailable", "不可用")
    else:
        nr_status = (
            loc.L("Reset announced · awaiting rollout", "已宣布重置 · 等待推送")
            if nr_announced
            else loc.L("Community reset forecast", "社区重置预测")
        )
        if nextreset.get("stale") and nextreset.get("source_delayed"):
            nr_badge = loc.L("Stale · Source delayed", "数据过期 · 源端延迟")
        elif nextreset.get("stale"):
            nr_badge = loc.L("Stale snapshot", "快照已过期")
        elif nextreset.get("source_delayed"):
            nr_badge = loc.L("Source delayed", "源端延迟")
        else:
            nr_badge = loc.L("Announced", "已宣布") if nr_announced else loc.L("Live", "实时")

    nr_caption = loc.L("Estimated chance in next 24h", "预计未来 24 小时重置概率")
    nr_tooltip = "\n".join(
        [
            f"NextReset · {nr_status} · {nr_caption}: {fmt_prob(nr_p24)}",
            loc.L(
                "Public community forecast (nextreset.ai), not your personal quota.",
                "公共社区预测（nextreset.ai），并非您的个人账户配额。",
            ),
        ]
    )

    collapsed_sel = linux_app.select_collapsed_quota(snap, language=language)

    return {
        "codex_avail": codex_avail,
        "gemini_avail": gemini_avail,
        "codex_val": codex_val,
        "gemini_val": gemini_val,
        "codex_cache": codex_cache,
        "gemini_cache": gemini_cache,
        "gemini_status_text": gemini_status_text,
        "gemini_alert": gemini_alert,
        "five_info": five_info,
        "weekly_info": weekly_info,
        "f_primary": f_primary,
        "f_secondary": f_secondary,
        "burn_badge": burn_badge,
        "reset_first": reset_first,
        "nr_prob24": fmt_prob(nr_p24) if nr_avail else "—",
        "nr_prob48": fmt_prob(nr_p48) if nr_avail else "—",
        "nr_prob72": fmt_prob(nr_p72) if nr_avail else "—",
        "nr_status": nr_status,
        "nr_badge": nr_badge,
        "nr_caption": nr_caption,
        "nr_avail": nr_avail,
        "nr_announced": nr_announced,
        "nr_signal_url": nextreset.get("signal_url"),
        "nr_tooltip": nr_tooltip,
        "collapsed_sel": collapsed_sel,
        "routing_pct": int(routing.get("gemini_percent", 75)),
    }


def launch_gtk_floating_panel(
    collector: bridge.Bridge,
    appearance: linux_app.AppearanceStore,
    codex_home: Path,
    web_url: str | None = None,
    start_collapsed: bool = False,
) -> bool:
    """Starts the native GTK3 borderless floating hover card panel and system tray icon on Linux."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib, Gtk, Pango
    except Exception:
        return False

    try:
        import gi

        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except Exception:
        AppIndicator = None

    class TokenPilotGTKApp:
        def __init__(self) -> None:
            self.collector = collector
            self.appearance = appearance
            self.codex_home = codex_home
            self.web_url = web_url
            self.collapsed = start_collapsed
            self.snapshot: dict[str, Any] = self.collector.snapshot(refresh=not self.collector.offline)
            self.updating_slider = False
            self.customizer_win: Gtk.Window | None = None
            self.css_provider = Gtk.CssProvider()
            screen = Gdk.Screen.get_default()
            if screen is not None:
                Gtk.StyleContext.add_provider_for_screen(
                    screen,
                    self.css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )

            self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            self.window.set_title("TokenPilot")
            self.window.set_decorated(False)
            self.window.set_keep_above(True)
            self.window.set_skip_taskbar_hint(True)
            self.window.set_skip_pager_hint(True)
            self.window.set_AcceptFocus(True) if hasattr(self.window, "set_AcceptFocus") else None
            self.window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            self.window.set_resizable(False)

            if screen is not None:
                visual = screen.get_rgba_visual()
                if visual is not None and screen.is_composited():
                    self.window.set_visual(visual)
            self.window.set_app_paintable(True)

            self.root_event_box = Gtk.EventBox()
            self.root_event_box.set_visible_window(False)
            self.root_event_box.add_events(
                Gdk.EventMask.BUTTON_PRESS_MASK
                | Gdk.EventMask.ENTER_NOTIFY_MASK
                | Gdk.EventMask.LEAVE_NOTIFY_MASK
            )
            self.root_event_box.connect("button-press-event", self._on_window_button_press)
            self.window.add(self.root_event_box)

            self.card_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.card_frame.get_style_context().add_class("tp-panel")
            self.root_event_box.add(self.card_frame)

            self._setup_tray_indicator()
            self._apply_theme_css()
            self._rebuild_panel_content()
            self._position_floating_panel()
            self.window.show_all()
            self._record_ui_state("show")

            GLib.timeout_add(3000, self._periodic_refresh)

        def _setup_tray_indicator(self) -> None:
            icon_path = str((APP_DIR / "TokenPilot.png").resolve())
            self.tray_menu = Gtk.Menu()

            self.menu_show_item = Gtk.MenuItem(label="Show / Hide TokenPilot")
            self.menu_show_item.connect("activate", lambda *_: self.toggle_visibility())
            self.tray_menu.append(self.menu_show_item)

            self.menu_collapse_item = Gtk.MenuItem(label="Collapse / Expand Hover Card")
            self.menu_collapse_item.connect("activate", lambda *_: self.toggle_collapsed())
            self.tray_menu.append(self.menu_collapse_item)

            self.menu_customize_item = Gtk.MenuItem(label="Customize Appearance…")
            self.menu_customize_item.connect("activate", lambda *_: self.show_customizer())
            self.tray_menu.append(self.menu_customize_item)

            if self.web_url:
                self.menu_web_item = Gtk.MenuItem(label=f"Open Full Theme Board ({self.web_url})")
                self.menu_web_item.connect("activate", lambda *_: self._open_url(self.web_url))
                self.tray_menu.append(self.menu_web_item)

            self.tray_menu.append(Gtk.SeparatorMenuItem())

            self.menu_quit_item = Gtk.MenuItem(label="Quit TokenPilot")
            self.menu_quit_item.connect("activate", lambda *_: Gtk.main_quit())
            self.tray_menu.append(self.menu_quit_item)
            self.tray_menu.show_all()

            if AppIndicator is not None:
                try:
                    self.indicator = AppIndicator.Indicator.new(
                        "tokenpilot-linux-indicator",
                        icon_path if Path(icon_path).is_file() else "preferences-system",
                        AppIndicator.IndicatorCategory.APPLICATION_STATUS,
                    )
                    self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
                    self.indicator.set_title("TokenPilot")
                    self.indicator.set_menu(self.tray_menu)
                    return
                except Exception:
                    pass

            try:
                self.status_icon = Gtk.StatusIcon()
                if Path(icon_path).is_file():
                    self.status_icon.set_from_file(icon_path)
                else:
                    self.status_icon.set_from_icon_name("preferences-system")
                self.status_icon.set_tooltip_text("TokenPilot — Click to toggle floating hover card")
                self.status_icon.connect("activate", lambda *_: self.toggle_visibility())
                self.status_icon.connect(
                    "popup-menu",
                    lambda icon, button, activate_time: self.tray_menu.popup(
                        None, None, None, None, button, activate_time
                    ),
                )
            except Exception:
                pass

        def _position_floating_panel(self) -> None:
            screen = Gdk.Screen.get_default()
            if screen is not None:
                width = 300 if self.collapsed else 360
                sw = screen.get_width()
                x = max(20, sw - width - 28)
                y = 44
                self.window.move(x, y)

        def _apply_theme_css(self) -> None:
            theme = self.appearance.theme
            pair = self.appearance.color_pair(theme)
            pal = linux_app.resolve_palette(theme, pair, "solid")
            spec = linux_app.THEME_SPECS.get(theme, linux_app.THEME_SPECS["graphite"])

            surf_hex = linux_app.rgb_to_hex(pal["surfaceRGB"])
            inset_hex = linux_app.rgb_to_hex(pal["insetRGB"])
            border_hex = linux_app.rgb_to_hex(pal["borderRGB"])
            track_hex = linux_app.rgb_to_hex(pal["trackRGB"])
            fg_hex = linux_app.rgb_to_hex(pal["primaryTextRGB"])
            sec_hex = linux_app.rgb_to_hex(pal["secondaryTextRGB"])
            pri_hex = linux_app.rgb_to_hex(pal["rawPrimaryRGB"])
            sec_acc_hex = linux_app.rgb_to_hex(pal["rawSecondaryRGB"])
            read_pri_hex = linux_app.rgb_to_hex(pal["readablePrimaryTextRGB"])
            green_hex = "#28BC6D" if spec["isDark"] else "#178248"
            warn_hex = "#FFB042" if spec["isDark"] else "#B85600"
            radius = int(spec.get("cornerRadius", 12))

            if theme == "vibrant" and pal["gradientStops"]:
                s1 = linux_app.rgb_to_hex(pal["gradientStops"][0])
                s2 = linux_app.rgb_to_hex(pal["gradientStops"][1])
                s3 = linux_app.rgb_to_hex(pal["gradientStops"][2])
                bg_rule = f"background-image: linear-gradient(135deg, {s1}, {s2}, {s3});"
            elif theme == "frosted":
                bg_rule = "background-color: rgba(29, 34, 43, 0.94);"
            elif theme == "liquid":
                bg_rule = "background-color: rgba(238, 243, 250, 0.95);"
            else:
                bg_rule = f"background-color: {surf_hex};"

            css = f"""
            window {{
                background-color: transparent;
            }}
            .tp-panel {{
                {bg_rule}
                color: {fg_hex};
                border: 1px solid {border_hex};
                border-radius: {radius}px;
                padding: {"10px 12px" if self.collapsed else "12px 18px"};
            }}
            .tp-title {{
                color: {fg_hex};
                font-weight: 700;
                font-size: 13px;
            }}
            .tp-primary {{
                color: {fg_hex};
                font-size: 12px;
                font-weight: 500;
            }}
            .tp-secondary {{
                color: {sec_hex};
                font-size: 11px;
            }}
            .tp-accent {{
                color: {read_pri_hex};
                font-size: 11px;
                font-weight: 600;
            }}
            .tp-green {{
                color: {green_hex};
                font-size: 11px;
                font-weight: 600;
            }}
            .tp-warn {{
                color: {warn_hex};
                font-size: 11px;
                font-weight: 600;
            }}
            .tp-counter {{
                color: {fg_hex};
                font-size: 22px;
                font-weight: 700;
                {"font-family: monospace;" if theme == "instrument" else ""}
            }}
            .tp-hero-prob {{
                color: {fg_hex};
                font-size: 26px;
                font-weight: 700;
                {"font-family: monospace;" if theme == "instrument" else ""}
            }}
            .tp-nr-card {{
                background-color: {inset_hex};
                border: 1px solid {border_hex};
                border-radius: 8px;
                padding: 9px 11px;
            }}
            .tp-btn {{
                background: transparent;
                border: none;
                color: {sec_hex};
                padding: 4px 6px;
                border-radius: 6px;
                min-height: 24px;
                min-width: 24px;
            }}
            .tp-btn:hover {{
                background-color: {inset_hex};
                color: {fg_hex};
            }}
            .tp-bar-track {{
                background-color: {track_hex};
                border-radius: 3px;
                min-height: 5px;
            }}
            .tp-bar-primary {{
                background-color: {pri_hex};
                border-radius: 3px;
                min-height: 5px;
            }}
            .tp-bar-secondary {{
                background-color: {sec_acc_hex};
                border-radius: 3px;
                min-height: 5px;
            }}
            scale trough {{
                background-color: {track_hex};
                border-radius: 4px;
                min-height: 5px;
            }}
            scale highlight {{
                background-color: {pri_hex};
                border-radius: 4px;
            }}
            scale slider {{
                background-color: {pri_hex};
                border: 2px solid {inset_hex};
                border-radius: 50%;
                min-width: 14px;
                min-height: 14px;
                margin: -5px 0;
            }}
            """
            self.css_provider.load_from_data(css.encode("utf-8"))

        def _make_label(
            self,
            text: str,
            style_class: str = "tp-primary",
            xalign: float = 0.0,
            wrap: bool = False,
            tooltip: str | None = None,
        ) -> Gtk.Label:
            lbl = Gtk.Label(label=text)
            lbl.set_xalign(xalign)
            lbl.get_style_context().add_class(style_class)
            if wrap:
                lbl.set_line_wrap(True)
                lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lbl.set_max_width_chars(38)
            else:
                lbl.set_ellipsize(Pango.EllipsizeMode.END)
            if tooltip:
                lbl.set_tooltip_text(tooltip)
            return lbl

        def _make_progress_bar(self, fraction: float, width: int = 310, tooltip: str | None = None) -> Gtk.Widget:
            clamped = min(max(float(fraction), 0.0), 1.0)
            eb = Gtk.EventBox()
            eb.set_visible_window(False)
            if tooltip:
                eb.set_tooltip_text(tooltip)
            fixed = Gtk.Fixed()
            track = Gtk.Box()
            track.set_size_request(width, 5)
            track.get_style_context().add_class("tp-bar-track")
            fixed.put(track, 0, 0)
            fill_w = int(round(width * clamped))
            if fill_w > 0:
                fill = Gtk.Box()
                fill.set_size_request(max(fill_w, 3), 5)
                fill.get_style_context().add_class("tp-bar-primary")
                fixed.put(fill, 0, 0)
            eb.add(fixed)
            return eb

        def _make_split_bar(self, codex_ratio: float | None, width: int = 310, tooltip: str | None = None) -> Gtk.Widget:
            eb = Gtk.EventBox()
            eb.set_visible_window(False)
            if tooltip:
                eb.set_tooltip_text(tooltip)
            fixed = Gtk.Fixed()
            base = Gtk.Box()
            base.set_size_request(width, 5)
            base.get_style_context().add_class("tp-bar-track" if codex_ratio is None else "tp-bar-secondary")
            fixed.put(base, 0, 0)
            if codex_ratio is not None:
                c_w = int(round(width * min(max(codex_ratio, 0.0), 1.0)))
                if c_w > 0:
                    fill = Gtk.Box()
                    fill.set_size_request(max(c_w, 3), 5)
                    fill.get_style_context().add_class("tp-bar-primary")
                    fixed.put(fill, 0, 0)
            eb.add(fixed)
            return eb

        def _rebuild_panel_content(self) -> None:
            for child in self.card_frame.get_children():
                self.card_frame.remove(child)

            lang = self.appearance.language
            loc = Localizer(lang)
            ui = build_widget_tooltip_and_strings(self.snapshot, language=lang)

            codex_tok = float((self.snapshot.get("codex") or {}).get("total_tokens") or 0.0) if ui["codex_avail"] else 0.0
            gemini_tok = float((self.snapshot.get("gemini") or {}).get("total_tokens") or 0.0) if ui["gemini_avail"] else 0.0
            total_tok = codex_tok + gemini_tok
            codex_ratio = (codex_tok / total_tok) if total_tok > 0 else None

            if self.collapsed:
                self.window.set_size_request(300, -1)
                self._build_collapsed_view(loc, ui, codex_ratio)
            else:
                self.window.set_size_request(360, -1)
                self._build_expanded_view(loc, ui, codex_ratio)

            self.card_frame.show_all()
            self.window.resize(300 if self.collapsed else 360, 1)

        def _build_collapsed_view(self, loc: Localizer, ui: dict[str, Any], codex_ratio: float | None) -> None:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

            # Row 1: Observed tokens + split bar
            tok_tip = loc.L(
                f"Observed tokens: Codex {ui['codex_val']}, Gemini {ui['gemini_val']} (Right-click or drag to move)",
                f"已记录 Token：Codex {ui['codex_val']}，Gemini {ui['gemini_val']}",
            )
            r1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            r1.pack_start(self._make_label(f"Codex {ui['codex_val']}", "tp-primary", tooltip=tok_tip), False, False, 0)
            r1.pack_start(self._make_split_bar(codex_ratio, width=110, tooltip=tok_tip), True, True, 0)
            r1.pack_end(self._make_label(f"{ui['gemini_val']} Gemini", "tp-primary", xalign=1.0, tooltip=tok_tip), False, False, 0)
            vbox.pack_start(r1, False, False, 0)

            # Row 2: Selected quota window + progress bar + %
            csel = ui["collapsed_sel"]
            q_tip = f"{ ui['five_info']['tooltip'] } | { ui['weekly_info']['tooltip'] }"
            r2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            r2.pack_start(
                self._make_label(
                    csel["label"],
                    "tp-green" if ui["reset_first"] else "tp-primary",
                    tooltip=q_tip,
                ),
                False,
                False,
                0,
            )
            q_frac = ui["weekly_info"]["fraction"] if "Wk" in csel["label"] or "周" in csel["label"] else ui["five_info"]["fraction"]
            r2.pack_start(self._make_progress_bar(q_frac, width=120, tooltip=q_tip), True, True, 0)
            r2.pack_end(self._make_label(csel["percentText"], "tp-accent", xalign=1.0, tooltip=q_tip), False, False, 0)
            vbox.pack_start(r2, False, False, 0)

            # Row 3: Routing preset + slider + expand button
            r3 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.pct_label = self._make_label(
                f"{loc.L('Routing: ', '路由：')}{ui['routing_pct']}% Gemini",
                "tp-accent",
                tooltip=loc.L(
                    f"Gemini delegation preference: {ui['routing_pct']}% for new coding tasks",
                    f"新编码任务的 Gemini 委派偏好：{ui['routing_pct']}%",
                ),
            )
            r3.pack_start(self.pct_label, False, False, 0)

            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            slider.set_draw_value(False)
            slider.set_value(ui["routing_pct"])
            slider.connect("value-changed", self._on_slider_changed)
            r3.pack_start(slider, True, True, 0)

            exp_btn = Gtk.Button(label="☰")
            exp_btn.get_style_context().add_class("tp-btn")
            exp_btn.set_tooltip_text(loc.L("Expand TokenPilot Hover Card", "展开 TokenPilot 悬浮卡片"))
            exp_btn.connect("clicked", lambda *_: self.toggle_collapsed())
            r3.pack_end(exp_btn, False, False, 0)

            vbox.pack_start(r3, False, False, 0)
            self.card_frame.pack_start(vbox, False, False, 0)

        def _build_expanded_view(self, loc: Localizer, ui: dict[str, Any], codex_ratio: float | None) -> None:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=11)

            # 1. Header Row (Draggable title + status dot + Customize / Collapse / Hide buttons)
            hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            dot = self._make_label("●", "tp-green", tooltip=loc.L("Connected to local collector", "已连接本地收集器"))
            title = self._make_label(
                "TokenPilot",
                "tp-title",
                tooltip=loc.L("Drag anywhere on card to move floating panel", "按住卡片任意空白处可拖动悬浮面板"),
            )
            hdr.pack_start(dot, False, False, 0)
            hdr.pack_start(title, False, False, 0)

            btn_hide = Gtk.Button(label="—")
            btn_hide.get_style_context().add_class("tp-btn")
            btn_hide.set_tooltip_text(loc.L("Hide TokenPilot (restore from system tray)", "隐藏 TokenPilot（可从系统托盘恢复）"))
            btn_hide.connect("clicked", lambda *_: self.window.hide())

            btn_collapse = Gtk.Button(label="☰")
            btn_collapse.get_style_context().add_class("tp-btn")
            btn_collapse.set_tooltip_text(loc.L("Collapse to compact 3-row mini hover card", "收起为 3 行迷你悬浮卡片"))
            btn_collapse.connect("clicked", lambda *_: self.toggle_collapsed())

            btn_cust = Gtk.Button(label="🎨")
            btn_cust.get_style_context().add_class("tp-btn")
            btn_cust.set_tooltip_text(loc.L("Customize theme, language & accent colors", "自定义主题、语言与强调色"))
            btn_cust.connect("clicked", lambda *_: self.show_customizer())

            hdr.pack_end(btn_hide, False, False, 0)
            hdr.pack_end(btn_collapse, False, False, 0)
            hdr.pack_end(btn_cust, False, False, 0)
            vbox.pack_start(hdr, False, False, 0)

            # 2. Today Observed Tokens Section
            tok_sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            tok_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            tok_head.pack_start(self._make_label(loc.L("Today", "今日用量"), "tp-secondary"), False, False, 0)
            tok_head.pack_end(
                self._make_label(
                    ui["gemini_status_text"],
                    "tp-warn" if ui["gemini_alert"] else "tp-secondary",
                    xalign=1.0,
                    tooltip=ui["gemini_status_text"],
                ),
                False,
                False,
                0,
            )
            tok_sec.pack_start(tok_head, False, False, 0)

            cols = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
            c_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            c_col.pack_start(self._make_label("Codex", "tp-secondary"), False, False, 0)
            c_col.pack_start(self._make_label(ui["codex_val"], "tp-counter"), False, False, 0)
            c_col.pack_start(self._make_label(ui["codex_cache"], "tp-secondary"), False, False, 0)
            cols.pack_start(c_col, True, True, 0)

            g_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            g_col.pack_start(self._make_label("Gemini", "tp-secondary"), False, False, 0)
            g_col.pack_start(self._make_label(ui["gemini_val"], "tp-counter"), False, False, 0)
            g_col.pack_start(self._make_label(ui["gemini_cache"], "tp-secondary"), False, False, 0)
            cols.pack_start(g_col, True, True, 0)
            tok_sec.pack_start(cols, False, False, 0)

            tok_sec.pack_start(
                self._make_split_bar(
                    codex_ratio,
                    width=322,
                    tooltip=f"Codex: {ui['codex_val']} | Gemini: {ui['gemini_val']}",
                ),
                False,
                False,
                2,
            )
            vbox.pack_start(tok_sec, False, False, 0)

            # 3. Account Quotas (5-hour & Weekly) + Forecast Row
            q_sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            for q_item in (ui["five_info"], ui["weekly_info"]):
                q_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                q_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                q_row.pack_start(
                    self._make_label(
                        f"{q_item['title']} · {q_item['left_str']}",
                        "tp-primary",
                        tooltip=q_item["tooltip"],
                    ),
                    False,
                    False,
                    0,
                )
                if q_item["reset_verb"]:
                    q_row.pack_end(
                        self._make_label(q_item["reset_verb"], "tp-secondary", xalign=1.0, tooltip=q_item["tooltip"]),
                        False,
                        False,
                        0,
                    )
                q_box.pack_start(q_row, False, False, 0)
                q_box.pack_start(
                    self._make_progress_bar(q_item["fraction"], width=322, tooltip=q_item["tooltip"]),
                    False,
                    False,
                    0,
                )
                q_sec.pack_start(q_box, False, False, 0)

            f_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            f_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            f_top.pack_start(
                self._make_label(
                    ui["f_primary"],
                    "tp-green" if ui["reset_first"] else "tp-primary",
                    tooltip=f"{ui['f_primary']} · {ui['f_secondary']}",
                ),
                False,
                False,
                0,
            )
            if ui["burn_badge"]:
                f_top.pack_end(self._make_label(ui["burn_badge"], "tp-secondary", xalign=1.0), False, False, 0)
            f_box.pack_start(f_top, False, False, 0)
            if ui["f_secondary"]:
                f_box.pack_start(self._make_label(ui["f_secondary"], "tp-secondary", wrap=True), False, False, 0)
            q_sec.pack_start(f_box, False, False, 0)
            vbox.pack_start(q_sec, False, False, 0)

            # 4. NextReset Community Forecast Card
            nr_eb = Gtk.EventBox()
            nr_eb.set_visible_window(False)
            nr_eb.set_tooltip_text(ui["nr_tooltip"])
            nr_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            nr_card.get_style_context().add_class("tp-nr-card")

            nr_hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            nr_link_btn = Gtk.Button(label="NextReset ↗")
            nr_link_btn.get_style_context().add_class("tp-btn")
            nr_link_btn.connect("clicked", lambda *_: self._open_url("https://nextreset.ai/"))
            nr_hdr.pack_start(nr_link_btn, False, False, 0)
            nr_hdr.pack_end(
                self._make_label(
                    ui["nr_badge"],
                    "tp-green" if ui["nr_announced"] else "tp-secondary",
                    xalign=1.0,
                ),
                False,
                False,
                0,
            )
            nr_card.pack_start(nr_hdr, False, False, 0)

            nr_hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            nr_hero.pack_start(self._make_label(ui["nr_prob24"], "tp-hero-prob"), False, False, 0)
            nr_meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            nr_meta.pack_start(self._make_label(ui["nr_status"], "tp-primary", wrap=True), False, False, 0)
            nr_meta.pack_start(self._make_label(ui["nr_caption"], "tp-secondary"), False, False, 0)
            nr_hero.pack_start(nr_meta, True, True, 0)
            nr_card.pack_start(nr_hero, False, False, 0)

            nr_foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            nr_foot.pack_start(
                self._make_label(
                    f"48h {ui['nr_prob48']}  ·  72h {ui['nr_prob72']}",
                    "tp-secondary",
                ),
                False,
                False,
                0,
            )
            if ui["nr_signal_url"]:
                sig_btn = Gtk.Button(label=loc.L("Original signal ↗", "原始信号 ↗"))
                sig_btn.get_style_context().add_class("tp-btn")
                sig_btn.connect("clicked", lambda *_: self._open_url(str(ui["nr_signal_url"])))
                nr_foot.pack_end(sig_btn, False, False, 0)
            nr_card.pack_start(nr_foot, False, False, 0)
            nr_eb.add(nr_card)
            vbox.pack_start(nr_eb, False, False, 0)

            # 5. Live Routing Preference Slider & Presets
            r_sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            r_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            r_top.pack_start(self._make_label(loc.L("Routing", "任务路由"), "tp-primary"), False, False, 0)

            self.pct_label = self._make_label(
                f"{ui['routing_pct']}% Gemini",
                "tp-accent",
                xalign=1.0,
                tooltip=loc.L(
                    f"Gemini delegation preference: {ui['routing_pct']}% for new coding tasks",
                    f"新编码任务的 Gemini 委派偏好：{ui['routing_pct']}%",
                ),
            )
            r_top.pack_end(self.pct_label, False, False, 0)
            r_sec.pack_start(r_top, False, False, 0)

            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            slider.set_draw_value(False)
            slider.set_value(ui["routing_pct"])
            slider.set_tooltip_text(
                loc.L(
                    "Slide to set live Gemini routing percentage (0% = GPT only, 100% = Gemini preferred)",
                    "拖动设置 Gemini 实时路由比例（0% = 仅 GPT，100% = 优先 Gemini）",
                )
            )
            slider.connect("value-changed", self._on_slider_changed)
            r_sec.pack_start(slider, False, False, 0)

            preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            for pct_val, lbl_text in (
                (0, loc.L("0% GPT", "0% 仅GPT")),
                (50, loc.L("50% Balanced", "50% 均衡")),
                (75, "75%"),
                (100, "100% Gemini"),
            ):
                p_btn = Gtk.Button(label=lbl_text)
                p_btn.get_style_context().add_class("tp-btn")
                p_btn.connect("clicked", lambda _b, v=pct_val: self._set_routing_preset(v))
                preset_row.pack_start(p_btn, False, False, 0)
            r_sec.pack_start(preset_row, False, False, 0)
            vbox.pack_start(r_sec, False, False, 0)

            # 6. Footer Row (Updated timestamp + Refresh + Theme cycle)
            foot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            now_hms = datetime.now().strftime("%H:%M:%S")
            foot.pack_start(
                self._make_label(loc.L(f"Updated {now_hms}", f"更新于 {now_hms}"), "tp-secondary"),
                False,
                False,
                0,
            )
            if (self.snapshot.get("limits") or {}).get("stale"):
                foot.pack_start(self._make_label(loc.L("· Stale", "· 缓存过期"), "tp-warn"), False, False, 0)

            btn_ref = Gtk.Button(label="↻")
            btn_ref.get_style_context().add_class("tp-btn")
            btn_ref.set_tooltip_text(loc.L("Refresh usage and quota", "刷新用量与配额"))
            btn_ref.connect("clicked", lambda *_: self.manual_refresh())
            foot.pack_end(btn_ref, False, False, 0)

            vbox.pack_start(foot, False, False, 0)
            self.card_frame.pack_start(vbox, False, False, 0)

        def _on_window_button_press(self, _widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
            if event.button == 1:
                self.window.begin_move_drag(
                    event.button,
                    int(event.x_root),
                    int(event.y_root),
                    event.time,
                )
                return True
            if event.button == 3:
                self.tray_menu.popup(None, None, None, None, event.button, event.time)
                return True
            return False

        def _on_slider_changed(self, scale: Gtk.Scale) -> None:
            if self.updating_slider:
                return
            pct = int(round(scale.get_value()))
            pct = min(max(pct, 0), 100)
            try:
                bridge.set_routing(self.codex_home, pct)
                if hasattr(self, "pct_label") and self.pct_label is not None:
                    prefix = (
                        ("路由：" if self.appearance.language == "zh-Hans" else "Routing: ")
                        if self.collapsed
                        else ""
                    )
                    self.pct_label.set_text(f"{prefix}{pct}% Gemini")
            except Exception:
                pass

        def _set_routing_preset(self, pct: int) -> None:
            try:
                bridge.set_routing(self.codex_home, pct)
                self.snapshot = self.collector.snapshot(refresh=False)
                self._rebuild_panel_content()
            except Exception:
                pass

        def manual_refresh(self) -> None:
            if not self.collector.offline:
                self.collector.refresh_limits(force=True)
                self.collector.refresh_nextreset(force=True)
            self.snapshot = self.collector.snapshot(refresh=False)
            self._rebuild_panel_content()

        def toggle_collapsed(self) -> None:
            self.collapsed = not self.collapsed
            self._apply_theme_css()
            self._rebuild_panel_content()
            self._record_ui_state("collapse" if self.collapsed else "expand")

        def toggle_visibility(self) -> None:
            if self.window.get_visible():
                self.window.hide()
                self._record_ui_state("hide")
            else:
                self.window.show_all()
                self.window.present()
                self._record_ui_state("show")

        def show_customizer(self) -> None:
            if self.customizer_win is not None:
                self.customizer_win.present()
                return

            loc = Localizer(self.appearance.language)
            win = Gtk.Window(title=loc.L("Customize TokenPilot", "自定义 TokenPilot"))
            win.set_default_size(420, 320)
            win.set_position(Gtk.WindowPosition.CENTER)
            win.connect("destroy", lambda *_: setattr(self, "customizer_win", None))

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.set_margin_top(16)
            box.set_margin_bottom(16)
            box.set_margin_start(16)
            box.set_margin_end(16)

            lang_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lang_row.pack_start(Gtk.Label(label=loc.L("Language / 界面语言:", "界面语言 / Language:")), False, False, 0)
            for code, title in (("en", "English"), ("zh-Hans", "简体中文")):
                b = Gtk.Button(label=title)
                b.connect("clicked", lambda _w, c=code: self._change_language(c))
                lang_row.pack_end(b, False, False, 0)
            box.pack_start(lang_row, False, False, 0)

            box.pack_start(Gtk.Label(label=loc.L("Select Theme (6 Styles):", "选择主题（6 款风格）："), xalign=0.0), False, False, 0)
            grid = Gtk.Grid()
            grid.set_row_spacing(8)
            grid.set_column_spacing(8)
            for idx, t_id in enumerate(linux_app.SUPPORTED_THEMES):
                spec = linux_app.THEME_SPECS[t_id]
                btn = Gtk.Button(label=f"{spec['enName']} / {spec['zhName']}")
                btn.connect("clicked", lambda _w, th=t_id: self._change_theme(th))
                grid.attach(btn, idx % 2, idx // 2, 1, 1)
            box.pack_start(grid, False, False, 0)

            if self.web_url:
                web_btn = Gtk.Button(label=loc.L("Open Full Color & Theme Studio in Browser", "在浏览器打开完整配色与主题工作台"))
                web_btn.connect("clicked", lambda *_: self._open_url(self.web_url))
                box.pack_start(web_btn, False, False, 0)

            win.add(box)
            win.show_all()
            self.customizer_win = win
            self._record_ui_state("customize")

        def _change_language(self, lang_code: str) -> None:
            self.appearance.language = lang_code
            self.appearance.persist_if_needed()
            self._apply_theme_css()
            self._rebuild_panel_content()

        def _change_theme(self, theme_id: str) -> None:
            self.appearance.theme = theme_id
            self.appearance.persist_if_needed()
            self._apply_theme_css()
            self._rebuild_panel_content()

        def _open_url(self, url: str | None) -> None:
            if not url:
                return
            xdg = shutil.which("xdg-open")
            if xdg:
                subprocess.Popen([xdg, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def _record_ui_state(self, action: str) -> None:
            try:
                pos = self.window.get_position()
                size = self.window.get_size()
                bridge.atomic_json(
                    self.codex_home / "argon" / "viewer" / "ui-state.json",
                    {
                        "action": action,
                        "visible": self.window.get_visible(),
                        "collapsed": self.collapsed,
                        "width": size.width,
                        "height": size.height,
                        "left": pos.root_x,
                        "top": pos.root_y,
                        "theme": self.appearance.theme,
                        "language": self.appearance.language,
                        "url": self.web_url,
                        "platform": sys.platform,
                    },
                )
            except Exception:
                pass

        def _periodic_refresh(self) -> bool:
            try:
                old_theme = self.appearance.theme
                old_lang = self.appearance.language
                self.appearance._load()
                theme_changed = (self.appearance.theme != old_theme) or (self.appearance.language != old_lang)
                self.snapshot = self.collector.snapshot(refresh=not self.collector.offline)
                if theme_changed:
                    self._apply_theme_css()
                self._rebuild_panel_content()
            except Exception:
                pass
            return True

    def _gtk_thread() -> None:
        TokenPilotGTKApp()
        Gtk.main()

    t = threading.Thread(target=_gtk_thread, daemon=True)
    t.start()
    return True


if __name__ == "__main__":
    codex_home = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser()
    codex_bin = os.environ.get("TOKENPILOT_CODEX_BIN") or shutil.which("codex")
    collector = bridge.Bridge(codex_home, codex_bin, ZoneInfo("UTC"), offline="--offline" in sys.argv)
    appearance = linux_app.AppearanceStore(prefs_path=linux_app.default_preferences_path(), persists_changes=True)
    if launch_gtk_floating_panel(collector, appearance, codex_home, start_collapsed="--collapsed" in sys.argv):
        while True:
            time.sleep(1)
