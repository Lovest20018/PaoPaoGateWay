#!/usr/bin/env python3
"""Emit OpenAPI plus deterministic responses through the real HTTP handler."""
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch
import hashlib
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_gateway_api import api, FakeCore, IDENTITY, BOOT, TOKEN, URL


def schema(kind, **kwargs):
    return {'type': kind, **kwargs}


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties,
            'required': list(properties) if required is None else required}


def main():
    timestamp = schema(['number', 'null'], description='UTC Unix seconds; original source time, null if unavailable', minimum=0)
    text = schema('string')
    identifier = schema('string', format='uuid')
    nullable_text = schema(['string', 'null'])
    number = schema(['number', 'null'])
    boolean = schema('boolean')
    freshness = obj({'age_seconds': number, 'max_age_seconds': {'const': 25}, 'stale': boolean})
    revision = schema('string', pattern='^[0-9a-f]{64}$')
    schemas = {
        'Instance': obj({'id': identifier, 'kind': {'const': 'paopaogateway'}, 'edition': {'const': 'debian'},
            'name': text, 'version': text, 'build_commit': text, 'core_version': nullable_text,
            'source_sha256': nullable_text,
            'boot_id': identifier, 'config_revision': revision}),
        'Capabilities': obj({'api_version': {'const': '1'}, 'instance_id': identifier,
            'access_role': {'const': 'observer'}, 'permissions': obj({k: boolean for k in
                ('observe', 'diagnose', 'configure', 'maintain')}),
            'features': schema('object', additionalProperties=boolean),
            'feature_states': schema('object', additionalProperties=text),
            'mode': schema('string', enum=['free', 'socks5', 'yaml', 'suburl', 'ovpn']),
            'supported_modes': schema('array', items=text), 'address_families': schema('object'),
            'probe_policy': {'const': 'exact_url_allowlist'}, 'probe_method': {'const': 'HEAD'},
            'probe_source': {'const': 'gateway_local_proxy'}, 'follows_redirects': {'const': False},
            'protocols': schema('array', items=text), 'config_revision': revision,
            'status': schema('string', enum=['available', 'stale']), 'freshness': freshness,
            'probe_urls': schema('array', items=schema('string', format='uri'), maxItems=16),
            'limits': obj({'connections': {'const': 200}, 'probe_timeout_ms': {'const': 10000},
                           'probe_concurrency': {'const': 2}})}),
        'Runtime': obj({'status': schema('string', enum=['available', 'stale']),
            'core_responding': {'const': True}, 'outbound_reachable': {'const': 'unknown'},
            'forwarding_verified': {'const': False}, 'source': {'const': 'sniffbox'},
            'boot_id': identifier, 'config_revision': revision, 'configuration': schema('object'),
            'core_version': nullable_text, 'build_commit': nullable_text,
            'outbound': schema('object'), 'trusted_dns': schema('object'),
            'source_sampled_at': timestamp, 'freshness': freshness, 'mode': text,
            'version': nullable_text, 'uptime_seconds': number, 'fakeip': schema('object'),
            'features': schema('object'), 'connections': schema('object'),
            'traffic': schema('object'), 'memory': schema('object')}),
        'Health': obj({'status': schema('string', enum=['available', 'stale']),
            'core_responding': {'const': True}, 'outbound_reachable': {'const': 'unknown'},
            'forwarding_verified': {'const': False}, 'boot_id': identifier,
            'config_revision': revision, 'source_sampled_at': timestamp, 'freshness': freshness}),
        'Metrics': obj({'source': {'const': 'sniffbox'}, 'source_sampled_at': timestamp,
            'freshness': freshness, 'metrics': schema('array', items=obj({
                'name': schema('string', enum=['download_total', 'upload_total']),
                'unit': {'const': 'bytes'}, 'sampled_at': timestamp, 'value': number,
                'available': boolean, 'kind': {'const': 'counter'}, 'reset': {'const': 'core_restart'},
                'boot_id': identifier}))}),
        'Connections': obj({'source': {'const': 'sniffbox_connection_snapshot'},
            'items': schema('array', maxItems=200, items=obj({'id': text, 'start': text,
                'upload': number, 'download': number, 'metadata': schema('object'),
                'ended_at': {'type': 'null'}, 'last_observed_at': {'type': 'null'},
                'state': {'const': 'snapshot'}, 'actual_target_address': {'type': 'null'},
                'outbound_id': {'type': 'null'}, 'source_identity': {'const': 'observed_address_only'},
                'decision': obj({'status': {'const': 'unavailable'}, 'reason': {'const': 'not_instrumented'}})},
                required=['metadata', 'state', 'decision'])),
            'status': schema('string', enum=['available', 'no_records']), 'total': schema('integer', minimum=0),
            'truncated': boolean, 'offset': schema('integer', minimum=0, maximum=10000),
            'next_offset': schema(['integer', 'null']), 'pagination': {'const': 'best_effort_live_offset'},
            'source_sampled_at': {'type': 'null'}, 'source_age_seconds': {'type': 'null'},
            'freshness_known': {'const': False}, 'complete_history': {'const': False},
            'limitations': schema('array', items=text)}),
        'Probe': obj({'probe_id': identifier, 'connection_id': {'type': 'null'},
            'correlation_id': nullable_text, 'config_revision': revision, 'boot_id': identifier,
            'configuration_changed': boolean, 'status': schema('string', enum=['passed', 'failed', 'unknown']),
            'probe_source': {'const': 'gateway_local_proxy'}, 'url': text,
            'method': {'const': 'HEAD'}, 'follow_redirects': {'const': False}, 'result': schema('object'),
            'failure_stage': schema(['string', 'null'], enum=['http', 'unknown', None]),
            'dns': schema('object'), 'connected_address': {'type': 'null'}, 'outbound_id': {'type': 'null'},
            'stages': schema('object'), 'client_path_verified': {'const': False},
            'limitations': schema('array', items=text)}),
        'ProbeRequest': obj({'url': schema('string', format='uri', maxLength=2048),
            'timeout_ms': schema('integer', minimum=1000, maximum=10000, default=5000),
            'correlation_id': schema('string', pattern='^[A-Za-z0-9_-]{1,64}$'),
            'address_family': schema('string', enum=['auto'], default='auto'),
            'outbound_id': {'type': 'null'}}, required=['url']),
    }
    schemas['ProbeRequest']['additionalProperties'] = False
    schemas['Metrics']['properties']['metrics']['items']['properties']['name'] = schema('string', enum=[
        'download_total', 'upload_total', 'active_connections', 'memory_total', 'memory_available', 'core_memory_used'])
    schemas['Metrics']['properties']['metrics']['items']['properties']['kind'] = schema('string', enum=['counter', 'gauge'])
    schemas['Metrics']['properties']['metrics']['items']['properties']['unit'] = schema('string', enum=['bytes', 'connections'])
    schemas['Metrics']['properties']['metrics']['items']['properties']['reset'] = schema(['string', 'null'], enum=['core_restart', None])
    schemas['Probe']['properties']['connection_id'] = nullable_text
    schemas['Probe']['properties']['connected_address'] = nullable_text
    schemas['Probe']['properties']['outbound_id'] = nullable_text
    schemas['Probe']['properties']['failure_stage'] = schema(['string', 'null'], enum=[
        'http', 'tls', 'local_proxy', 'outbound_connect', 'unknown', None])
    schemas['Probe']['properties']['timings_ms'] = schema('object', additionalProperties=schema('number', minimum=0))
    schemas['Probe']['properties']['timed_out'] = boolean
    schemas['ObservedRecord'] = obj({
        'id': text, 'sequence': schema(['integer', 'null'], minimum=0), 'started_at': timestamp,
        'ended_at': timestamp, 'last_observed_at': timestamp,
        'state': schema('string', enum=['connecting', 'active', 'closed', 'failed_or_cancelled']),
        'transport': {'const': 'tcp'}, 'inbound': text,
        'source_address': text, 'source_port': schema('integer', minimum=0, maximum=65535),
        'original_destination': text, 'destination_port': schema('integer', minimum=0, maximum=65535),
        'domain': nullable_text, 'actual_target_address': nullable_text, 'upstream_peer': nullable_text,
        'upload': schema('integer', minimum=0), 'download': schema('integer', minimum=0),
        'resolution': obj({'source': text, 'server': nullable_text, 'domain': nullable_text,
                           'selected_address': nullable_text, 'complete_answers': {'const': False}}, required=[]),
        'decision': obj({'status': schema('string', enum=['observed', 'unavailable']), 'source': text,
            'rule_id': text, 'rule_kind': {'const': 'mode_dispatch'}, 'reason': text,
            'outbound_id': nullable_text, 'fallback': schema(['boolean', 'null']),
            'config_revision': revision, 'decided_at': timestamp,
            'connect_result': schema('string', enum=['unknown', 'connected', 'failed']),
            'failure_stage': nullable_text, 'application_result': {'const': 'unknown'}},
            required=['status', 'source', 'rule_id', 'rule_kind', 'reason', 'outbound_id',
                      'fallback', 'config_revision', 'decided_at', 'connect_result', 'application_result'])},
        required=['id', 'state', 'decision'])
    schemas['ObservationPage'] = obj({'status': schema('string', enum=['available', 'no_records']),
        'items': schema('array', maxItems=200, items={'$ref': '#/components/schemas/ObservedRecord'}),
        'total': schema('integer', minimum=0), 'truncated': boolean,
        'next_sequence': schema('integer', minimum=0), 'oldest_sequence': schema(['integer', 'null']),
        'latest_sequence': schema('integer', minimum=0), 'gap': boolean,
        'discarded_records': schema('integer', minimum=0), 'capacity': {'const': 1024},
        'retention_seconds': {'const': 300}, 'complete_history': {'const': False},
        'scope': {'const': 'tcp_after_sniff'}, 'view': schema('string', enum=['active', 'recent']),
        'next_cursor': schema('string', maxLength=128), 'boot_id': identifier,
        'source_sampled_at': timestamp, 'freshness': freshness}, required=['status', 'items',
            'total', 'truncated', 'gap', 'discarded_records', 'capacity', 'retention_seconds',
            'complete_history', 'scope', 'view', 'boot_id', 'source_sampled_at', 'freshness'])
    schemas['Connections'] = {'oneOf': [schemas['Connections'], {'$ref': '#/components/schemas/ObservationPage'}]}
    schemas['FakeIp'] = obj({'status': schema('string', enum=['matched', 'no_records']),
        'source': {'const': 'actual_fakeip_cache'}, 'domain': nullable_text, 'fakeip': nullable_text,
        'cidr': text, 'dns_ttl_seconds': schema('integer', minimum=0), 'valid_until': {'type': 'null'},
        'validity': {'const': 'until_evicted_or_core_restart'}, 'algorithm_prediction': {'const': False},
        'historical_ownership_verified': {'const': False}, 'boot_id': identifier,
        'source_sampled_at': timestamp, 'freshness': freshness})
    schemas['Runtime']['properties']['mihomo_configuration'] = obj({
        'status': schema('string', enum=['loaded', 'conflict', 'invalid']),
        'source_revision': revision, 'policy_revision': revision, 'effective_revision': revision,
        'loaded_at': timestamp, 'revision_scope': {'const': 'mihomo_source_policy_effective'}}, required=['status', 'revision_scope'])
    schemas['History'] = obj({'status': schema('string', enum=['available', 'no_records']),
        'items': schema('array', maxItems=500, items=obj({'boot_id': text, 'id': text, 'source':
            schema('string', enum=['sniffbox_lifecycle', 'mihomo_snapshot']), 'first_seen_at': timestamp,
            'last_seen_at': timestamp, 'record': schema('object')})),
        'next_before': schema(['integer', 'null'], minimum=1), 'complete_history': {'const': False}},
        required=['status', 'items', 'next_before', 'complete_history'])
    schemas['MihomoSnapshots'] = obj({'items': schema('array', maxItems=500, items=obj({
        'id': text, 'start': text, 'upload': number, 'download': number,
        'chains': schema('array', items=text), 'rule': text, 'rulePayload': text,
        'metadata': schema('object'), 'decision_status': schema('string', enum=['observed_snapshot', 'unknown'])},
        required=['metadata', 'decision_status'])),
        'source': {'const': 'mihomo_connection_snapshot'}, 'complete_history': {'const': False},
        'source_age_seconds': {'type': 'null'}, 'remote_target_verified': {'const': False},
        'limitations': schema('array', items=text)})
    def envelope(content):
        return obj({'api_version': {'const': '1'}, 'instance_id': identifier,
            'request_id': schema('string', pattern='^[A-Za-z0-9_-]{8,64}$'),
            'fetched_at': schema('number', description='API retrieval time, UTC Unix seconds', minimum=0),
            'sampled_at': timestamp, **content})
    schemas['Error'] = envelope({'error': obj({'code': text})})
    paths = {}
    for path, model in [('instance', 'Instance'), ('capabilities', 'Capabilities'), ('runtime', 'Runtime'),
                        ('health', 'Health'), ('metrics', 'Metrics'), ('connections', 'Connections'),
                        ('diagnostics/probe', 'Probe'), ('fakeip/lookup', 'FakeIp'), ('decisions', 'ObservationPage'),
                        ('history', 'History'), ('mihomo/connections', 'MihomoSnapshots')]:
        response = envelope({'data': {'$ref': '#/components/schemas/' + model}})
        operation = {'operationId': model.lower(), 'summary': model,
            'parameters': [{'in': 'header', 'name': 'X-Request-ID',
                            'schema': schema('string', pattern='^[A-Za-z0-9_-]{8,64}$')}],
            'responses': {'200': {'description': 'Inspect semantic status; HTTP success does not verify forwarding',
                          'content': {'application/json': {'schema': response}}}}}
        for status in (400, 401, 403, 404, 405, 409, 413, 429, 501, 502, 503):
            operation['responses'][str(status)] = {'description': 'Stable error.code; no raw upstream text',
                'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}}}
        if path in {'connections', 'decisions'}:
            for name, spec in {'limit': schema('integer', minimum=1, maximum=200, default=100),
                'offset': schema('integer', minimum=0, maximum=10000, default=0),
                'domain': schema('string', maxLength=253), 'client': text,
                'since': schema('number', minimum=0, description='UTC Unix seconds; last observation, recent/observed_active only'),
                'until': schema('number', minimum=0), 'cursor': schema('string', maxLength=128),
                'view': schema('string', enum=['snapshot', 'recent', 'observed_active'],
                               default='snapshot' if path == 'connections' else 'recent')}.items():
                operation['parameters'].append({'in': 'query', 'name': name, 'schema': spec})
        if path == 'fakeip/lookup':
            operation['description'] = 'Exactly one of domain or IPv4 ip; actual cache only, no allocation or prediction.'
            for name in ('domain', 'ip'):
                operation['parameters'].append({'in': 'query', 'name': name, 'schema': text})
        if path in {'history', 'mihomo/connections'}:
            parameters = {'limit': schema('integer', minimum=1, maximum=500, default=100)}
            if path == 'history':
                parameters.update(before=schema('integer', minimum=1), domain=text, client=text,
                                  source=schema('string', enum=['sniffbox_lifecycle', 'mihomo_snapshot']))
            operation['parameters'].extend({'in': 'query', 'name': name, 'schema': spec} for name, spec in parameters.items())
        if path == 'diagnostics/probe':
            operation['requestBody'] = {'required': True, 'content': {'application/json': {
                'schema': {'$ref': '#/components/schemas/ProbeRequest'}}}}
        paths['/api/v1/' + path] = {'post' if path == 'diagnostics/probe' else 'get': operation}
    document = {'openapi': '3.1.0', 'info': {'title': 'PaoPaoGateway HNM Integration API',
        'version': '1.2.0', 'description': 'Observer API; source semantics are documented in integration-api-v1.md'},
        'security': [{'observerBearer': []}], 'paths': paths,
        'components': {'securitySchemes': {'observerBearer': {'type': 'http', 'scheme': 'bearer'}}, 'schemas': schemas}}
    (ROOT / 'docs').mkdir(exist_ok=True)
    (ROOT / 'docs/openapi-v1.json').write_text(json.dumps(document, indent=2, sort_keys=True) + '\n', newline='\n')

    core = FakeCore()
    app = api.Application(api.Settings(IDENTITY, TOKEN, probe_urls=(URL,)), core)
    samples = []
    server = api.ApiServer(('127.0.0.1', 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def capture(name, path, body=None, token=TOKEN):
        conn = HTTPConnection('127.0.0.1', server.server_port, timeout=2)
        conn.request('GET' if body is None else 'POST', path,
                     body=None if body is None else json.dumps(body),
                     headers={'Authorization': 'Bearer ' + token, 'X-Request-ID': 'fixture-request-1'})
        response = conn.getresponse()
        samples.append({'name': name, 'method': 'GET' if body is None else 'POST', 'path': path,
                        'request': body, 'status': response.status, 'body': json.loads(response.read())})
        conn.close()
    try:
        with patch('gateway_api.time.time', return_value=1700000000.0), patch('gateway_api.uuid.uuid4', return_value=api.uuid.UUID(BOOT)):
            core.info['timestamp'] = 1700000000.0
            for name in ('instance', 'capabilities', 'runtime', 'health', 'metrics', 'connections'):
                capture(name, '/api/v1/' + name)
            capture('probe_passed', api.PROBE_PATH, {'url': URL, 'correlation_id': 'fixture-run'})
            core.result = {'ok': False, 'error': 'private timeout'}
            capture('probe_failed', api.PROBE_PATH, {'url': URL})
            capture('forbidden_target', api.PROBE_PATH, {'url': 'https://unapproved.example/'})
            capture('ipv6_unsupported', api.PROBE_PATH, {'url': URL, 'address_family': 'ipv6'})
            capture('auth_failure', '/api/v1/runtime', token='wrong')
            core.info['timestamp'] = 1699999900.0
            capture('stale', '/api/v1/runtime')
            from test_evidence_api import EvidenceCore, row
            evidence_core = EvidenceCore()
            evidence_core.info['timestamp'] = 1700000000.0
            app.core = evidence_core
            capture('capabilities_evidence', '/api/v1/capabilities')
            capture('fakeip_matched', '/api/v1/fakeip/lookup?domain=example.com')
            capture('recent_connection', '/api/v1/connections?view=recent')
            capture('decision', '/api/v1/decisions')
            evidence_core.gap = True
            capture('retention_gap', '/api/v1/decisions')
            evidence_core.records = []
            capture('no_recent_records', '/api/v1/connections?view=recent')
            evidence_core.records = [row()]
            evidence_core.records[0].update(source_port=50001, inbound='healthcheck')
            app.probe_runner = lambda url, timeout: {'ok': True, 'status': 204, 'ms': 25,
                'local_source_port': 50001, 'stages': {'tcp': 'connected', 'tls': 'completed', 'http': 'completed'},
                'timings_ms': {'local_proxy': 1, 'tls': 20, 'http': 4}}
            capture('staged_probe_joined', '/api/v1/diagnostics/probe', {'url': URL})
            app.probe_runner = None
            app.core = core
            core.info['mode'] = 'suburl'
            core.rows = [{'id': 'mihomo-fixture', 'start': '2023-11-14T22:13:20Z', 'upload': 1, 'download': 2,
                          'chains': ['FixtureNode', 'FixtureGroup'], 'rule': 'DomainSuffix', 'rulePayload': 'example.com',
                          'metadata': {'network': 'tcp', 'host': 'example.com', 'sourceIP': '192.0.2.1'}}]
            capture('mihomo_snapshot', '/api/v1/mihomo/connections')
            from history_store import store, query as query_history
            with tempfile.TemporaryDirectory() as temporary:
                database = Path(temporary) / 'history.sqlite3'
                store(BOOT, 'mihomo_snapshot', core.rows, path=database, now=1700000000.0)
                with patch('history_store.query', side_effect=lambda *args: query_history(*args, path=database)):
                    capture('persisted_history', '/api/v1/history?domain=example.com')
                    capture('history_no_records', '/api/v1/history?domain=missing.example')
            core.error = api.ApiError(503, 'CORE_UNAVAILABLE')
            capture('core_stopped', '/api/v1/health')
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    directory = ROOT / 'integration/fixtures'
    directory.mkdir(exist_ok=True)
    (directory / 'samples.json').write_text(json.dumps(samples, indent=2, sort_keys=True) + '\n', newline='\n')
    manifest = {str(path.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (ROOT / 'docs/openapi-v1.json', directory / 'samples.json')}
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', newline='\n')
    print(f'Exported OpenAPI and {len(samples)} deterministic HTTP samples.')


if __name__ == '__main__':
    main()
