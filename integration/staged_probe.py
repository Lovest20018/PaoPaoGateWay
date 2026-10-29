"""Bounded HEAD via the core's existing loopback health-check SOCKS ingress.

The socket's local port provides an exact join to the core connection record.
SOCKS success alone does not prove the upstream TCP connection succeeded.
"""
import socket
import ssl
import time
from urllib.parse import urlsplit


class StagedProbe:
    def __call__(self, url: str, timeout_ms: int) -> dict:
        started = time.monotonic()
        deadline = started + timeout_ms / 1000
        stages = {'tcp': 'unknown', 'tls': 'not_tested', 'http': 'not_tested'}
        timings = {}
        result = {'ok': False, 'status': None, 'stages': stages, 'timings_ms': timings,
                  'failure_stage': 'local_proxy', 'local_source_port': None}
        stream = None
        phase = 'local_proxy'
        phase_start = started

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError()
            return value

        def receive(size):
            data = bytearray()
            while len(data) < size:
                stream.settimeout(remaining())
                block = stream.recv(size - len(data))
                if not block:
                    raise ConnectionError()
                data.extend(block)
            return bytes(data)

        try:
            target = urlsplit(url)
            host = target.hostname
            port = target.port or (443 if target.scheme == 'https' else 80)
            stream = socket.create_connection(('127.0.0.1', 1079), remaining())
            result['local_source_port'] = stream.getsockname()[1]
            stream.settimeout(remaining())
            stream.sendall(b'\x05\x01\x00')
            if receive(2) != b'\x05\x00':
                raise ConnectionError()
            name = host.encode('idna')
            if not 1 <= len(name) <= 253:
                raise ValueError()
            stream.sendall(b'\x05\x01\x00\x03' + bytes([len(name)]) + name + port.to_bytes(2, 'big'))
            response = receive(4)
            if response[:3] != b'\x05\x00\x00':
                raise ConnectionError()
            length = {1: 4, 4: 16}.get(response[3])
            if response[3] == 3:
                length = receive(1)[0]
            if length is None:
                raise ConnectionError()
            receive(length + 2)
            timings['local_proxy'] = round((time.monotonic() - phase_start) * 1000, 3)
            if target.scheme == 'https':
                phase, phase_start = 'tls', time.monotonic()
                stream.settimeout(remaining())
                stream = ssl.create_default_context().wrap_socket(stream, server_hostname=host)
                stages['tls'] = 'completed'
                stages['tcp'] = 'connected'
                timings['tls'] = round((time.monotonic() - phase_start) * 1000, 3)
            phase, phase_start = 'http', time.monotonic()
            path = target.path or '/'
            if target.query:
                path += '?' + target.query
            authority = host if target.port is None else f'{host}:{port}'
            request = (f'HEAD {path} HTTP/1.1\r\nHost: {authority}\r\n'
                       'User-Agent: PaoPaoGateway-HNM/1\r\nConnection: close\r\n\r\n').encode('ascii')
            stream.settimeout(remaining())
            stream.sendall(request)
            header = bytearray()
            while b'\r\n\r\n' not in header:
                stream.settimeout(remaining())
                block = stream.recv(min(1024, 32768 - len(header)))
                if not block or len(header) + len(block) >= 32768:
                    raise ValueError()
                header.extend(block)
            first = bytes(header).split(b'\r\n', 1)[0].split(b' ')
            if len(first) < 2 or first[0] not in (b'HTTP/1.0', b'HTTP/1.1'):
                raise ValueError()
            status = int(first[1])
            if not 100 <= status <= 599:
                raise ValueError()
            stages['http'], stages['tcp'] = 'completed', 'connected'
            timings['http'] = round((time.monotonic() - phase_start) * 1000, 3)
            result.update(ok=True, status=status, failure_stage=None if status < 400 else 'http')
        except (OSError, ValueError, UnicodeError) as error:
            # No certificate subject, URL, header, exception or credential is returned.
            result['failure_stage'] = phase
            result['timed_out'] = isinstance(error, TimeoutError) or time.monotonic() >= deadline
            if phase in {'tls', 'http'}:
                stages[phase] = 'failed'
            timings[phase] = round((time.monotonic() - phase_start) * 1000, 3)
        finally:
            if stream is not None:
                stream.close()
        result['ms'] = round((time.monotonic() - started) * 1000, 3)
        return result
