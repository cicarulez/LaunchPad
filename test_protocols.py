import struct
import unittest
from unittest.mock import patch

import protocols
from discovery import build_services


class ProtocolTests(unittest.TestCase):
    def test_smtp_greeting_and_mailpit_image(self):
        from unittest.mock import MagicMock
        for greeting, expected in [(b"220 local ESMTP ready\r\n", True),
                                   (b"220 local SMTP ready\r\n", True),
                                   (b"220 FTP ready\r\n", False),
                                   (b"HTTP/1.1 200 OK\r\n", False)]:
            sock = MagicMock()
            sock.__enter__.return_value = sock
            sock.recv.side_effect = [bytes([byte]) for byte in greeting]
            with patch("protocols.socket.create_connection", return_value=sock):
                self.assertEqual(protocols.probe_smtp("127.0.0.1", 1025), expected)
            sock.sendall.assert_not_called()
        with patch.dict(protocols.PROBES, {"SMTP": lambda host, port: True}):
            self.assertEqual(protocols.identify_tcp("127.0.0.1", 2525, None, "axllent/mailpit:v1.27"),
                             {"protocol": "SMTP", "protocol_evidence": "handshake"})

    def test_handshake_then_process_then_port_evidence(self):
        with patch.dict(protocols.PROBES, {"PostgreSQL": lambda host, port: True}):
            self.assertEqual(protocols.identify_tcp("127.0.0.1", 5432, None),
                             {"protocol": "PostgreSQL", "protocol_evidence": "handshake"})
        with patch.dict(protocols.PROBES, {"PostgreSQL": lambda host, port: False}):
            self.assertEqual(protocols.identify_tcp("127.0.0.1", 5432, None)["protocol_evidence"], "port")
            self.assertEqual(protocols.identify_tcp("127.0.0.1", 5555, "postgres")["protocol_evidence"], "process")
            self.assertEqual(protocols.identify_tcp("127.0.0.1", 5434, None, "postgres:16")["protocol_evidence"], "image")
        self.assertEqual(protocols.identify_tcp("127.0.0.1", 12345, None)["protocol"], None)

    def test_non_http_listener_keeps_protocol_result(self):
        services = build_services([{"address": "127.0.0.1", "port": 27017, "pids": []}], [],
                                  probe=lambda host, port: None,
                                  fingerprint=lambda host, port, command, image: {
                                      "protocol": "MongoDB", "protocol_evidence": "handshake"})
        self.assertEqual((services[0]["kind"], services[0]["protocol"], services[0]["protocol_evidence"]),
                         ("tcp", "MongoDB", "handshake"))

    def test_mongodb_hello_response_header(self):
        class Socket:
            def __init__(self):
                self.sent = b""
                self.response = struct.pack("<iiii", 40, 2, 1, 2013)

            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, timeout): pass
            def sendall(self, data): self.sent = data
            def recv(self, size):
                chunk, self.response = self.response[:size], self.response[size:]
                return chunk

        sock = Socket()
        with patch("protocols.socket.create_connection", return_value=sock):
            self.assertTrue(protocols.probe_mongodb("127.0.0.1", 27017))
        self.assertEqual(struct.unpack("<i", sock.sent[12:16])[0], 2013)
        self.assertIn(b"hello\x00", sock.sent)


if __name__ == "__main__":
    unittest.main()
