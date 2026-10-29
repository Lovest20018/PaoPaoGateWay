import unittest
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from staged_probe import StagedProbe


class FakeSocket:
    def __init__(self, response=b'HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n'):
        self.data = bytearray(b'\x05\x00\x05\x00\x00\x01\x7f\x00\x00\x01\x1f\x90' + response)
        self.writes = []
        self.closed = False
    def settimeout(self, seconds):
        assert 0 < seconds <= 10
    def getsockname(self):
        return ('127.0.0.1', 50001)
    def sendall(self, data):
        self.writes.append(data)
    def recv(self, count):
        value = bytes(self.data[:count])
        del self.data[:count]
        return value
    def close(self):
        self.closed = True


class StagedTests(unittest.TestCase):
    def test_timeout_has_explicit_failure_status_without_exception_text(self):
        with patch('staged_probe.socket.create_connection', side_effect=TimeoutError('private timeout')):
            result = StagedProbe()('https://example.com/', 1000)
        self.assertTrue(result['timed_out'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['failure_stage'], 'local_proxy')
        self.assertNotIn('private', str(result))

    def test_head_no_redirect_body_or_secret(self):
        sock = FakeSocket(b'HTTP/1.1 302 Found\r\nLocation: http://private.local/\r\nSet-Cookie: secret\r\n\r\n')
        with patch('staged_probe.socket.create_connection', return_value=sock):
            result = StagedProbe()('http://example.com/path?x=1', 2000)
        self.assertEqual(result['status'], 302)
        self.assertEqual(result['local_source_port'], 50001)
        self.assertTrue(result['ok'])
        self.assertTrue(sock.closed)
        self.assertEqual(len(sock.writes), 3)
        self.assertTrue(sock.writes[-1].startswith(b'HEAD /path?x=1 HTTP/1.1'))
        self.assertNotIn('secret', str(result))
        self.assertEqual(result['stages']['tls'], 'not_tested')

    def test_tls_sni_and_certificate_failure_stage(self):
        sock = FakeSocket()
        class Context:
            def wrap_socket(self, stream, server_hostname):
                self.hostname = server_hostname
                raise OSError('private certificate error')
        context = Context()
        with patch('staged_probe.socket.create_connection', return_value=sock), patch('staged_probe.ssl.create_default_context', return_value=context):
            result = StagedProbe()('https://example.com/', 2000)
        self.assertEqual(context.hostname, 'example.com')
        self.assertEqual(result['failure_stage'], 'tls')
        self.assertFalse(result['ok'])
        self.assertNotIn('private', str(result))

    def test_bad_or_oversized_http_header_is_bounded(self):
        for response in (b'bad', b'X' * 40000):
            sock = FakeSocket(response)
            with patch('staged_probe.socket.create_connection', return_value=sock):
                result = StagedProbe()('http://example.com/', 2000)
            self.assertFalse(result['ok'])
            self.assertEqual(result['failure_stage'], 'http')
            self.assertTrue(sock.closed)


if __name__ == '__main__':
    unittest.main()
