#!/usr/bin/env python3
"""Host-side check restricted to the named, isolated Docker lab instances."""
import json
from pathlib import Path
import subprocess
import time

GATEWAY = 'paopaogw-debian-lab'
CLIENT = 'paopaogw-lab-client'


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main():
    gateway_ip = json.loads(docker('inspect', GATEWAY))[0]['NetworkSettings']['Networks']['bridge']['IPAddress']
    client_code = '''import json,socket,struct,subprocess,sys
gateway=sys.argv[1]
domain='example.com'
q=struct.pack('!6H',0x484e,0x100,1,0,0,0)+b''.join(bytes([len(p)])+p.encode() for p in domain.split('.'))+b'\\0'+struct.pack('!HH',1,1)
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(3);s.sendto(q,(gateway,53));data=s.recv(4096);s.close()
fake=socket.inet_ntoa(data[-4:]);assert fake.startswith('7.')
print(json.dumps({'dns_server':gateway,'domain':domain,'fakeip':fake}),flush=True)
subprocess.run(['ip','route','add','7.0.0.0/8','via',gateway],check=True)
r=subprocess.run(['curl','--silent','--show-error','--max-time','20','--limit-rate','100','--resolve',domain+':443:'+fake,'--output','/tmp/result','--write-out','%{http_code}','https://example.com/'],capture_output=True,text=True)
print(json.dumps({'curl_returncode':r.returncode,'http_status':r.stdout,'tls_verification':'curl_default_enabled','proxy_used':False}),flush=True)
sys.exit(r.returncode)
'''
    started = time.time()
    container_id = docker('run', '-d', '--name', CLIENT, '--cap-add', 'NET_ADMIN',
        'paopaogw-lab-systemd', 'python3', '-u', '-c', client_code, gateway_ip)
    samples = []
    try:
        for _ in range(10):
            query = '''import json;from pathlib import Path;import urllib.request
t=Path('/etc/paopaogw/read-token').read_text().strip()
r=urllib.request.Request('http://127.0.0.1:8788/api/v1/connections?domain=example.com',headers={'Authorization':'Bearer '+t})
print(urllib.request.urlopen(r,timeout=5).read().decode())'''
            value = json.loads(docker('exec', GATEWAY, 'python3', '-c', query))
            samples.append(value)
            if any(item['metadata'].get('type') == 'TProxy' for item in value['data']['items']):
                break
            time.sleep(0.5)
        exit_code = int(docker('wait', CLIENT))
        logs = [json.loads(line) for line in docker('logs', CLIENT).splitlines() if line.startswith('{')]
        observed = [item for sample in samples for item in sample['data']['items']
                    if item['metadata'].get('type') == 'TProxy']
        result = {'kind': 'isolated_transparent_ingress', 'sampled_at': time.time(), 'started_at': started,
            'source': 'independent_docker_client', 'gateway_ip': gateway_ip,
            'request': logs, 'observed_connections': observed,
            'passed': exit_code == 0 and logs[-1].get('http_status') == '200' and bool(observed),
            'limitations': ['One free-mode IPv4 lab flow; not household ROS/DNS/IPv6 validation.',
                'Selected node/rule and final outbound address are unavailable in this provider.']}
        Path('/opt/paopaogw-lab/transparent-validation.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'passed': result['passed'], 'observed_tproxy_connections': len(observed)}))
        assert result['passed']
    finally:
        subprocess.run(['docker', 'rm', '-f', CLIENT], check=False, capture_output=True)


if __name__ == '__main__':
    main()
