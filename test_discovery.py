import unittest
import http.client
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from unittest.mock import patch

from discovery import (Handler, LiveFeed, PageMetadataParser, allowed_local_host, build_services, browser_host, classify_http,
                       content_version, fetch_metadata, find_pane, local_asset_url,
                       parse_ss, parse_tmux, probe_http, watch_source)


class DiscoveryTests(unittest.TestCase):
    def test_smtp_greeting_prevents_http_and_tls_on_any_port(self):
        received = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            def serve():
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(2)
                    connection.sendall(b"220 test Mailpit ESMTP Service ready\r\n")
                    received.append(connection.recv(4096))
            thread = Thread(target=serve)
            thread.start()
            try:
                with patch("discovery.ssl._create_unverified_context") as tls:
                    self.assertIsNone(probe_http(*listener.getsockname()))
                    tls.assert_not_called()
            finally:
                thread.join(3)
        self.assertEqual(received, [b""])

    def test_http_still_detected_on_smtp_port(self):
        # Port numbers alone must never suppress a working web app.
        from unittest.mock import MagicMock
        connection = MagicMock()
        connection.recv.return_value = b"HTTP/1.1 200 OK"
        with (patch("discovery.socket.create_connection", return_value=connection),
              patch("discovery.select.select", return_value=([], [], []))):
            self.assertEqual(probe_http("127.0.0.1", 1025), "http")
        connection.sendall.assert_called_once_with(b"HEAD / HTTP/1.0\r\nHost: localhost\r\n\r\n")

    def test_https_fallback_is_preserved(self):
        from unittest.mock import MagicMock
        plain, secure = MagicMock(), MagicMock()
        plain.recv.side_effect = ConnectionResetError
        secure.recv.return_value = b"HTTP/1.1 200 OK"
        context = MagicMock()
        context.wrap_socket.return_value = secure
        with (patch("discovery.socket.create_connection", side_effect=[plain, MagicMock()]),
              patch("discovery.select.select", return_value=([], [], [])),
              patch("discovery.ssl._create_unverified_context", return_value=context)):
            self.assertEqual(probe_http("127.0.0.1", 8443), "https")
        context.wrap_socket.assert_called_once()

    def test_health_identifies_launchpad_without_waiting_for_discovery(self):
        # No feed or actions are attached: health must work before the first scan.
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=2)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), {"application": "launchpad", "status": "ok"})
            connection.request("GET", "/api/health", headers={"Host": "example.com"})
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_only_loopback_hosts_are_accepted(self):
        self.assertTrue(allowed_local_host("localhost:7777", 7777))
        self.assertTrue(allowed_local_host("127.0.0.1:7777", 7777))
        self.assertTrue(allowed_local_host("[::1]:7777", 7777))
        self.assertFalse(allowed_local_host("evil.example:7777", 7777))
        self.assertFalse(allowed_local_host("localhost:9999", 7777))
        self.assertFalse(allowed_local_host("localhost:7777@evil.example", 7777))

    def test_ss_parses_ipv4_ipv6_and_processes(self):
        output = (
            'LISTEN 0 128 127.0.0.1:3000 0.0.0.0:* users:(("node",pid=42,fd=22))\n'
            'LISTEN 0 128 [::]:8000 [::]:* users:(("python",pid=9,fd=3))\n'
            'ESTAB 0 0 127.0.0.1:1 127.0.0.1:2\n'
        )
        self.assertEqual(parse_ss(output), [
            {"address": "127.0.0.1", "port": 3000, "pids": [42]},
            {"address": "::", "port": 8000, "pids": [9]},
        ])

    def test_tmux_ancestor_and_duplicate_bindings(self):
        panes = parse_tmux("api\tserver\t%1\t10\t/home/me/project\n")
        listeners = [
            {"address": "0.0.0.0", "port": 3000, "pids": [42]},
            {"address": "127.0.0.1", "port": 3000, "pids": [42]},
            {"address": "::", "port": 8765, "pids": [7]},
        ]
        def fake_proc(pid):
            return {42: {"pid": 42, "ppid": 10, "command": "node", "cwd": "/home/me/project"},
                    10: {"pid": 10, "ppid": 1, "command": "bash", "cwd": "/home/me/project"}}[pid]
        with patch("discovery.proc_info", side_effect=fake_proc):
            services = build_services(listeners, panes, own_port=8765,
                                      probe=lambda host, port: "http",
                                      metadata=lambda host, port, scheme: {"title": None, "favicon": None, "kind": "web"},
                                      fingerprint=lambda *args: {"protocol": None, "protocol_evidence": None})
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0]["url"], "http://localhost:3000")
        self.assertEqual(services[0]["project"], "project")
        self.assertEqual(services[0]["kind"], "web")
        self.assertEqual(services[0]["tmux"]["session"], "api")

    def test_non_web_listener_has_no_link(self):
        services = build_services([{"address": "::", "port": 5432, "pids": []}], [],
                                  probe=lambda host, port: None,
                                  fingerprint=lambda *args: {"protocol": None, "protocol_evidence": None})
        self.assertEqual(services[0]["host"], "::1")
        self.assertIsNone(services[0]["url"])
        self.assertIsNone(find_pane(0, {}, {}))
        self.assertEqual(browser_host("0.0.0.0"), "127.0.0.1")

    def test_web_services_come_first_and_other_hosts_are_preserved(self):
        listeners = [
            {"address": "127.0.0.1", "port": 5432, "pids": []},
            {"address": "0.0.0.0", "port": 8000, "pids": []},
            {"address": "192.168.1.10", "port": 9000, "pids": []},
        ]
        services = build_services(
            listeners, [], probe=lambda host, port: "http" if port != 5432 else None,
            metadata=lambda host, port, scheme: {"title": None, "favicon": None,
                                                 "kind": "web" if port == 8000 else "api"},
            fingerprint=lambda *args: {"protocol": None, "protocol_evidence": None},
        )
        self.assertEqual([service["port"] for service in services], [8000, 9000, 5432])
        self.assertEqual(services[0]["url"], "http://localhost:8000")
        self.assertEqual(services[1]["url"], "http://192.168.1.10:9000")

    def test_title_and_icon_from_html(self):
        parser = PageMetadataParser()
        parser.feed('<html><head><title>  My &amp; App  </title>'
                    '<link rel="shortcut icon" href="/assets/icon.svg"></head></html>')
        self.assertEqual(parser.title, "My & App")
        self.assertEqual(parser.icon, "/assets/icon.svg")
        self.assertEqual(local_asset_url("http://localhost:3000/", parser.icon,
                                         "127.0.0.1", 3000, "http"),
                         "http://localhost:3000/assets/icon.svg")
        self.assertIsNone(local_asset_url("http://localhost:3000/", "https://example.com/icon.svg",
                                          "127.0.0.1", 3000, "http"))
        self.assertIsNone(local_asset_url("http://localhost:3000/", "http://[bad/icon.svg",
                                          "127.0.0.1", 3000, "http"))
        inline_icon = "data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%3E%3C/svg%3E"
        self.assertEqual(local_asset_url("http://localhost:3000/", inline_icon,
                                         "127.0.0.1", 3000, "http"), inline_icon)
        self.assertIsNone(local_asset_url("http://localhost:3000/", "data:text/html,<script></script>",
                                          "127.0.0.1", 3000, "http"))

    def test_metadata_follows_local_redirect_and_uses_default_favicon(self):
        from email.message import Message

        redirect_headers = Message()
        redirect_headers["Location"] = "/app"
        page_headers = Message()
        page_headers["Content-Type"] = "text/html; charset=utf-8"
        icon_headers = Message()
        icon_headers["Content-Type"] = "image/x-icon"
        responses = [
            (302, redirect_headers, b""),
            (200, page_headers, b"<title>Dashboard</title>"),
            (200, icon_headers, b""),
        ]
        with patch("discovery.request_local", side_effect=responses) as request:
            result = fetch_metadata("127.0.0.1", 3000, "http")
        self.assertEqual(result, {"title": "Dashboard", "favicon": "http://localhost:3000/favicon.ico",
                                  "kind": "web"})
        self.assertEqual([call.args[3:] for call in request.call_args_list],
                         [("GET", "/"), ("GET", "/app"), ("HEAD", "/favicon.ico")])

    def test_fetch_metadata_from_local_web_service(self):
        class WebPage(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (b'<html><head><title>Local frontend</title>'
                        b'<link rel="icon" href="/icon.svg"></head></html>')
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), WebPage)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            self.assertEqual(fetch_metadata("127.0.0.1", port, "http"), {
                "title": "Local frontend",
                "favicon": f"http://localhost:{port}/icon.svg",
                "kind": "web",
            })
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_api_response_is_not_classified_as_web_app(self):
        from email.message import Message

        headers = Message()
        headers["Content-Type"] = "application/json"
        with patch("discovery.request_local", return_value=(200, headers, b'{"ok":true}')) as request:
            result = fetch_metadata("127.0.0.1", 3333, "http")
        self.assertEqual(result, {"title": None, "favicon": None, "kind": "api"})
        self.assertEqual(request.call_count, 1)

        with patch("discovery.request_local", return_value=(405, headers, b'{}')):
            self.assertEqual(fetch_metadata("127.0.0.1", 3333, "http")["kind"], "api")
        self.assertEqual(classify_http(200, True, "Swagger UI"), "api")

    def test_source_change_triggers_restart_and_updates_version(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "discovery.py"
            source.write_text("before")
            original_version = content_version((source,))

            class Server:
                stopped = False

                def shutdown(self):
                    self.stopped = True

            server = Server()
            changed = Event()
            thread = Thread(target=watch_source, args=(server, changed, source, 0.01))
            thread.start()
            try:
                # The watcher must see the original file before it changes.
                self.assertFalse(changed.wait(0.03))
                source.write_text("after and longer")
                self.assertTrue(changed.wait(1))
                self.assertTrue(server.stopped)
                self.assertNotEqual(content_version((source,)), original_version)
            finally:
                changed.set()
                thread.join()

    def test_live_feed_publishes_only_changed_snapshots(self):
        class Actions:
            directory = Path("/nonexistent")
            token = "test-token"

            def public_jobs(self):
                return []

        class Source:
            own_port = 7777

            def __init__(self):
                self.services = []
                self.calls = 0

            def snapshot(self, force=False):
                self.calls += 1
                return {"services": list(self.services), "updated_at": self.calls,
                        "listener_error": None, "tmux_error": None, "version": "v1"}

        source = Source()
        feed = LiveFeed(source, Actions())
        with patch("discovery.run_command", return_value=("", None)):
            feed.start()
            try:
                revision, state = feed.wait_for_change(-1, 2)
                self.assertEqual(revision, 1)
                self.assertEqual(state["services"], [])
                feed.refresh(2)
                self.assertEqual(feed.wait_for_change(revision, .02), (revision, None))
                source.services = [{"port": 5432, "tmux": None}]
                feed.refresh(2)
                next_revision, state = feed.wait_for_change(revision, 2)
                self.assertEqual(next_revision, 2)
                self.assertEqual(state["services"][0]["port"], 5432)
            finally:
                feed.stop()


if __name__ == "__main__":
    unittest.main()
