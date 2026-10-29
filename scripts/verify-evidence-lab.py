#!/usr/bin/env python3
"""Provider v1.1 checks inside the explicitly isolated Debian lab instance."""
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN = Path('/etc/paopaogw/read-token').read_text().strip()
BASE = 'http://127.0.0.1:8788/api/v1/'


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as res:
        return res.code, json.load(res)


def main():
    checks = {}
    result = {'kind': 'provider_evidence_v1_1', 'checks': checks}
    for _ in range(40):
        status, value = call('capabilities')
        if status == 200:
            break
        time.sleep(0.25)
    assert call('capabilities')[1]['data']['features']['connection_history']
    assert call('capabilities')[1]['data']['features']['fakeip_lookup']
    unseen = 'not-observed-' + str(int(time.time())) + '.example'
    assert call('fakeip/lookup?domain=' + unseen)[1]['data']['status'] == 'no_records'
    assert call('fakeip/lookup?domain=' + unseen)[1]['data']['status'] == 'no_records'
    checks['lookup_no_allocation'] = True
    # Ask core DNS to allocate a real mapping through its normal wire protocol.
    import struct
    domain = 'example.com'
    packet = struct.pack('!6H', 0x484e, 0x100, 1, 0, 0, 0) + b''.join(bytes([len(p)]) + p.encode() for p in domain.split('.')) + b'\0' + struct.pack('!HH', 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.sendto(packet, ('127.0.0.1', 53))
        answer = sock.recv(4096)
    fakeip = socket.inet_ntoa(answer[-4:])
    mapping = call('fakeip/lookup?domain=example.com')[1]
    assert mapping['data']['fakeip'] == fakeip
    assert call('fakeip/lookup?ip=' + fakeip)[1]['data']['domain'] == domain
    result['mapping'] = mapping
    checks['lookup_matches_actual_dns'] = True
    status, probe = call('diagnostics/probe', {'url': 'https://example.com/', 'correlation_id': 'evidence-live'})
    assert status == 200 and probe['data']['status'] == 'passed'
    value = probe['data']
    assert value['stages']['tcp'] == 'connected'
    assert value['stages']['tls'] == 'completed'
    assert value['stages']['http'] == 'completed'
    assert value['connection_id'] and value['connected_address'] and value['outbound_id'] == 'direct'
    assert value['dns']['addresses'] == [value['connected_address']]
    assert not value['dns']['complete_answers']
    result['probe'] = probe
    checks['probe_stages_and_exact_connection_join'] = True
    # Completed short flow is retained after it disappears from the active table.
    time.sleep(0.2)
    history = call('connections?view=recent&domain=example.com')[1]
    own = [row for row in history['data']['items'] if row['id'] == value['connection_id']]
    assert len(own) == 1 and own[0]['ended_at'] is not None
    assert own[0]['decision']['connect_result'] == 'connected'
    assert own[0]['decision']['reason'] == 'free_mode_no_rule'
    assert own[0]['decision']['application_result'] == 'unknown'
    result['short_connection'] = own[0]
    checks['completed_short_connection_retained'] = True
    cursor = call('decisions?domain=example.com&limit=1')[1]['data']['next_cursor']
    assert call('decisions?domain=changed.example&cursor=' + urllib.parse.quote(cursor))[0] == 400
    assert call('decisions?domain=example.com&cursor=' + urllib.parse.quote(cursor))[0] == 200
    checks['cursor_filter_binding'] = True
    # Exercise a failed core TCP connection to an unused loopback port, outside API target allowlist.
    started = time.time()
    unused = socket.socket()
    unused.bind(('127.0.0.1', 0))
    port = unused.getsockname()[1]
    unused.close()
    subprocess.run(['curl', '--silent', '--max-time', '2', '--proxy', 'socks5h://127.0.0.1:1079',
                    f'http://127.0.0.1:{port}/'], capture_output=True)
    time.sleep(0.2)
    failed = call('decisions?client=127.0.0.1&since=' + str(started))[1]['data']['items']
    assert any(row['decision']['connect_result'] == 'failed' for row in failed)
    result['failed_connections'] = failed
    checks['actual_outbound_connect_failure'] = True
    instance = call('instance')[1]['data']
    subprocess.run(['systemctl', 'restart', 'paopaogw'], check=True)
    for _ in range(40):
        state, current = call('instance')
        if state == 200 and current['data']['boot_id'] != instance['boot_id']:
            break
        time.sleep(0.25)
    assert current['data']['id'] == instance['id']
    assert call('decisions?domain=example.com&cursor=' + urllib.parse.quote(cursor))[0] == 409
    assert call('connections?view=recent')[1]['data']['status'] == 'no_records'
    checks['core_restart_expires_cursor_and_history'] = True
    result['instance'] = current['data']
    result['sampled_at'] = time.time()
    result['limitations'] = ['Only free-mode IPv4 is tested here.', 'Decision is mode dispatch, not domain-rule simulation.',
        'History covers TCP after sniffing; bounded retention is not a complete traffic ledger.']
    Path('/var/lib/paopaogw/evidence-validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(checks))


if __name__ == '__main__':
    main()
