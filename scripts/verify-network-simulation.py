#!/usr/bin/env python3
"""Host-side, disposable Docker-only IPv4/IPv6/UDP/OpenVPN integration fixture."""
import json
from pathlib import Path
import subprocess
import time

GW = 'paopaogw-release-validation'
CLIENT = 'paopaogw-release-client'
SERVER = 'paopaogw-release-target'
NETWORK = 'paopaogw-release-sim'
IMAGE = 'paopaogw-lab-systemd'
REPORT = Path('/opt/paopaogw-lab/network-simulation-validation.json')


def run(*args, input=None, check=True):
    result = subprocess.run(args, input=input, capture_output=True, text=True, timeout=50)
    if check and result.returncode:
        raise RuntimeError(f'{args[:4]} exited {result.returncode}: {result.stderr[-600:]}')
    return result.stdout.strip()


def python(container, source):
    return run('docker', 'exec', '-i', container, 'python3', '-', input=source)


def exec_(container, *args):
    return run('docker', 'exec', container, *args)


SERVER_SCRIPT = r'''
import http.server,json,socket,threading
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  raw=json.dumps({'peer':self.client_address[0],'path':self.path}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def log_message(self,*args):pass
class V6(http.server.ThreadingHTTPServer):
 address_family=socket.AF_INET6
 def server_bind(self):
  self.socket.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
  super().server_bind()
for cls,addr in [(http.server.ThreadingHTTPServer,('0.0.0.0',8088)),(V6,('::',8088))]:
 server=cls(addr,Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
def echo(family,port):
 sock=socket.socket(family,socket.SOCK_DGRAM)
 if family==socket.AF_INET6:sock.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
 sock.bind(('::' if family==socket.AF_INET6 else '0.0.0.0',port))
 while True:
  data,peer=sock.recvfrom(4096);sock.sendto(b'echo:'+data,peer)
for family in (socket.AF_INET,socket.AF_INET6):
 for port in (19000,443):threading.Thread(target=echo,args=(family,port),daemon=True).start()
threading.Event().wait()
'''


def wait_core(mode):
    for _ in range(90):
        try:
            info = json.loads(python(GW, "import sys,json;from pathlib import Path;sys.path.insert(0,'/usr/local/lib/paopaogw');from gateway_api import CoreClient;print(json.dumps(CoreClient(Path('/var/lib/paopaogw/core-token')).request('/sniffbox/info')))"))
            if info['mode'] == mode: return info
        except Exception: pass
        time.sleep(.5)
    raise AssertionError('Simulation gateway not ready')


def main():
    assert exec_(GW, 'test', '-f', '/run/release-validation-only') == ''
    exec_(GW, 'test', '-f', '/var/lib/paopaogw/release-features-validation.json')
    for name in (CLIENT, SERVER):
        if json.loads(run('docker', 'container', 'inspect', name, check=False) or '[]'):
            raise AssertionError('Refuse to reuse an existing simulation container')
    run('docker', 'network', 'create', '--ipv6', '--subnet', '172.29.91.0/24', '--subnet', 'fd99:91::/64', NETWORK)
    try:
        run('docker', 'network', 'connect', '--ip', '172.29.91.2', '--ip6', 'fd99:91::2', NETWORK, GW)
        for name, suffix in ((SERVER, '3'), (CLIENT, '4')):
            run('docker', 'run', '-d', '--name', name, '--privileged', '--network', NETWORK,
                '--ip', '172.29.91.' + suffix, '--ip6', 'fd99:91::' + suffix,
                IMAGE, 'sleep', 'infinity')
        exec_(SERVER, 'ip', 'addr', 'add', '198.18.91.3/24', 'dev', 'eth0')
        exec_(SERVER, 'ip', '-6', 'addr', 'add', '2001:db8:91::3/64', 'dev', 'eth0', 'nodad')
        exec_(GW, 'ip', 'addr', 'add', '198.18.91.2/24', 'dev', 'eth1')
        exec_(GW, 'ip', '-6', 'addr', 'add', '2001:db8:91::2/64', 'dev', 'eth1', 'nodad')
        exec_(CLIENT, 'ip', 'route', 'add', '198.18.91.3/32', 'via', '172.29.91.2')
        exec_(CLIENT, 'ip', '-6', 'route', 'add', '2001:db8:91::3/128', 'via', 'fd99:91::2')
        python(SERVER, "from pathlib import Path;Path('/var/tmp/target.py').write_text(" + repr(SERVER_SCRIPT) + ")")
        run('docker', 'exec', '-d', SERVER, 'python3', '/var/tmp/target.py')
        python(GW, "from pathlib import Path;import sys;sys.path.insert(0,'/usr/local/lib/paopaogw');from subscription_admin import update_config;p=Path('/etc/paopaogw/files/ppgw.ini');p.write_bytes(update_config(p.read_bytes(),{'mode':'free'}));p=Path('/etc/paopaogw/service.env');p.write_text(p.read_text().replace('PPGW_INTERFACE=eth0','PPGW_INTERFACE=eth1'))")
        exec_(GW, 'systemctl', 'restart', 'paopaogw'); wait_core('free')
        time.sleep(1)
        report = {'environment': 'isolated simulated Docker topology, documentation/test addresses', 'checks': {}}
        for family, address in (('ipv4', '198.18.91.3'), ('ipv6', '[2001:db8:91::3]')):
            response = json.loads(exec_(CLIENT, 'curl', '--noproxy', '*', '-g', '-sS', '--max-time', '8', f'http://{address}:8088/{family}'))
            assert response['peer'] not in {'172.29.91.4', 'fd99:91::4'}, response
            report['checks'][family + '_transparent_http'] = response
        udp = json.loads(python(CLIENT, r'''import socket,json
checks={}
for family,address in ((socket.AF_INET,'198.18.91.3'),(socket.AF_INET6,'2001:db8:91::3')):
 for port in (19000,443):
  with socket.socket(family,socket.SOCK_DGRAM) as s:
   s.settimeout(4);s.sendto(b'fixture',(address,port));data,peer=s.recvfrom(4096)
   assert data==b'echo:fixture' and peer[0]==address
   checks[('ipv6' if family==socket.AF_INET6 else 'ipv4')+'_udp_'+str(port)]=True
print(json.dumps(checks))'''))
        report['checks'].update(udp)
        auth = json.loads(python(GW, "from pathlib import Path;import json;print(json.dumps(Path('/etc/paopaogw/proxy-auth').read_text().strip()))"))
        response = exec_(CLIENT, 'curl', '--noproxy', '', '--socks5-hostname', '172.29.91.2:1080',
                         '--proxy-user', auth, '-sS', '--max-time', '8', 'http://198.18.91.3:8088/authenticated')
        assert json.loads(response)['path'] == '/authenticated'
        unauthorized = run('docker', 'exec', CLIENT, 'curl', '--noproxy', '', '--socks5-hostname', '172.29.91.2:1080',
                           '-sS', '--max-time', '3', 'http://198.18.91.3:8088/unauthorized', check=False)
        assert not unauthorized
        report['checks']['private_authenticated_explicit_proxy'] = True
        lifecycle = json.loads(python(GW, "from pathlib import Path;import sys,json;sys.path.insert(0,'/usr/local/lib/paopaogw');from gateway_api import CoreClient;r=CoreClient(Path('/var/lib/paopaogw/core-token')).request('/sniffbox/evidence',{'resource':'connections','limit':200});print(json.dumps(r))"))
        rows = lifecycle['data']['items']
        assert any(row.get('inbound') == 'tproxy' and ':' in row.get('source_address','') for row in rows), rows
        report['checks']['ipv6_actual_tcp_lifecycle'] = True
        REPORT.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
        # Keep the target/client topology until the OpenVPN phase has been verified.
        report['topology_retained_for_openvpn'] = True
    except Exception:
        raise


if __name__ == '__main__':
    main()
