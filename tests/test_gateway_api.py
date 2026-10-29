import copy
from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integration'))
import gateway_api as api

IDENTITY = '00000000-0000-4000-8000-000000000001'
BOOT = '00000000-0000-4000-8000-000000000002'
TOKEN = 'observer_' + 'x' * 40
URL = 'https://example.com/health'


def snapshot():
    return {'timestamp': time.time(), 'mode': 'free', 'version': 'test-core',
        'integration': {'schema_version': 1, 'boot_id': BOOT, 'config_revision': 'a' * 64,
                        'core_version': 'test-core', 'build_commit': 'b' * 40,
                        'configuration_state': 'loaded', 'loaded_at': 1700000000,
                        'last_load_result': 'success', 'reload_policy': 'restart',
                        'revision_scope': 'sniffbox_config'},
        'dns': {'trusted': {'ip': '192.0.2.53', 'port': 5304}},
        'fakeip': {'cidr': '7.0.0.0/8', 'mapped': 0},
        'traffic': {'downloadTotal': 0, 'uploadTotal': 12},
        'secret': 'must-not-leak', 'pplog': 'private-reporting-address'}


class FakeCore:
    def __init__(self):
        self.info = snapshot()
        self.rows = []
        self.result = {'ok': True, 'status': 204, 'ms': 20, 'body': 'private-body',
                       'setCookie': ['private-cookie'], 'error': 'private-exception'}
        self.calls = []
        self.error = None

    def request(self, path, body=None, timeout=3):
        self.calls.append((path, body, timeout))
        if self.error:
            raise self.error
        if path == '/sniffbox/info':
            return copy.deepcopy(self.info)
        if path == '/connections':
            return {'connections': self.rows}
        if path == '/sniffbox/probe':
            return copy.deepcopy(self.result)
        raise AssertionError('Unexpected core path: ' + path)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.core = FakeCore()
        self.settings = api.Settings(IDENTITY, TOKEN, probe_urls=(URL,))
        self.app = api.Application(self.settings, self.core)

    def request(self, path, method='GET', body=None):
        return self.app.dispatch(method, path, b'' if body is None else json.dumps(body).encode())

    def assert_error(self, code, path, method='GET', body=None):
        with self.assertRaises(api.ApiError) as caught:
            self.request(path, method, body)
        self.assertEqual(caught.exception.code, code)

    def test_runtime_identity_revision_and_projection(self):
        result = self.request('/api/v1/runtime')
        self.assertEqual(result['data']['boot_id'], BOOT)
        self.assertEqual(result['data']['config_revision'], 'a' * 64)
        self.assertEqual(result['data']['outbound_reachable'], 'unknown')
        self.assertFalse(result['data']['forwarding_verified'])
        self.assertEqual(result['data']['trusted_dns']['port'], 5304)
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('must-not-leak', json.dumps(result))

    def test_stale_does_not_refresh_source_time(self):
        self.core.info['timestamp'] = time.time() - 100
        result = self.request('/api/v1/runtime')
        self.assertEqual(result['data']['status'], 'stale')
        self.assertEqual(result['sampled_at'], self.core.info['timestamp'])
        self.assertLess(result['sampled_at'], result['fetched_at'] - 90)
        self.assert_error('STALE_DATA', api.PROBE_PATH, 'POST', {'url': URL})

    def test_mihomo_decision_snapshot_is_projected_and_requires_active_mode(self):
        self.assert_error('MIHOMO_NOT_ACTIVE', '/api/v1/mihomo/connections')
        self.core.info['mode'] = 'suburl'
        self.core.rows = [{'id': 'm1', 'chains': ['Node', 'Group'], 'rule': 'DomainSuffix',
                           'rulePayload': 'example.com', 'metadata': {'host': 'example.com', 'headers': 'private'},
                           'credentials': 'private'}]
        data = self.request('/api/v1/mihomo/connections')['data']
        self.assertEqual(data['items'][0]['chains'], ['Node', 'Group'])
        self.assertFalse(data['remote_target_verified'])
        self.assertNotIn('private', json.dumps(data))
        self.core.rows = [None]
        self.assert_error('INVALID_CORE_RESPONSE', '/api/v1/mihomo/connections')

    def test_persistent_history_query_validation_and_normalization(self):
        for query in ('before=-1', 'before=0', 'before=99999999999999999999', 'source=unknown', 'domain=', 'client=bad', 'limit=501'):
            self.assert_error('INVALID_QUERY', '/api/v1/history?' + query)
        with patch('history_store.query', return_value={'items': []}) as mocked:
            self.request('/api/v1/history?domain=EXAMPLE.COM.&client=2001:db8::1&source=mihomo_snapshot')
            self.assertEqual(mocked.call_args.args, (100, None, 'example.com', '2001:db8::1', 'mihomo_snapshot'))

    def test_unknown_timestamp(self):
        for value in (None, float('nan'), time.time() + 100, True, 'today'):
            self.core.info['timestamp'] = value
            result = self.request('/api/v1/runtime')['data']
            self.assertTrue(result['freshness']['stale'])
            self.assertIsNone(result['source_sampled_at'])

    def test_zero_and_missing_metric_are_distinct(self):
        for value, available in ((0, True), (None, False), (-1, False), (float('nan'), False)):
            self.core.info['traffic']['downloadTotal'] = value
            metric = self.request('/api/v1/metrics')['data']['metrics'][0]
            self.assertEqual(metric['available'], available)
            self.assertEqual(metric['value'], 0 if available else None)
            self.assertEqual(metric['reset'], 'core_restart')

    def test_empty_connections_and_filters(self):
        result = self.request('/api/v1/connections')['data']
        self.assertEqual(result['status'], 'no_records')
        self.assertFalse(result['complete_history'])
        self.assertFalse(result['freshness_known'])
        self.core.rows = [{'id': str(i), 'metadata': {'host': 'example.com', 'sourceIP': '192.0.2.1'},
                           'rule': 'unverified', 'chains': ['unverified']} for i in range(3)]
        result = self.request('/api/v1/connections?domain=EXAMPLE.COM&client=192.0.2.1&limit=1&offset=1')['data']
        self.assertEqual(result['items'][0]['id'], '1')
        self.assertEqual(result['next_offset'], 2)
        self.assertEqual(result['items'][0]['decision']['status'], 'unavailable')
        self.assertNotIn('unverified', json.dumps(result))
        for query in ('limit=0', 'offset=-1', 'limit=1&limit=2', 'bad=x', 'client=invalid'):
            with self.assertRaises(api.ApiError):
                self.request('/api/v1/connections?' + query)
        self.assert_error('TIME_FILTER_UNSUPPORTED', '/api/v1/connections?since=0')

    def test_capabilities_modes_and_unsupported(self):
        for mode in ('free', 'socks5', 'ovpn', 'yaml', 'suburl'):
            self.core.info['mode'] = mode
            result = self.request('/api/v1/capabilities')['data']
            self.assertEqual(result['mode'], mode)
            self.assertFalse(result['permissions']['configure'])
            self.assertEqual(result['feature_states']['decisions'], 'unsupported')
        self.assert_error('UNSUPPORTED', '/api/v1/decisions')
        self.assert_error('UNSUPPORTED', '/api/v1/fakeip/lookup?ip=7.1.1.1')

    def test_probe_mapping_no_redirects_and_no_response_leak(self):
        result = self.request(api.PROBE_PATH, 'POST', {'url': URL, 'timeout_ms': 2000,
                                                     'correlation_id': 'hnm-run-1'})['data']
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['correlation_id'], 'hnm-run-1')
        call = next(c for c in self.core.calls if c[0] == '/sniffbox/probe')
        self.assertEqual(call[1], {'url': URL, 'method': 'HEAD', 'follow': False,
                                  'timeoutMs': 2000, 'maxBody': 1})
        self.assertIsNone(result['connected_address'])
        self.assertEqual(result['stages']['tls'], 'unknown')
        self.assertNotIn('private', json.dumps(result))

    def test_probe_failures_are_not_successful_http_200(self):
        for raw in ({'ok': False, 'error': 'private-timeout'}, {'ok': True, 'status': 503, 'ms': 30}):
            self.core.result = raw
            result = self.request(api.PROBE_PATH, 'POST', {'url': URL})['data']
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('private', json.dumps(result))

    def test_probe_bounds_and_unsupported(self):
        for body in ({'url': URL + '?secret=1'}, {'url': URL, 'timeout_ms': 10001},
                     {'url': URL, 'correlation_id': 'x' * 65}, {'url': URL, 'follow': True}):
            with self.assertRaises(api.ApiError):
                self.request(api.PROBE_PATH, 'POST', body)
        for body in ({'url': URL, 'address_family': 'ipv6'}, {'url': URL, 'outbound_id': 'node1'}):
            self.assert_error('UNSUPPORTED', api.PROBE_PATH, 'POST', body)
        self.app.probes.acquire()
        self.app.probes.acquire()
        self.assert_error('PROBE_BUSY', api.PROBE_PATH, 'POST', {'url': URL})
        self.assertFalse(any(c[0] == '/sniffbox/probe' for c in self.core.calls))

    def test_restart_and_replacement_identity(self):
        before = self.request('/api/v1/instance')['data']
        self.core.info['integration']['boot_id'] = '00000000-0000-4000-8000-000000000003'
        after = self.request('/api/v1/instance')['data']
        self.assertEqual(before['id'], after['id'])
        self.assertNotEqual(before['boot_id'], after['boot_id'])
        self.assertEqual(before['config_revision'], after['config_revision'])
        self.assertNotEqual(api.Settings('00000000-0000-4000-8000-000000000004', TOKEN).instance_id, before['id'])

    def test_core_down_and_legacy_core(self):
        self.core.error = api.ApiError(503, 'CORE_UNAVAILABLE')
        self.assert_error('CORE_UNAVAILABLE', '/api/v1/runtime')
        self.core.error = None
        self.core.info.pop('integration')
        self.assert_error('INCOMPATIBLE_CORE', '/api/v1/runtime')

    def test_malformed_core_identity(self):
        for key, value in (('config_revision', None), ('boot_id', 'bad')):
            self.core.info = snapshot()
            self.core.info['integration'][key] = value
            self.assert_error('INCOMPATIBLE_CORE', '/api/v1/runtime')

    def test_auth_http_request_id_and_no_admin_proxy(self):
        server = api.ApiServer(('127.0.0.1', 0), self.app)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            for token, status in (('', 401), ('bad', 401), (TOKEN, 200)):
                conn = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
                conn.request('GET', '/api/v1/runtime', headers={'Authorization': 'Bearer ' + token,
                             'X-Request-ID': 'hnm-test-123'})
                response = conn.getresponse()
                body = json.loads(response.read())
                self.assertEqual(response.status, status)
                self.assertEqual(body['request_id'], 'hnm-test-123')
                self.assertEqual(response.getheader('X-Request-ID'), body['request_id'])
                self.assertEqual(response.getheader('Cache-Control'), 'no-store')
                conn.close()
            for method, path, status in (('DELETE', '/api/v1/connections', 405),
                                         ('GET', '/sniffbox/reload', 404),
                                         ('GET', '/api/v1/runtime?token=' + TOKEN, 400)):
                conn = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
                conn.request(method, path, headers={'Authorization': 'Bearer ' + TOKEN})
                response = conn.getresponse()
                raw = response.read()
                self.assertEqual(response.status, status)
                self.assertNotIn(TOKEN.encode(), raw)
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_target_policy(self):
        for url in ('file:///etc/passwd', 'http://127.0.0.1/', 'https://user:secret@example.com/',
                    'https://example.com:444/', 'https://example.local/', 'https://example.com/#private'):
            self.assertFalse(api.valid_probe_url(url), url)


if __name__ == '__main__':
    unittest.main()
