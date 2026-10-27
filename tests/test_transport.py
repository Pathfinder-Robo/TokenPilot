"""Unit tests for Argon worker HTTP IncompleteRead handling and bounded polling retries.
Argon 工作进程 HTTP IncompleteRead 容错处理与有界轮询重试的单元测试。
"""
import http.client
import io
import json
from pathlib import Path
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / 'app', ROOT / 'routing' / 'scripts', ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import argon_worker


def response(value=None, error=None):
    stream = MagicMock()
    stream.__enter__.return_value = stream
    stream.read.side_effect = [error] if error else [json.dumps(value).encode(), b'']
    return stream


class TransportTests(unittest.TestCase):
    def test_accepts_complete_json_with_broken_chunk_trailer(self):
        value = {'id': 'one', 'status': 'completed'}
        stream = response(error=http.client.IncompleteRead(json.dumps(value).encode()))
        self.assertEqual(argon_worker.read_json_response(stream), value)

    def test_rejects_truncated_json(self):
        stream = response(error=http.client.IncompleteRead(b'{"id":"one","sta'))
        with self.assertRaises(http.client.IncompleteRead):
            argon_worker.read_json_response(stream)

    def test_retries_get_without_repeating_post(self):
        queued = response({'id': 'one', 'status': 'in_progress'})
        broken = response(error=http.client.IncompleteRead(b'{"id":'))
        done = response({'id': 'one', 'status': 'completed', 'usage': {'total_tokens': 10}})
        output = io.StringIO()
        with patch.object(argon_worker.urllib.request, 'urlopen', side_effect=[queued, broken, done]) as open_url, \
             patch.object(argon_worker.time, 'sleep'), redirect_stdout(output):
            result = argon_worker.api_request({'model': 'gemini-4-argon'}, 'synthetic-secret', 60)
        self.assertEqual(result['usage']['total_tokens'], 10)
        methods = [call.args[0].get_method() for call in open_url.call_args_list]
        self.assertEqual(methods, ['POST', 'GET', 'GET'])
        self.assertNotIn('synthetic-secret', output.getvalue())

    def test_does_not_replay_ambiguous_post(self):
        broken = response(error=http.client.IncompleteRead(b'{"id":'))
        with patch.object(argon_worker.urllib.request, 'urlopen', return_value=broken) as open_url:
            with self.assertRaises(http.client.IncompleteRead):
                argon_worker.api_request({'model': 'gemini-4-argon'}, 'synthetic-secret', 60)
        self.assertEqual(open_url.call_count, 1)

    def test_poll_retries_are_bounded(self):
        queued = response({'id': 'one', 'status': 'in_progress'})
        failures = [response(error=http.client.IncompleteRead(b'{')) for _ in range(4)]
        with patch.object(argon_worker.urllib.request, 'urlopen', side_effect=[queued, *failures]) as open_url, \
             patch.object(argon_worker.time, 'sleep'), redirect_stdout(io.StringIO()):
            with self.assertRaises(http.client.IncompleteRead):
                argon_worker.api_request({'model': 'gemini-4-argon'}, 'synthetic-secret', 60)
        self.assertEqual(open_url.call_count, 5)


if __name__ == '__main__':
    unittest.main()
