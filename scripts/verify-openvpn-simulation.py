#!/usr/bin/env python3
"""Continue the disposable network fixture with locally generated TLS credentials."""
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib.util import spec_from_file_location, module_from_spec
spec = spec_from_file_location('network_fixture', Path(__file__).with_name('verify-network-simulation.py'))
fixture = module_from_spec(spec); spec.loader.exec_module(fixture)
GW, CLIENT, SERVER = fixture.GW, fixture.CLIENT, fixture.SERVER
run, exec_, python = fixture.run, fixture.exec_, fixture.python
ROOT = Path('/opt/paopaogw-lab')


def main():
    exec_(GW, 'test', '-f', '/run/release-validation-only')
    result = json.loads((ROOT / 'network-simulation-validation.json').read_text())
    assert result['checks']['ipv6_transparent_http']
    exec_(SERVER, 'sh', '-c', 'apt-get update >/var/tmp/deps.log 2>&1 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openvpn openssl >>/var/tmp/deps.log 2>&1')
    # Credentials are test-only, generated and retained only inside disposable containers.
    python(GW, r'''from pathlib import Path
import subprocess,os
root=Path('/var/tmp/release-vpn-pki');root.mkdir(mode=0o700,exist_ok=True)
def cmd(*args):subprocess.run(args,cwd=root,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
cmd('openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=PPGW-Disposable-Lab-CA','-keyout','ca.key','-out','ca.crt')
for name,usage in [('server','serverAuth'),('client','clientAuth')]:
 cmd('openssl','req','-newkey','rsa:2048','-nodes','-subj','/CN=PPGW-Lab-'+name,'-keyout',name+'.key','-out',name+'.csr')
 (root/(name+'.ext')).write_text('basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage='+usage+'\n')
 cmd('openssl','x509','-req','-in',name+'.csr','-CA','ca.crt','-CAkey','ca.key','-CAcreateserial','-days','1','-out',name+'.crt','-extfile',name+'.ext')
for p in root.iterdir():p.chmod(0o600)
client='client\nproto udp\nremote 172.29.91.3 1194\nnobind\nremote-cert-tls server\nresolv-retry infinite\nconnect-retry 1\npersist-key\npersist-tun\ndata-ciphers AES-256-GCM\nauth-nocache\nroute 198.18.91.3 255.255.255.255\nverb 3\n'
for tag,file in [('ca','ca.crt'),('cert','client.crt'),('key','client.key')]:client+='<'+tag+'>\n'+(root/file).read_text()+'</'+tag+'>\n'
Path('/etc/paopaogw/files/custom.ovpn').write_text(client)
Path('/etc/paopaogw/files/custom.ovpn').chmod(0o600)
''')
    # docker cp transfers keys directly between our temporary containers through a root-only host folder.
    pki = ROOT / 'disposable-vpn-pki'; pki.mkdir(mode=0o700, exist_ok=True)
    for name in ('ca.crt', 'server.crt', 'server.key'):
        run('docker', 'cp', GW + ':/var/tmp/release-vpn-pki/' + name, str(pki / name))
        (pki / name).chmod(0o600)
    exec_(SERVER, 'mkdir', '-p', '/var/tmp/release-vpn')
    for name in ('ca.crt', 'server.crt', 'server.key'):
        run('docker', 'cp', str(pki / name), SERVER + ':/var/tmp/release-vpn/' + name)
    server = '''local 172.29.91.3
port 1194
proto udp
dev tun-lab
topology subnet
server 10.77.91.0 255.255.255.0
ca /var/tmp/release-vpn/ca.crt
cert /var/tmp/release-vpn/server.crt
key /var/tmp/release-vpn/server.key
dh none
data-ciphers AES-256-GCM
keepalive 2 10
persist-key
persist-tun
verb 3
log /var/tmp/release-vpn/server.log
'''
    python(SERVER, "from pathlib import Path;Path('/var/tmp/release-vpn/server.conf').write_text(" + repr(server) + ")")
    run('docker', 'exec', '-d', SERVER, 'openvpn', '--config', '/var/tmp/release-vpn/server.conf')
    python(GW, "from pathlib import Path;import sys;sys.path.insert(0,'/usr/local/lib/paopaogw');from subscription_admin import update_config;p=Path('/etc/paopaogw/files/ppgw.ini');p.write_bytes(update_config(p.read_bytes(),{'mode':'ovpn','ovpnfile':'custom.ovpn'}))")
    exec_(GW, 'systemctl', 'restart', 'paopaogw'); fixture.wait_core('ovpn')
    for _ in range(100):
        if '10.77.91.' in run('docker', 'exec', GW, 'ip', '-4', 'addr', 'show', 'dev', 'tun114', check=False):break
        time.sleep(.5)
    assert '10.77.91.' in exec_(GW, 'ip', '-4', 'addr', 'show', 'dev', 'tun114')
    response = json.loads(exec_(CLIENT, 'curl', '--noproxy', '*', '-sS', '--max-time', '12', 'http://198.18.91.3:8088/through-openvpn'))
    assert response['peer'].startswith('10.77.91.'), response
    first_pid = exec_(GW, 'pgrep', '-x', 'openvpn')
    exec_(GW, 'kill', '-KILL', first_pid)
    current, recovered = first_pid, {}
    for _ in range(100):
        time.sleep(.5)
        current = run('docker', 'exec', GW, 'pgrep', '-x', 'openvpn', check=False)
        if current and current != first_pid:
            candidate = run('docker', 'exec', CLIENT, 'curl', '--noproxy', '*', '-sS', '--max-time', '3', 'http://198.18.91.3:8088/reconnected', check=False)
            if candidate:
                recovered = json.loads(candidate)
                if recovered['peer'].startswith('10.77.91.'): break
    assert current != first_pid and recovered.get('peer', '').startswith('10.77.91.')
    result['checks']['openvpn_tls_tunnel_actual_transparent_http'] = response
    result['checks']['openvpn_supervisor_reconnect_after_process_kill'] = True
    result['limitations'] = ['Simulated TLS OpenVPN server and isolated documentation-address IPv6 network.',
        'UDP/443 echo proves datagram forwarding, not a complete QUIC application or Full Cone NAT conformance suite.',
        'No ARM64, native VM installation, household path or long-duration performance validation.']
    (ROOT / 'openvpn-ipv6-validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
