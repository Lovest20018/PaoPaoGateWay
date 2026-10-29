#!/usr/bin/env python3
"""Prepare private legacy paths, then run the existing five-mode controller."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import shlex
import stat
import shutil

BASE = Path('/usr/local/lib/paopaogw')


def main():
    interface = os.environ.get('PPGW_INTERFACE', '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', interface):
        sys.exit('Set PPGW_INTERFACE in /etc/paopaogw/service.env.')
    data = Path('/etc/paopaogw/files/ppgw.ini').read_text()
    if not data.startswith('#paopao-gateway'):
        sys.exit('Invalid ppgw.ini header.')
    result = subprocess.run([str(BASE / 'bin/sniffbox'), 'ppgw', '-ini-export',
                            '/etc/paopaogw/files/ppgw.ini'], capture_output=True, text=True)
    if result.returncode:
        sys.exit('Invalid ppgw.ini.')
    # Never execute source configuration in a shell. Rust emits shell-quoted values.
    values = dict(item.split('=', 1) for item in shlex.split(result.stdout) if '=' in item)
    if values.get('mode') == 'ovpn':
        tun = Path('/dev/net/tun')
        if not shutil.which('openvpn') or not tun.exists() or not stat.S_ISCHR(tun.stat().st_mode):
            sys.exit('OpenVPN mode requires openvpn and a usable /dev/net/tun device.')
    Path('/tmp/ppgw.ini').write_text(data)
    # Obtain effective password from the same Rust parser; output never enters logs.
    password = values.get('clash_web_password', '')
    if len(password) < 16 or password == 'clashpass':
        sys.exit('Set a dashboard password of at least 16 characters.')
    os.environ['cloudflared_token'] = ''
    os.environ.setdefault('TZ', 'Asia/Shanghai')
    os.umask(0o077)
    token = Path('/var/lib/paopaogw/core-token')
    pending = token.with_suffix('.new')
    pending.write_text(hashlib.sha256(password.encode()).hexdigest())
    os.chmod(pending, 0o640)
    pending.replace(token)
    os.environ['PPGW_REVISION_KEY'] = Path('/etc/paopaogw/revision-key').read_text().strip()
    Path('/var/lib/paopaogw/logs').mkdir(mode=0o750, exist_ok=True)
    base = (BASE / 'base.yaml').read_text().replace('#eth0', '#' + interface)
    Path('/etc/config/clash/base.yaml').write_text(base)
    os.execv('/bin/sh', ['/bin/sh', str(BASE / 'ppg.sh')])


if __name__ == '__main__':
    main()
