#!/usr/bin/env python3
"""Run inside the isolated Debian lab instance. Never prints credentials."""
import hashlib
import json
from pathlib import Path
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.request


ROOT = Path('/var/lib/paopaogw')
BASE = 'http://127.0.0.1:8788/api/v1/'
TOKEN = Path('/etc/paopaogw/read-token').read_text().strip()
EVIDENCE = {'kind': 'isolated-debian-validation', 'checks': {}}


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def request(route, body=None, token=TOKEN):
    req = urllib.request.Request(BASE + route,
        data=None if body is None else json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                 'X-Request-ID': 'lab-validation-20261026'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def wait_core():
    for _ in range(40):
        try:
            status, value = request('instance')
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
            continue
        if status == 200:
            return value['data']
        time.sleep(0.5)
    raise AssertionError('Core did not become available')


def query_fakeip(domain):
    query_id = 0x484e
    labels = b''.join(bytes([len(p)]) + p.encode() for p in domain.split('.')) + b'\0'
    packet = struct.pack('!6H', query_id, 0x100, 1, 0, 0, 0) + labels + struct.pack('!HH', 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.sendto(packet, ('127.0.0.1', 53))
        data = sock.recv(4096)
    assert data[:2] == struct.pack('!H', query_id)
    assert data[3] & 15 == 0
    assert struct.unpack('!H', data[6:8])[0] > 0
    return socket.inet_ntoa(data[-4:])


def main():
    initial = wait_core()
    EVIDENCE['instance'] = initial
    assert request('runtime', token='invalid')[0] == 401
    EVIDENCE['checks']['auth_failure'] = True
    status, runtime = request('runtime')
    assert status == 200 and runtime['data']['outbound_reachable'] == 'unknown'
    assert not runtime['data']['forwarding_verified']
    EVIDENCE['runtime'] = runtime
    assert request('diagnostics/probe', {'url': 'http://127.0.0.1/'})[0] == 403
    assert request('diagnostics/probe', {'url': 'https://example.com/', 'address_family': 'ipv6'})[0] == 501
    EVIDENCE['checks']['forbidden_target_and_ipv6'] = True
    fake = query_fakeip('example.com')
    EVIDENCE['dns_fakeip'] = {'source': 'lab-loopback-dns', 'domain': 'example.com', 'type': 'A', 'answer': fake}
    assert fake.startswith('7.')
    # A real local proxy request. This does not traverse a TPROXY ingress.
    result = subprocess.run(['curl', '--silent', '--show-error', '--max-time', '12',
        '--proxy', 'socks5h://127.0.0.1:1079', '-I', 'https://example.com/'], capture_output=True, text=True)
    EVIDENCE['local_proxy'] = {'source': 'gateway_local_proxy', 'returncode': result.returncode,
        'http_status_lines': [line for line in result.stdout.splitlines() if line.startswith('HTTP/')]}
    status, probe = request('diagnostics/probe', {'url': 'https://example.com/', 'correlation_id': 'lab-probe'})
    assert status == 200
    EVIDENCE['probe'] = probe
    EVIDENCE['checks']['actual_request'] = result.returncode == 0
    # The existing policy table is unrelated to this project and must survive.
    before = run('nft', '-j', 'list', 'table', 'inet', 'lab_sentinel')
    hosts = hashlib.sha256(Path('/etc/hosts').read_bytes()).hexdigest()
    core_pids = run('pgrep', '-x', 'sniffbox')
    run('systemctl', 'stop', 'paopaogw-api')
    assert run('pgrep', '-x', 'sniffbox') == core_pids
    assert query_fakeip('api-stopped.example.com').startswith('7.')
    run('systemctl', 'start', 'paopaogw-api')
    after_api = wait_core()
    assert after_api['boot_id'] == initial['boot_id']
    EVIDENCE['checks']['api_stopped_core_dns_continued'] = True
    run('systemctl', 'restart', 'paopaogw')
    restarted = wait_core()
    assert restarted['id'] == initial['id']
    assert restarted['boot_id'] != initial['boot_id']
    assert restarted['config_revision'] == initial['config_revision']
    EVIDENCE['checks']['restart_identity_and_revision'] = True
    config = Path('/etc/paopaogw/files/ppgw.ini')
    original_config = config.read_text()
    config.write_text(original_config + '\nbox=route.block_unknown=true\n')
    run('systemctl', 'restart', 'paopaogw')
    changed = wait_core()
    assert changed['config_revision'] != restarted['config_revision']
    config.write_text(original_config)
    run('systemctl', 'restart', 'paopaogw')
    restored = wait_core()
    assert restored['config_revision'] == restarted['config_revision']
    EVIDENCE['checks']['effective_revision_change_and_restore'] = True
    run('systemctl', 'stop', 'paopaogw')
    status, down = request('health')
    assert status == 503 and down['error']['code'] == 'CORE_UNAVAILABLE'
    assert run('nft', '-j', 'list', 'table', 'inet', 'lab_sentinel') == before
    assert hashlib.sha256(Path('/etc/hosts').read_bytes()).hexdigest() == hosts
    routes = subprocess.run(['ip', '-4', '-j', 'route', 'show', 'table', '100'], capture_output=True, text=True)
    assert routes.returncode != 0 or json.loads(routes.stdout) == []
    EVIDENCE['checks']['core_stop_no_automatic_restart'] = True
    EVIDENCE['checks']['host_hosts_and_unrelated_firewall_preserved'] = True
    run('systemctl', 'start', 'paopaogw')
    wait_core()
    EVIDENCE['limitations'] = ['Local proxy probe is not a transparent-ingress or client-path test.',
        'DNS real address, final target address, rule/node decision and TLS/TCP stage evidence unavailable.',
        'Only free mode and IPv4 lab DNS have been exercised; other modes require separate validation.']
    Path('/var/lib/paopaogw/validation.json').write_text(json.dumps(EVIDENCE, indent=2) + '\n')
    print(json.dumps({'checks': EVIDENCE['checks'], 'evidence': '/var/lib/paopaogw/validation.json'}))


if __name__ == '__main__':
    main()
