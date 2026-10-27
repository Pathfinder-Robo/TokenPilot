"""Small public NextReset summary. No browser scripts, votes, or account data.
NextReset 公共社区重置预测客户端与模式验证器；不发送账户数据或执行脚本。
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import math
import urllib.parse
import urllib.request

URL = 'https://nextreset.ai/'
API_URL = 'https://nextreset.ai/api/forecast'
USER_AGENT = 'TokenPilot/2.2 (public reset summary)'
MAX_RESPONSE_BYTES = 1_000_000
MAX_CLOCK_SKEW_SECONDS = 300
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}


class SummaryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.card_depth = None
        self.heading_depth = None
        self.heading = []
        self.latest = None
        self.delayed = False
        self.kind = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in VOID:
            self.depth += 1
        if attrs.get('id') == 'next-reset':
            self.card_depth = self.depth
            self.kind = attrs.get('data-next-kind')
        if self.card_depth is not None and tag == 'h2':
            self.heading_depth = self.depth
        if self.latest is None and attrs.get('data-elapsed'):
            self.latest = attrs['data-elapsed']
        if 'tracking-delay' in attrs.get('class', '').split():
            self.delayed = True

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.heading_depth == self.depth:
            self.heading_depth = None
        if self.card_depth == self.depth:
            self.card_depth = None
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.heading_depth is not None:
            self.heading.append(data)


def parse_summary(html):
    parser = SummaryParser()
    parser.feed(html)
    headline = ' '.join(' '.join(parser.heading).split())
    if not headline:
        raise ValueError('NextReset page format changed')
    return {'headline': headline[:140], 'url': URL, 'latest_report_at': parser.latest,
            'source_delayed': parser.delayed, 'stale': False, 'error': None}


def _is_finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _normalize_https_url(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return None
    try:
        parsed = urllib.parse.urlsplit(text)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() != 'https' or not parsed.netloc or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return urllib.parse.urlunsplit(('https', parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _parse_iso_utc(value, field='timestamp'):
    if not isinstance(value, str):
        raise ValueError(f'Invalid {field} timestamp')
    text = value.strip()
    if not text or ('T' not in text and 't' not in text and ' ' not in text):
        raise ValueError(f'Invalid {field} timestamp')
    normalized = text[:-1] + '+00:00' if text.endswith(('Z', 'z')) else text
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f'Invalid {field} timestamp') from exc
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f'Missing timezone on {field} timestamp')
    return dt.astimezone(timezone.utc)


def _format_iso_utc(dt):
    dt_utc = dt.astimezone(timezone.utc)
    if dt_utc.microsecond == 0:
        return dt_utc.isoformat(timespec='seconds').replace('+00:00', 'Z')
    if dt_utc.microsecond % 1000 == 0:
        return dt_utc.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    return dt_utc.isoformat().replace('+00:00', 'Z')


def _resolve_now(now):
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            raise ValueError('now datetime must be timezone-aware')
        return now.astimezone(timezone.utc)
    if isinstance(now, str):
        return _parse_iso_utc(now, 'now')
    if _is_finite_number(now):
        return datetime.fromtimestamp(float(now), tz=timezone.utc)
    raise ValueError('Invalid now timestamp')


def parse_forecast(payload, now=None):
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid UTF-8 in forecast payload') from exc
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError('Invalid JSON in forecast payload') from exc
    if not isinstance(payload, dict):
        raise ValueError('Forecast payload must be a JSON object')

    state = payload.get('state')
    if not isinstance(state, str) or not state.strip():
        raise ValueError('Forecast state must be a non-empty string')
    announcement_state = state.strip()

    as_of_dt = _parse_iso_utc(payload.get('asOf'), 'asOf')
    expires_at_dt = _parse_iso_utc(payload.get('expiresAt'), 'expiresAt')
    if expires_at_dt <= as_of_dt:
        raise ValueError('Forecast expiresAt must be after asOf')

    windows = payload.get('windows')
    if not isinstance(windows, list):
        raise ValueError('Forecast windows must be a list')

    probability_24h = None
    probability_48h = None
    probability_72h = None
    seen_24h = False
    seen_48h = False
    seen_72h = False
    for window in windows:
        if not isinstance(window, dict):
            raise ValueError('Forecast window entry must be an object')
        hours = window.get('hours')
        if not _is_finite_number(hours) or hours <= 0:
            raise ValueError('Forecast window hours must be a positive number')
        prob = window.get('probability')
        if prob is not None:
            if not _is_finite_number(prob) or not (0.0 <= float(prob) <= 1.0):
                raise ValueError('Forecast window probability must be between 0 and 1')
            prob = float(prob)
        if hours == 24 and not seen_24h:
            seen_24h = True
            probability_24h = prob
        elif hours == 48 and not seen_48h:
            seen_48h = True
            probability_48h = prob
        elif hours == 72 and not seen_72h:
            seen_72h = True
            probability_72h = prob

    history = payload.get('history')
    latest_completed_at = None
    if history is not None:
        if not isinstance(history, dict):
            raise ValueError('Forecast history must be an object')
        last = history.get('last')
        if last is not None:
            if not isinstance(last, dict):
                raise ValueError('Forecast history.last must be an object')
            if last.get('date') is not None:
                latest_completed_at = _format_iso_utc(_parse_iso_utc(last.get('date'), 'history.last.date'))

    social = payload.get('social')
    published_at = None
    expected_by = None
    signal_url = None
    social_carried = False
    if social is not None:
        if not isinstance(social, dict):
            raise ValueError('Forecast social must be an object')
        if social.get('publishedAt') is not None:
            published_at = _format_iso_utc(_parse_iso_utc(social.get('publishedAt'), 'social.publishedAt'))
        if social.get('expectedBy') is not None:
            expected_by = _format_iso_utc(_parse_iso_utc(social.get('expectedBy'), 'social.expectedBy'))
        signal_url = _normalize_https_url(social.get('source'))
        carried = social.get('carriedFromLastGoodCheck')
        if carried is not None:
            if not isinstance(carried, bool):
                raise ValueError('Forecast social.carriedFromLastGoodCheck must be a boolean')
            social_carried = carried

    degraded = payload.get('degraded')
    if degraded is not None and not isinstance(degraded, bool):
        raise ValueError('Forecast degraded flag must be a boolean')

    checks = payload.get('checks')
    checks_failed = False
    if checks is not None:
        if not isinstance(checks, dict):
            raise ValueError('Forecast checks must be an object')
        for check_name, check_val in checks.items():
            if not isinstance(check_val, dict):
                raise ValueError(f'Forecast check {check_name} must be an object')
            if 'ok' in check_val and check_val['ok'] is not None:
                if not isinstance(check_val['ok'], bool):
                    raise ValueError(f'Forecast check {check_name}.ok must be a boolean')
                if not check_val['ok']:
                    checks_failed = True

    source_delayed = bool(degraded) or checks_failed or social_carried

    if announcement_state == 'announced':
        headline = 'Reset announced · awaiting rollout'
        latest_report_at = published_at if published_at is not None else latest_completed_at
    else:
        headline = 'Community reset forecast'
        latest_report_at = latest_completed_at

    now_dt = _resolve_now(now)
    if now_dt >= expires_at_dt:
        stale = True
        error = 'Forecast snapshot expired'
    elif (as_of_dt - now_dt).total_seconds() > MAX_CLOCK_SKEW_SECONDS:
        stale = True
        error = 'Forecast snapshot timestamp is in the future'
    else:
        stale = False
        error = None

    return {
        'headline': headline[:140],
        'url': URL,
        'latest_report_at': latest_report_at,
        'source_delayed': source_delayed,
        'stale': stale,
        'error': error,
        'probability_24h': probability_24h,
        'probability_48h': probability_48h,
        'probability_72h': probability_72h,
        'source_as_of': _format_iso_utc(as_of_dt),
        'expires_at': _format_iso_utc(expires_at_dt),
        'announcement_state': announcement_state,
        'latest_completed_at': latest_completed_at,
        'expected_by': expected_by,
        'signal_url': signal_url,
    }


def summary_is_stale(summary, now=None):
    if not isinstance(summary, dict):
        return True
    stale_flag = summary.get('stale')
    if stale_flag is not None and not isinstance(stale_flag, bool):
        return True
    if stale_flag is True:
        return True
    if summary.get('url') != URL or not summary.get('source_as_of'):
        return True
    expires_raw = summary.get('expires_at')
    if not expires_raw:
        return True
    try:
        now_dt = _resolve_now(now)
        expires_at_dt = _parse_iso_utc(expires_raw, 'expires_at')
        if now_dt >= expires_at_dt:
            return True
        if 'source_as_of' in summary:
            as_of_raw = summary.get('source_as_of')
            if not as_of_raw:
                return True
            as_of_dt = _parse_iso_utc(as_of_raw, 'source_as_of')
            if expires_at_dt <= as_of_dt:
                return True
            if (as_of_dt - now_dt).total_seconds() > MAX_CLOCK_SKEW_SECONDS:
                return True
    except (TypeError, ValueError):
        return True
    return False


def fetch_summary(now=None):
    request = urllib.request.Request(
        API_URL,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        final_url = response.geturl() if hasattr(response, 'geturl') else getattr(response, 'url', None)
        if final_url is not None:
            parsed_url = urllib.parse.urlsplit(str(final_url))
            if parsed_url.scheme != 'https' or parsed_url.hostname != 'nextreset.ai':
                raise ValueError('Unexpected NextReset response URL')
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError('NextReset response too large')
    try:
        payload = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid NextReset JSON response') from exc
    return parse_forecast(payload, now=now)
