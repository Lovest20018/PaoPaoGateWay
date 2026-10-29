import copy
import json
import time
import unittest
from unittest.mock import patch
from test_gateway_api import api, FakeCore, IDENTITY, BOOT, TOKEN, URL


def row(sequence=1):
    return {'id': str(sequence), 'sequence': sequence, 'started_at': 1700000000.0,
        'ended_at': 1700000001.0, 'last_observed_at': 1700000001.0, 'state': 'closed',
        'transport': 'tcp', 'inbound': 'tproxy', 'source_address': '192.0.2.1', 'source_port': 1234,
        'original_destination': '7.1.2.3', 'destination_port': 443, 'domain': 'example.com',
        'actual_target_address': '93.184.215.14', 'upload': 10, 'download': 100,
        'decision': {'status': 'observed', 'source': 'sniffbox_outbound_dispatch', 'rule_id': 'mode:free',
            'rule_kind': 'mode_dispatch', 'reason': 'free_mode_no_rule', 'outbound_id': 'direct',
            'fallback': False, 'config_revision': 'a' * 64, 'decided_at': 1700000000.0,
            'connect_result': 'connected', 'application_result': 'unknown'},
        'secret': 'private-config'}


class EvidenceCore(FakeCore):
    def __init__(self):
        super().__init__()
        self.info['integration']['evidence_schema'] = 1
        self.evidence_boot = BOOT
        self.evidence_time = None
        self.records = [row()]
        self.gap = False

    def request(self, path, body=None, timeout=3):
        if path != '/sniffbox/evidence':
            return super().request(path, body, timeout)
        self.calls.append((path, body, timeout))
        sampled = self.evidence_time if self.evidence_time is not None else time.time()
        if body['resource'] == 'fakeip':
            data = {'status': 'matched', 'source': 'actual_fakeip_cache', 'domain': 'example.com',
                    'fakeip': '7.1.2.3', 'cidr': '7.0.0.0/8', 'dns_ttl_seconds': 3,
                    'valid_until': None, 'validity': 'until_evicted_or_core_restart',
                    'algorithm_prediction': False, 'historical_ownership_verified': False,
                    'secret': 'private-config'}
        else:
            records = [r for r in self.records if r['sequence'] > body.get('after', 0)]
            data = {'status': 'available' if records else 'no_records', 'items': copy.deepcopy(records[:body['limit']]),
                'total': len(records), 'truncated': len(records) > body['limit'],
                'next_sequence': records[body['limit'] - 1]['sequence'] if len(records) > body['limit'] else max([r['sequence'] for r in self.records], default=0),
                'oldest_sequence': min([r['sequence'] for r in self.records], default=0),
                'latest_sequence': max([r['sequence'] for r in self.records], default=0),
                'gap': self.gap, 'discarded_records': 4 if self.gap else 0,
                'capacity': 1024, 'retention_seconds': 300, 'complete_history': False,
                'scope': 'tcp_after_sniff', 'view': 'active' if body['active'] else 'recent'}
        return {'sampled_at': sampled, 'boot_id': self.evidence_boot, 'data': data}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.core = EvidenceCore()
        self.app = api.Application(api.Settings(IDENTITY, TOKEN, probe_urls=(URL,)), self.core)

    def get(self, path):
        return self.app.dispatch('GET', '/api/v1/' + path)

    def error(self, code, path):
        with self.assertRaises(api.ApiError) as caught:
            self.get(path)
        self.assertEqual(caught.exception.code, code)

    def test_fakeip_real_mapping_and_projection(self):
        value = self.get('fakeip/lookup?domain=EXAMPLE.COM.')
        self.assertEqual(value['data']['source'], 'actual_fakeip_cache')
        self.assertIsNone(value['data']['valid_until'])
        self.assertFalse(value['data']['algorithm_prediction'])
        self.assertNotIn('private', json.dumps(value))
        self.get('fakeip/lookup?ip=7.1.2.3')
        for query in ('', '?ip=::1', '?domain=x&ip=7.1.2.3', '?domain=x&domain=y'):
            with self.assertRaises(api.ApiError):
                self.get('fakeip/lookup' + query)

    def test_short_connection_cursor_mode_and_filter_binding(self):
        self.core.records = [row(1), row(2), row(3)]
        first = self.get('connections?view=recent&limit=1')['data']
        self.assertTrue(first['truncated'])
        self.assertEqual(first['items'][0]['id'], '1')
        self.assertEqual(first['items'][0]['decision']['outbound_id'], 'direct')
        second = self.get('connections?view=recent&limit=1&cursor=' + first['next_cursor'])['data']
        self.assertEqual(second['items'][0]['id'], '2')
        self.assertNotIn('private', json.dumps(second))
        self.error('INVALID_CURSOR', 'decisions?cursor=' + first['next_cursor'])
        self.error('INVALID_CURSOR', 'connections?view=recent&domain=changed.com&cursor=' + first['next_cursor'])
        self.error('CURSOR_OFFSET_CONFLICT', 'connections?view=recent&offset=1&cursor=' + first['next_cursor'])

    def test_restart_cursor_expiry_gap_and_source_age(self):
        cursor = self.get('decisions')['data']['next_cursor']
        self.core.info['integration']['boot_id'] = '00000000-0000-4000-8000-000000000003'
        self.error('CURSOR_EXPIRED', 'decisions?cursor=' + cursor)
        self.core.info['integration']['boot_id'] = BOOT
        self.core.gap = True
        self.core.evidence_time = time.time() - 100
        result = self.get('decisions')['data']
        self.assertTrue(result['gap'])
        self.assertTrue(result['freshness']['stale'])
        self.assertFalse(result['complete_history'])
        self.assertEqual(result['source_sampled_at'], self.core.evidence_time)

    def test_time_filters_limits_and_mode_capabilities(self):
        self.get('decisions?since=100&until=200')
        payload = self.core.calls[-1][1]
        self.assertEqual(payload['since'], 100.0)
        for query in ('limit=201', 'since=nan', 'since=200&until=100', 'cursor=bad', 'client=invalid'):
            with self.assertRaises(api.ApiError):
                self.get('decisions?' + query)
        capability = self.get('capabilities')['data']
        self.assertTrue(capability['features']['fakeip_lookup'])
        self.assertTrue(capability['features']['decision_history'])
        self.core.info['mode'] = 'yaml'
        capability = self.get('capabilities')['data']
        self.assertFalse(capability['features']['decision_history'])
        self.assertEqual(capability['feature_states']['decisions'], 'unavailable')

    def test_probe_joins_exact_loopback_port_and_records_stage_results(self):
        value = row()
        value.update(source_port=50001, inbound='healthcheck')
        self.core.records = [value]
        runner = lambda url, timeout: {'ok': True, 'status': 204, 'ms': 20,
            'local_source_port': 50001, 'stages': {'tcp': 'connected', 'tls': 'completed', 'http': 'completed'},
            'timings_ms': {'tls': 10, 'http': 3}}
        self.app.probe_runner = runner
        result = self.app.probe(json.dumps({'url': URL}).encode())['data']
        self.assertEqual(result['connection_id'], '1')
        self.assertEqual(result['connected_address'], '93.184.215.14')
        self.assertEqual(result['outbound_id'], 'direct')
        self.assertEqual(result['stages']['tls'], 'completed')
        self.assertEqual(self.get('runtime')['data']['last_probe']['probe_id'], result['probe_id'])

    def test_probe_does_not_join_by_domain_and_time_only(self):
        self.core.records[0].update(source_port=50002, inbound='healthcheck')
        self.app.probe_runner = lambda url, timeout: {'ok': False, 'local_source_port': 50001,
            'stages': {'tcp': 'unknown', 'tls': 'failed', 'http': 'not_tested'},
            'failure_stage': 'tls', 'timed_out': True}
        result = self.app.probe(json.dumps({'url': URL}).encode())['data']
        self.assertIsNone(result['connection_id'])
        self.assertIsNone(result['connected_address'])
        self.assertTrue(result['timed_out'])


if __name__ == '__main__':
    unittest.main()
