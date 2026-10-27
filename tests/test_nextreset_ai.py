"""Unit tests for NextReset JSON/HTML parsing, expiry validation, and HTTPS signal URLs.
NextReset 预测 JSON/HTML 解析、过期校验与 HTTPS 信号链接的单元测试。
"""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / 'app', ROOT / 'routing' / 'scripts', ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import nextreset


FIXTURE_PATH = Path(__file__).with_name('forecast-fixture.json')
FIXTURE_NOW = datetime(2026, 10, 25, 5, 10, 0, tzinfo=timezone.utc)


def load_fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))


class FakeHTTPResponse:
    def __init__(self, body, url=nextreset.API_URL):
        self._body = body if isinstance(body, bytes) else body.encode('utf-8')
        self.url = url
        self.read_Lengths = []

    def geturl(self):
        return self.url

    def read(self, amt=None):
        self.read_Lengths.append(amt)
        if amt is None:
            return self._body
        return self._body[:amt]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class NextResetAITests(unittest.TestCase):
    def test_canonical_constants_and_legacy_html_parser(self):
        self.assertEqual(nextreset.URL, 'https://nextreset.ai/')
        self.assertEqual(nextreset.API_URL, 'https://nextreset.ai/api/forecast')
        self.assertIn('TokenPilot/2.2', nextreset.USER_AGENT)

        html = (
            '<div id="next-reset" data-next-kind="auto">'
            '<h2>  Reset   window   open </h2>'
            '<span data-elapsed="2026-10-23T17:00:00Z" class="badge tracking-delay"></span>'
            '</div>'
        )
        legacy = nextreset.parse_summary(html)
        self.assertEqual(legacy['headline'], 'Reset window open')
        self.assertEqual(legacy['url'], 'https://nextreset.ai/')
        self.assertEqual(legacy['latest_report_at'], '2026-10-23T17:00:00Z')
        self.assertTrue(legacy['source_delayed'])
        self.assertFalse(legacy['stale'])
        self.assertIsNone(legacy['error'])

    def test_announced_fixture_mapping(self):
        payload = load_fixture()
        summary = nextreset.parse_forecast(payload, now=FIXTURE_NOW)

        self.assertEqual(summary['headline'], 'Reset announced · awaiting rollout')
        self.assertEqual(summary['url'], 'https://nextreset.ai/')
        self.assertEqual(summary['announcement_state'], 'announced')
        self.assertEqual(summary['latest_report_at'], '2026-10-25T03:40:04Z')
        self.assertEqual(summary['latest_completed_at'], '2026-10-23T17:00:00Z')
        self.assertEqual(summary['expected_by'], '2026-10-25T07:00:00Z')
        self.assertEqual(summary['source_as_of'], '2026-10-25T05:05:21.971Z')
        self.assertEqual(summary['expires_at'], '2026-10-25T05:20:21.971Z')
        self.assertAlmostEqual(summary['probability_24h'], 0.95)
        self.assertAlmostEqual(summary['probability_48h'], 0.97)
        self.assertAlmostEqual(summary['probability_72h'], 0.98)
        self.assertEqual(
            summary['signal_url'],
            'https://x.com/example/status/123',
        )
        self.assertFalse(summary['source_delayed'])
        self.assertFalse(summary['stale'])
        self.assertIsNone(summary['error'])

    def test_24h_probability_selected_not_48h_and_absent_is_none(self):
        payload = load_fixture()
        payload['windows'] = list(reversed(payload['windows']))
        summary = nextreset.parse_forecast(payload, now=FIXTURE_NOW)
        self.assertAlmostEqual(summary['probability_24h'], 0.95)
        self.assertNotAlmostEqual(summary['probability_24h'], 0.97)

        no_24h = load_fixture()
        no_24h['windows'] = [w for w in no_24h['windows'] if w['hours'] != 24]
        summary_no_24h = nextreset.parse_forecast(no_24h, now=FIXTURE_NOW)
        self.assertIsNone(summary_no_24h['probability_24h'])

        none_prob = load_fixture()
        none_prob['windows'][0]['probability'] = None
        summary_none_prob = nextreset.parse_forecast(none_prob, now=FIXTURE_NOW)
        self.assertIsNone(summary_none_prob['probability_24h'])

        zero_prob = load_fixture()
        zero_prob['windows'][0]['probability'] = 0
        summary_zero_prob = nextreset.parse_forecast(zero_prob, now=FIXTURE_NOW)
        self.assertEqual(summary_zero_prob['probability_24h'], 0.0)

    def test_unknown_state_is_benign_and_does_not_claim_completed_reset(self):
        for state in ('monitoring', 'normal', 'experimental_future_state'):
            payload = load_fixture()
            payload['state'] = state
            summary = nextreset.parse_forecast(payload, now=FIXTURE_NOW)
            self.assertEqual(summary['headline'], 'Community reset forecast')
            self.assertEqual(summary['announcement_state'], state)
            self.assertEqual(summary['latest_report_at'], '2026-10-23T17:00:00Z')
            self.assertNotIn('completed', summary['headline'].lower())

    def test_announcement_not_completed_after_expected_by_deadline(self):
        payload = load_fixture()
        payload['asOf'] = '2026-10-25T07:05:00Z'
        payload['expiresAt'] = '2026-10-25T07:20:00Z'
        after_deadline_now = datetime(2026, 10, 25, 7, 10, 0, tzinfo=timezone.utc)

        summary = nextreset.parse_forecast(payload, now=after_deadline_now)
        self.assertEqual(summary['headline'], 'Reset announced · awaiting rollout')
        self.assertEqual(summary['announcement_state'], 'announced')
        self.assertEqual(summary['latest_report_at'], '2026-10-25T03:40:04Z')
        self.assertEqual(summary['latest_completed_at'], '2026-10-23T17:00:00Z')
        self.assertFalse(summary['stale'])
        self.assertIsNone(summary['error'])

    def test_stale_expiry_and_future_clock_skew(self):
        payload = load_fixture()

        at_expiry = datetime(2026, 10, 25, 5, 20, 21, 971000, tzinfo=timezone.utc)
        expired_summary = nextreset.parse_forecast(payload, now=at_expiry)
        self.assertTrue(expired_summary['stale'])
        self.assertEqual(expired_summary['error'], 'Forecast snapshot expired')

        skewed_now = datetime(2026, 10, 25, 4, 59, 0, tzinfo=timezone.utc)
        skewed_summary = nextreset.parse_forecast(payload, now=skewed_now)
        self.assertTrue(skewed_summary['stale'])
        self.assertIsNotNone(skewed_summary['error'])

        minor_skew_now = datetime(2026, 10, 25, 5, 2, 0, tzinfo=timezone.utc)
        fresh_summary = nextreset.parse_forecast(payload, now=minor_skew_now)
        self.assertFalse(fresh_summary['stale'])
        self.assertIsNone(fresh_summary['error'])

    def test_timezone_parsing_and_normalization(self):
        payload = load_fixture()
        payload['asOf'] = '2026-10-25T07:05:21.971+02:00'
        payload['expiresAt'] = '2026-10-25T07:20:21.971+02:00'
        payload['social']['publishedAt'] = '2026-10-25T05:40:04+02:00'
        payload['social']['expectedBy'] = '2026-10-25T02:00:00-05:00'
        payload['history']['last']['date'] = '2026-10-23T12:00:00-05:00'

        summary = nextreset.parse_forecast(payload, now=FIXTURE_NOW)
        self.assertEqual(summary['source_as_of'], '2026-10-25T05:05:21.971Z')
        self.assertEqual(summary['expires_at'], '2026-10-25T05:20:21.971Z')
        self.assertEqual(summary['latest_report_at'], '2026-10-25T03:40:04Z')
        self.assertEqual(summary['expected_by'], '2026-10-25T07:00:00Z')
        self.assertEqual(summary['latest_completed_at'], '2026-10-23T17:00:00Z')

        for field in ('asOf', 'expiresAt'):
            bad = load_fixture()
            bad[field] = '2026-10-25T05:05:21.971'
            with self.assertRaises(ValueError):
                nextreset.parse_forecast(bad, now=FIXTURE_NOW)

        bad_order = load_fixture()
        bad_order['expiresAt'] = bad_order['asOf']
        with self.assertRaises(ValueError):
            nextreset.parse_forecast(bad_order, now=FIXTURE_NOW)

    def test_degraded_checks_and_carried_status_set_source_delayed(self):
        degraded_payload = load_fixture()
        degraded_payload['degraded'] = True
        degraded_summary = nextreset.parse_forecast(degraded_payload, now=FIXTURE_NOW)
        self.assertTrue(degraded_summary['source_delayed'])
        self.assertEqual(degraded_summary['headline'], 'Reset announced · awaiting rollout')

        check_failed_payload = load_fixture()
        check_failed_payload['checks']['context']['ok'] = False
        check_failed_summary = nextreset.parse_forecast(check_failed_payload, now=FIXTURE_NOW)
        self.assertTrue(check_failed_summary['source_delayed'])

        carried_payload = load_fixture()
        carried_payload['social']['carriedFromLastGoodCheck'] = True
        carried_summary = nextreset.parse_forecast(carried_payload, now=FIXTURE_NOW)
        self.assertTrue(carried_summary['source_delayed'])

    def test_missing_optional_data_is_benign_and_never_fakes_from_as_of(self):
        payload = load_fixture()
        payload.pop('social', None)
        payload.pop('history', None)
        payload.pop('checks', None)
        payload.pop('degraded', None)

        summary = nextreset.parse_forecast(payload, now=FIXTURE_NOW)
        self.assertIsNone(summary['latest_report_at'])
        self.assertIsNone(summary['latest_completed_at'])
        self.assertIsNone(summary['expected_by'])
        self.assertIsNone(summary['signal_url'])
        self.assertFalse(summary['source_delayed'])
        self.assertFalse(summary['stale'])

        fallback_payload = load_fixture()
        fallback_payload['social'] = None
        fallback_summary = nextreset.parse_forecast(fallback_payload, now=FIXTURE_NOW)
        self.assertEqual(fallback_summary['latest_report_at'], '2026-10-23T17:00:00Z')
        self.assertIsNone(fallback_summary['signal_url'])

    def test_48h_and_72h_probabilities_normalization_duplicates_and_boundaries(self):
        payload = load_fixture()
        payload['windows'] = [
            {'hours': 72, 'probability': 1},
            {'hours': 48, 'probability': 0},
            {'hours': 24, 'probability': 0.25},
            {'hours': 48, 'probability': 0.85},
            {'hours': 72, 'probability': 0.95},
        ]
        summary = nextreset.parse_forecast(payload, now=FIXTURE_NOW)
        self.assertEqual(summary['probability_24h'], 0.25)
        self.assertEqual(summary['probability_48h'], 0.0)
        self.assertIsInstance(summary['probability_48h'], float)
        self.assertEqual(summary['probability_72h'], 1.0)
        self.assertIsInstance(summary['probability_72h'], float)

        missing_48_72 = load_fixture()
        missing_48_72['windows'] = [{'hours': 24, 'probability': 0.5}]
        summary_missing = nextreset.parse_forecast(missing_48_72, now=FIXTURE_NOW)
        self.assertEqual(summary_missing['probability_24h'], 0.5)
        self.assertIsNone(summary_missing['probability_48h'])
        self.assertIsNone(summary_missing['probability_72h'])

        none_48_72 = load_fixture()
        none_48_72['windows'] = [
            {'hours': 24, 'probability': 0.5},
            {'hours': 48, 'probability': None},
            {'hours': 72, 'probability': None},
        ]
        summary_none = nextreset.parse_forecast(none_48_72, now=FIXTURE_NOW)
        self.assertIsNone(summary_none['probability_48h'])
        self.assertIsNone(summary_none['probability_72h'])

        for target_hours in (48, 72):
            for bad_prob in (True, False, -0.001, 1.001, float('nan'), float('inf'), '0.5'):
                bad = load_fixture()
                bad['windows'] = [
                    {'hours': 24, 'probability': 0.5},
                    {'hours': target_hours, 'probability': bad_prob},
                ]
                with self.assertRaises(ValueError):
                    nextreset.parse_forecast(bad, now=FIXTURE_NOW)

    def test_signal_url_https_validation_and_boundaries(self):
        payload = load_fixture()
        payload['social']['source'] = '  https://x.com/example/status/456  '
        summary = nextreset.parse_forecast(payload, now=FIXTURE_NOW)
        self.assertEqual(
            summary['signal_url'],
            'https://x.com/example/status/456',
        )

        upper_scheme = load_fixture()
        upper_scheme['social']['source'] = 'HTTPS://twitter.com/example/status/123?s=20'
        summary_upper = nextreset.parse_forecast(upper_scheme, now=FIXTURE_NOW)
        self.assertEqual(
            summary_upper['signal_url'],
            'https://twitter.com/example/status/123?s=20',
        )

        for rejected_source in (
            None,
            '',
            '   ',
            'http://x.com/example/status/123',
            'javascript:alert(1)',
            'ftp://x.com/status/123',
            'https://',
            'https:///status/123',
            'https://user:pass@x.com/status/123',
            'https://x.com/status/with space',
            'https://x.com:99999/status/123',
            123,
            True,
            ['https://x.com/status/123'],
            {'url': 'https://x.com/status/123'},
        ):
            candidate = load_fixture()
            candidate['social']['source'] = rejected_source
            parsed = nextreset.parse_forecast(candidate, now=FIXTURE_NOW)
            self.assertIsNone(parsed['signal_url'])

    def test_malformed_schema_probabilities_and_bytes_rejected(self):
        for invalid_payload in (
            '<html><body> snapshot </body></html>',
            b'\xff\xfeinvalid-utf8',
            [],
            None,
            123,
        ):
            with self.assertRaises(ValueError):
                nextreset.parse_forecast(invalid_payload, now=FIXTURE_NOW)

        for key in ('state', 'asOf', 'expiresAt', 'windows'):
            missing = load_fixture()
            missing.pop(key, None)
            with self.assertRaises(ValueError):
                nextreset.parse_forecast(missing, now=FIXTURE_NOW)

        for bad_prob in (True, False, -0.01, 1.01, 96, float('nan'), float('inf'), '0.96'):
            bad = copy.deepcopy(load_fixture())
            bad['windows'][0]['probability'] = bad_prob
            with self.assertRaises(ValueError):
                nextreset.parse_forecast(bad, now=FIXTURE_NOW)

        for bad_hours in (True, False, 0, -24, '24', float('nan')):
            bad = copy.deepcopy(load_fixture())
            bad['windows'][0]['hours'] = bad_hours
            with self.assertRaises(ValueError):
                nextreset.parse_forecast(bad, now=FIXTURE_NOW)

    def test_summary_is_stale_checks_url_expiry_and_recorded_stale(self):
        fresh = nextreset.parse_forecast(load_fixture(), now=FIXTURE_NOW)
        self.assertFalse(nextreset.summary_is_stale(fresh, now=FIXTURE_NOW))

        old_url = dict(fresh, url='https://nextreset.net/')
        self.assertTrue(nextreset.summary_is_stale(old_url, now=FIXTURE_NOW))

        expired_now = datetime(2026, 10, 25, 5, 25, 0, tzinfo=timezone.utc)
        self.assertTrue(nextreset.summary_is_stale(fresh, now=expired_now))

        missing_expiry = dict(fresh)
        missing_expiry.pop('expires_at', None)
        self.assertTrue(nextreset.summary_is_stale(missing_expiry, now=FIXTURE_NOW))

        malformed_expiry = dict(fresh, expires_at='not-a-timestamp')
        self.assertTrue(nextreset.summary_is_stale(malformed_expiry, now=FIXTURE_NOW))

        network_error_stale = dict(fresh, stale=True, error='Public tracker refresh unavailable')
        self.assertTrue(nextreset.summary_is_stale(network_error_stale, now=FIXTURE_NOW))

        future_skew_now = datetime(2026, 10, 25, 4, 55, 0, tzinfo=timezone.utc)
        self.assertTrue(nextreset.summary_is_stale(fresh, now=future_skew_now))

        self.assertTrue(nextreset.summary_is_stale(None, now=FIXTURE_NOW))

    def test_fetch_summary_network_wrapper_and_bounds(self):
        raw_bytes = FIXTURE_PATH.read_bytes()
        fake_response = FakeHTTPResponse(raw_bytes)

        with mock.patch('urllib.request.urlopen', return_value=fake_response) as mocked_open:
            summary = nextreset.fetch_summary(now=FIXTURE_NOW)

        self.assertEqual(mocked_open.call_count, 1)
        request = mocked_open.call_args[0][0]
        self.assertEqual(request.full_url, 'https://nextreset.ai/api/forecast')
        self.assertEqual(mocked_open.call_args[1].get('timeout'), 12)
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers.get('accept'), 'application/json')
        self.assertIn('TokenPilot/2.2', headers.get('user-agent', ''))
        self.assertEqual(fake_response.read_Lengths, [1_000_001])
        self.assertEqual(summary['url'], 'https://nextreset.ai/')
        self.assertEqual(summary['headline'], 'Reset announced · awaiting rollout')

        oversized = FakeHTTPResponse(b'x' * 1_000_001)
        with mock.patch('urllib.request.urlopen', return_value=oversized):
            with self.assertRaises(ValueError):
                nextreset.fetch_summary(now=FIXTURE_NOW)

        wrong_host = FakeHTTPResponse(raw_bytes, url='https://nextreset.net/')
        with mock.patch('urllib.request.urlopen', return_value=wrong_host):
            with self.assertRaises(ValueError):
                nextreset.fetch_summary(now=FIXTURE_NOW)

        invalid_utf8 = FakeHTTPResponse(b'\xff\xfe')
        with mock.patch('urllib.request.urlopen', return_value=invalid_utf8):
            with self.assertRaises(ValueError):
                nextreset.fetch_summary(now=FIXTURE_NOW)

        with mock.patch('urllib.request.urlopen', side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(urllib.error.URLError):
                nextreset.fetch_summary(now=FIXTURE_NOW)


if __name__ == '__main__':
    unittest.main()
