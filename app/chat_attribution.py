"""Resolve usage events to local chat metadata and build period totals."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from chat_usage_trend import (
    ROLLING_TREND_BUCKET_SECONDS,
    ROLLING_TREND_WINDOW_SECONDS,
    build_usage_trend,
)
from pricing import enrich_usage_with_pricing, event_has_valid_categories

TOKEN_FIELDS = ('total_tokens', 'cached_tokens', 'input_tokens', 'output_tokens')
CHAT_KEY_RE = re.compile(r'[0-9a-f]{64}')
UUID_PATTERN = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
UUID_RE = re.compile(UUID_PATTERN, re.IGNORECASE)
FILENAME_UUID_RE = re.compile(r'(' + UUID_PATTERN + r')\.jsonl$', re.IGNORECASE)
CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]+')
OWNER_CONFLICT = object()


def normalize_uuid(value):
    return value.lower() if isinstance(value, str) and UUID_RE.fullmatch(value) else None


def extract_uuid_from_filename(filename):
    match = FILENAME_UUID_RE.search(Path(filename).name)
    return normalize_uuid(match[1]) if match else None


def hash_chat_key(thread_id):
    return hashlib.sha256(f'tokenpilot:chat:{thread_id}'.encode('utf-8')).hexdigest()


def is_valid_chat_key(value):
    return isinstance(value, str) and bool(CHAT_KEY_RE.fullmatch(value))


def merge_owner(existing, incoming):
    if existing is None:
        return incoming
    if incoming is None or incoming == existing:
        return existing
    return OWNER_CONFLICT


def sanitize_chat_title(value):
    if not isinstance(value, str):
        return None
    title = ' '.join(CONTROL_CHARS_RE.sub(' ', value).split())[:160].strip()
    if not title or title.startswith(('/', '~/', '../', './')) or UUID_RE.fullmatch(title):
        return None
    return title


def validate_chat_titles(value):
    if not isinstance(value, dict) or len(value) > 2048:
        raise ValueError('Chat titles must be a bounded object')
    result = {}
    for key, title in value.items():
        if not is_valid_chat_key(key) or not isinstance(title, str) or sanitize_chat_title(title) != title:
            raise ValueError('Invalid chat title metadata')
        result[key] = title
    return result


class ChatAttributionResolver:
    """Cache titles and parent relations using only local index metadata."""

    def __init__(self, codex_home):
        self.home = Path(codex_home)
        self.signature = None
        self.parents = {}
        self.index_titles = {}
        self.db_titles = {}

    def refresh(self):
        db = self.home / 'state_5.sqlite'
        index = self.home / 'session_index.jsonl'
        signature = tuple(self._signature(p) for p in (db, db.with_name(db.name + '-wal'), index))
        if signature == self.signature:
            return
        parents, index_titles, db_titles = {}, {}, {}
        if signature[0]:
            try:
                with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)) as conn:
                    try:
                        edges = conn.execute('SELECT child_thread_id, parent_thread_id FROM thread_spawn_edges')
                        for child, parent in edges:
                            child, parent = normalize_uuid(child), normalize_uuid(parent)
                            if child and parent and child != parent:
                                parents[child] = merge_owner(parents.get(child), parent)
                    except sqlite3.OperationalError:
                        pass
                    try:
                        for thread_id, name, title in conn.execute('SELECT id, name, title FROM threads'):
                            key = normalize_uuid(thread_id)
                            display = sanitize_chat_title(name) or sanitize_chat_title(title)
                            if key and display:
                                db_titles[key] = display
                    except sqlite3.OperationalError:
                        for thread_id, title in conn.execute('SELECT id, title FROM threads'):
                            key, display = normalize_uuid(thread_id), sanitize_chat_title(title)
                            if key and display:
                                db_titles[key] = display
            except sqlite3.Error:
                db_titles, parents = self.db_titles, self.parents
        if signature[2]:
            try:
                with index.open('rb') as source:
                    for line in source:
                        try:
                            row = json.loads(line)
                        except (ValueError, UnicodeError):
                            continue
                        if isinstance(row, dict):
                            key = normalize_uuid(row.get('id'))
                            title = sanitize_chat_title(row.get('thread_name'))
                            if key and title:
                                index_titles[key] = title
            except OSError:
                index_titles = self.index_titles
        self.signature = signature
        self.parents, self.index_titles, self.db_titles = parents, index_titles, db_titles

    @staticmethod
    def _signature(path):
        try:
            stat = path.stat()
            return stat.st_ino, stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def chat_key(self, value):
        thread = normalize_uuid(value)
        seen = set()
        while thread and thread not in seen and thread is not OWNER_CONFLICT:
            parent = self.parents.get(thread)
            if parent is None:
                return hash_chat_key(thread)
            seen.add(thread)
            thread = parent
        return None

    def titles(self, *, explicit_only=False, active_keys=None):
        sources = self.index_titles if explicit_only else {**self.db_titles, **self.index_titles}
        titles = {}
        for thread, title in sources.items():
            key = hash_chat_key(thread)
            if active_keys is None or key in active_keys:
                titles[key] = title
        return titles


def put_chat_key(event, key):
    if is_valid_chat_key(key):
        event['chat_key'] = key
    return event


def build_chat_usage(
    start,
    end,
    providers,
    titles,
    partial,
    *,
    trend_partial=None,
    trend_codex_events=None,
):
    rows = {}
    for provider, events in providers.items():
        groups = {}
        for event in events:
            if any(event[field] > 0 for field in TOKEN_FIELDS):
                groups.setdefault(event.get('chat_key') or 'unattributed', []).append(event)
        for key, group in groups.items():
            row = rows.setdefault(key, {'id': key, 'title': titles.get(key)})
            counts = {field: sum(event[field] for event in group) for field in TOKEN_FIELDS}
            counts['available'] = True
            valid = all(event_has_valid_categories(event) for event in group)
            row[provider] = enrich_usage_with_pricing(counts, categories_valid=valid)

    def stamp(epoch):
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat()

    chat_rows = list(rows.values())
    trend_events = providers.get('codex', ()) if trend_codex_events is None else trend_codex_events
    return {
        'start_at': stamp(start),
        'end_at': stamp(end),
        'partial': partial or 'unattributed' in rows,
        'rows': chat_rows,
        'trend': build_usage_trend(
            end - ROLLING_TREND_WINDOW_SECONDS,
            end,
            {'codex': trend_events},
            None,
            partial if trend_partial is None else trend_partial,
            bucket_seconds=ROLLING_TREND_BUCKET_SECONDS,
            titles=titles,
        ),
    }
