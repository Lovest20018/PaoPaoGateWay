#!/usr/bin/env python3
"""Manage only reserved ppgw tables, rule priority 11099 and local table 100."""
import json
import os
from pathlib import Path
import subprocess
import sys

LEDGER = Path('/run/paopaogw/network-owned.json')


def command(*args, check=True):
    result = subprocess.run(args, check=check, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ''


def start():
    interface = os.environ['PPGW_INTERFACE']
    if not interface or any(not (c.isalnum() or c in '_.:-') for c in interface):
        raise ValueError('invalid interface')
    if LEDGER.exists():
        return
    # Reserve resources before making any change. Never adopt host-owned rules.
    for family in ('ip', 'ip6'):
        result = subprocess.run(['nft', 'list', 'table', family, 'ppgw'], capture_output=True)
        if result.returncode == 0:
            raise ValueError('existing ppgw nft table belongs to another service')
    families = ['-4']
    if os.environ.get('PPGW_IPV6') == 'yes':
        families.append('-6')
    for family in families:
        routes = json.loads(command('ip', family, '-j', 'route', 'show', 'table', '100', check=False) or '[]')
        rules = json.loads(command('ip', family, '-j', 'rule', 'show'))
        if routes or any(r.get('priority') == 11099 or r.get('table') == 100
                         or str(r.get('fwmark', '')).split('/')[0] in ('1', '0x1') for r in rules):
            raise ValueError('policy table 100 / mark 1 / priority 11099 is already used')
    # Persist intent before mutation so partial setup can be cleaned up.
    LEDGER.write_text(json.dumps({'families': families, 'interface': interface}))
    for family in families:
        command('ip', family, 'route', 'add', 'local', 'default', 'dev', 'lo', 'table', '100')
        command('ip', family, 'rule', 'add', 'priority', '11099', 'fwmark', '1', 'table', '100')


def stop():
    if not LEDGER.exists():
        return
    state = json.loads(LEDGER.read_text())
    for family in ('ip', 'ip6'):
        command('nft', 'delete', 'table', family, 'ppgw', check=False)
    for family in state['families']:
        command('ip', family, 'rule', 'del', 'priority', '11099', 'fwmark', '1', 'table', '100', check=False)
        command('ip', family, 'route', 'del', 'local', 'default', 'dev', 'lo', 'table', '100', check=False)
    LEDGER.unlink()


if __name__ == '__main__':
    try:
        {'start': start, 'stop': stop}[sys.argv[1]]()
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        sys.exit('Gateway policy setup failed; check reserved resources and interface.')
