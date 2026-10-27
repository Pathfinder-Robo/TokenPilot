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
from zoneinfo import ZoneInfo
from forecasting import QuotaHistory, log_sample, current_sample, build_forecast, epoch
from nextreset import fetch_summary, summary_is_stale, URL as NEXTRESET_URL

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
    return {'total_tokens': 0, 'cached_tokens': 0, 'input_tokens': 0,
            'output_tokens': 0, 'available': available}


def describe_run(status):
    if status == 'completed':
        return 'completed', 'Latest Gemini run completed', False
    if status == 'running':
        return 'running', 'Gemini running · waiting for reported usage', False
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
    """Tail files; retain only timestamps, numeric usage and response IDs in memory."""
    def __init__(self, home):
        self.home = Path(home)
        self.files = {}

    def collect(self, day, tz):
        paths = []
        for folder in ('sessions', 'archived_sessions'):
            root = self.home / folder
            if root.is_dir():
                paths.extend(root.rglob('*.jsonl'))
        live = set()
        for path in paths:
            key = str(path)
            live.add(key)
            try:
                stat = path.stat()
                state = self.files.get(key)
                if not state or state['inode'] != stat.st_ino or stat.st_size < state['offset']:
                    state = {'inode': stat.st_ino, 'offset': 0, 'records': {}, 'fallback': [], 'previous': {}, 'quota': []}
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
                        if b'"token_usage_record"' not in line and b'"token_count"' not in line:
                            continue
                        try:
                            record = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        payload = record.get('payload') or {}
                        if not isinstance(payload, dict):
                            continue
                        if record.get('type') == 'token_usage_record' and isinstance(payload.get('usage'), dict):
                            response = payload.get('response_id') or f'{path.name}:{record.get("ordinal", start)}'
                            state['records'][response] = (record.get('timestamp'), payload['usage'])
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
                            state['previous'] = current
                            state['fallback'].append((record.get('timestamp'), delta))
            except OSError:
                continue
        self.files = {key: state for key, state in self.files.items() if key in live}
        total = empty_usage()
        records = {}
        fallback_files = set()
        for key, state in self.files.items():
            if state['records']:
                total['available'] = True
                records.update(state['records'])  # A response copied/archived twice counts once.
            elif state['fallback'] and Path(key).name not in fallback_files:
                total['available'] = True
                fallback_files.add(Path(key).name)
                for stamp, usage in state['fallback']:
                    if on_day(stamp, day, tz):
                        add_usage(total, usage)
        for stamp, usage in records.values():
            if on_day(stamp, day, tz):
                add_usage(total, usage)
        return total

    def recent_activity(self, now):
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


class GeminiUsage:
    def __init__(self, home):
        self.home = Path(home)
        self.cache = {}

    def collect(self, day, tz):
        summaries = list((self.home / 'argon/usage-cache').glob('*.json'))
        if summaries:
            return self.collect_summaries(summaries, day, tz)
        directories = {}
        for name in ('outcomes.jsonl', 'runs.jsonl'):
            for record in json_lines(self.home / 'argon' / name):
                if isinstance(record.get('result_dir'), str):
                    directory = Path(record['result_dir']).expanduser().resolve()
                    directories[directory] = record.get('started_at') or record.get('date')
        total = empty_usage(available=True)  # This is explicitly tracked local runs only.
        total.update(status='idle', status_message='No tracked Gemini runs today', last_run_at=None,
                     failed_runs_today=0, last_usage_at=None)
        seen = set()
        for directory, registered_at in directories.items():
            try:
                result = json.loads((directory / 'result.json').read_text())
            except (OSError, ValueError):
                result = None
            if on_day(registered_at, day, tz):
                state = result.get('status') if isinstance(result, dict) else 'running'
                display_status, display_message, failed = describe_run(state)
                if failed:
                    total['failed_runs_today'] += 1
                if total['last_run_at'] is None or registered_at > total['last_run_at']:
                    total['last_run_at'] = registered_at
                    total['status'] = display_status
                    total['status_message'] = display_message
            paths = sorted(directory.glob('interaction-*.json'))
            if paths:
                for path in paths:
                    try:
                        stat = path.stat()
                        key = str(path)
                        cached = self.cache.get(key)
                        if not cached or cached[0] != stat.st_mtime_ns or cached[1] != stat.st_size:
                            value = json.loads(path.read_text())
                            cached = (stat.st_mtime_ns, stat.st_size, {k: value.get(k) for k in ('id', 'created', 'usage')})
                            self.cache[key] = cached
                        interaction = cached[2]
                        identity = interaction.get('id') or key
                        if identity in seen:
                            continue
                        seen.add(identity)
                        stamp = interaction.get('created') or registered_at
                        if on_day(stamp, day, tz) and isinstance(interaction.get('usage'), dict):
                            add_usage(total, interaction['usage'], gemini=True)
                            if total['last_usage_at'] is None or stamp > total['last_usage_at']:
                                total['last_usage_at'] = stamp
                    except (OSError, ValueError, TypeError):
                        continue
            else:
                try:
                    if on_day(registered_at, day, tz) and isinstance(result.get('usage'), dict):
                        add_usage(total, result['usage'], gemini=True)
                except (OSError, ValueError, AttributeError):
                    pass
        return total

    def collect_summaries(self, paths, day, tz):
        total = empty_usage(available=True)
        total.update(status='idle', status_message='No tracked Gemini runs today', last_run_at=None,
                     failed_runs_today=0, last_usage_at=None)
        seen = set()
        for path in paths:
            try:
                state = json.loads(path.read_text())
                stamp = state.get('started_at')
                if on_day(stamp, day, tz):
                    status = state.get('status', 'running')
                    display_status, display_message, failed = describe_run(status)
                    total['failed_runs_today'] += int(failed)
                    if total['last_run_at'] is None or stamp > total['last_run_at']:
                        total.update(last_run_at=stamp, status=display_status, status_message=display_message)
                interactions = state.get('interactions') or {}
                if not interactions and on_day(stamp, day, tz):
                    add_usage(total, state.get('usage') or {}, gemini=True)
                for identity, interaction in interactions.items():
                    if identity in seen:
                        continue
                    seen.add(identity)
                    created = interaction.get('created')
                    if on_day(created, day, tz):
                        add_usage(total, interaction.get('usage') or {}, gemini=True)
                        if total['last_usage_at'] is None or created > total['last_usage_at']:
                            total['last_usage_at'] = created
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        return total


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
            answer[label] = {'used_percent': max(0, min(100, used)), 'resets_at': window.get('resetsAt')}
    if answer['five_hour'] is None and answer['weekly'] is None:
        raise ValueError('No 5-hour or weekly quota window returned')
    return answer


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
    def __init__(self, home, binary, tz, offline=False):
        self.home, self.binary, self.tz = Path(home), binary, tz
        self.offline = offline
        self.codex = CodexUsage(home)
        self.gemini = GeminiUsage(home)
        self.limits = {'five_hour': None, 'weekly': None, 'bucket': 'codex',
                       'fetched_at': None, 'stale': True, 'error': 'Connecting to Codex…'}
        self.lock = threading.Lock()
        self.fetching = False
        self.next_fetch = 0
        self.history = QuotaHistory(home, atomic_json)
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

    def snapshot(self, refresh=True):
        if refresh and not self.offline:
            self.refresh_limits()
            self.refresh_nextreset()
        today = datetime.now(self.tz).date()
        warnings = []
        try:
            routing = read_routing(self.home)
        except (OSError, ValueError, TypeError):
            routing = {'gemini_percent': 0}
            warnings.append('Routing file invalid; Gemini launcher will refuse new tasks. Choose a preference to repair.')
        with self.lock:
            limits = dict(self.limits)
            nextreset = dict(self.nextreset)
        codex = self.codex.collect(today, self.tz)
        now = time.time()
        if summary_is_stale(nextreset, now=now):
            nextreset['stale'] = True
        burn_tokens, samples = self.codex.recent_activity(now)
        current = current_sample(limits)
        if current:
            samples.append(current)
        try:
            self.history.add(samples, now)
        except OSError:
            warnings.append('Could not persist quota trend history')
        forecast = build_forecast(self.history.samples, limits, burn_tokens, now)
        return {'timestamp': utc_now(), 'day': str(today), 'timezone': str(self.tz),
                'codex': codex, 'gemini': self.gemini.collect(today, self.tz),
                'routing': routing, 'limits': limits, 'warnings': warnings,
                'forecast': forecast, 'nextreset': nextreset}

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
    parser.add_argument('--codex-home', default=os.environ.get('CODEX_HOME') or str(Path.home() / '.codex'))
    parser.add_argument('--codex-binary', default=shutil.which('codex'))
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
