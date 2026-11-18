#!/usr/bin/env python3
"""Persistent numeric quota reset tracker for TokenPilot.

Tracks actual account weekly quota resets—both scheduled weekly rollovers and
early/ad-hoc resets (provider/Tibo grants, user reset coupons)—from numeric
quota samples and live account rate-limit snapshots.

Never reads conversation text, prompts, or credentials, and never uses public
community NextReset forecasts as personal account reset times.
"""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time

WEEKLY_WINDOW_SECONDS = 10080 * 60  # 604800 seconds (7 days)
RESET_JITTER_SECONDS = 120.0
STALE_LIVE_LIMIT_SECONDS = 300.0
MAX_HISTORY_ENTRIES = 64
MAX_SEEN_AT_TIMESTAMP = 32


def finite_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def parse_timestamp_epoch(value):
    if finite_number(value):
        val = float(value)
        return val if val > 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith('Z') or text.endswith('z'):
        text = text[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return None
    ep = dt.timestamp()
    return ep if math.isfinite(ep) and ep > 0 else None


def format_epoch_utc(epoch_val):
    if not finite_number(epoch_val) or epoch_val <= 0:
        return None
    return (
        datetime.fromtimestamp(float(epoch_val), timezone.utc)
        .isoformat()
        .replace('+00:00', 'Z')
    )


def same_reset_deadline(a, b):
    if a is None and b is None:
        return True
    if finite_number(a) and finite_number(b):
        return abs(float(a) - float(b)) <= RESET_JITTER_SECONDS
    return False


def is_meaningful_usage_drop(used_prev, used_curr):
    if not (finite_number(used_prev) and finite_number(used_curr)):
        return False
    # Positive -> 0 is always a reset even if prior usage was small (e.g. 0.5% or 1% -> 0%).
    if used_prev > 0 and used_curr == 0:
        return True
    drop = float(used_prev) - float(used_curr)
    # Concurrent requests / rounding can jitter by 1-2 percentage points.
    if drop <= 2.0:
        return False
    return drop >= 5.0 or drop >= float(used_prev) * 0.5


def _atomic_write_json(path, value):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Reset tracker path must not be a symlink')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.reset-tracker-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def extract_weekly_sample(raw_sample, now_epoch=None):
    """Validate and normalize a quota sample containing a weekly window.

    Returns None for stale, future, malformed, or 5-hour-only samples.
    """
    if not isinstance(raw_sample, dict):
        return None
    if raw_sample.get('stale') is True or raw_sample.get('error'):
        return None

    at_raw = raw_sample.get('at')
    if at_raw is None:
        at_raw = raw_sample.get('fetched_at') or raw_sample.get('timestamp')
    at = parse_timestamp_epoch(at_raw)
    if at is None:
        return None
    if now_epoch is not None and finite_number(now_epoch):
        if at > float(now_epoch):
            return None

    bucket = raw_sample.get('bucket') or raw_sample.get('limit_id') or raw_sample.get('limitId') or 'codex'
    if not isinstance(bucket, str) or not bucket.strip():
        return None
    bucket = bucket.strip()

    weekly = raw_sample.get('weekly')
    if not isinstance(weekly, dict):
        # Check if raw_sample is an un-normalized rate_limits dict with primary/secondary slots
        for slot in ('primary', 'secondary'):
            win = raw_sample.get(slot)
            if isinstance(win, dict):
                dur = win.get('window_minutes', win.get('windowDurationMins'))
                if dur == 10080:
                    weekly = win
                    break
    if not isinstance(weekly, dict):
        return None

    used_raw = weekly.get('used')
    if used_raw is None:
        used_raw = weekly.get('used_percent', weekly.get('usedPercent'))
    if not finite_number(used_raw):
        return None
    used = float(used_raw)
    if used < 0.0 or used > 100.0:
        return None

    reset_raw = weekly.get('reset')
    if reset_raw is None and 'reset' not in weekly:
        reset_raw = weekly.get('resets_at', weekly.get('resetsAt'))
    reset = None
    if reset_raw is not None:
        if not finite_number(reset_raw) or float(reset_raw) <= 0:
            return None
        reset = float(reset_raw)
        # Reject samples whose weekly reset deadline is already far in the past relative to sample time
        if reset < at - 600.0:
            return None

    exact_reset_raw = (
        weekly.get('last_reset_at')
        or weekly.get('lastResetAt')
        or weekly.get('reset_at')
        or weekly.get('resetAt')
    )
    exact_reset = None
    if exact_reset_raw is not None:
        exact_ep = parse_timestamp_epoch(exact_reset_raw)
        if exact_ep is None or exact_ep > at:
            return None
        exact_reset = exact_ep

    return {
        'at': at,
        'bucket': bucket,
        'used': used,
        'reset': reset,
        'exact_reset': exact_reset,
    }


def sample_from_limits(limits, now_epoch=None):
    """Extract a validated weekly sample from a live Bridge limits dict."""
    if not isinstance(limits, dict):
        return None
    if limits.get('stale') is True or limits.get('error'):
        return None
    fetched_at = parse_timestamp_epoch(limits.get('fetched_at'))
    if fetched_at is None:
        return None
    if now_epoch is not None and finite_number(now_epoch):
        now_f = float(now_epoch)
        if fetched_at > now_f:
            return None
        if now_f - fetched_at > STALE_LIVE_LIMIT_SECONDS:
            return None
    return extract_weekly_sample(
        {
            'at': fetched_at,
            'bucket': limits.get('bucket') or 'codex',
            'weekly': limits.get('weekly'),
        },
        now_epoch=now_epoch,
    )


class ResetTracker:
    """Persistent numeric tracker for the account's latest actual weekly quota reset."""

    def __init__(self, codex_home, writer=None):
        self.home = Path(codex_home).expanduser()
        self.path = self.home / 'argon/viewer/reset-tracker.json'
        self.writer = writer or _atomic_write_json
        self._reset_memory_state()
        self._load()

    def _reset_memory_state(self):
        self.bucket = 'codex'
        self.period_start_epoch = None
        self.source = 'unavailable'
        self.approximate = True
        self.detected_at_epoch = None
        self.window_earliest_epoch = None
        self.uncertainty_seconds = None
        self.resets_at = None
        self.last_sample = None
        self.seen_at_last = []
        self.history = []

    def _load(self):
        try:
            if not self.path.exists():
                return
            if self.path.is_symlink() or self.path.parent.is_symlink():
                return
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(raw, dict) or raw.get('version') != 1:
                return

            bucket = raw.get('bucket')
            if isinstance(bucket, str) and bucket.strip():
                self.bucket = bucket.strip()

            p_start = parse_timestamp_epoch(raw.get('period_start_epoch'))
            if p_start is None:
                p_start = parse_timestamp_epoch(raw.get('period_start'))
            source = raw.get('source')
            if p_start is not None and source in ('scheduled', 'early_reset', 'scheduled_inference', 'service_timestamp'):
                self.period_start_epoch = p_start
                self.source = source
                self.approximate = bool(raw.get('approximate', source != 'scheduled'))
            else:
                self.period_start_epoch = None
                self.source = 'unavailable'
                self.approximate = True

            det = parse_timestamp_epoch(raw.get('detected_at_epoch'))
            if det is None:
                det = parse_timestamp_epoch(raw.get('detected_at'))
            self.detected_at_epoch = det

            earliest = parse_timestamp_epoch(raw.get('window_earliest_epoch'))
            if earliest is None:
                earliest = parse_timestamp_epoch(raw.get('window_earliest_at'))
            self.window_earliest_epoch = earliest

            unc = raw.get('uncertainty_seconds')
            self.uncertainty_seconds = float(unc) if finite_number(unc) and unc >= 0 else None

            resets_at = raw.get('resets_at')
            self.resets_at = float(resets_at) if finite_number(resets_at) and resets_at > 0 else None

            last_s = raw.get('last_sample')
            if isinstance(last_s, dict):
                cleaned_last = extract_weekly_sample(
                    {
                        'at': last_s.get('at'),
                        'bucket': last_s.get('bucket') or self.bucket,
                        'weekly': {
                            'used': last_s.get('used'),
                            'reset': last_s.get('reset'),
                        },
                    }
                )
                if cleaned_last is not None:
                    self.last_sample = cleaned_last

            seen_raw = raw.get('seen_at_last')
            if isinstance(seen_raw, list) and self.last_sample is not None:
                valid_seen = []
                for item in seen_raw[:MAX_SEEN_AT_TIMESTAMP]:
                    if isinstance(item, list) and len(item) == 3 and finite_number(item[0]):
                        r_val = float(item[1]) if finite_number(item[1]) else None
                        b_val = item[2] if isinstance(item[2], str) else self.bucket
                        valid_seen.append((float(item[0]), r_val, b_val))
                self.seen_at_last = valid_seen
            elif self.last_sample is not None:
                self.seen_at_last = [
                    (self.last_sample['used'], self.last_sample['reset'], self.last_sample['bucket'])
                ]

            hist_raw = raw.get('history')
            if isinstance(hist_raw, list):
                self.history = [h for h in hist_raw if isinstance(h, dict)][-MAX_HISTORY_ENTRIES:]
        except (OSError, ValueError, TypeError):
            self._reset_memory_state()

    def _serialize(self):
        return {
            'version': 1,
            'bucket': self.bucket,
            'period_start': format_epoch_utc(self.period_start_epoch),
            'period_start_epoch': self.period_start_epoch,
            'source': self.source if self.period_start_epoch is not None else 'unavailable',
            'approximate': self.approximate if self.period_start_epoch is not None else True,
            'detected_at': format_epoch_utc(self.detected_at_epoch),
            'detected_at_epoch': self.detected_at_epoch,
            'window_earliest_at': format_epoch_utc(self.window_earliest_epoch),
            'window_earliest_epoch': self.window_earliest_epoch,
            'uncertainty_seconds': self.uncertainty_seconds,
            'resets_at': self.resets_at,
            'last_sample': (
                {
                    'at': self.last_sample['at'],
                    'used': self.last_sample['used'],
                    'reset': self.last_sample['reset'],
                    'bucket': self.last_sample['bucket'],
                }
                if self.last_sample is not None
                else None
            ),
            'seen_at_last': [
                [u, r, b] for (u, r, b) in self.seen_at_last[-MAX_SEEN_AT_TIMESTAMP:]
            ],
            'history': self.history[-MAX_HISTORY_ENTRIES:],
        }

    def _record_transition(
        self,
        *,
        boundary_epoch,
        source,
        approximate,
        detected_at_epoch,
        window_earliest_epoch=None,
        uncertainty_seconds=None,
        resets_at=None,
    ):
        # Never move the active boundary backwards.
        if self.period_start_epoch is not None and boundary_epoch < self.period_start_epoch:
            return
        self.period_start_epoch = float(boundary_epoch)
        self.source = source
        self.approximate = bool(approximate)
        self.detected_at_epoch = float(detected_at_epoch) if detected_at_epoch is not None else None
        self.window_earliest_epoch = (
            float(window_earliest_epoch) if window_earliest_epoch is not None else None
        )
        self.uncertainty_seconds = (
            float(uncertainty_seconds) if uncertainty_seconds is not None else None
        )
        if resets_at is not None:
            self.resets_at = float(resets_at)
        self.history.append(
            {
                'period_start': format_epoch_utc(self.period_start_epoch),
                'period_start_epoch': self.period_start_epoch,
                'source': self.source,
                'approximate': self.approximate,
                'detected_at': format_epoch_utc(self.detected_at_epoch),
                'window_earliest_at': format_epoch_utc(self.window_earliest_epoch),
                'resets_at': self.resets_at,
            }
        )
        if len(self.history) > MAX_HISTORY_ENTRIES:
            self.history = self.history[-MAX_HISTORY_ENTRIES:]

    def _process_sample(self, curr):
        # Ignore samples from a different quota bucket so bucket changes never trigger false resets.
        if self.last_sample is None and self.period_start_epoch is None:
            self.bucket = curr['bucket']
        elif curr['bucket'] != self.bucket:
            return

        if self.last_sample is not None:
            last_at = self.last_sample['at']
            if curr['at'] < last_at:
                # Ignore stale/out-of-order samples older than the latest processed sample.
                return
            sig = (curr['used'], curr['reset'], curr['bucket'])
            if curr['at'] == last_at:
                if sig in self.seen_at_last:
                    return
                self.seen_at_last.append(sig)
            else:
                self.seen_at_last = [sig]
        else:
            self.seen_at_last = [(curr['used'], curr['reset'], curr['bucket'])]

        # 1. Exact service reset timestamp if explicitly provided by the sample
        if curr.get('exact_reset') is not None:
            exact_ep = curr['exact_reset']
            if self.period_start_epoch is None or exact_ep > self.period_start_epoch:
                self._record_transition(
                    boundary_epoch=exact_ep,
                    source='scheduled',
                    approximate=False,
                    detected_at_epoch=curr['at'],
                    window_earliest_epoch=None,
                    uncertainty_seconds=0.0,
                    resets_at=curr['reset'],
                )
            self.last_sample = curr
            if curr['reset'] is not None:
                self.resets_at = curr['reset']
            return

        # 2. First sample ever processed
        if self.last_sample is None:
            self.last_sample = curr
            if curr['reset'] is not None:
                self.resets_at = curr['reset']
            if self.period_start_epoch is None and curr['reset'] is not None and curr['reset'] > curr['at']:
                inferred = min(curr['at'], curr['reset'] - WEEKLY_WINDOW_SECONDS)
                self._record_transition(
                    boundary_epoch=inferred,
                    source='scheduled_inference',
                    approximate=True,
                    detected_at_epoch=curr['at'],
                    window_earliest_epoch=None,
                    uncertainty_seconds=None,
                    resets_at=curr['reset'],
                )
            return

        # Ignore late samples from an older quota cycle whose reset deadline already passed
        # or belongs to a pre-reset cycle.
        if curr['reset'] is not None:
            if self.resets_at is not None and (self.resets_at - curr['reset']) > 86400.0:
                return
            if (
                self.period_start_epoch is not None
                and curr['reset'] <= self.period_start_epoch + RESET_JITTER_SECONDS
            ):
                return
            if (
                self.resets_at is not None
                and curr['reset'] < self.resets_at - 3600.0
                and self.source in ('scheduled', 'early_reset')
                and not is_meaningful_usage_drop(self.last_sample['used'], curr['used'])
            ):
                return
        elif self.resets_at is not None:
            return

        # 3. Compare against previous sample in the same bucket
        prev = self.last_sample
        t_prev = prev['at']
        used_prev = prev['used']
        reset_prev = prev['reset'] if prev['reset'] is not None else self.resets_at

        t_curr = curr['at']
        used_curr = curr['used']
        reset_curr = curr['reset']

        # Check A: Scheduled weekly rollover
        # Previous reset deadline was reached/passed and resets_at moved to a new window (or usage reset at/after deadline).
        scheduled_rollover = False
        if reset_prev is not None and t_curr >= reset_prev - RESET_JITTER_SECONDS:
            if reset_curr is not None and reset_curr > reset_prev + RESET_JITTER_SECONDS:
                scheduled_rollover = True
            elif is_meaningful_usage_drop(used_prev, used_curr):
                scheduled_rollover = True

        if scheduled_rollover:
            # If multiple weekly windows elapsed while offline, use the latest window start (reset_curr - 7d);
            # otherwise use the known previous scheduled reset deadline (reset_prev).
            if (
                reset_curr is not None
                and (reset_curr - WEEKLY_WINDOW_SECONDS) > reset_prev + RESET_JITTER_SECONDS
                and (reset_curr - WEEKLY_WINDOW_SECONDS) <= t_curr + RESET_JITTER_SECONDS
            ):
                boundary = min(t_curr, reset_curr - WEEKLY_WINDOW_SECONDS)
                is_approx = True
                uncertainty = max(0.0, t_curr - boundary)
            else:
                boundary = min(t_curr, float(reset_prev))
                is_approx = False
                uncertainty = 0.0

            self._record_transition(
                boundary_epoch=boundary,
                source='scheduled',
                approximate=is_approx,
                detected_at_epoch=t_curr,
                window_earliest_epoch=None,
                uncertainty_seconds=uncertainty,
                resets_at=reset_curr if reset_curr is not None else self.resets_at,
            )
            self.last_sample = curr
            if reset_curr is not None:
                self.resets_at = reset_curr
            return

        # Check B: Early / ad-hoc / coupon quota reset (including when resets_at is unchanged)
        early_drop = is_meaningful_usage_drop(used_prev, used_curr)
        early_deadline_shift = (
            reset_prev is not None
            and reset_curr is not None
            and reset_curr > reset_prev + 3600.0
            and t_curr < reset_prev - RESET_JITTER_SECONDS
            and used_curr + 2.0 < used_prev
        )
        if early_drop or early_deadline_shift:
            self._record_transition(
                boundary_epoch=t_curr,
                source='early_reset',
                approximate=True,
                detected_at_epoch=t_curr,
                window_earliest_epoch=t_prev,
                uncertainty_seconds=max(0.0, t_curr - t_prev),
                resets_at=reset_curr if reset_curr is not None else self.resets_at,
            )
            self.last_sample = curr
            if reset_curr is not None:
                self.resets_at = reset_curr
            return

        # Check C: No reset transition, but if origin was still unavailable and a valid resets_at is now present
        if self.period_start_epoch is None and reset_curr is not None and reset_curr > t_curr:
            inferred = min(t_curr, reset_curr - WEEKLY_WINDOW_SECONDS)
            self._record_transition(
                boundary_epoch=inferred,
                source='scheduled_inference',
                approximate=True,
                detected_at_epoch=t_curr,
                window_earliest_epoch=None,
                uncertainty_seconds=None,
                resets_at=reset_curr,
            )

        self.last_sample = curr
        if reset_curr is not None:
            self.resets_at = reset_curr

    def update(self, samples=(), limits=None, now=None):
        """Process chronological quota samples and optional live limits, persisting any change."""
        now_epoch = parse_timestamp_epoch(now) if now is not None else time.time()
        if now_epoch is None:
            now_epoch = time.time()

        before_state = self._serialize()

        cleaned = []
        for raw in samples or ():
            item = extract_weekly_sample(raw, now_epoch=now_epoch)
            if item is not None:
                cleaned.append(item)

        live_item = sample_from_limits(limits, now_epoch=now_epoch)
        if live_item is not None:
            cleaned.append(live_item)

        # Stable sort by sample timestamp so equal-timestamp samples keep their observation order.
        cleaned.sort(key=lambda s: s['at'])

        for item in cleaned:
            self._process_sample(item)

        after_state = self._serialize()
        if after_state != before_state and (
            self.last_sample is not None or self.period_start_epoch is not None
        ):
            self.writer(self.path, after_state)

        return self.current_period(now=now_epoch)

    def current_period(self, now=None):
        now_epoch = parse_timestamp_epoch(now) if now is not None else time.time()
        if now_epoch is None:
            now_epoch = time.time()
        available = self.period_start_epoch is not None
        start_iso = format_epoch_utc(self.period_start_epoch) if available else None
        return {
            'available': available,
            'start_at': start_iso,
            'period_start': start_iso,
            'start_epoch': self.period_start_epoch if available else None,
            'period_start_epoch': self.period_start_epoch if available else None,
            'end_at': format_epoch_utc(now_epoch),
            'end_epoch': now_epoch,
            'source': self.source if available else 'unavailable',
            'approximate': self.approximate if available else True,
            'detected_at': format_epoch_utc(self.detected_at_epoch) if available else None,
            'detected_at_epoch': self.detected_at_epoch if available else None,
            'window_earliest_at': format_epoch_utc(self.window_earliest_epoch) if available else None,
            'window_earliest_epoch': self.window_earliest_epoch if available else None,
            'uncertainty_seconds': self.uncertainty_seconds if available else None,
            'resets_at': self.resets_at,
            'bucket': self.bucket,
        }
