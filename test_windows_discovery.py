import subprocess
import unittest
from unittest.mock import patch

from discovery import Discovery, build_services, fetch_metadata
import windows_discovery


class WindowsDiscoveryTests(unittest.TestCase):
    def test_only_explicitly_enabled_windows_ports_receive_http_requests(self):
        with (patch.dict('os.environ', {'LAUNCHPAD_WINDOWS_HTTP_PORTS': '5178'}),
              patch('windows_discovery.request_local') as request):
            self.assertIsNone(windows_discovery.probe_http('127.0.0.1', 28252))
            request.assert_not_called()
            self.assertEqual(windows_discovery.probe_http('127.0.0.1', 5178), 'http')
            request.assert_called_once_with('127.0.0.1', 5178, 'http', 'HEAD', '/')
        with (patch.dict('os.environ', {}, clear=True),
              patch('windows_discovery.request_local') as request):
            self.assertIsNone(windows_discovery.probe_http('127.0.0.1', 5178))
            request.assert_not_called()

    def test_windows_pids_never_use_linux_process_or_tmux_data(self):
        rows = [
            {'address': '127.0.0.1', 'port': 5178, 'pids': [42], 'pid': 42,
             'command': 'PulseDeck.Agent', 'started_at': 100},
            {'address': '::', 'port': 5178, 'pids': [42], 'pid': 42,
             'command': 'PulseDeck.Agent', 'started_at': 100},
        ]
        with patch('discovery.proc_info', side_effect=AssertionError('Linux PID lookup')):
            services = build_services(rows, [{'pid': 42, 'cwd': '/linux/project'}],
                                      source='windows', probe=lambda *args: 'http',
                                      metadata=lambda *args: {'kind': 'web'})
        self.assertEqual(len(services), 1)
        self.assertEqual(services[0]['command'], 'PulseDeck.Agent')
        self.assertEqual(services[0]['started_at'], 100)
        self.assertEqual(services[0]['source'], 'windows')
        self.assertIsNone(services[0]['tmux'])
        self.assertIsNone(services[0]['project'])
        self.assertEqual(services[0]['url'], 'http://localhost:5178')

    def test_snapshot_keeps_windows_and_linux_on_the_same_port(self):
        row = {'address': '127.0.0.1', 'port': 5178, 'pids': [42],
               'command': 'Windows app', 'started_at': None}
        with (patch('discovery.run_command', return_value=('', None)),
              patch('discovery.parse_ss', return_value=[{**row, 'pids': []}]),
              patch('discovery.docker_discovery.read_bindings', return_value=([], None)),
              patch('discovery.windows_discovery.read_listeners', return_value=([row], None)),
              patch('discovery.windows_discovery.probe_http', return_value=None),
              patch('discovery.socket.create_connection', side_effect=OSError)):
            services = Discovery(7777).snapshot()['services']
        self.assertEqual([(s['port'], s['source']) for s in services],
                         [(5178, 'linux'), (5178, 'windows')])

    def test_disabled_outside_wsl_and_timeout_is_reported(self):
        with (patch('windows_discovery.executable', return_value=None),
              patch('windows_discovery.subprocess.run') as run):
            self.assertEqual(windows_discovery.read_listeners(), ([], None))
            run.assert_not_called()
        with (patch('windows_discovery.executable', return_value='powershell.exe'),
              patch('windows_discovery.subprocess.run',
                    side_effect=subprocess.TimeoutExpired('powershell.exe', 6))):
            rows, error = windows_discovery.read_listeners()
        self.assertEqual(rows, [])
        self.assertIn('timed out', error)

    def test_windows_http_metadata_uses_windows_transport(self):
        body = b'<html><head><title>Windows app</title><link rel="icon" href="/icon.svg"></head></html>'
        result = subprocess.CompletedProcess([], 0,
                    b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n' + body, b'')
        with (patch('windows_discovery.executable', return_value='curl.exe'),
              patch('windows_discovery.subprocess.run', return_value=result) as run,
              patch('discovery.request_local', side_effect=AssertionError('Linux HTTP request'))):
            metadata = fetch_metadata('127.0.0.1', 5178, 'http', request=windows_discovery.request_local)
        self.assertEqual(metadata['title'], 'Windows app')
        self.assertEqual(metadata['kind'], 'web')
        self.assertEqual(metadata['favicon'], 'http://localhost:5178/icon.svg')
        self.assertIn('--noproxy', run.call_args.args[0])

    def test_invalid_powershell_output_is_reported(self):
        result = subprocess.CompletedProcess([], 0, b'not json', b'')
        with (patch('windows_discovery.executable', return_value='powershell.exe'),
              patch('windows_discovery.subprocess.run', return_value=result)):
            rows, error = windows_discovery.read_listeners()
        self.assertEqual(rows, [])
        self.assertIsNotNone(error)


if __name__ == '__main__':
    unittest.main()
