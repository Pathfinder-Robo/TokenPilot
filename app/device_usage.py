#!/usr/bin/env python3
"""Data-only cross-computer numeric usage exporter and multi-device aggregator for TokenPilot.

Exports and aggregates token counts, timestamps, and scoped attribution metadata.
Never reads API keys or auth.json, never makes external network calls, and never
exports conversation text, tool outputs, filenames, or filesystem paths.
"""
import argparse
from datetime import date, datetime, timezone, tzinfo
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from chat_attribution import build_chat_usage, is_valid_chat_key, merge_owner, put_chat_key, validate_chat_titles
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

SCHEMA_VERSION = 1
SCHEMA_V2 = 2
SUPPORTED_SCHEMAS = (1, 2)
MANIFEST_VERSION = 1
DEFAULT_HISTORY_DAYS = 10
MAX_HISTORY_DAYS = 366
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
MAX_EVENTS_PER_PROVIDER = 100000
FRESH_THRESHOLD_SECONDS = 60
MAX_FUTURE_SKEW_SECONDS = 60
TOKEN_FIELDS = ('total_tokens', 'cached_tokens', 'input_tokens', 'output_tokens')
EVENT_KEYS = frozenset(('id', *TOKEN_FIELDS))
EVENT_KEYS_V2 = frozenset(('id', 'timestamp', *TOKEN_FIELDS))
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
FORBIDDEN_EXPORT_KEYS = frozenset((
    'api_key', 'apikey', 'gemini_api_key', 'auth', 'authorization', 'token',
    'secret', 'password', 'credential', 'credentials', 'result_dir', 'path',
    'file', 'filename', 'session_file', 'prompt', 'steps', 'messages', 'text',
    'content', 'contents', 'output_text', 'tool_calls', 'workspace',
))
ALLOWED_TOP_KEYS_V1 = frozenset((
    'schema', 'version', 'device_id', 'device_name', 'name',
    'day', 'timezone', 'collected_at', 'providers',
))
ALLOWED_TOP_KEYS_V2 = frozenset((
    'schema', 'version', 'device_id', 'device_name', 'name',
    'day', 'timezone', 'collected_at',
    'coverage_start', 'coverage_end',
    'window_start', 'window_end',
    'history_start', 'history_end',
    'retention_days', 'providers', 'chat_titles',
))
ALLOWED_CODEX_PROVIDER_KEYS = frozenset((
    'available', 'partial', 'unidentified_events', 'unidentified_timestamps',
    'total_tokens', 'cached_tokens', 'input_tokens', 'output_tokens',
    'events', 'metadata',
))
ALLOWED_GEMINI_PROVIDER_KEYS = frozenset((
    *ALLOWED_CODEX_PROVIDER_KEYS,
    'status', 'status_message', 'last_run_at', 'failed_runs_today', 'last_usage_at',
))
ALLOWED_METADATA_KEYS = frozenset((
    'available', 'partial', 'unidentified_events', 'unidentified_timestamps',
    'status', 'status_message', 'last_run_at', 'failed_runs_today', 'last_usage_at',
))


def valid_token_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def clean_number(value):
    return value if valid_token_number(value) else 0


def parse_iso_timestamp(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('Timestamp must be a non-empty ISO-8601 string')
    text = raw.strip()
    if text.endswith('Z') or text.endswith('z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f'Invalid ISO-8601 timestamp: {raw!r}') from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError('Timestamp must include an explicit timezone offset')
    epoch_val = parsed.timestamp()
    if not math.isfinite(epoch_val):
        raise ValueError('Non-finite timestamp')
    return parsed, epoch_val


def format_utc(dt):
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def coerce_now(now=None):
    if now is None:
        now_epoch = time.time()
        return now_epoch, datetime.fromtimestamp(now_epoch, timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            dt = now.replace(tzinfo=timezone.utc)
        else:
            dt = now.astimezone(timezone.utc)
        return dt.timestamp(), dt
    if isinstance(now, (int, float)) and not isinstance(now, bool) and math.isfinite(now):
        now_epoch = float(now)
        return now_epoch, datetime.fromtimestamp(now_epoch, timezone.utc)
    if isinstance(now, str):
        dt, now_epoch = parse_iso_timestamp(now)
        return now_epoch, dt.astimezone(timezone.utc)
    raise ValueError('Unsupported time value for now')


def coerce_timezone(tz):
    if isinstance(tz, ZoneInfo):
        return tz, getattr(tz, 'key', str(tz))
    if isinstance(tz, tzinfo):
        name = getattr(tz, 'key', None) or str(tz)
        return tz, name
    if isinstance(tz, str) and tz.strip():
        cleaned = tz.strip()
        if cleaned.upper() == 'UTC':
            return timezone.utc, 'UTC'
        try:
            return ZoneInfo(cleaned), cleaned
        except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
            raise ValueError(f'Invalid timezone: {tz!r}') from exc
    raise ValueError('Timezone must be a valid IANA timezone name')


def hash_event_id(provider, item_id):
    if provider not in ('codex', 'gemini'):
        raise ValueError(f'Unsupported provider: {provider!r}')
    if not isinstance(item_id, str) or not item_id.strip():
        raise ValueError('Event identity must be a non-empty string')
    payload = f'{provider}:{item_id.strip()}'.encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def hash_codex_fallback_id(session_filename, timestamp, ordinal, counts):
    if not isinstance(session_filename, str) or not session_filename.strip():
        raise ValueError('Session filename must be a non-empty string')
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ValueError('Fallback timestamp must be a non-empty string')
    if type(ordinal) is not int or ordinal < 0:
        raise ValueError('Fallback ordinal must be a non-negative integer')
    name_hash = hashlib.sha256(Path(session_filename).name.encode('utf-8')).hexdigest()
    total = clean_number(counts.get('total_tokens'))
    cached = clean_number(counts.get('cached_tokens'))
    inp = clean_number(counts.get('input_tokens'))
    out = clean_number(counts.get('output_tokens'))
    raw = f'codex:fallback:{name_hash}:{timestamp.strip()}:{ordinal}:{total}:{cached}:{inp}:{out}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def extract_codex_counts(usage):
    if not isinstance(usage, dict):
        return {'total_tokens': 0, 'cached_tokens': 0, 'input_tokens': 0, 'output_tokens': 0}
    cached_raw = usage.get('cached_input_tokens') if 'cached_input_tokens' in usage else usage.get('cached_tokens')
    return {
        'total_tokens': clean_number(usage.get('total_tokens')),
        'cached_tokens': clean_number(cached_raw),
        'input_tokens': clean_number(usage.get('input_tokens')),
        'output_tokens': clean_number(usage.get('output_tokens')),
    }


def extract_gemini_counts(usage):
    if not isinstance(usage, dict):
        return {'total_tokens': 0, 'cached_tokens': 0, 'input_tokens': 0, 'output_tokens': 0}
    cached_raw = usage.get('total_cached_tokens') if 'total_cached_tokens' in usage else usage.get('cached_tokens')
    inp_raw = usage.get('total_input_tokens') if 'total_input_tokens' in usage else usage.get('input_tokens')
    if 'total_output_tokens' in usage or 'total_thought_tokens' in usage:
        out_val = clean_number(usage.get('total_output_tokens')) + clean_number(usage.get('total_thought_tokens'))
    else:
        out_val = clean_number(usage.get('output_tokens'))
    return {
        'total_tokens': clean_number(usage.get('total_tokens')),
        'cached_tokens': clean_number(cached_raw),
        'input_tokens': clean_number(inp_raw),
        'output_tokens': out_val,
    }


def has_positive_counts(counts):
    return any(clean_number(counts.get(field)) > 0 for field in TOKEN_FIELDS)


def sum_event_counts(events):
    totals = {field: 0 for field in TOKEN_FIELDS}
    for event in events:
        for field in TOKEN_FIELDS:
            totals[field] += clean_number(event.get(field))
    return totals


def _lazy_bridge():
    import bridge
    return bridge


class DeviceExporter:
    """Reusable, incremental numeric usage exporter for a single TokenPilot host."""

    def __init__(self, codex_home=None, device_id=None, device_name=None, timezone='Asia/Shanghai', *,
                 home=None, name=None, tz=None, now_fn=None, schema=None,
                 history_days=None, retention_days=None):
        resolved_home = codex_home if codex_home is not None else home
        if resolved_home is None:
            resolved_home = os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
        self.home = Path(resolved_home).expanduser()
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError('device_id must be a non-empty string')
        self.device_id = device_id.strip()
        resolved_name = device_name if device_name is not None else name
        if resolved_name is None:
            resolved_name = self.device_id
        if not isinstance(resolved_name, str) or not resolved_name.strip():
            raise ValueError('device_name must be a non-empty string')
        self.device_name = resolved_name.strip()
        resolved_tz = tz if tz is not None else timezone
        self.tz, self.tz_name = coerce_timezone(resolved_tz)
        self.now_fn = now_fn

        requested_days = history_days if history_days is not None else retention_days
        if schema is None:
            resolved_schema = SCHEMA_V2 if requested_days is not None else SCHEMA_VERSION
        else:
            if type(schema) is not int or schema not in SUPPORTED_SCHEMAS:
                raise ValueError(f'Unsupported schema version: {schema!r}')
            resolved_schema = schema
        self.schema = resolved_schema

        if requested_days is None:
            effective_days = DEFAULT_HISTORY_DAYS
        else:
            if (
                not isinstance(requested_days, (int, float))
                or isinstance(requested_days, bool)
                or not math.isfinite(requested_days)
                or requested_days <= 0
                or requested_days > MAX_HISTORY_DAYS
            ):
                raise ValueError('history_days must be a finite number between 0 and 366')
            effective_days = int(requested_days) if float(requested_days).is_integer() else float(requested_days)
        self.history_days = effective_days

        self._lock = threading.Lock()
        bridge_mod = _lazy_bridge()
        self.codex = bridge_mod.CodexUsage(self.home)
        self.gemini = bridge_mod.GeminiUsage(self.home)

    def snapshot(self, now=None, *, schema=None, history_days=None):
        effective_now = now if now is not None else (self.now_fn() if self.now_fn is not None else None)
        now_epoch, now_dt = coerce_now(effective_now)
        day = now_dt.astimezone(self.tz).date()

        active_schema = self.schema if schema is None else schema
        if type(active_schema) is not int or active_schema not in SUPPORTED_SCHEMAS:
            raise ValueError(f'Unsupported schema version: {active_schema!r}')

        active_days = self.history_days if history_days is None else history_days
        if (
            not isinstance(active_days, (int, float))
            or isinstance(active_days, bool)
            or not math.isfinite(active_days)
            or active_days <= 0
            or active_days > MAX_HISTORY_DAYS
        ):
            raise ValueError('history_days must be a finite number between 0 and 366')

        if active_schema == SCHEMA_VERSION:
            with self._lock:
                codex_provider = self.codex.collect_events(day, self.tz)
                gemini_provider = self.gemini.collect_events(day, self.tz)
            return {
                'schema': SCHEMA_VERSION,
                'device_id': self.device_id,
                'device_name': self.device_name,
                'name': self.device_name,
                'day': str(day),
                'timezone': self.tz_name,
                'collected_at': format_utc(now_dt),
                'providers': {
                    'codex': codex_provider,
                    'gemini': gemini_provider,
                },
            }

        coverage_start_epoch = now_epoch - float(active_days) * 86400.0
        coverage_start_dt = datetime.fromtimestamp(coverage_start_epoch, timezone.utc)
        with self._lock:
            codex_provider = self.codex.collect_events_in_range(
                coverage_start_epoch, now_epoch, include_timestamps=True
            )
            gemini_provider = self.gemini.collect_events_in_range(
                coverage_start_epoch, now_epoch, include_timestamps=True
            )
        chat_keys = {
            event['chat_key']
            for provider in (codex_provider, gemini_provider)
            for event in provider['events']
            if 'chat_key' in event
        }
        return {
            'schema': SCHEMA_V2,
            'device_id': self.device_id,
            'device_name': self.device_name,
            'name': self.device_name,
            'day': str(day),
            'timezone': self.tz_name,
            'collected_at': format_utc(now_dt),
            'coverage_start': format_utc(coverage_start_dt),
            'coverage_end': format_utc(now_dt),
            'retention_days': active_days,
            'chat_titles': self.codex.chat_attribution.titles(explicit_only=True, active_keys=chat_keys),
            'providers': {
                'codex': codex_provider,
                'gemini': gemini_provider,
            },
        }

    def export_json(self, now=None):
        return json.dumps(self.snapshot(now=now), allow_nan=False)

    def __call__(self, now=None):
        return self.snapshot(now=now)


UsageExporter = DeviceExporter
Exporter = DeviceExporter


def export_snapshot(codex_home, device_id, device_name, timezone='Asia/Shanghai', now=None, *,
                    schema=SCHEMA_VERSION, history_days=None):
    exporter = DeviceExporter(
        codex_home=codex_home,
        device_id=device_id,
        device_name=device_name,
        timezone=timezone,
        schema=schema,
        history_days=history_days,
    )
    return exporter.snapshot(now=now)


def load_devices_manifest(codex_home):
    home = Path(codex_home).expanduser()
    argon_dir = home / 'argon'
    manifest_path = argon_dir / 'devices.json'
    if not manifest_path.is_file() and not manifest_path.is_symlink():
        return None
    if manifest_path.is_symlink() or argon_dir.is_symlink():
        raise ValueError('Devices manifest path must not be a symlink')
    try:
        raw = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError('Devices manifest could not be read') from exc
    if not isinstance(raw, dict):
        raise ValueError('Devices manifest must be a JSON object')
    version = raw.get('version') if 'version' in raw else raw.get('schema')
    if type(version) is not int or version != MANIFEST_VERSION:
        raise ValueError('Devices manifest version must be 1')
    local_cfg = raw.get('local_device')
    if not isinstance(local_cfg, dict):
        raise ValueError('Devices manifest local_device must be an object')
    local_id = local_cfg.get('id')
    local_name = local_cfg.get('name')
    if not isinstance(local_id, str) or not local_id.strip():
        raise ValueError('local_device.id must be a non-empty string')
    if not isinstance(local_name, str) or not local_name.strip():
        raise ValueError('local_device.name must be a non-empty string')
    local_id = local_id.strip()
    local_name = local_name.strip()

    remotes_raw = raw.get('remotes')
    if not isinstance(remotes_raw, list) or not remotes_raw:
        raise ValueError('Devices manifest remotes must be a non-empty list')

    seen_ids = {local_id}
    remotes = []
    for entry in remotes_raw:
        if not isinstance(entry, dict):
            raise ValueError('Each remote device entry must be an object')
        rid = entry.get('id')
        rname = entry.get('name')
        sfile = entry.get('snapshot_file')
        if not isinstance(rid, str) or not rid.strip():
            raise ValueError('Remote device id must be a non-empty string')
        if not isinstance(rname, str) or not rname.strip():
            raise ValueError('Remote device name must be a non-empty string')
        if not isinstance(sfile, str) or not sfile.strip():
            raise ValueError('Remote device snapshot_file must be a non-empty string')
        rid = rid.strip()
        rname = rname.strip()
        if rid in seen_ids:
            raise ValueError(f'Duplicate device id in manifest: {rid}')
        seen_ids.add(rid)
        remotes.append({
            'id': rid,
            'name': rname,
            'snapshot_file': sfile.strip(),
        })

    return {
        'version': MANIFEST_VERSION,
        'local_device': {'id': local_id, 'name': local_name},
        'remotes': remotes,
    }


def resolve_snapshot_path(codex_home, snapshot_file):
    if not isinstance(snapshot_file, str) or not snapshot_file.strip():
        raise ValueError('snapshot_file must be a non-empty string')
    home = Path(codex_home).expanduser()
    argon_dir = home / 'argon'
    devices_dir = argon_dir / 'devices'
    if home.is_symlink() or argon_dir.is_symlink() or devices_dir.is_symlink():
        raise ValueError('argon/devices directory must not be a symlink')
    resolved_devices_dir = devices_dir.resolve()

    raw_path = Path(snapshot_file.strip()).expanduser()
    if raw_path.is_absolute():
        candidate = raw_path
    elif raw_path.parts[:2] == ('argon', 'devices') and len(raw_path.parts) > 2:
        candidate = home / raw_path
    elif raw_path.parts[:1] == ('devices',) and len(raw_path.parts) > 1:
        candidate = argon_dir / raw_path
    else:
        candidate = devices_dir / raw_path

    if candidate.is_symlink() or candidate.parent.is_symlink():
        raise ValueError('Remote snapshot file must not be a symlink')
    resolved = candidate.resolve()
    if resolved.parent != resolved_devices_dir or resolved == resolved_devices_dir:
        raise ValueError('snapshot_file must resolve directly inside argon/devices')
    return resolved


def _validate_provider_payload(
    provider_name,
    prov,
    *,
    schema=SCHEMA_VERSION,
    coverage_start_epoch=None,
    coverage_end_epoch=None,
    collected_epoch=None,
    now_epoch=None,
    allow_chat_keys=False,
    allow_local_timestamps=False,
):
    if not isinstance(prov, dict):
        raise ValueError(f'Provider {provider_name} must be an object')
    allowed_prov_keys = (
        ALLOWED_GEMINI_PROVIDER_KEYS if provider_name == 'gemini' else ALLOWED_CODEX_PROVIDER_KEYS
    )
    for key in prov:
        if not isinstance(key, str):
            raise ValueError(f'Invalid key type in provider {provider_name}')
        if key.lower() in FORBIDDEN_EXPORT_KEYS:
            raise ValueError(f'Forbidden key in provider {provider_name}: {key}')
        if key not in allowed_prov_keys:
            raise ValueError(f'Unexpected key in provider {provider_name}: {key}')

    metadata = prov.get('metadata')
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ValueError(f'Provider {provider_name} metadata must be an object')
        for key in metadata:
            if not isinstance(key, str):
                raise ValueError(f'Invalid metadata key type in provider {provider_name}')
            if key.lower() in FORBIDDEN_EXPORT_KEYS:
                raise ValueError(f'Forbidden key in provider {provider_name} metadata: {key}')
            if key not in ALLOWED_METADATA_KEYS:
                raise ValueError(f'Unexpected key in provider {provider_name} metadata: {key}')
    else:
        metadata = {}

    available = prov.get('available', metadata.get('available'))
    if not isinstance(available, bool):
        raise ValueError(f'Provider {provider_name} available must be a boolean')
    partial = prov.get('partial', metadata.get('partial', False))
    if not isinstance(partial, bool):
        raise ValueError(f'Provider {provider_name} partial must be a boolean')
    unidentified = prov.get('unidentified_events', metadata.get('unidentified_events', 0))
    if type(unidentified) is not int or unidentified < 0:
        raise ValueError(f'Provider {provider_name} unidentified_events must be a non-negative int')
    if unidentified > 0:
        partial = True

    unidentified_timestamps_raw = prov.get(
        'unidentified_timestamps', metadata.get('unidentified_timestamps')
    )
    unidentified_timestamps = None
    if unidentified_timestamps_raw is not None:
        if not isinstance(unidentified_timestamps_raw, list) or len(unidentified_timestamps_raw) > MAX_EVENTS_PER_PROVIDER:
            raise ValueError(f'Provider {provider_name} unidentified_timestamps must be a bounded list')
        unidentified_timestamps = []
        for ts_raw in unidentified_timestamps_raw:
            ts_dt, ts_ep = parse_iso_timestamp(ts_raw)
            if now_epoch is not None and ts_ep - now_epoch > MAX_FUTURE_SKEW_SECONDS:
                raise ValueError(f'Provider {provider_name} unidentified timestamp is in the future')
            if collected_epoch is not None and ts_ep - collected_epoch > MAX_FUTURE_SKEW_SECONDS:
                raise ValueError(f'Provider {provider_name} unidentified timestamp exceeds collected_at')
            unidentified_timestamps.append(format_utc(ts_dt))

    events_raw = prov.get('events')
    if not isinstance(events_raw, list):
        raise ValueError(f'Provider {provider_name} events must be a list')
    if len(events_raw) > MAX_EVENTS_PER_PROVIDER:
        raise ValueError(f'Provider {provider_name} events exceed maximum count')

    expected_event_keys = EVENT_KEYS_V2 if schema == SCHEMA_V2 else EVENT_KEYS
    normalized_events = []
    seen_ids = {}
    owners = {}
    for item in events_raw:
        if not isinstance(item, dict):
            raise ValueError(f'Provider {provider_name} event must be an object')
        for k in item:
            if isinstance(k, str) and k.lower() in FORBIDDEN_EXPORT_KEYS:
                raise ValueError(f'Forbidden key in provider {provider_name} event: {k}')
        keys = set(item)
        if (schema == SCHEMA_V2 or allow_chat_keys) and 'chat_key' in keys:
            if not is_valid_chat_key(item['chat_key']):
                raise ValueError(f'Provider {provider_name} event has invalid chat_key')
            keys.remove('chat_key')
        has_timestamp = schema == SCHEMA_V2 or (allow_local_timestamps and 'timestamp' in keys)
        if allow_local_timestamps and schema != SCHEMA_V2:
            keys.discard('timestamp')
        if keys != expected_event_keys:
            raise ValueError(f'Provider {provider_name} event has invalid keys')
        eid = item.get('id')
        if not isinstance(eid, str) or not SHA256_RE.match(eid):
            raise ValueError(f'Provider {provider_name} event has unsupported or unhashed id')

        canonical_ts = None
        if has_timestamp:
            ev_dt, ev_epoch = parse_iso_timestamp(item.get('timestamp'))
            if now_epoch is not None and ev_epoch - now_epoch > MAX_FUTURE_SKEW_SECONDS:
                raise ValueError(f'Provider {provider_name} event timestamp is in the future')
            if collected_epoch is not None and ev_epoch - collected_epoch > MAX_FUTURE_SKEW_SECONDS:
                raise ValueError(f'Provider {provider_name} event timestamp exceeds collected_at')
            if coverage_start_epoch is not None and ev_epoch < coverage_start_epoch - 1.0:
                raise ValueError(f'Provider {provider_name} event timestamp precedes coverage_start')
            if coverage_end_epoch is not None and ev_epoch - coverage_end_epoch > MAX_FUTURE_SKEW_SECONDS:
                raise ValueError(f'Provider {provider_name} event timestamp exceeds coverage_end')
            canonical_ts = format_utc(ev_dt)

        counts = {}
        for field in TOKEN_FIELDS:
            val = item.get(field)
            if not valid_token_number(val):
                raise ValueError(f'Provider {provider_name} event has invalid {field}')
            counts[field] = val
        tuple_counts = tuple(counts[f] for f in TOKEN_FIELDS)
        owners[eid] = merge_owner(owners.get(eid), item.get('chat_key'))
        if eid in seen_ids:
            if seen_ids[eid] != tuple_counts:
                raise ValueError(f'Provider {provider_name} has conflicting duplicate event {eid}')
            continue
        seen_ids[eid] = tuple_counts
        if has_timestamp:
            normalized_events.append({'id': eid, 'timestamp': canonical_ts, **counts})
        else:
            normalized_events.append({'id': eid, **counts})

    for event in normalized_events:
        put_chat_key(event, owners.get(event['id']))
    summed = sum_event_counts(normalized_events)
    for field in TOKEN_FIELDS:
        if field in prov:
            reported = prov.get(field)
            if not valid_token_number(reported):
                raise ValueError(f'Provider {provider_name} has invalid summary {field}')
            if reported != summed[field]:
                raise ValueError(
                    f'Provider {provider_name} summary {field} ({reported}) does not match events ({summed[field]})'
                )

    if not available and (normalized_events or any(summed[f] > 0 for f in TOKEN_FIELDS)):
        raise ValueError(f'Provider {provider_name} marked unavailable but has non-empty usage')

    usage = {
        'total_tokens': summed['total_tokens'],
        'cached_tokens': summed['cached_tokens'],
        'input_tokens': summed['input_tokens'],
        'output_tokens': summed['output_tokens'],
        'available': available,
    }
    if partial:
        usage['partial'] = True

    if provider_name == 'gemini':
        status = prov.get('status', metadata.get('status', 'idle' if available else 'unavailable'))
        if not isinstance(status, str) or not status.strip():
            raise ValueError('Gemini status must be a non-empty string')
        status = status.strip()
        default_status_message = 'No tracked Gemini runs today' if available else 'Unavailable'
        if status in ('cancelled', 'interrupted'):
            status = 'paused'
            default_status_message = 'Gemini stopped by supervisor · usage recorded'
        status_message = prov.get(
            'status_message',
            metadata.get('status_message', default_status_message),
        )
        last_run_at = prov.get('last_run_at', metadata.get('last_run_at'))
        last_usage_at = prov.get('last_usage_at', metadata.get('last_usage_at'))
        failed_runs = prov.get('failed_runs_today', metadata.get('failed_runs_today', 0))
        if status_message is not None and not isinstance(status_message, str):
            raise ValueError('Gemini status_message must be a string or null')
        if last_run_at is not None:
            lr_dt, lr_ep = parse_iso_timestamp(last_run_at)
            if now_epoch is not None and lr_ep - now_epoch > MAX_FUTURE_SKEW_SECONDS:
                raise ValueError('Gemini last_run_at is in the future')
            last_run_at = format_utc(lr_dt)
        if last_usage_at is not None:
            lu_dt, lu_ep = parse_iso_timestamp(last_usage_at)
            if now_epoch is not None and lu_ep - now_epoch > MAX_FUTURE_SKEW_SECONDS:
                raise ValueError('Gemini last_usage_at is in the future')
            last_usage_at = format_utc(lu_dt)
        if type(failed_runs) is not int or failed_runs < 0:
            raise ValueError('Gemini failed_runs_today must be a non-negative integer')
        usage.update(
            status=status,
            status_message=status_message,
            last_run_at=last_run_at,
            failed_runs_today=failed_runs,
            last_usage_at=last_usage_at,
        )

    categories_valid = all(event_has_valid_categories(ev) for ev in normalized_events)
    usage = enrich_usage_with_pricing(usage, categories_valid=categories_valid)

    result = {
        'usage': usage,
        'events': normalized_events,
        'partial': partial,
        'unidentified_events': unidentified,
    }
    if unidentified_timestamps is not None:
        result['unidentified_timestamps'] = unidentified_timestamps
    return result


def validate_remote_snapshot(payload, *, expected_device_id=None, expected_timezone=None, expected_day=None, now=None):
    if not isinstance(payload, dict):
        raise ValueError('Snapshot payload must be a JSON object')

    schema = payload.get('schema')
    if type(schema) is not int or schema not in SUPPORTED_SCHEMAS:
        raise ValueError('Unsupported or missing snapshot schema version')
    if 'version' in payload and (type(payload.get('version')) is not int or payload.get('version') != schema):
        raise ValueError('Unsupported snapshot version field')

    allowed_top = ALLOWED_TOP_KEYS_V2 if schema == SCHEMA_V2 else ALLOWED_TOP_KEYS_V1
    for key in payload:
        if not isinstance(key, str):
            raise ValueError('Invalid key type in snapshot payload')
        if key.lower() in FORBIDDEN_EXPORT_KEYS:
            raise ValueError(f'Forbidden key in snapshot payload: {key}')
        if key not in allowed_top:
            raise ValueError(f'Unexpected key in snapshot payload: {key}')

    device_id = payload.get('device_id')
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError('Snapshot device_id must be a non-empty string')
    device_id = device_id.strip()
    if expected_device_id is not None and device_id != expected_device_id:
        raise ValueError(f'Snapshot device_id {device_id!r} does not match expected {expected_device_id!r}')

    has_device_name = 'device_name' in payload
    has_name = 'name' in payload
    if not has_device_name and not has_name:
        raise ValueError('Snapshot must include device_name or name')
    if has_device_name:
        if not isinstance(payload['device_name'], str) or not payload['device_name'].strip():
            raise ValueError('Snapshot device_name must be a non-empty string')
    if has_name:
        if not isinstance(payload['name'], str) or not payload['name'].strip():
            raise ValueError('Snapshot name must be a non-empty string')
    if has_device_name and has_name and payload['device_name'].strip() != payload['name'].strip():
        raise ValueError('Snapshot device_name and name do not match')
    device_name = (payload.get('device_name') or payload.get('name')).strip()

    tz_raw = payload.get('timezone')
    if not isinstance(tz_raw, str) or not tz_raw.strip():
        raise ValueError('Snapshot timezone must be a non-empty string')
    tz_obj, tz_name = coerce_timezone(tz_raw)
    if expected_timezone is not None and tz_name != str(expected_timezone):
        raise ValueError(f'Snapshot timezone {tz_name!r} does not match {expected_timezone!r}')

    collected_at_raw = payload.get('collected_at')
    collected_dt, collected_epoch = parse_iso_timestamp(collected_at_raw)

    now_epoch, _ = coerce_now(now)
    if collected_epoch - now_epoch > MAX_FUTURE_SKEW_SECONDS:
        raise ValueError('Snapshot collected_at is in the future (>60s skew)')

    day_raw = payload.get('day')
    if day_raw is not None or schema == SCHEMA_VERSION:
        if not isinstance(day_raw, str) or not day_raw.strip():
            raise ValueError('Snapshot day must be a non-empty YYYY-MM-DD string')
        try:
            snapshot_day = date.fromisoformat(day_raw.strip())
        except ValueError as exc:
            raise ValueError(f'Invalid snapshot day: {day_raw!r}') from exc
        if collected_dt.astimezone(tz_obj).date() != snapshot_day:
            raise ValueError('Snapshot collected_at date does not match snapshot day in timezone')
        if schema == SCHEMA_VERSION and expected_day is not None and str(snapshot_day) != str(expected_day):
            raise ValueError(f'Snapshot day {snapshot_day} is not today ({expected_day})')
    else:
        snapshot_day = collected_dt.astimezone(tz_obj).date()

    coverage_start_str = None
    coverage_end_str = None
    coverage_start_epoch = None
    coverage_end_epoch = None
    retention_days = None

    if schema == SCHEMA_V2:
        cov_start_raw = (
            payload.get('coverage_start')
            if 'coverage_start' in payload
            else (payload.get('window_start') if 'window_start' in payload else payload.get('history_start'))
        )
        cov_end_raw = (
            payload.get('coverage_end')
            if 'coverage_end' in payload
            else (payload.get('window_end') if 'window_end' in payload else payload.get('history_end'))
        )
        if cov_start_raw is None or cov_end_raw is None:
            raise ValueError('Schema 2 snapshot must include coverage_start and coverage_end')
        cov_start_dt, coverage_start_epoch = parse_iso_timestamp(cov_start_raw)
        cov_end_dt, coverage_end_epoch = parse_iso_timestamp(cov_end_raw)
        if coverage_start_epoch > coverage_end_epoch:
            raise ValueError('Snapshot coverage_start must not be after coverage_end')
        if coverage_end_epoch - collected_epoch > MAX_FUTURE_SKEW_SECONDS:
            raise ValueError('Snapshot coverage_end exceeds collected_at')
        if coverage_end_epoch - now_epoch > MAX_FUTURE_SKEW_SECONDS:
            raise ValueError('Snapshot coverage_end is in the future')
        if coverage_end_epoch - coverage_start_epoch > MAX_HISTORY_DAYS * 86400 + 3600:
            raise ValueError('Snapshot coverage span exceeds maximum bounded retention')
        coverage_start_str = format_utc(cov_start_dt)
        coverage_end_str = format_utc(cov_end_dt)

        if 'retention_days' in payload and payload['retention_days'] is not None:
            r_days = payload['retention_days']
            if (
                not isinstance(r_days, (int, float))
                or isinstance(r_days, bool)
                or not math.isfinite(r_days)
                or r_days <= 0
                or r_days > MAX_HISTORY_DAYS
            ):
                raise ValueError('Snapshot retention_days must be a positive finite number <= 366')
            retention_days = r_days

    providers = payload.get('providers')
    if not isinstance(providers, dict) or set(providers.keys()) != {'codex', 'gemini'}:
        raise ValueError('Snapshot providers must contain exactly codex and gemini')

    codex_validated = _validate_provider_payload(
        'codex',
        providers.get('codex'),
        schema=schema,
        coverage_start_epoch=coverage_start_epoch,
        coverage_end_epoch=coverage_end_epoch,
        collected_epoch=collected_epoch,
        now_epoch=now_epoch,
    )
    gemini_validated = _validate_provider_payload(
        'gemini',
        providers.get('gemini'),
        schema=schema,
        coverage_start_epoch=coverage_start_epoch,
        coverage_end_epoch=coverage_end_epoch,
        collected_epoch=collected_epoch,
        now_epoch=now_epoch,
    )

    raw_age = max(0.0, now_epoch - collected_epoch)
    is_fresh = raw_age <= FRESH_THRESHOLD_SECONDS
    status = 'fresh' if is_fresh else 'stale'
    age_seconds = int(raw_age) if is_fresh else max(FRESH_THRESHOLD_SECONDS + 1, int(raw_age))

    result = {
        'schema': schema,
        'device_id': device_id,
        'device_name': device_name,
        'day': str(snapshot_day),
        'timezone': tz_name,
        'collected_at': format_utc(collected_dt),
        'collected_epoch': collected_epoch,
        'status': status,
        'age_seconds': age_seconds,
        'codex': codex_validated,
        'gemini': gemini_validated,
    }
    if schema == SCHEMA_V2:
        result.update(
            coverage_start=coverage_start_str,
            coverage_end=coverage_end_str,
            coverage_start_epoch=coverage_start_epoch,
            coverage_end_epoch=coverage_end_epoch,
            retention_days=retention_days,
            chat_titles=validate_chat_titles(payload.get('chat_titles', {})),
        )
    return result


def _UnavailableDeviceRow(device_id, device_name):
    return {
        'id': device_id,
        'name': device_name,
        'day': None,
        'collected_at': None,
        'status': 'unavailable',
        'age_seconds': None,
        'codex': enrich_usage_with_pricing({
            'total_tokens': 0,
            'cached_tokens': 0,
            'input_tokens': 0,
            'output_tokens': 0,
            'available': False,
        }),
        'gemini': enrich_usage_with_pricing({
            'total_tokens': 0,
            'cached_tokens': 0,
            'input_tokens': 0,
            'output_tokens': 0,
            'available': False,
            'status': 'unavailable',
            'status_message': 'Device snapshot unavailable',
            'last_run_at': None,
            'failed_runs_today': 0,
            'last_usage_at': None,
        }),
    }


def _filter_validated_provider_to_range(provider_name, validated_prov, start_epoch, end_epoch):
    filtered_events = []
    last_usage_ep = None
    last_usage_iso = None
    for ev in validated_prov['events']:
        ev_dt, ev_ep = parse_iso_timestamp(ev['timestamp'])
        if start_epoch <= ev_ep <= end_epoch:
            filtered_events.append(ev)
            if last_usage_ep is None or ev_ep > last_usage_ep:
                last_usage_ep = ev_ep
                last_usage_iso = format_utc(ev_dt)

    summed = sum_event_counts(filtered_events)
    if 'unidentified_timestamps' in validated_prov and validated_prov['unidentified_timestamps'] is not None:
        unid = 0
        for ts in validated_prov['unidentified_timestamps']:
            _, ts_ep = parse_iso_timestamp(ts)
            if start_epoch <= ts_ep <= end_epoch:
                unid += 1
        partial = unid > 0
    else:
        unid = validated_prov['unidentified_events']
        partial = validated_prov['partial']

    available = bool(validated_prov['usage'].get('available'))
    if partial and not filtered_events:
        available = False

    usage = {
        'total_tokens': summed['total_tokens'],
        'cached_tokens': summed['cached_tokens'],
        'input_tokens': summed['input_tokens'],
        'output_tokens': summed['output_tokens'],
        'available': available,
    }
    if partial:
        usage['partial'] = True

    if provider_name == 'gemini':
        orig_usage = validated_prov['usage']
        orig_run_at = orig_usage.get('last_run_at')
        run_in_range = False
        if orig_run_at is not None:
            try:
                _, lr_ep = parse_iso_timestamp(orig_run_at)
                run_in_range = start_epoch <= lr_ep <= end_epoch
            except ValueError:
                run_in_range = False
        if run_in_range:
            status = orig_usage.get('status', 'idle' if available else 'unavailable')
            status_message = orig_usage.get(
                'status_message', 'No tracked Gemini runs since last reset' if available else 'Unavailable'
            )
            last_run_at = orig_run_at
            failed_runs = int(clean_number(orig_usage.get('failed_runs_today')))
        elif filtered_events:
            status = 'completed'
            status_message = 'Latest Gemini run completed'
            last_run_at = last_usage_iso
            failed_runs = 0
        else:
            status = 'idle' if available else 'unavailable'
            status_message = (
                'Gemini usage has missing or unsupported event identity'
                if (partial and not available)
                else ('No tracked Gemini runs since last reset' if available else 'Unavailable')
            )
            last_run_at = None
            failed_runs = 0

        usage.update(
            status=status,
            status_message=status_message,
            last_run_at=last_run_at,
            failed_runs_today=failed_runs,
            last_usage_at=last_usage_iso,
        )

    categories_valid = all(event_has_valid_categories(ev) for ev in filtered_events)
    usage = enrich_usage_with_pricing(usage, categories_valid=categories_valid)

    return {
        'usage': usage,
        'events': filtered_events,
        'partial': partial,
        'unidentified_events': unid,
    }


def apply_historical_chat_keys(snapshot_path, device_id, provider):
    """Use locally verified event identities while an older remote collector is still active."""
    path = snapshot_path.with_suffix('.chats.json')
    try:
        if path.is_symlink() or path.stat().st_size > 512 * 1024:
            return {}
        archive = json.loads(path.read_text())
        if not isinstance(archive, dict) or archive.get('device_id') != device_id:
            return {}
        titles = validate_chat_titles(archive.get('titles', {}))
        event_keys = archive.get('event_chats')
        if not isinstance(event_keys, dict):
            return {}
    except (OSError, ValueError):
        return {}

    provider['events'] = [
        event if 'chat_key' in event else put_chat_key(dict(event), event_keys.get(event['id']))
        for event in provider['events']
    ]
    return titles


def combine_provider_events(provider_name, device_entries, warnings_out, *, events_out=None):
    """Deduplicate hashed event IDs across devices; handle count conflicts deterministically."""
    merged_events = {}
    owners = {}
    timestamps = {}
    duplicate_events = 0
    conflict_events = 0
    any_available = False
    any_partial = False

    for entry in device_entries:
        prov = entry[provider_name]
        usage = prov['usage']
        if usage.get('available'):
            any_available = True
        if prov.get('partial') or usage.get('partial'):
            any_partial = True
        for event in prov['events']:
            eid = event['id']
            if events_out is not None:
                owners[eid] = merge_owner(owners.get(eid), event.get('chat_key'))
                timestamp = event.get('timestamp')
                if timestamp is not None:
                    previous = timestamps.get(eid)
                    if previous is None or (
                        previous != timestamp
                        and parse_iso_timestamp(timestamp)[1] < parse_iso_timestamp(previous)[1]
                    ):
                        timestamps[eid] = timestamp
            counts = tuple(clean_number(event[f]) for f in TOKEN_FIELDS)
            if eid not in merged_events:
                merged_events[eid] = counts
            else:
                duplicate_events += 1
                existing = merged_events[eid]
                if existing != counts:
                    conflict_events += 1
                    any_partial = True
                    # Deterministic conflict resolution: element-wise maximum, never summing conflicts.
                    merged_events[eid] = tuple(max(a, b) for a, b in zip(existing, counts))
                    warnings_out.append(
                        f'Conflicting {provider_name} token counts for event {eid[:12]} across devices; deduplicated deterministically'
                    )

    totals = {field: 0 for field in TOKEN_FIELDS}
    categories_valid = True
    for entry in device_entries:
        u = entry[provider_name]['usage']
        if u.get('available') and u.get('categories_valid') is False:
            categories_valid = False
    for eid, counts in merged_events.items():
        ev_dict = dict(zip(TOKEN_FIELDS, counts))
        if events_out is not None:
            if eid in timestamps:
                ev_dict['timestamp'] = timestamps[eid]
            events_out.append(put_chat_key(ev_dict, owners.get(eid)))
        if not event_has_valid_categories(ev_dict):
            categories_valid = False
        for field, val in zip(TOKEN_FIELDS, counts):
            totals[field] += val

    combined_usage = {
        'total_tokens': totals['total_tokens'],
        'cached_tokens': totals['cached_tokens'],
        'input_tokens': totals['input_tokens'],
        'output_tokens': totals['output_tokens'],
        'available': any_available,
    }
    if any_partial:
        combined_usage['partial'] = True

    if provider_name == 'gemini':
        failed_runs_today = 0
        last_run_at = None
        last_run_ep = None
        last_usage_at = None
        last_usage_ep = None
        chosen_status = 'idle' if any_available else 'unavailable'
        chosen_message = 'No tracked Gemini runs today' if any_available else 'Unavailable'
        for entry in device_entries:
            g_usage = entry['gemini']['usage']
            if not g_usage.get('available'):
                continue
            failed_runs_today += int(clean_number(g_usage.get('failed_runs_today')))
            run_at = g_usage.get('last_run_at')
            if isinstance(run_at, str):
                try:
                    _, r_ep = parse_iso_timestamp(run_at)
                except ValueError:
                    r_ep = None
                if r_ep is not None and (last_run_ep is None or r_ep > last_run_ep):
                    last_run_ep = r_ep
                    last_run_at = run_at
                    chosen_status = g_usage.get('status') or 'idle'
                    chosen_message = g_usage.get('status_message') or chosen_message
            elif last_run_at is None and g_usage.get('status') not in (None, 'idle', 'unavailable'):
                chosen_status = g_usage.get('status')
                chosen_message = g_usage.get('status_message') or chosen_message
            usage_at = g_usage.get('last_usage_at')
            if isinstance(usage_at, str):
                try:
                    _, u_ep = parse_iso_timestamp(usage_at)
                except ValueError:
                    u_ep = None
                if u_ep is not None and (last_usage_ep is None or u_ep > last_usage_ep):
                    last_usage_ep = u_ep
                    last_usage_at = usage_at
        combined_usage.update(
            status=chosen_status,
            status_message=chosen_message,
            last_run_at=last_run_at,
            failed_runs_today=failed_runs_today,
            last_usage_at=last_usage_at,
        )

    combined_usage = enrich_usage_with_pricing(combined_usage, categories_valid=categories_valid)

    return combined_usage, duplicate_events, conflict_events, any_partial


class DeviceUsageAggregator:
    """Reads ~/.codex/argon/devices.json and aggregates local + remote numeric snapshots."""

    def __init__(self, codex_home):
        self.home = Path(codex_home).expanduser()
        self.last_good = {}
        self._lock = threading.Lock()

    def _evict_expired_cache(self, today_str, tz_str, period_start_epoch, period_origin_available):
        expired = []
        for dev_id, cached in self.last_good.items():
            if cached.get('timezone') != tz_str:
                expired.append(dev_id)
                continue
            if cached.get('schema') == SCHEMA_V2:
                cov_end = cached.get('coverage_end_epoch')
                if cov_end is None or cov_end < period_start_epoch:
                    expired.append(dev_id)
            else:
                if period_origin_available or cached.get('day') != today_str:
                    expired.append(dev_id)
        for dev_id in expired:
            self.last_good.pop(dev_id, None)

    def _recompute_cached_age(self, cached, now_epoch):
        raw_age = max(0.0, now_epoch - cached['collected_epoch'])
        is_fresh = raw_age <= FRESH_THRESHOLD_SECONDS
        status = 'fresh' if is_fresh else 'stale'
        age_seconds = int(raw_age) if is_fresh else max(FRESH_THRESHOLD_SECONDS + 1, int(raw_age))
        updated = dict(cached)
        updated['status'] = status
        updated['age_seconds'] = age_seconds
        return updated

    def aggregate(
        self,
        today,
        tz,
        codex_collector,
        gemini_collector,
        *,
        now=None,
        period_start=None,
        period_origin_available=False,
        local_providers=None,
    ):
        now_epoch, now_dt = coerce_now(now)
        tz_obj, tz_str = coerce_timezone(tz)
        today_date = date.fromisoformat(str(today)) if not isinstance(today, date) else today
        today_str = str(today_date)
        today_midnight_epoch = datetime(
            today_date.year, today_date.month, today_date.day, tzinfo=tz_obj
        ).timestamp()

        if period_start is not None:
            period_start_epoch, _ = coerce_now(period_start)
        else:
            period_start_epoch = today_midnight_epoch

        use_range_mode = bool(
            period_origin_available
            or period_start is not None
            or abs(period_start_epoch - today_midnight_epoch) > 1.0
        )
        warnings = []

        with self._lock:
            self._evict_expired_cache(today_str, tz_str, period_start_epoch, period_origin_available)
            try:
                manifest = load_devices_manifest(self.home)
            except ValueError as exc:
                return {'devices': None, 'totals': None, 'warnings': [str(exc)]}

            if manifest is None:
                return {'devices': None, 'totals': None, 'warnings': []}

            local_cfg = manifest['local_device']
            if local_providers is not None:
                local_codex, local_gemini = local_providers
            elif use_range_mode and hasattr(codex_collector, 'collect_events_in_range'):
                local_codex = codex_collector.collect_events_in_range(
                    period_start_epoch, now_epoch, include_timestamps=True
                )
                local_gemini = gemini_collector.collect_events_in_range(
                    period_start_epoch, now_epoch, include_timestamps=True
                )
            else:
                local_codex = codex_collector.collect_events(today_date, tz_obj)
                local_gemini = gemini_collector.collect_events(today_date, tz_obj)

            local_codex_validated = _validate_provider_payload(
                'codex', local_codex, allow_chat_keys=True, allow_local_timestamps=True
            )
            local_gemini_validated = _validate_provider_payload(
                'gemini', local_gemini, allow_chat_keys=True, allow_local_timestamps=True
            )

            if local_codex_validated['partial']:
                warnings.append(f"{local_cfg['name']} Codex usage has records with missing or unsupported identity")
            if local_gemini_validated['partial']:
                warnings.append(f"{local_cfg['name']} Gemini usage has records with missing or unsupported identity")

            devices_out = [
                {
                    'id': local_cfg['id'],
                    'name': local_cfg['name'],
                    'day': today_str,
                    'collected_at': format_utc(now_dt),
                    'status': 'fresh',
                    'age_seconds': 0,
                    'codex': dict(local_codex_validated['usage']),
                    'gemini': dict(local_gemini_validated['usage']),
                }
            ]
            active_entries = [
                {
                    'id': local_cfg['id'],
                    'name': local_cfg['name'],
                    'codex': local_codex_validated,
                    'gemini': local_gemini_validated,
                }
            ]
            # Coverage and freshness issues below affect both providers.
            partial = False
            remote_titles = {}

            for remote in manifest['remotes']:
                rid = remote['id']
                rname = remote['name']
                try:
                    snap_path = resolve_snapshot_path(self.home, remote['snapshot_file'])
                except ValueError as exc:
                    self.last_good.pop(rid, None)
                    partial = True
                    warnings.append(f'{rname} snapshot path rejected: {exc}')
                    devices_out.append(_UnavailableDeviceRow(rid, rname))
                    continue

                if not snap_path.exists():
                    self.last_good.pop(rid, None)
                    partial = True
                    warnings.append(f'{rname} usage snapshot unavailable; combined total is partial')
                    devices_out.append(_UnavailableDeviceRow(rid, rname))
                    continue

                validated = None
                validation_error = None
                try:
                    if snap_path.is_symlink():
                        raise ValueError('Remote snapshot file must not be a symlink')
                    raw_bytes = snap_path.read_bytes()
                    if len(raw_bytes) > MAX_SNAPSHOT_BYTES:
                        raise ValueError('Snapshot file exceeds 8 MiB limit')
                    raw_payload = json.loads(raw_bytes.decode('utf-8'))
                    validated = validate_remote_snapshot(
                        raw_payload,
                        expected_device_id=rid,
                        expected_timezone=tz_str,
                        expected_day=today_str,
                        now=now_epoch,
                    )
                    # Only cache as last_good if schema is compatible with current period mode
                    if validated['schema'] == SCHEMA_V2 or (
                        not period_origin_available and abs(period_start_epoch - today_midnight_epoch) <= 1.0
                    ):
                        self.last_good[rid] = validated
                except json.JSONDecodeError:
                    validation_error = 'invalid snapshot JSON'
                except ValueError as exc:
                    validation_error = str(exc)
                except (OSError, UnicodeError):
                    validation_error = 'unreadable snapshot file'

                if validated is None:
                    cached = self.last_good.get(rid)
                    can_use_cached = False
                    if cached is not None and cached.get('timezone') == tz_str:
                        if cached.get('schema') == SCHEMA_V2:
                            can_use_cached = cached.get('coverage_end_epoch', 0) >= period_start_epoch
                        else:
                            can_use_cached = (
                                not period_origin_available
                                and abs(period_start_epoch - today_midnight_epoch) <= 1.0
                                and cached.get('day') == today_str
                            )
                    if can_use_cached:
                        partial = True
                        validated = self._recompute_cached_age(cached, now_epoch)
                        warnings.append(
                            f'{rname} incoming snapshot invalid ({validation_error}); preserving previous good snapshot'
                        )
                    else:
                        partial = True
                        warnings.append(
                            f'{rname} usage snapshot unavailable ({validation_error}); combined total is partial'
                        )
                        devices_out.append(_UnavailableDeviceRow(rid, rname))
                        continue

                # Honest schema 1 handling: never mix day-only schema 1 counts when a reset period is active
                # or when the period spans outside today's midnight boundary.
                if validated['schema'] == SCHEMA_VERSION and (
                    period_origin_available or abs(period_start_epoch - today_midnight_epoch) > 1.0
                ):
                    partial = True
                    warnings.append(
                        f'{rname} usage snapshot uses legacy day-only schema 1 and cannot cover the reset period; combined total is partial'
                    )
                    devices_out.append(_UnavailableDeviceRow(rid, rname))
                    continue

                if validated['schema'] == SCHEMA_V2:
                    codex_for_period = _filter_validated_provider_to_range(
                        'codex', validated['codex'], period_start_epoch, now_epoch
                    )
                    gemini_for_period = _filter_validated_provider_to_range(
                        'gemini', validated['gemini'], period_start_epoch, now_epoch
                    )
                    cov_start_ep = validated['coverage_start_epoch']
                    cov_end_ep = validated['coverage_end_epoch']
                    if cov_start_ep > period_start_epoch + 1.0:
                        partial = True
                        codex_for_period['partial'] = True
                        codex_for_period['usage']['partial'] = True
                        gemini_for_period['partial'] = True
                        gemini_for_period['usage']['partial'] = True
                        warnings.append(
                            f"{rname} history coverage starts at {validated['coverage_start']}, after reset period start; combined total is partial"
                        )
                    if cov_end_ep < period_start_epoch:
                        partial = True
                        codex_for_period['partial'] = True
                        codex_for_period['usage']['partial'] = True
                        gemini_for_period['partial'] = True
                        gemini_for_period['usage']['partial'] = True
                        warnings.append(
                            f'{rname} usage snapshot predates the latest reset; combined total may be partial'
                        )
                else:
                    codex_for_period = validated['codex']
                    gemini_for_period = validated['gemini']

                remote_titles.update(validated.get('chat_titles', {}))
                if validated['schema'] == SCHEMA_V2:
                    for key, title in apply_historical_chat_keys(snap_path, rid, codex_for_period).items():
                        remote_titles.setdefault(key, title)

                if validated['status'] != 'fresh':
                    partial = True
                    warnings.append(
                        f"{rname} usage snapshot is stale ({validated['age_seconds']}s old); combined total may be partial"
                    )
                if codex_for_period['unidentified_events'] > 0:
                    warnings.append(f'{rname} Codex usage has records with missing or unsupported identity')
                if gemini_for_period['unidentified_events'] > 0:
                    warnings.append(f'{rname} Gemini usage has records with missing or unsupported identity')

                devices_out.append({
                    'id': rid,
                    'name': validated['device_name'] or rname,
                    'day': validated['day'],
                    'collected_at': validated['collected_at'],
                    'status': validated['status'],
                    'age_seconds': validated['age_seconds'],
                    'codex': dict(codex_for_period['usage']),
                    'gemini': dict(gemini_for_period['usage']),
                })
                active_entries.append({
                    'id': rid,
                    'name': validated['device_name'] or rname,
                    'codex': codex_for_period,
                    'gemini': gemini_for_period,
                })

            providers = {'codex': [], 'gemini': []}
            combined_codex, codex_dups, codex_conflicts, codex_partial = combine_provider_events(
                'codex', active_entries, warnings, events_out=providers['codex']
            )
            combined_gemini, gemini_dups, gemini_conflicts, gemini_partial = combine_provider_events(
                'gemini', active_entries, warnings, events_out=providers['gemini']
            )
            total_dups = codex_dups + gemini_dups
            total_conflicts = codex_conflicts + gemini_conflicts
            trend_partial = partial or codex_partial or any(
                not entry['codex']['usage']['available'] for entry in active_entries
            )
            if codex_partial or gemini_partial or total_conflicts > 0:
                partial = True
            if partial:
                combined_codex['partial'] = True
                combined_gemini['partial'] = True

            totals = {
                'codex': combined_codex,
                'gemini': combined_gemini,
                'partial': partial,
                'duplicate_events': total_dups,
                'pricing_reference': pricing_reference_metadata(
                    now=now_epoch, day=today_str, tz=tz_str
                ),
            }
            if total_conflicts > 0:
                totals['conflict_events'] = total_conflicts

            resolver = getattr(codex_collector, 'chat_attribution', None)
            if resolver is not None:
                remote_titles.update(resolver.titles())
            return {
                'devices': devices_out,
                'totals': totals,
                'warnings': warnings,
                'chat_usage': build_chat_usage(
                    period_start_epoch, now_epoch, providers, remote_titles,
                    partial or not combined_codex['available'] or not combined_gemini['available'],
                    trend_partial=trend_partial,
                ),
            }


def main(argv=None):
    os.environ.pop('GEMINI_API_KEY', None)
    parser = argparse.ArgumentParser(description='TokenPilot data-only numeric usage exporter')
    parser.add_argument('--export', action='store_true', help='Export one numeric device usage snapshot as JSON')
    parser.add_argument('--codex-home', default=os.environ.get('CODEX_HOME') or str(Path.home() / '.codex'))
    parser.add_argument('--device-id', required=True, help='Stable device identifier (e.g. ubuntu)')
    parser.add_argument('--device-name', required=True, help='Display name for device (e.g. Ubuntu)')
    parser.add_argument('--timezone', default='Asia/Shanghai', help='IANA timezone name (e.g. Asia/Shanghai)')
    parser.add_argument('--schema', type=int, choices=SUPPORTED_SCHEMAS, default=None, help='Snapshot schema version (1 or 2)')
    parser.add_argument('--history-days', type=float, default=None, help='Rolling history retention days for schema 2')
    parser.add_argument('--now', default=None, help='Optional ISO-8601 or epoch timestamp for deterministic tests')
    args = parser.parse_args(argv)
    exporter = DeviceExporter(
        codex_home=args.codex_home,
        device_id=args.device_id,
        device_name=args.device_name,
        timezone=args.timezone,
        schema=args.schema,
        history_days=args.history_days,
    )
    print(exporter.export_json(now=args.now), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
