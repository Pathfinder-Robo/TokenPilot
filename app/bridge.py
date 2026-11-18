#!/usr/bin/env python3
"""Local-only usage collector and routing preference bridge for TokenPilot.
TokenPilot 的本地用量收集器与路由偏好通信桥接服务。

Reads numerical usage records, never reads auth.json or Gemini credentials.
Only Codex's own CLI handles authentication for the read-only limits endpoint.
"""
import argparse
import atexit
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from datetime import timezone as _tz

    def ZoneInfo(_name: str):  # type: ignore[misc]
        return _tz.utc
from forecasting import QuotaHistory, log_sample, current_sample, build_forecast, epoch
from nextreset import fetch_summary, summary_is_stale, URL as NEXTRESET_URL

from reset_tracker import ResetTracker
from chat_attribution import (
    ChatAttributionResolver,
    OWNER_CONFLICT,
    build_chat_usage,
    extract_uuid_from_filename,
    merge_owner,
    normalize_uuid,
    put_chat_key,
)
from pricing import (
    CACHED_INPUT_USD_PER_MILLION,
    OUTPUT_USD_PER_MILLION,
    PRICING_AS_OF_DATE,
    PRICING_CURRENCY,
    PRICING_INTRO_THROUGH_DATE,
    PRICING_MODEL,
    PRICING_POST_INTRO_EFFECTIVE_DATE,
    PRICING_POST_INTRO_MULTIPLIER,
    PRICING_REFERENCE_ID,
    PRICING_REFERENCE_VERSION,
    PRICING_SOURCE_URL,
    PRICING_TIER,
    UNCACHED_INPUT_USD_PER_MILLION,
    enrich_usage_with_pricing,
    estimate_usage_usd,
    event_has_valid_categories,
    format_usd,
    is_reference_outdated,
    pricing_reference_metadata,
    provider_split_ratio,
)
from device_usage import (
    DeviceUsageAggregator,
    coerce_now,
    extract_codex_counts,
    extract_gemini_counts,
    format_utc,
    has_positive_counts,
    hash_codex_fallback_id,
    hash_event_id,
    parse_iso_timestamp,
    sum_event_counts,
)

CHILDREN = set()
CHILD_LOCK = threading.Lock()


@atexit.register
def stop_children():
    with CHILD_LOCK:
        for child in list(CHILDREN):
            if child.poll() is None:
                child.terminate()


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else 0


def on_day(timestamp, day, tz):
    try:
        return datetime.fromisoformat(timestamp.replace('Z', '+00:00')).astimezone(tz).date() == day
    except (TypeError, ValueError, AttributeError):
        return False


def in_range_timestamp(timestamp, start_epoch, end_epoch):
    try:
        parsed_dt, ep = parse_iso_timestamp(timestamp)
    except ValueError:
        return False, None, None
    iso_utc = format_utc(parsed_dt)
    return (start_epoch <= ep <= end_epoch), iso_utc, ep


def atomic_json(path, value):
    path = Path(path)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Inspect configuration symlinks before writing')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.routing-float-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_routing(home):
    path = Path(home) / 'argon/routing.json'
    if not path.exists():
        return {'gemini_percent': 75}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Routing preference must be an object')
    percent = value.get('gemini_percent')
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError('Routing preference must be an integer from 0 to 100')
    return value


def set_routing(home, percent):
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError('Routing preference must be an integer from 0 to 100')
    value = {'version': 1, 'gemini_percent': percent, 'updated_at': utc_now(),
             'meaning': 'Delegation preference for new coding tasks, not token or spending allocation'}
    atomic_json(Path(home) / 'argon/routing.json', value)
    return value


def empty_usage(available=False):
    return enrich_usage_with_pricing({
        'total_tokens': 0,
        'cached_tokens': 0,
        'input_tokens': 0,
        'output_tokens': 0,
        'available': available,
    })


def describe_run(status):
    if status == 'completed':
        return 'completed', 'Latest Gemini run completed', False
    if status == 'running':
        return 'running', 'Gemini running · waiting for reported usage', False
    if status in ('cancelled', 'interrupted'):
        return 'paused', 'Gemini stopped by supervisor · usage recorded', False
    if status in ('token_limit', 'turn_limit', 'time_limit'):
        reason = status.replace('_', ' ')
        return 'paused', f'Gemini stopped at {reason} · usage recorded', False
    return 'error', 'Gemini request failed · no newer usage reported', True


def add_usage(total, usage, gemini=False):
    if gemini:
        total['input_tokens'] += number(usage.get('total_input_tokens'))
        total['cached_tokens'] += number(usage.get('total_cached_tokens'))
        total['output_tokens'] += number(usage.get('total_output_tokens')) + number(usage.get('total_thought_tokens'))
    else:
        total['input_tokens'] += number(usage.get('input_tokens'))
        total['cached_tokens'] += number(usage.get('cached_input_tokens'))
        total['output_tokens'] += number(usage.get('output_tokens'))
    total['total_tokens'] += number(usage.get('total_tokens'))


class CodexUsage:
    """Tail files and retain usage events and their originating thread identifiers."""
    def __init__(self, home):
        self.home = Path(home)
        self.files = {}
        self.chat_attribution = ChatAttributionResolver(home)

    def _scan_files(self):
        paths = []
        for folder in ('sessions', 'archived_sessions'):
            root = self.home / folder
            if root.is_dir():
                paths.extend(sorted(root.rglob('*.jsonl')))
        live = set()
        for path in paths:
            key = str(path)
            live.add(key)
            try:
                stat = path.stat()
                state = self.files.get(key)
                if not state or state['inode'] != stat.st_ino or stat.st_size < state['offset']:
                    state = {
                        'inode': stat.st_ino,
                        'offset': 0,
                        'records': {},
                        'owners': {},
                        'thread_id': extract_uuid_from_filename(path.name),
                        'unidentified': [],
                        'fallback': [],
                        'fallback_ordinals': [],
                        'previous': {},
                        'quota': [],
                    }
                    self.files[key] = state
                if stat.st_size == state['offset']:
                    continue
                with path.open('rb') as stream:
                    stream.seek(state['offset'])
                    while True:
                        start = stream.tell()
                        line = stream.readline()
                        if not line:
                            break
                        if not line.endswith(b'\n'):
                            stream.seek(start)
                            break
                        state['offset'] = stream.tell()
                        # Do not decode chat bodies/tool output at all.
                        if not any(marker in line for marker in (b'"token_usage_record"', b'"token_count"')) and not (
                            start == 0 and b'"session_meta"' in line
                        ):
                            continue
                        try:
                            record = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        payload = record.get('payload') or {}
                        if not isinstance(payload, dict):
                            continue
                        if record.get('type') == 'session_meta':
                            state['thread_id'] = normalize_uuid(payload.get('id')) or state['thread_id']
                        elif record.get('type') == 'token_usage_record' and isinstance(payload.get('usage'), dict):
                            raw_usage = payload['usage']
                            clean_u = {
                                field: number(raw_usage.get(field))
                                for field in ('total_tokens', 'input_tokens', 'cached_input_tokens', 'output_tokens')
                            }
                            raw_resp = payload.get('response_id')
                            if isinstance(raw_resp, str) and raw_resp.strip():
                                resp = raw_resp.strip()
                                state['records'][resp] = (record.get('timestamp'), clean_u)
                                owner = normalize_uuid(payload.get('thread_id')) or state['thread_id']
                                state['owners'][resp] = merge_owner(state['owners'].get(resp), owner)
                            else:
                                state['unidentified'].append((record.get('timestamp'), clean_u))
                        elif record.get('type') == 'event_msg' and payload.get('type') == 'token_count':
                            quota = log_sample(record.get('timestamp'), payload.get('rate_limits'))
                            if quota:
                                state['quota'].append(quota)
                            info = payload.get('info') or {}
                            current = info.get('total_token_usage')
                            if not isinstance(current, dict):
                                continue
                            previous = state['previous']
                            delta = {field: max(0, number(current.get(field)) - number(previous.get(field)))
                                     for field in ('total_tokens', 'input_tokens', 'cached_input_tokens', 'output_tokens')}
                            state['previous'] = {
                                field: number(current.get(field))
                                for field in ('total_tokens', 'input_tokens', 'cached_input_tokens', 'output_tokens')
                            }
                            raw_ord = record.get('ordinal')
                            ordinal = (
                                raw_ord
                                if type(raw_ord) is int and raw_ord >= 0
                                else len(state['fallback'])
                            )
                            state['fallback'].append((record.get('timestamp'), delta))
                            state['fallback_ordinals'].append(ordinal)
            except OSError:
                continue
        self.files = {key: state for key, state in self.files.items() if key in live}

    def collect_events(self, day, tz):
        self._scan_files()
        primary_files = {Path(key).name for key, state in self.files.items() if state['records']}
        records = {}
        fallback_files = set()
        events_by_id = {}
        has_valid_history = False
        unidentified_today = 0

        for key in sorted(self.files.keys()):
            state = self.files[key]
            for stamp, usage in state.get('unidentified', ()):
                if on_day(stamp, day, tz) and has_positive_counts(extract_codex_counts(usage)):
                    unidentified_today += 1
            if state['records']:
                has_valid_history = True
                records.update(state['records'])  # A response copied/archived twice counts once.
            elif state['fallback'] and Path(key).name not in primary_files and Path(key).name not in fallback_files:
                has_valid_history = True
                fname = Path(key).name
                fallback_files.add(fname)
                ordinals = state.get('fallback_ordinals') or list(range(len(state['fallback'])))
                for (stamp, usage), ordinal in zip(state['fallback'], ordinals):
                    if on_day(stamp, day, tz) and isinstance(stamp, str) and stamp.strip():
                        counts = extract_codex_counts(usage)
                        eid = hash_codex_fallback_id(fname, stamp, ordinal, counts)
                        events_by_id[eid] = {'id': eid, **counts}

        for resp_id in sorted(records.keys()):
            stamp, usage = records[resp_id]
            if on_day(stamp, day, tz):
                counts = extract_codex_counts(usage)
                eid = hash_event_id('codex', resp_id)
                events_by_id[eid] = {'id': eid, **counts}

        events = [events_by_id[eid] for eid in sorted(events_by_id.keys())]
        summed = sum_event_counts(events)
        available = has_valid_history
        partial = unidentified_today > 0
        if partial and not events:
            available = False

        metadata = {
            'available': available,
            'partial': partial,
            'unidentified_events': unidentified_today,
        }
        return {
            'available': available,
            'partial': partial,
            'unidentified_events': unidentified_today,
            'total_tokens': summed['total_tokens'],
            'cached_tokens': summed['cached_tokens'],
            'input_tokens': summed['input_tokens'],
            'output_tokens': summed['output_tokens'],
            'events': events,
            'metadata': metadata,
        }

    def collect_events_in_range(self, start_epoch, end_epoch, *, include_timestamps=False):
        self._scan_files()
        self.chat_attribution.refresh()
        primary_files = {Path(key).name for key, state in self.files.items() if state['records']}
        records = {}
        owners = {}
        fallback_files = set()
        events_by_id = {}
        has_valid_history = False
        unidentified_count = 0
        unidentified_timestamps = []

        for key in sorted(self.files.keys()):
            state = self.files[key]
            for stamp, usage in state.get('unidentified', ()):
                in_rng, iso_ts, _ = in_range_timestamp(stamp, start_epoch, end_epoch)
                if in_rng and has_positive_counts(extract_codex_counts(usage)):
                    unidentified_count += 1
                    unidentified_timestamps.append(iso_ts)
            if state['records']:
                has_valid_history = True
                records.update(state['records'])
                for resp, owner in state['owners'].items():
                    chat_key = OWNER_CONFLICT if owner is OWNER_CONFLICT else self.chat_attribution.chat_key(owner)
                    owners[resp] = merge_owner(owners.get(resp), chat_key)
            elif state['fallback'] and Path(key).name not in primary_files and Path(key).name not in fallback_files:
                has_valid_history = True
                fname = Path(key).name
                fallback_files.add(fname)
                chat_key = self.chat_attribution.chat_key(state['thread_id'])
                ordinals = state.get('fallback_ordinals') or list(range(len(state['fallback'])))
                for (stamp, usage), ordinal in zip(state['fallback'], ordinals):
                    in_rng, iso_ts, _ = in_range_timestamp(stamp, start_epoch, end_epoch)
                    if in_rng:
                        counts = extract_codex_counts(usage)
                        eid = hash_codex_fallback_id(fname, stamp, ordinal, counts)
                        item = put_chat_key({'id': eid, **counts}, chat_key)
                        if include_timestamps:
                            item['timestamp'] = iso_ts
                        events_by_id[eid] = item

        for resp_id in sorted(records.keys()):
            stamp, usage = records[resp_id]
            in_rng, iso_ts, _ = in_range_timestamp(stamp, start_epoch, end_epoch)
            if in_rng:
                counts = extract_codex_counts(usage)
                eid = hash_event_id('codex', resp_id)
                item = put_chat_key({'id': eid, **counts}, owners.get(resp_id))
                if include_timestamps:
                    item['timestamp'] = iso_ts
                events_by_id[eid] = item

        events = [events_by_id[eid] for eid in sorted(events_by_id.keys())]
        summed = sum_event_counts(events)
        available = has_valid_history
        partial = unidentified_count > 0
        if partial and not events:
            available = False

        metadata = {
            'available': available,
            'partial': partial,
            'unidentified_events': unidentified_count,
        }
        if include_timestamps:
            metadata['unidentified_timestamps'] = unidentified_timestamps

        result = {
            'available': available,
            'partial': partial,
            'unidentified_events': unidentified_count,
            'total_tokens': summed['total_tokens'],
            'cached_tokens': summed['cached_tokens'],
            'input_tokens': summed['input_tokens'],
            'output_tokens': summed['output_tokens'],
            'events': events,
            'metadata': metadata,
        }
        if include_timestamps:
            result['unidentified_timestamps'] = unidentified_timestamps
        return result

    def collect(self, day, tz):
        exported = self.collect_events(day, tz)
        return self._exported_to_total(exported)

    def collect_in_range(self, start_epoch, end_epoch):
        exported = self.collect_events_in_range(start_epoch, end_epoch, include_timestamps=False)
        return self._exported_to_total(exported)

    @staticmethod
    def _exported_to_total(exported):
        total = {
            'total_tokens': exported['total_tokens'],
            'cached_tokens': exported['cached_tokens'],
            'input_tokens': exported['input_tokens'],
            'output_tokens': exported['output_tokens'],
            'available': exported['available'],
        }
        if exported['partial']:
            total['partial'] = True
        events = exported.get('events') or ()
        categories_valid = all(event_has_valid_categories(ev) for ev in events)
        return enrich_usage_with_pricing(total, categories_valid=categories_valid)

    def recent_activity(self, now):
        self._scan_files()
        records = {}
        fallback_files = set()
        fallback = []
        quotas = []
        primary_files = {Path(key).name for key, state in self.files.items() if state['records']}
        for key, state in self.files.items():
            records.update(state['records'])
            quotas.extend(state.get('quota', []))
            name = Path(key).name
            if not state['records'] and name not in primary_files and name not in fallback_files:
                fallback_files.add(name)
                fallback.extend(state['fallback'])
        tokens = 0
        for stamp, usage in [*records.values(), *fallback]:
            at = epoch(stamp)
            if at is not None and now - 900 < at <= now:
                tokens += number(usage.get('total_tokens'))
        return tokens, quotas


def json_lines(path):
    try:
        with Path(path).open() as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    if isinstance(record, dict):
                        yield record
                except ValueError:
                    pass
    except OSError:
        return


def _clean_gemini_usage_dict(raw_usage):
    if not isinstance(raw_usage, dict):
        return None
    return {
        k: number(raw_usage.get(k))
        for k in (
            'total_tokens',
            'total_input_tokens',
            'total_cached_tokens',
            'total_output_tokens',
            'total_thought_tokens',
            'input_tokens',
            'cached_tokens',
            'output_tokens',
        )
        if k in raw_usage
    }


class GeminiUsage:
    def __init__(self, home):
        self.home = Path(home)
        self.cache = {}
        self.summary_cache = {}
        self.chat_attribution = ChatAttributionResolver(home)

    def collect_events(self, day, tz):
        summaries = sorted((self.home / 'argon/usage-cache').glob('*.json'))
        if summaries:
            return self.collect_summaries_events(summaries, day, tz)
        return self._collect_registered_dirs_events(day, tz)

    def collect_events_in_range(self, start_epoch, end_epoch, *, include_timestamps=False):
        summaries = sorted((self.home / 'argon/usage-cache').glob('*.json'))
        if summaries:
            return self.collect_summaries_events_in_range(
                summaries, start_epoch, end_epoch, include_timestamps=include_timestamps
            )
        return self._collect_registered_dirs_events_in_range(
            start_epoch, end_epoch, include_timestamps=include_timestamps
        )

    def _collect_registered_dirs_events(self, day, tz):
        directories = {}
        outcome_usages = {}
        for name in ('outcomes.jsonl', 'runs.jsonl'):
            for record in json_lines(self.home / 'argon' / name):
                if isinstance(record.get('result_dir'), str) and record['result_dir'].strip():
                    directory = Path(record['result_dir']).expanduser().resolve()
                    directories[directory] = record.get('started_at') or record.get('date')
                    if isinstance(record.get('usage'), dict):
                        outcome_usages[directory] = _clean_gemini_usage_dict(record['usage'])
        status = 'idle'
        status_message = 'No tracked Gemini runs today'
        last_run_at = None
        failed_runs_today = 0
        last_usage_at = None
        seen = set()
        events_by_id = {}
        unidentified_today = 0

        for directory, registered_at in directories.items():
            try:
                result = json.loads((directory / 'result.json').read_text())
            except (OSError, ValueError):
                result = None
            if on_day(registered_at, day, tz):
                state = result.get('status') if isinstance(result, dict) else 'running'
                display_status, display_message, failed = describe_run(state)
                if failed:
                    failed_runs_today += 1
                if last_run_at is None or (isinstance(registered_at, str) and registered_at > last_run_at):
                    last_run_at = registered_at
                    status = display_status
                    status_message = display_message
            paths = sorted(directory.glob('interaction-*.json'))
            if paths:
                for path in paths:
                    try:
                        stat = path.stat()
                        key = str(path)
                        cached = self.cache.get(key)
                        if not cached or cached[0] != stat.st_mtime_ns or cached[1] != stat.st_size:
                            value = json.loads(path.read_text())
                            if not isinstance(value, dict):
                                continue
                            clean_interaction = {
                                'id': value.get('id'),
                                'created': value.get('created'),
                                'usage': _clean_gemini_usage_dict(value.get('usage')),
                            }
                            cached = (stat.st_mtime_ns, stat.st_size, clean_interaction)
                            self.cache[key] = cached
                        interaction = cached[2]
                        raw_id = interaction.get('id')
                        valid_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else None
                        stamp = interaction.get('created') or registered_at
                        usage_dict = interaction.get('usage')
                        counts = extract_gemini_counts(usage_dict or {})
                        if not valid_id:
                            if on_day(stamp, day, tz) and isinstance(usage_dict, dict) and has_positive_counts(counts):
                                unidentified_today += 1
                            continue
                        if valid_id in seen:
                            continue
                        seen.add(valid_id)
                        if on_day(stamp, day, tz) and isinstance(usage_dict, dict):
                            eid = hash_event_id('gemini', valid_id)
                            events_by_id[eid] = {'id': eid, **counts}
                            if last_usage_at is None or (isinstance(stamp, str) and stamp > last_usage_at):
                                last_usage_at = stamp
                    except (OSError, ValueError, TypeError):
                        continue
            else:
                if on_day(registered_at, day, tz):
                    raw_res_usage = (
                        result.get('usage')
                        if isinstance(result, dict) and isinstance(result.get('usage'), dict)
                        else outcome_usages.get(directory)
                    )
                    if isinstance(raw_res_usage, dict) and has_positive_counts(extract_gemini_counts(raw_res_usage)):
                        unidentified_today += 1

        return self._finalize_gemini_events(
            events_by_id,
            status=status,
            status_message=status_message,
            last_run_at=last_run_at,
            failed_runs_today=failed_runs_today,
            last_usage_at=last_usage_at,
            unidentified_today=unidentified_today,
        )

    def _collect_registered_dirs_events_in_range(self, start_epoch, end_epoch, *, include_timestamps=False):
        self.chat_attribution.refresh()
        directories = {}
        outcome_usages = {}
        run_owners = {}
        for name in ('outcomes.jsonl', 'runs.jsonl'):
            for record in json_lines(self.home / 'argon' / name):
                if isinstance(record.get('result_dir'), str) and record['result_dir'].strip():
                    directory = Path(record['result_dir']).expanduser().resolve()
                    directories[directory] = record.get('started_at') or record.get('date')
                    chat = self.chat_attribution.chat_key(record.get('thread_id'))
                    if chat:
                        run_owners[directory] = merge_owner(run_owners.get(directory), chat)
                    if isinstance(record.get('usage'), dict):
                        outcome_usages[directory] = _clean_gemini_usage_dict(record['usage'])
        status = 'idle'
        status_message = 'No tracked Gemini runs today'
        last_run_at = None
        last_run_ep = None
        failed_runs_today = 0
        last_usage_at = None
        last_usage_ep = None
        seen = set()
        events_by_id = {}
        owners = {}
        unidentified_count = 0
        unidentified_timestamps = []

        for directory, registered_at in directories.items():
            try:
                result = json.loads((directory / 'result.json').read_text())
            except (OSError, ValueError):
                result = None
            reg_in_rng, reg_iso, reg_ep = in_range_timestamp(registered_at, start_epoch, end_epoch)
            if reg_in_rng:
                state = result.get('status') if isinstance(result, dict) else 'running'
                display_status, display_message, failed = describe_run(state)
                if failed:
                    failed_runs_today += 1
                if last_run_ep is None or reg_ep > last_run_ep:
                    last_run_ep = reg_ep
                    last_run_at = reg_iso
                    status = display_status
                    status_message = display_message
            paths = sorted(directory.glob('interaction-*.json'))
            if paths:
                for path in paths:
                    try:
                        stat = path.stat()
                        key = str(path)
                        cached = self.cache.get(key)
                        if not cached or cached[0] != stat.st_mtime_ns or cached[1] != stat.st_size:
                            value = json.loads(path.read_text())
                            if not isinstance(value, dict):
                                continue
                            clean_interaction = {
                                'id': value.get('id'),
                                'created': value.get('created'),
                                'usage': _clean_gemini_usage_dict(value.get('usage')),
                            }
                            cached = (stat.st_mtime_ns, stat.st_size, clean_interaction)
                            self.cache[key] = cached
                        interaction = cached[2]
                        raw_id = interaction.get('id')
                        valid_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else None
                        stamp = interaction.get('created') or registered_at
                        usage_dict = interaction.get('usage')
                        counts = extract_gemini_counts(usage_dict or {})
                        in_rng, iso_ts, ts_ep = in_range_timestamp(stamp, start_epoch, end_epoch)
                        if not valid_id:
                            if in_rng and isinstance(usage_dict, dict) and has_positive_counts(counts):
                                unidentified_count += 1
                                unidentified_timestamps.append(iso_ts)
                            continue
                        eid = hash_event_id('gemini', valid_id)
                        owners[eid] = merge_owner(owners.get(eid), run_owners.get(directory))
                        if valid_id in seen:
                            continue
                        seen.add(valid_id)
                        if in_rng and isinstance(usage_dict, dict):
                            item = {'id': eid, **counts}
                            if include_timestamps:
                                item['timestamp'] = iso_ts
                            events_by_id[eid] = item
                            if last_usage_ep is None or ts_ep > last_usage_ep:
                                last_usage_ep = ts_ep
                                last_usage_at = iso_ts
                            if last_run_ep is None:
                                state = result.get('status') if isinstance(result, dict) else 'running'
                                display_status, display_message, _ = describe_run(state)
                                status = display_status
                                status_message = display_message
                                last_run_at = iso_ts
                                last_run_ep = ts_ep
                    except (OSError, ValueError, TypeError):
                        continue
            else:
                if reg_in_rng:
                    raw_res_usage = (
                        result.get('usage')
                        if isinstance(result, dict) and isinstance(result.get('usage'), dict)
                        else outcome_usages.get(directory)
                    )
                    if isinstance(raw_res_usage, dict) and has_positive_counts(extract_gemini_counts(raw_res_usage)):
                        unidentified_count += 1
                        unidentified_timestamps.append(reg_iso)

        return self._finalize_gemini_events(
            events_by_id,
            status=status,
            status_message=status_message,
            last_run_at=last_run_at,
            failed_runs_today=failed_runs_today,
            last_usage_at=last_usage_at,
            unidentified_today=unidentified_count,
            unidentified_timestamps=unidentified_timestamps if include_timestamps else None,
            owners=owners,
        )

    def collect_summaries_events(self, paths, day, tz):
        status = 'idle'
        status_message = 'No tracked Gemini runs today'
        last_run_at = None
        failed_runs_today = 0
        last_usage_at = None
        seen = set()
        events_by_id = {}
        unidentified_today = 0
        live_keys = set()

        for path in sorted(paths):
            try:
                stat = path.stat()
                key = str(path)
                live_keys.add(key)
                cached = self.summary_cache.get(key)
                if not cached or cached[0] != stat.st_mtime_ns or cached[1] != stat.st_size:
                    raw_state = json.loads(path.read_text())
                    if not isinstance(raw_state, dict):
                        continue
                    raw_interactions = raw_state.get('interactions')
                    clean_interactions = None
                    if isinstance(raw_interactions, dict):
                        clean_interactions = {}
                        for ikey, ival in raw_interactions.items():
                            if isinstance(ival, dict):
                                clean_interactions[ikey] = {
                                    'id': ival.get('id'),
                                    'created': ival.get('created'),
                                    'usage': _clean_gemini_usage_dict(ival.get('usage')),
                                }
                            else:
                                clean_interactions[ikey] = None
                    clean_state = {
                        'started_at': raw_state.get('started_at'),
                        'status': raw_state.get('status', 'running'),
                        'usage': _clean_gemini_usage_dict(raw_state.get('usage')),
                        'interactions': clean_interactions,
                    }
                    cached = (stat.st_mtime_ns, stat.st_size, clean_state)
                    self.summary_cache[key] = cached
                state = cached[2]
                stamp = state.get('started_at')
                if on_day(stamp, day, tz):
                    run_status = state.get('status', 'running')
                    display_status, display_message, failed = describe_run(run_status)
                    failed_runs_today += int(failed)
                    if last_run_at is None or (isinstance(stamp, str) and stamp > last_run_at):
                        last_run_at = stamp
                        status = display_status
                        status_message = display_message
                interactions = state.get('interactions') or {}
                if not interactions:
                    if on_day(stamp, day, tz):
                        summary_counts = extract_gemini_counts(state.get('usage') or {})
                        if has_positive_counts(summary_counts):
                            unidentified_today += 1
                    continue
                for identity, interaction in interactions.items():
                    if not isinstance(interaction, dict):
                        continue
                    raw_id = interaction.get('id')
                    valid_id = (
                        raw_id.strip()
                        if isinstance(raw_id, str) and raw_id.strip()
                        else (identity.strip() if isinstance(identity, str) and identity.strip() else None)
                    )
                    created = interaction.get('created')
                    usage_dict = interaction.get('usage') or {}
                    counts = extract_gemini_counts(usage_dict)
                    if not valid_id:
                        if on_day(created or stamp, day, tz) and has_positive_counts(counts):
                            unidentified_today += 1
                        continue
                    if valid_id in seen:
                        continue
                    seen.add(valid_id)
                    if on_day(created, day, tz):
                        eid = hash_event_id('gemini', valid_id)
                        events_by_id[eid] = {'id': eid, **counts}
                        if last_usage_at is None or (isinstance(created, str) and created > last_usage_at):
                            last_usage_at = created
            except (OSError, ValueError, TypeError, AttributeError):
                continue

        self.summary_cache = {k: v for k, v in self.summary_cache.items() if k in live_keys}
        return self._finalize_gemini_events(
            events_by_id,
            status=status,
            status_message=status_message,
            last_run_at=last_run_at,
            failed_runs_today=failed_runs_today,
            last_usage_at=last_usage_at,
            unidentified_today=unidentified_today,
        )

    def collect_summaries_events_in_range(self, paths, start_epoch, end_epoch, *, include_timestamps=False):
        self.chat_attribution.refresh()
        status = 'idle'
        status_message = 'No tracked Gemini runs today'
        last_run_at = None
        last_run_ep = None
        failed_runs_today = 0
        last_usage_at = None
        last_usage_ep = None
        seen = set()
        events_by_id = {}
        owners = {}
        unidentified_count = 0
        unidentified_timestamps = []
        live_keys = set()

        for path in sorted(paths):
            try:
                stat = path.stat()
                key = str(path)
                live_keys.add(key)
                cached = self.summary_cache.get(key)
                if not cached or cached[0] != stat.st_mtime_ns or cached[1] != stat.st_size:
                    raw_state = json.loads(path.read_text())
                    if not isinstance(raw_state, dict):
                        continue
                    raw_interactions = raw_state.get('interactions')
                    clean_interactions = None
                    if isinstance(raw_interactions, dict):
                        clean_interactions = {}
                        for ikey, ival in raw_interactions.items():
                            if isinstance(ival, dict):
                                clean_interactions[ikey] = {
                                    'id': ival.get('id'),
                                    'created': ival.get('created'),
                                    'usage': _clean_gemini_usage_dict(ival.get('usage')),
                                }
                            else:
                                clean_interactions[ikey] = None
                    clean_state = {
                        'started_at': raw_state.get('started_at'),
                        'status': raw_state.get('status', 'running'),
                        'usage': _clean_gemini_usage_dict(raw_state.get('usage')),
                        'interactions': clean_interactions,
                        'thread_id': normalize_uuid(raw_state.get('thread_id')),
                    }
                    cached = (stat.st_mtime_ns, stat.st_size, clean_state)
                    self.summary_cache[key] = cached
                state = cached[2]
                chat_key = self.chat_attribution.chat_key(state.get('thread_id'))
                stamp = state.get('started_at')
                run_in_rng, run_iso, run_ep = in_range_timestamp(stamp, start_epoch, end_epoch)
                if run_in_rng:
                    run_status = state.get('status', 'running')
                    display_status, display_message, failed = describe_run(run_status)
                    failed_runs_today += int(failed)
                    if last_run_ep is None or run_ep > last_run_ep:
                        last_run_ep = run_ep
                        last_run_at = run_iso
                        status = display_status
                        status_message = display_message
                interactions = state.get('interactions') or {}
                if not interactions:
                    if run_in_rng:
                        summary_counts = extract_gemini_counts(state.get('usage') or {})
                        if has_positive_counts(summary_counts):
                            unidentified_count += 1
                            unidentified_timestamps.append(run_iso)
                    continue
                for identity, interaction in interactions.items():
                    if not isinstance(interaction, dict):
                        continue
                    raw_id = interaction.get('id')
                    valid_id = (
                        raw_id.strip()
                        if isinstance(raw_id, str) and raw_id.strip()
                        else (identity.strip() if isinstance(identity, str) and identity.strip() else None)
                    )
                    created = interaction.get('created')
                    usage_dict = interaction.get('usage') or {}
                    counts = extract_gemini_counts(usage_dict)
                    if not valid_id:
                        u_rng, u_iso, _ = in_range_timestamp(created or stamp, start_epoch, end_epoch)
                        if u_rng and has_positive_counts(counts):
                            unidentified_count += 1
                            unidentified_timestamps.append(u_iso)
                        continue
                    eid = hash_event_id('gemini', valid_id)
                    owners[eid] = merge_owner(owners.get(eid), chat_key)
                    if valid_id in seen:
                        continue
                    seen.add(valid_id)
                    c_rng, c_iso, c_ep = in_range_timestamp(created, start_epoch, end_epoch)
                    if c_rng:
                        item = {'id': eid, **counts}
                        if include_timestamps:
                            item['timestamp'] = c_iso
                        events_by_id[eid] = item
                        if last_usage_ep is None or c_ep > last_usage_ep:
                            last_usage_ep = c_ep
                            last_usage_at = c_iso
                        if last_run_ep is None:
                            run_status = state.get('status', 'running')
                            display_status, display_message, _ = describe_run(run_status)
                            status = display_status
                            status_message = display_message
                            last_run_at = c_iso
                            last_run_ep = c_ep
            except (OSError, ValueError, TypeError, AttributeError):
                continue

        self.summary_cache = {k: v for k, v in self.summary_cache.items() if k in live_keys}
        return self._finalize_gemini_events(
            events_by_id,
            status=status,
            status_message=status_message,
            last_run_at=last_run_at,
            failed_runs_today=failed_runs_today,
            last_usage_at=last_usage_at,
            unidentified_today=unidentified_count,
            unidentified_timestamps=unidentified_timestamps if include_timestamps else None,
            owners=owners,
        )

    def _finalize_gemini_events(self, events_by_id, *, status, status_message, last_run_at,
                                failed_runs_today, last_usage_at, unidentified_today,
                                unidentified_timestamps=None, owners=None):
        events = [events_by_id[eid] for eid in sorted(events_by_id.keys())]
        if owners:
            for event in events:
                put_chat_key(event, owners.get(event['id']))
        summed = sum_event_counts(events)
        partial = unidentified_today > 0
        available = True
        if partial and not events:
            available = False
            status_message = 'Gemini usage has missing or unsupported event identity'
        metadata = {
            'available': available,
            'partial': partial,
            'unidentified_events': unidentified_today,
            'status': status,
            'status_message': status_message,
            'last_run_at': last_run_at,
            'failed_runs_today': failed_runs_today,
            'last_usage_at': last_usage_at,
        }
        if unidentified_timestamps is not None:
            metadata['unidentified_timestamps'] = unidentified_timestamps
        result = {
            'available': available,
            'partial': partial,
            'unidentified_events': unidentified_today,
            'total_tokens': summed['total_tokens'],
            'cached_tokens': summed['cached_tokens'],
            'input_tokens': summed['input_tokens'],
            'output_tokens': summed['output_tokens'],
            'status': status,
            'status_message': status_message,
            'last_run_at': last_run_at,
            'failed_runs_today': failed_runs_today,
            'last_usage_at': last_usage_at,
            'events': events,
            'metadata': metadata,
        }
        if unidentified_timestamps is not None:
            result['unidentified_timestamps'] = unidentified_timestamps
        return result

    def collect_summaries(self, paths, day, tz):
        exported = self.collect_summaries_events(paths, day, tz)
        return self._exported_to_total(exported)

    def collect(self, day, tz):
        exported = self.collect_events(day, tz)
        return self._exported_to_total(exported)

    def collect_in_range(self, start_epoch, end_epoch):
        exported = self.collect_events_in_range(start_epoch, end_epoch, include_timestamps=False)
        return self._exported_to_total(exported)

    @staticmethod
    def _exported_to_total(exported):
        meta = exported['metadata']
        total = {
            'total_tokens': exported['total_tokens'],
            'cached_tokens': exported['cached_tokens'],
            'input_tokens': exported['input_tokens'],
            'output_tokens': exported['output_tokens'],
            'available': exported['available'],
            'status': meta['status'],
            'status_message': meta['status_message'],
            'last_run_at': meta['last_run_at'],
            'failed_runs_today': meta['failed_runs_today'],
            'last_usage_at': meta['last_usage_at'],
        }
        if exported['partial']:
            total['partial'] = True
        events = exported.get('events') or ()
        categories_valid = all(event_has_valid_categories(ev) for ev in events)
        return enrich_usage_with_pricing(total, categories_valid=categories_valid)


INT64_MAX = (1 << 63) - 1


def normalize_limits(result):
    buckets = result.get('rateLimitsByLimitId') or {}
    if not isinstance(buckets, dict):
        buckets = {}
    legacy = result.get('rateLimits')
    if not buckets and isinstance(legacy, dict):
        buckets = {legacy.get('limitId') or 'codex': legacy}
    if not buckets:
        raise ValueError('Account quota data unavailable')
    name = 'codex' if 'codex' in buckets else next(iter(buckets))
    bucket = buckets[name]
    answer = {'five_hour': None, 'weekly': None, 'bucket': name,
              'fetched_at': utc_now(), 'stale': False, 'error': None}
    for slot in ('primary', 'secondary'):
        window = bucket.get(slot)
        if not isinstance(window, dict) or not isinstance(window.get('usedPercent'), (int, float)):
            continue
        duration = window.get('windowDurationMins')
        label = {300: 'five_hour', 10080: 'weekly'}.get(duration)
        if label:
            used = window['usedPercent']
            if not math.isfinite(used):
                continue
            entry = {'used_percent': max(0, min(100, used)), 'resets_at': window.get('resetsAt')}
            for exact_key in ('lastResetAt', 'last_reset_at', 'resetAt', 'reset_at'):
                if exact_key in window and window[exact_key] is not None:
                    entry['last_reset_at'] = window[exact_key]
                    break
            answer[label] = entry
    if answer['five_hour'] is None and answer['weekly'] is None:
        raise ValueError('No 5-hour or weekly quota window returned')
    return answer


def discover_codex_binary(explicit=None, home=None):
    if explicit and Path(explicit).is_file() and os.access(explicit, os.X_OK):
        return str(Path(explicit))
    for env_key in ('TOKENPILOT_CODEX_BIN', 'CODEX_BIN'):
        env_val = os.environ.get(env_key)
        if env_val and Path(env_val).is_file() and os.access(env_val, os.X_OK):
            return str(Path(env_val))
    which_bin = shutil.which('codex')
    if which_bin:
        return which_bin
    candidates = []
    if home:
        candidates.append(Path(home) / 'plugins' / '.plugin-appserver' / 'codex')
    user_home = Path.home()
    candidates.extend([
        user_home / '.codex' / 'plugins' / '.plugin-appserver' / 'codex',
        user_home / '.local' / 'opt' / 'chatgpt' / 'usr' / 'lib' / 'chatgpt' / 'resources' / 'codex',
        Path('/opt/chatgpt/usr/lib/chatgpt/resources/codex'),
        Path('/usr/lib/chatgpt/resources/codex'),
        user_home / '.local' / 'bin' / 'codex',
    ])
    for candidate in candidates:
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return explicit


def fetch_limits(binary, home, timeout=25):
    if not binary or not Path(binary).is_file():
        raise RuntimeError('Codex CLI not found')
    environment = dict(os.environ, CODEX_HOME=str(home))
    # Never hand the Google key to an unrelated child.
    environment.pop('GEMINI_API_KEY', None)
    process = subprocess.Popen([str(binary), 'app-server'], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment)
    with CHILD_LOCK:
        CHILDREN.add(process)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    def send(value):
        process.stdin.write(json.dumps(value).encode() + b'\n')
        process.stdin.flush()
    try:
        send({'id': 0, 'method': 'initialize', 'params': {'clientInfo': {
            'name': 'routing_float', 'title': 'TokenPilot', 'version': '1.0.0'}}})
        buffer = b''
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not selector.select(min(1, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('Codex account connection closed')
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if message.get('id') == 0:
                    if 'error' in message:
                        raise RuntimeError('Codex account handshake failed')
                    send({'method': 'initialized', 'params': {}})
                    send({'id': 1, 'method': 'account/rateLimits/read'})
                elif message.get('id') == 1:
                    if 'error' in message:
                        raise RuntimeError('Account quotas unavailable; check Codex sign-in')
                    return normalize_limits(message.get('result') or {})
        raise TimeoutError('Account quota request timed out')
    finally:
        selector.close()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()
        with CHILD_LOCK:
            CHILDREN.discard(process)


class Bridge:
    def __init__(self, home, binary, tz, offline=False, now_fn=None):
        self.home, self.binary, self.tz = Path(home), discover_codex_binary(binary, home), tz
        self.offline = offline
        self.now_fn = now_fn
        self.codex = CodexUsage(home)
        self.gemini = GeminiUsage(home)
        self.device_aggregator = DeviceUsageAggregator(home)
        self.limits = {'five_hour': None, 'weekly': None, 'bucket': 'codex',
                       'fetched_at': None, 'stale': True, 'error': 'Connecting to Codex…'}
        self.lock = threading.Lock()
        self.fetching = False
        self.next_fetch = 0
        self.history = QuotaHistory(home, atomic_json)
        self.reset_tracker = ResetTracker(home, atomic_json)
        self.nextreset_path = self.home / 'argon/viewer/nextreset-cache.json'
        try:
            self.nextreset = json.loads(self.nextreset_path.read_text())
            if not isinstance(self.nextreset, dict) or self.nextreset.get('url') != NEXTRESET_URL:
                raise ValueError('Discard cache from the previous information source')
            self.nextreset.update(stale=True)
        except (OSError, ValueError):
            self.nextreset = {'headline': 'Loading public reset status…', 'url': NEXTRESET_URL,
                              'latest_report_at': None, 'fetched_at': None, 'source_delayed': False,
                              'stale': True, 'error': None}
        self.nextreset_fetching = False
        self.nextreset_due = 0

    def refresh_nextreset(self, force=False):
        with self.lock:
            if self.nextreset_fetching or (not force and time.monotonic() < self.nextreset_due):
                return
            self.nextreset_fetching = True
            self.nextreset_due = time.monotonic() + 300
        def background():
            try:
                value = dict(fetch_summary(), fetched_at=utc_now())
                atomic_json(self.nextreset_path, value)
            except Exception:
                with self.lock:
                    value = dict(self.nextreset, stale=True, error='Public tracker refresh unavailable')
            with self.lock:
                self.nextreset = value
                self.nextreset_fetching = False
        threading.Thread(target=background, daemon=True).start()

    def refresh_limits(self, force=False):
        with self.lock:
            if self.fetching or (not force and time.monotonic() < self.next_fetch):
                return
            self.fetching = True
            self.next_fetch = time.monotonic() + 60
        def background():
            try:
                result = fetch_limits(self.binary, self.home)
            except Exception as error:
                with self.lock:
                    result = dict(self.limits, stale=True, error=str(error))
            with self.lock:
                self.limits = result
                self.fetching = False
        threading.Thread(target=background, daemon=True).start()

    def snapshot(self, refresh=True, now=None):
        if refresh and not self.offline:
            self.refresh_limits()
            self.refresh_nextreset()
        effective_now = (
            now
            if now is not None
            else (self.now_fn() if self.now_fn is not None else time.time())
        )
        now_epoch, now_dt = coerce_now(effective_now)
        today = now_dt.astimezone(self.tz).date()
        today_midnight_epoch = datetime(
            today.year, today.month, today.day, tzinfo=self.tz
        ).timestamp()
        warnings = []
        try:
            routing = read_routing(self.home)
        except (OSError, ValueError, TypeError):
            routing = {'gemini_percent': 0}
            warnings.append('Routing file invalid; Gemini launcher will refuse new tasks. Choose a preference to repair.')
        with self.lock:
            limits = dict(self.limits)
            nextreset = dict(self.nextreset)
        if summary_is_stale(nextreset, now=now_epoch):
            nextreset['stale'] = True

        # Examine recent_activity quota samples BEFORE QuotaHistory trims them to 1 hour.
        burn_tokens, raw_samples = self.codex.recent_activity(now_epoch)
        try:
            reset_period = self.reset_tracker.update(
                [*self.history.samples, *raw_samples],
                limits=limits,
                now=now_epoch,
            )
        except (OSError, ValueError):
            warnings.append('Could not persist quota reset tracker state')
            reset_period = self.reset_tracker.current_period(now=now_epoch)

        period_origin_available = bool(reset_period.get('available'))
        period_start_epoch = (
            reset_period['period_start_epoch']
            if period_origin_available and reset_period.get('period_start_epoch') is not None
            else today_midnight_epoch
        )

        codex_events = self.codex.collect_events_in_range(period_start_epoch, now_epoch, include_timestamps=True)
        gemini_events = self.gemini.collect_events_in_range(period_start_epoch, now_epoch, include_timestamps=True)
        if period_start_epoch != today_midnight_epoch:
            codex = self.codex.collect(today, self.tz)
            gemini = self.gemini.collect(today, self.tz)
        else:
            codex = self.codex._exported_to_total(codex_events)
            gemini = self.gemini._exported_to_total(gemini_events)

        forecast_samples = list(raw_samples)
        current = current_sample(limits)
        if current:
            forecast_samples.append(current)
        try:
            self.history.add(forecast_samples, now_epoch)
        except OSError:
            warnings.append('Could not persist quota trend history')
        forecast = build_forecast(self.history.samples, limits, burn_tokens, now_epoch)
        agg = self.device_aggregator.aggregate(
            today,
            self.tz,
            self.codex,
            self.gemini,
            now=now_epoch,
            period_start=period_start_epoch,
            period_origin_available=period_origin_available,
            local_providers=(codex_events, gemini_events),
        )
        if agg['warnings']:
            warnings.extend(agg['warnings'])
        pricing_ref = pricing_reference_metadata(
            now=now_epoch, day=str(today), tz=str(self.tz)
        )
        result = {
            'timestamp': format_utc(now_dt),
            'day': str(today),
            'timezone': str(self.tz),
            'reset_period': reset_period,
            'pricing_reference': pricing_ref,
            'codex': codex,
            'gemini': gemini,
            'routing': routing,
            'limits': limits,
            'warnings': warnings,
            'forecast': forecast,
            'nextreset': nextreset,
        }
        if agg['devices'] is not None and agg['totals'] is not None:
            result['devices'] = agg['devices']
            result['totals'] = agg['totals']
        result['chat_usage'] = agg.get('chat_usage') or build_chat_usage(
            period_start_epoch, now_epoch,
            {'codex': codex_events['events'], 'gemini': gemini_events['events']},
            self.codex.chat_attribution.titles(),
            codex_events['partial'] or gemini_events['partial'] or bool(agg['warnings'])
            or not codex_events['available'] or not gemini_events['available'],
        )
        return result

    def serve(self):
        selector = selectors.DefaultSelector()
        selector.register(sys.stdin.buffer, selectors.EVENT_READ)
        buffer = b''
        next_snapshot = 0
        last_manual = 0
        try:
            while True:
                if time.monotonic() >= next_snapshot:
                    print(json.dumps(self.snapshot()), flush=True)
                    next_snapshot = time.monotonic() + 5
                if not selector.select(.25):
                    continue
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    break
                buffer += data
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    try:
                        command = json.loads(line)
                        if command.get('command') == 'set_routing':
                            set_routing(self.home, command.get('gemini_percent'))
                        elif command.get('command') == 'refresh':
                            if time.monotonic() - last_manual >= 15:
                                self.refresh_limits(force=True)
                                self.refresh_nextreset(force=True)
                                last_manual = time.monotonic()
                        else:
                            raise ValueError('Unknown command')
                        next_snapshot = 0
                    except (ValueError, OSError, AttributeError) as error:
                        print(json.dumps({'error': str(error)}), flush=True)
                if len(buffer) > 65536:
                    buffer = b''
                    print(json.dumps({'error': 'Command too large'}), flush=True)
        finally:
            selector.close()


def main():
    parser = argparse.ArgumentParser()
    default_home = os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
    parser.add_argument('--codex-home', default=default_home)
    parser.add_argument('--codex-binary', default=discover_codex_binary(None, default_home))
    parser.add_argument('--timezone', default='Asia/Shanghai')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--snapshot', action='store_true')
    parser.add_argument('--limits', action='store_true')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    bridge = Bridge(args.codex_home, args.codex_binary, ZoneInfo(args.timezone), args.offline)
    if args.serve:
        bridge.serve()
    elif args.limits:
        print(json.dumps(fetch_limits(args.codex_binary, args.codex_home)))
    else:
        print(json.dumps(bridge.snapshot(refresh=False)))


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
