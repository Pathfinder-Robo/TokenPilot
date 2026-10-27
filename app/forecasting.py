"""Trailing-15-minute quota trend; token counts are context, not quota units.
过去 15 分钟配额消耗趋势预测模块；Token 计数仅作上下文参考而非配额单位。
"""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

WINDOW = 900


def epoch(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def log_sample(stamp, raw):
    at = epoch(stamp)
    if at is None or not isinstance(raw, dict):
        return None
    answer = {'at': at, 'bucket': raw.get('limit_id') or raw.get('limitId') or 'codex'}
    for name in ('primary', 'secondary'):
        window = raw.get(name)
        if not isinstance(window, dict):
            continue
        duration = window.get('window_minutes', window.get('windowDurationMins'))
        label = {300: 'five_hour', 10080: 'weekly'}.get(duration)
        used = window.get('used_percent', window.get('usedPercent'))
        if label and finite(used):
            answer[label] = {'used': min(100, max(0, used)),
                             'reset': window.get('resets_at', window.get('resetsAt'))}
    return answer if any(key in answer for key in ('five_hour', 'weekly')) else None


def current_sample(limits):
    at = epoch(limits.get('fetched_at'))
    if at is None or limits.get('stale'):
        return None
    sample = {'at': at, 'bucket': limits.get('bucket') or 'codex'}
    for name in ('five_hour', 'weekly'):
        value = limits.get(name)
        if isinstance(value, dict) and finite(value.get('used_percent')):
            sample[name] = {'used': value['used_percent'], 'reset': value.get('resets_at')}
    return sample


class QuotaHistory:
    def __init__(self, home, writer):
        self.path = Path(home) / 'argon/viewer/quota-history.json'
        self.writer = writer
        try:
            value = json.loads(self.path.read_text())
            self.samples = value if isinstance(value, list) else []
        except (ValueError, OSError):
            self.samples = []

    def add(self, samples, now=None):
        now = time.time() if now is None else now
        unique = {}
        for sample in [*self.samples, *samples]:
            if isinstance(sample, dict) and finite(sample.get('at')) and now - 3600 <= sample['at'] <= now + 5:
                unique[(sample['at'], sample.get('bucket'))] = sample
        updated = sorted(unique.values(), key=lambda value: value['at'])[-3000:]
        if updated != self.samples:
            self.samples = updated
            self.writer(self.path, self.samples)


def same_reset(a, b):
    # Service timestamps may drift by a few seconds; actual new windows move by hours.
    return (a is None and b is None) or (finite(a) and finite(b) and abs(a - b) <= 120)


def window_forecast(name, samples, limits, now):
    value = limits.get(name)
    if not isinstance(value, dict) or not finite(value.get('used_percent')):
        return {'status': 'unavailable', 'exhaustion_at': None, 'message': 'Quota unavailable'}
    used = min(100, max(0, value['used_percent']))
    if limits.get('stale') or epoch(limits.get('fetched_at')) is None or now - epoch(limits['fetched_at']) > 150:
        return {'status': 'stale', 'exhaustion_at': None, 'message': 'Waiting for fresh quota data'}
    end = epoch(limits['fetched_at'])
    if used >= 100:
        return {'status': 'reached', 'exhaustion_at': end, 'message': 'Usage limit reached'}
    cutoff = end - WINDOW
    data = [(s['at'], s[name]) for s in samples if s.get('bucket') == limits.get('bucket', 'codex')
            and name in s and cutoff - 120 <= s['at'] <= end
            and same_reset(s[name].get('reset'), value.get('resets_at'))]
    data.sort(key=lambda item: item[0])
    before = [item for item in data if item[0] <= cutoff]
    after = [item for item in data if item[0] >= cutoff]
    learning = {'status': 'learning', 'exhaustion_at': None, 'message': 'Collecting 15m quota history'}
    if not before or not after:
        return learning
    left, right = before[-1], after[0]
    if right[0] - left[0] > 120:
        return learning  # Never interpolate across idle/missing hours.
    # Concurrent clients and rounded service samples can jitter by 1–2 points.
    # Only a material drop is treated as a reset; timestamp changes handle normal windows.
    if any(a[1]['used'] - b[1]['used'] > max(2.0, a[1]['used'] * .5)
           for a, b in zip(data, data[1:])):
        return dict(learning, message='Reset detected · collecting a new 15m trend')
    baseline = left[1]['used']
    if right[0] > left[0]:
        baseline += (right[1]['used'] - baseline) * (cutoff - left[0]) / (right[0] - left[0])
    rate = (used - baseline) / 15
    if rate <= 0:
        return {'status': 'idle', 'exhaustion_at': None, 'message': 'No measurable quota burn in 15m'}
    predicted = end + (100 - used) / rate * 60
    reset = value.get('resets_at')
    if finite(reset) and predicted >= reset:
        return {'status': 'resets_first', 'exhaustion_at': None, 'message': 'Scheduled reset comes before projected depletion'}
    return {'status': 'projected', 'exhaustion_at': predicted,
            'message': f'Estimate from the last 15m: {rate:.2f} quota percentage points/min; assumes unchanged pace'}


def select_display(limits, five, weekly):
    first, second = limits.get('five_hour'), limits.get('weekly')
    if first and second and 100 - first['used_percent'] > 95:
        return 'weekly', '5-hour has more than 95% left; showing Weekly'
    available = [(key, forecast) for key, forecast in [('five_hour', five), ('weekly', weekly)]
                 if forecast['status'] in ('projected', 'reached') and forecast['exhaustion_at'] is not None]
    reached = [(key, forecast) for key, forecast in available if forecast['status'] == 'reached']
    present = [(key, forecast) for key, forecast in [('five_hour', five), ('weekly', weekly)] if limits.get(key)]
    known = all(forecast['status'] in ('projected', 'reached', 'idle', 'resets_first') for _, forecast in present)
    if reached:
        return reached[0][0], 'Showing the quota that has already reached its limit'
    if available and known:
        key, _ = min(available, key=lambda item: item[1]['exhaustion_at'])
        return key, 'Showing the earliest available exhaustion forecast'
    choices = [(key, value) for key, value in [('five_hour', first), ('weekly', second)] if value]
    if choices:
        return max(choices, key=lambda item: item[1]['used_percent'])[0], 'Forecast provisional; showing lower remaining quota while the trend warms up'
    return 'five_hour', 'Quota data unavailable'


def build_forecast(samples, limits, burn_tokens, now=None):
    now = time.time() if now is None else now
    five = window_forecast('five_hour', samples, limits, now)
    weekly = window_forecast('weekly', samples, limits, now)
    display, reason = select_display(limits, five, weekly)
    detail = '15m trend · cache included · assumes unchanged pace'
    reset_window = None
    predictions = [f for f in (five, weekly) if f['status'] in ('projected', 'reached')]
    if predictions:
        chosen = min(predictions, key=lambda f: f['exhaustion_at'])
        status, at = chosen['status'], chosen['exhaustion_at']
        headline = 'Usage limit reached' if status == 'reached' else 'Projected exhaustion'
        resets = [(value['resets_at'], key) for key in ('five_hour', 'weekly')
                  if isinstance((value := limits.get(key)), dict)
                  and finite(value.get('resets_at')) and value['resets_at'] > now]
        if status == 'projected' and resets:
            reset_at, reset_window = min(resets)
            if at >= reset_at:
                status, at, headline = 'reset_before_projection', None, 'Next reset comes first'
                detail = ('5-hour resets first · weekly quota remains'
                          if reset_window == 'five_hour' else 'Weekly resets first · 5-hour quota remains')
    elif any(f['status'] == 'learning' for f in (five, weekly)):
        status, at, headline = 'learning', None, 'Learning 15m quota trend'
    elif all(f['status'] == 'unavailable' for f in (five, weekly)):
        status, at, headline = 'unavailable', None, 'Waiting for quota data'
    elif all(f['status'] in ('resets_first', 'idle', 'unavailable') for f in (five, weekly)):
        status, at = 'steady', None
        if any(f['status'] == 'resets_first' for f in (five, weekly)):
            status, headline = 'reset_before_projection', 'Next reset comes first'
            names = [label for label, value in [('5-hour', five), ('Weekly', weekly)] if value['status'] == 'resets_first']
            detail = ' and '.join(names) + ' resets before projected depletion'
            safe = [(limits[key]['resets_at'], key) for key, value in [('five_hour', five), ('weekly', weekly)]
                    if value['status'] == 'resets_first' and finite((limits.get(key) or {}).get('resets_at'))]
            if safe:
                reset_window = min(safe)[1]
        else:
            headline = 'No measurable quota burn'
    else:
        status, at, headline = 'stale', None, 'Waiting for fresh quota data'
    return {'status': status, 'headline': headline, 'exhaustion_at': at,
            'next_reset_window': reset_window,
            'tokens_per_minute': burn_tokens / 15, 'tokens_last_15m': burn_tokens,
            'display_window': display, 'selection_reason': reason,
            'detail': detail,
            'five_hour': five, 'weekly': weekly}
