#!/usr/bin/env python3
"""Owner-authorized test defaults: authenticated private-address explicit proxy."""
from pathlib import Path
import json
import os
import re
import secrets

PRIVATE = '127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7,fe80::/10'


def assignments(raw, changes):
    lines = [line for line in raw.splitlines() if not any(re.match(r'^\s*' + re.escape(key) + r'\s*=', line) for key in changes)]
    return '\n'.join(lines + [key + '="' + value + '"' for key, value in changes.items()]) + '\n'


def enable(root=Path('/')):
    config = root / 'etc/paopaogw'
    ini = config / 'files/ppgw.ini'
    raw = ini.read_text(encoding='utf-8')
    credential = config / 'proxy-auth'
    if not credential.exists():
        fd = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write('ppgw:' + secrets.token_urlsafe(24) + '\n')
    auth = credential.read_text().strip()
    box_assignments = re.findall(r'^\s*box\s*=\s*(.*)$', raw, re.MULTILINE)
    previous_box = box_assignments[-1].strip().strip('\"\'') if box_assignments else ''
    box_options = [item.strip() for item in previous_box.split(',')
                   if item.strip() and not item.strip().startswith('route.block_quic=')]
    box_options.append('route.block_quic=false')
    raw = assignments(raw, {'openport': 'yes', 'openport_auth': auth, 'proxy_cidr': PRIVATE,
                           'udp_enable': 'yes', 'net_rec': 'yes', 'dns_burn': 'yes',
                           'box': ','.join(box_options)})
    ini.write_text(raw, encoding='utf-8')
    ini.chmod(0o600)
    env = config / 'service.env'
    env.write_text(assignments(env.read_text(), {'PPGW_IPV6': 'yes', 'TZ': 'Asia/Shanghai'}), encoding='utf-8')
    network = config / 'network'
    if 'eth06' not in network.read_text():
        network.write_text(network.read_text() + '\n# eth06: gateway IPv6 listeners and rules enabled\n')
    api = config / 'api.json'
    settings = json.loads(api.read_bytes())
    settings['probe_urls'] = list(dict.fromkeys(settings.get('probe_urls', []) + ['https://example.com/']))
    api.write_text(json.dumps(settings, indent=2) + '\n')


if __name__ == '__main__':
    enable()
    print('Test features enabled. Proxy credentials: /etc/paopaogw/proxy-auth; private addresses only.')
