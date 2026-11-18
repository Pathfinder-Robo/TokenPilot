#!/usr/bin/env python3
"""Canonical Google Argon (gemini-4-argon) reference pricing valuation for TokenPilot.

Values both Codex and Gemini token usage using one fixed introductory Google Argon
reference rate schedule as of 2026-10-27:
  - Uncached input: $2.00 per 1,000,000 tokens
  - Cached input:   $0.10 per 1,000,000 tokens
  - Output (including thinking/reasoning): $10.00 per 1,000,000 tokens
  - Introductory period through 2026-11-30 (official prices double on 2026-12-01).

This valuation is a fixed-reference comparison/estimate across providers and devices,
not an actual Codex subscription charge or a live Google invoice/credit balance.
Historical totals are never silently revalued when the calendar crosses 2026-12-01;
instead, the reference metadata marks the introductory rate schedule as outdated.
"""
from datetime import date, datetime, timezone, tzinfo
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRICING_REFERENCE_VERSION = 1
PRICING_REFERENCE_ID = 'gemini-4-argon-intro-2026-10-27'
PRICING_MODEL = 'gemini-4-argon'
PRICING_TIER = 'introductory'
PRICING_SOURCE_URL = 'https://ai.google.dev/gemini-api/docs/latest-model?hl=en#pricing'
PRICING_AS_OF_DATE = '2026-10-27'
PRICING_INTRO_THROUGH_DATE = '2026-11-30'
PRICING_POST_INTRO_EFFECTIVE_DATE = '2026-12-01'
PRICING_POST_INTRO_MULTIPLIER = 2.0
PRICING_CURRENCY = 'USD'

UNCACHED_INPUT_USD_PER_MILLION = 2.0
CACHED_INPUT_USD_PER_MILLION = 0.10
OUTPUT_USD_PER_MILLION = 10.0
TOKENS_PER_MILLION = 1_000_000.0

INTRO_END_DATE = date(2026, 11, 30)
POST_INTRO_START_DATE = date(2026, 12, 1)


def is_valid_non_negative_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _resolve_tz(tz):
    if tz is None:
        return timezone.utc
    if isinstance(tz, tzinfo):
        return tz
    if isinstance(tz, str) and tz.strip():
        cleaned = tz.strip()
        if cleaned.upper() == 'UTC':
            return timezone.utc
        try:
            return ZoneInfo(cleaned)
        except (ZoneInfoNotFoundError, KeyError, ValueError):
            return timezone.utc
    return timezone.utc


def _resolve_date(*, now=None, day=None, tz=None):
    if isinstance(day, date) and not isinstance(day, datetime):
        return day
    if isinstance(day, str) and day.strip():
        try:
            return date.fromisoformat(day.strip()[:10])
        except ValueError:
            pass
    if now is None:
        return None
    tz_obj = _resolve_tz(tz)
    if isinstance(now, datetime):
        dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz_obj).date()
    if is_valid_non_negative_number(now):
        return datetime.fromtimestamp(float(now), tz=timezone.utc).astimezone(tz_obj).date()
    if isinstance(now, str) and now.strip():
        text = now.strip()
        if text.endswith('Z') or text.endswith('z'):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(tz_obj).date()
        except ValueError:
            try:
                return date.fromisoformat(now.strip()[:10])
            except ValueError:
                return None
    return None


def is_reference_outdated(*, now=None, day=None, tz=None):
    resolved = _resolve_date(now=now, day=day, tz=tz)
    if resolved is None:
        return False
    return resolved >= POST_INTRO_START_DATE


def pricing_reference_metadata(*, now=None, day=None, tz=None):
    outdated = is_reference_outdated(now=now, day=day, tz=tz)
    return {
        'version': PRICING_REFERENCE_VERSION,
        'reference_id': PRICING_REFERENCE_ID,
        'model': PRICING_MODEL,
        'pricing_tier': PRICING_TIER,
        'source_url': PRICING_SOURCE_URL,
        'as_of': PRICING_AS_OF_DATE,
        'intro_through': PRICING_INTRO_THROUGH_DATE,
        'post_intro_effective_date': PRICING_POST_INTRO_EFFECTIVE_DATE,
        'post_intro_multiplier': PRICING_POST_INTRO_MULTIPLIER,
        'currency': PRICING_CURRENCY,
        'uncached_input_per_million_usd': UNCACHED_INPUT_USD_PER_MILLION,
        'cached_input_per_million_usd': CACHED_INPUT_USD_PER_MILLION,
        'output_per_million_usd': OUTPUT_USD_PER_MILLION,
        'output_includes_thinking': True,
        'input_includes_cached': True,
        'reference_outdated': outdated,
        'intro_expired': outdated,
        'status': 'reference_outdated' if outdated else 'active',
    }


def event_has_valid_categories(event):
    if not isinstance(event, dict):
        return False
    inp = event.get('input_tokens')
    cached = event.get('cached_tokens')
    out = event.get('output_tokens')
    total = event.get('total_tokens', 0)
    if not (
        is_valid_non_negative_number(inp)
        and is_valid_non_negative_number(cached)
        and is_valid_non_negative_number(out)
        and is_valid_non_negative_number(total)
    ):
        return False
    if cached > inp:
        return False
    if total > 0 and inp == 0 and out == 0:
        return False
    return True


def validate_usage_categories(usage):
    if not isinstance(usage, dict):
        return False, None, None, None
    if 'available' in usage and usage.get('available') is not True:
        return False, None, None, None
    if usage.get('categories_valid') is False:
        return False, None, None, None

    inp = usage.get('input_tokens')
    cached = usage.get('cached_tokens')
    out = usage.get('output_tokens')
    if not (
        is_valid_non_negative_number(inp)
        and is_valid_non_negative_number(cached)
        and is_valid_non_negative_number(out)
    ):
        return False, None, None, None

    if 'total_tokens' in usage and usage.get('total_tokens') is not None:
        total = usage.get('total_tokens')
        if not is_valid_non_negative_number(total):
            return False, None, None, None
        if total > 0 and inp == 0 and out == 0:
            return False, None, None, None

    if cached > inp:
        return False, None, None, None

    uncached_inp = inp - cached
    if not is_valid_non_negative_number(uncached_inp):
        return False, None, None, None

    return True, uncached_inp, cached, out


def estimate_usage_usd(usage):
    ok, uncached_inp, cached, out = validate_usage_categories(usage)
    if not ok:
        return None
    usd = (
        float(uncached_inp) * UNCACHED_INPUT_USD_PER_MILLION
        + float(cached) * CACHED_INPUT_USD_PER_MILLION
        + float(out) * OUTPUT_USD_PER_MILLION
    ) / TOKENS_PER_MILLION
    if not math.isfinite(usd) or usd < 0:
        return None
    return 0.0 if usd == 0.0 else usd


def enrich_usage_with_pricing(usage, *, categories_valid=True):
    if not isinstance(usage, dict):
        return usage
    out = dict(usage)
    effective_valid = bool(categories_valid and out.get('categories_valid', True) is not False)
    if not effective_valid:
        out['categories_valid'] = False
        out['uncached_input_tokens'] = None
        out['estimated_usd'] = None
        out['pricing_reference_version'] = PRICING_REFERENCE_VERSION
        return out

    if out.get('available') is not True:
        out['uncached_input_tokens'] = None
        out['estimated_usd'] = None
        out['pricing_reference_version'] = PRICING_REFERENCE_VERSION
        return out

    ok, uncached_inp, _, _ = validate_usage_categories(out)
    if not ok:
        out['uncached_input_tokens'] = None
        out['estimated_usd'] = None
        out['pricing_reference_version'] = PRICING_REFERENCE_VERSION
        return out

    out['uncached_input_tokens'] = uncached_inp
    out['estimated_usd'] = estimate_usage_usd(out)
    out['pricing_reference_version'] = PRICING_REFERENCE_VERSION
    return out


def provider_split_ratio(codex, gemini):
    if isinstance(codex, dict):
        if codex.get('available') is not True:
            return None
        codex_usd = estimate_usage_usd(codex)
    else:
        codex_usd = float(codex) if is_valid_non_negative_number(codex) else None

    if isinstance(gemini, dict):
        if gemini.get('available') is not True:
            return None
        gemini_usd = estimate_usage_usd(gemini)
    else:
        gemini_usd = float(gemini) if is_valid_non_negative_number(gemini) else None

    if codex_usd is None or gemini_usd is None:
        return None
    total = codex_usd + gemini_usd
    if not math.isfinite(total) or total <= 0:
        return None
    return min(max(codex_usd / total, 0.0), 1.0)


def format_usd(value):
    if not is_valid_non_negative_number(value):
        return '—'
    val = float(value)
    if val == 0.0:
        return '$0.00'
    if val < 0.0001:
        return '<$0.0001'
    if val < 0.01:
        if round(val, 4) >= 0.01:
            return f'${val:.2f}'
        return f'${val:.4f}'
    return f'${val:.2f}'
