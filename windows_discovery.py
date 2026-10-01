"""Read Windows listeners and probe HTTP from Windows when running in WSL."""

from __future__ import annotations

from email.parser import BytesParser
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess


WINDOWS_SYSTEM = Path('/mnt/c/Windows/System32')
LISTENERS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$processes = @{}
Get-Process | ForEach-Object { $processes[$_.Id] = $_ }
$rows = @(Get-NetTCPConnection -State Listen | ForEach-Object {
    $process = $processes[[int]$_.OwningProcess]
    $started = $null
    try { $started = ([DateTimeOffset]$process.StartTime).ToUnixTimeSeconds() } catch {}
    [PSCustomObject]@{
        address = $_.LocalAddress; port = [int]$_.LocalPort
        pid = [int]$_.OwningProcess; command = $process.ProcessName; started_at = $started
    }
})
ConvertTo-Json -InputObject $rows -Compress
"""


def executable(name: str) -> str | None:
    try:
        if 'microsoft' not in Path('/proc/sys/kernel/osrelease').read_text().lower():
            return None
    except OSError:
        return None
    found = shutil.which(name)
    fallback = WINDOWS_SYSTEM / ('WindowsPowerShell/v1.0/powershell.exe' if name == 'powershell.exe' else name)
    return found or (str(fallback) if fallback.is_file() else None)


def read_listeners() -> tuple[list[dict], str | None]:
    powershell = executable('powershell.exe')
    if not powershell:
        return [], None
    try:
        result = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command', LISTENERS_SCRIPT],
                                capture_output=True, timeout=6, check=False)
        if result.returncode:
            return [], result.stderr.decode('utf-8', errors='replace').strip() or 'PowerShell non disponibile'
        rows = json.loads(result.stdout.decode('utf-8-sig'))
        if isinstance(rows, dict):
            rows = [rows]
        return [{**row, 'pids': [row['pid']]} for row in rows or []], None
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return [], str(exc)


def request_local(host: str, port: int, scheme: str, method: str, path: str):
    curl = executable('curl.exe')
    if not curl:
        raise OSError('curl.exe non disponibile')
    host = f'[{host}]' if ':' in host else host
    args = [curl, '--silent', '--noproxy', '*', '--insecure', '--max-time', '0.7',
            '--max-filesize', '131072', '--dump-header', '-', '--output', '-']
    if method == 'HEAD':
        args.append('--head')
    args.append(f'{scheme}://{host}:{port}{path}')
    try:
        result = subprocess.run(args, capture_output=True, timeout=2, check=False)
    except subprocess.TimeoutExpired as exc:
        raise OSError('Timeout HTTP Windows') from exc
    # curl can return a size/timeout error after receiving valid HTTP headers.
    header, separator, body = result.stdout.partition(b'\r\n\r\n')
    if not separator or not header.startswith(b'HTTP/'):
        raise OSError('Nessuna risposta HTTP Windows')
    status_line, _, fields = header.partition(b'\r\n')
    try:
        status = int(status_line.split()[1])
    except (ValueError, IndexError) as exc:
        raise http.client.HTTPException('Risposta HTTP Windows non valida') from exc
    return status, BytesParser().parsebytes(fields), body[:131072] if method == 'GET' else b''


def probe_http(host: str, port: int) -> str | None:
    allowed = {value.strip() for value in os.environ.get('LAUNCHPAD_WINDOWS_HTTP_PORTS', '').split(',')}
    if str(port) not in allowed:
        return None
    for scheme in ('http', 'https'):
        try:
            request_local(host, port, scheme, 'HEAD', '/')
            return scheme
        except (OSError, http.client.HTTPException):
            pass
    return None
