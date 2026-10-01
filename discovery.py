#!/usr/bin/env python3
"""Local dashboard for TCP listeners, with optional tmux context."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import docker_discovery
import hashlib
from html.parser import HTMLParser
import http.client
import json
import ipaddress
import os
from pathlib import Path
import protocols
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
import tmux_control
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlsplit


ROOT = Path(__file__).resolve().parent
SOURCE_FILE = ROOT / "discovery.py"
BACKEND_FILES = (SOURCE_FILE, ROOT / "tmux_control.py", ROOT / "protocols.py", ROOT / "docker_discovery.py")
FRONTEND_FILES = tuple(ROOT / "static" / name for name in ("index.html", "app.js", "style.css", "favicon.svg"))
PID_PATTERN = re.compile(r"pid=(\d+)")
REFRESH_SECONDS = 8
PROBE_TIMEOUT = 0.35
MAX_HTML_BYTES = 128 * 1024
MAX_INLINE_ICON_LENGTH = 8192
INLINE_ICON_PATTERN = re.compile(
    r"^data:image/(?:png|jpeg|gif|webp|svg\+xml|x-icon|vnd\.microsoft\.icon)(?:;[^,]*)?,",
    re.IGNORECASE,
)


def boot_time() -> int | None:
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


BOOT_TIME = boot_time()
TICKS_PER_SECOND = os.sysconf("SC_CLK_TCK")


def file_stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def content_version(paths: tuple[Path, ...] = (*BACKEND_FILES, *FRONTEND_FILES)) -> str:
    stamps = [(str(path), file_stamp(path)) for path in paths]
    return hashlib.sha256(repr(stamps).encode()).hexdigest()[:12]


def watch_source(server: ThreadingHTTPServer, changed: threading.Event,
                 sources: tuple[Path, ...] | Path = BACKEND_FILES, interval: float = 1.0):
    if isinstance(sources, Path):
        sources = (sources,)
    original = tuple(file_stamp(source) for source in sources)
    while not changed.wait(interval):
        if tuple(file_stamp(source) for source in sources) != original:
            changed.set()
            server.shutdown()
            return


def run_command(args: list[str]) -> tuple[str, str | None]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    if result.returncode:
        return "", result.stderr.strip() or f"{' '.join(args)} exited with {result.returncode}"
    if result.stderr.strip():
        return result.stdout, result.stderr.strip()
    return result.stdout, None


def parse_endpoint(value: str) -> tuple[str, int] | None:
    if ":" not in value:
        return None
    address, port_text = value.rsplit(":", 1)
    if not port_text.isdigit():
        return None
    port = int(port_text)
    if not 0 < port < 65536:
        return None
    return address.strip("[]"), port


def parse_ss(output: str) -> list[dict]:
    listeners = []
    for line in output.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) < 5 or fields[0] != "LISTEN":
            continue
        endpoint = parse_endpoint(fields[3])
        if endpoint is None:
            continue
        address, port = endpoint
        listeners.append({
            "address": address,
            "port": port,
            "pids": sorted({int(pid) for pid in PID_PATTERN.findall(line)}),
        })
    return listeners


def parse_tmux(output: str) -> list[dict]:
    panes = []
    for line in output.splitlines():
        fields = line.split("\t", 4)
        if len(fields) != 5 or not fields[3].isdigit():
            continue
        session, window, pane, pid, cwd = fields
        panes.append({"session": session, "window": window, "pane": pane,
                      "pid": int(pid), "cwd": cwd})
    return panes


def proc_info(pid: int) -> dict:
    base = Path("/proc") / str(pid)
    info = {"pid": pid, "ppid": None, "command": None, "cwd": None, "started_at": None}
    try:
        stat = (base / "stat").read_text()
        # The command in parentheses can contain spaces and closing parentheses.
        fields = stat.rsplit(") ", 1)[1].split()
        info["ppid"] = int(fields[1])
        if BOOT_TIME is not None:
            info["started_at"] = int(BOOT_TIME + int(fields[19]) / TICKS_PER_SECOND)
    except (OSError, IndexError, ValueError):
        pass
    try:
        raw = (base / "cmdline").read_bytes().split(b"\0", 1)[0]
        info["command"] = os.path.basename(os.fsdecode(raw)) or None
    except OSError:
        pass
    try:
        info["cwd"] = str(base.joinpath("cwd").resolve(strict=True))
    except (OSError, RuntimeError):
        pass
    return info


def find_pane(pid: int, panes_by_pid: dict[int, dict], process_cache: dict[int, dict]) -> dict | None:
    seen = set()
    while pid > 0 and pid not in seen:
        if pid in panes_by_pid:
            return panes_by_pid[pid]
        seen.add(pid)
        process = process_cache.setdefault(pid, proc_info(pid))
        pid = process["ppid"] or 0
    return None


def browser_host(address: str) -> str:
    if address in ("*", "0.0.0.0"):
        return "127.0.0.1"
    if address == "::":
        return "::1"
    if "%" in address:
        address = address.split("%", 1)[0]
    return address


def host_for_url(host: str) -> str:
    if host == "127.0.0.1":
        return "localhost"
    return f"[{host}]" if ":" in host else host


def allowed_local_host(host_header: str, port: int) -> bool:
    try:
        parsed = urlsplit(f"http://{host_header}")
        hostname = parsed.hostname or ""
        local = hostname == "localhost" or ipaddress.ip_address(hostname).is_loopback
        return (local and parsed.port == port and not parsed.path and not parsed.query
                and not parsed.fragment and not parsed.username and not parsed.password)
    except ValueError:
        return False


def probe_http(host: str, port: int) -> str | None:
    # A short HEAD request distinguishes a web app from databases and other TCP services.
    for scheme in ("http", "https"):
        try:
            connection = socket.create_connection((host, port), timeout=PROBE_TIMEOUT)
            connection.settimeout(PROBE_TIMEOUT)
            if scheme == "https":
                context = ssl._create_unverified_context()
                connection = context.wrap_socket(connection, server_hostname=host)
            with connection:
                connection.sendall(b"HEAD / HTTP/1.0\r\nHost: localhost\r\n\r\n")
                if connection.recv(16).startswith(b"HTTP/"):
                    return scheme
        except (OSError, ssl.SSLError):
            continue
    return None


class PageMetadataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts: list[str] = []
        self.icon: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag == "title":
            self.in_title = True
        elif tag == "link" and self.icon is None:
            values = dict(attrs)
            if "icon" in (values.get("rel") or "").lower().split():
                self.icon = values.get("href")

    def handle_endtag(self, tag: str):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str):
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str | None:
        title = " ".join("".join(self.title_parts).split())[:200]
        return title or None


def local_asset_url(page_url: str, href: str, host: str, port: int, scheme: str) -> str | None:
    if not href or "\\" in href or any(ord(char) < 32 for char in href):
        return None
    if href.lower().startswith("data:"):
        return href if len(href) <= MAX_INLINE_ICON_LENGTH and INLINE_ICON_PATTERN.match(href) else None
    try:
        parsed = urlsplit(urljoin(page_url, href))
        same_port = parsed.port == port
    except ValueError:
        return None
    if (parsed.scheme != scheme or parsed.hostname not in {host, host_for_url(host)}
            or not same_port or parsed.username or parsed.password):
        return None
    origin = f"{scheme}://{host_for_url(host)}:{port}"
    return origin + (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")


def request_local(host: str, port: int, scheme: str, method: str, path: str):
    if scheme == "https":
        connection = http.client.HTTPSConnection(
            host, port, timeout=PROBE_TIMEOUT, context=ssl._create_unverified_context())
    else:
        connection = http.client.HTTPConnection(host, port, timeout=PROBE_TIMEOUT)
    try:
        connection.request(method, path, headers={
            "Host": f"{host_for_url(host)}:{port}",
            "Accept": "text/html, image/*",
            "Accept-Encoding": "identity",
        })
        response = connection.getresponse()
        return response.status, response.headers, response.read(MAX_HTML_BYTES) if method == "GET" else b""
    finally:
        connection.close()


def classify_http(status: int, is_html: bool, title: str | None) -> str:
    if title and re.search(r"\b(swagger|redoc|openapi|api documentation|django rest framework)\b", title, re.I):
        return "api"
    if 300 <= status < 400 or (200 <= status < 300 and is_html):
        return "web"
    return "api"


def fetch_metadata(host: str, port: int, scheme: str) -> dict:
    origin = f"{scheme}://{host_for_url(host)}:{port}"
    page_url = origin + "/"
    title = None
    favicon = None
    kind = "http"
    for _ in range(3):
        try:
            parsed = urlsplit(page_url)
            path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
            status, headers, body = request_local(host, port, scheme, "GET", path)
        except (OSError, http.client.HTTPException, ValueError):
            break
        if status in (301, 302, 303, 307, 308):
            kind = "web"
            target = local_asset_url(page_url, headers.get("Location", ""), host, port, scheme)
            if target is None or target == page_url:
                break
            page_url = target
            continue
        content_type = headers.get("Content-Type", "").lower()
        is_html = "html" in content_type or (
            not content_type and body.lstrip().lower().startswith((b"<!doctype html", b"<html"))
        )
        if 200 <= status < 300 and is_html:
            parser = PageMetadataParser()
            charset = headers.get_content_charset() or "utf-8"
            try:
                parser.feed(body.decode(charset, errors="replace"))
                title = parser.title
                if parser.icon:
                    favicon = local_asset_url(page_url, parser.icon, host, port, scheme)
            except (LookupError, ValueError):
                pass
        kind = classify_http(status, is_html, title)
        break

    if favicon is None and kind == "web":
        try:
            status, headers, _ = request_local(host, port, scheme, "HEAD", "/favicon.ico")
            icon_type = headers.get("Content-Type", "").lower()
            if 200 <= status < 300 and (icon_type.startswith("image/") or "icon" in icon_type):
                favicon = origin + "/favicon.ico"
        except (OSError, http.client.HTTPException, ValueError):
            pass
    return {"title": title, "favicon": favicon, "kind": kind}


def _address_priority(address: str) -> int:
    if address in ("127.0.0.1", "::1"):
        return 0
    if address in ("0.0.0.0", "::", "*"):
        return 1
    return 2


def build_services(listeners: list[dict], panes: list[dict], own_port: int | None = None,
                   probe=probe_http, metadata=fetch_metadata,
                   fingerprint=protocols.identify_tcp, docker_bindings: list[dict] | None = None) -> list[dict]:
    # Merge dual-stack sockets, but keep distinct containers on the same port separate.
    by_endpoint: dict[tuple[int, str | None], dict] = {}
    for item in listeners:
        if item["port"] == own_port:
            continue
        docker = docker_discovery.match_binding(item["address"], item["port"], docker_bindings or [])
        key = (item["port"], docker["id"] if docker else None)
        current = by_endpoint.get(key)
        if current is None:
            by_endpoint[key] = {**item, "pids": list(item["pids"]), "docker": docker}
        else:
            current["pids"] = sorted(set(current["pids"]) | set(item["pids"]))
            if _address_priority(item["address"]) < _address_priority(current["address"]):
                current["address"] = item["address"]

    panes_by_pid = {pane["pid"]: pane for pane in panes}
    process_cache: dict[int, dict] = {}
    services = []
    for item in sorted(by_endpoint.values(), key=lambda entry: (entry["port"], entry["address"])):
        port = item["port"]
        processes = [process_cache.setdefault(pid, proc_info(pid)) for pid in item["pids"]]
        pane = next((found for pid in item["pids"]
                     if (found := find_pane(pid, panes_by_pid, process_cache))), None)
        process = processes[0] if processes else None
        cwd = (pane or {}).get("cwd") or (process or {}).get("cwd")
        project = Path(cwd).name if cwd else None
        command = (process or {}).get("command")
        host = browser_host(item["address"])
        services.append({
            "port": port,
            "address": item["address"],
            "host": host,
            "project": project,
            "command": command,
            "pid": item["pids"][0] if item["pids"] else None,
            "started_at": (process or {}).get("started_at"),
            "docker": item["docker"],
            "tmux": {key: pane[key] for key in ("session", "window", "pane")} if pane else None,
            "url": None,
            "title": None,
            "favicon": None,
            "kind": "tcp",
            "protocol": None,
            "protocol_evidence": None,
        })

    with ThreadPoolExecutor(max_workers=12) as executor:
        schemes = list(executor.map(lambda service: probe(service["host"], service["port"]), services))
    for service, scheme in zip(services, schemes):
        if scheme:
            service["url"] = f"{scheme}://{host_for_url(service['host'])}:{service['port']}"
            service["kind"] = "http"
    with ThreadPoolExecutor(max_workers=12) as executor:
        web_services = [service for service in services if service["url"]]
        details = executor.map(
            lambda service: metadata(service["host"], service["port"], urlsplit(service["url"]).scheme),
            web_services,
        )
        for service, detail in zip(web_services, details):
            service.update(detail)
    with ThreadPoolExecutor(max_workers=12) as executor:
        tcp_services = [service for service in services if service["kind"] == "tcp"]
        fingerprints = executor.map(
            lambda service: fingerprint(service["host"], service["port"], service["command"],
                                        (service["docker"] or {}).get("image")),
            tcp_services,
        )
        for service, detail in zip(tcp_services, fingerprints):
            service.update(detail)
    priority = {"web": 0, "api": 1, "http": 2, "tcp": 3}
    services.sort(key=lambda service: (priority[service["kind"]], service["port"]))
    return services


class Discovery:
    def __init__(self, own_port: int):
        self.own_port = own_port
        self.lock = threading.Lock()
        self.cached_at = 0.0
        self.cached: dict = {}

    def snapshot(self, force: bool = False) -> dict:
        with self.lock:
            if not force and time.monotonic() - self.cached_at < REFRESH_SECONDS:
                return {**self.cached, "version": content_version()}
            ss_output, ss_error = run_command(["ss", "-H", "-ltnp"])
            tmux_output, tmux_error = run_command([
                "tmux", "list-panes", "-a", "-F",
                "#{session_name}\t#{window_name}\t#{pane_id}\t#{pane_pid}\t#{pane_current_path}",
            ])
            listeners = parse_ss(ss_output)
            docker_bindings, docker_error = docker_discovery.read_bindings()
            services = build_services(listeners, parse_tmux(tmux_output), self.own_port,
                                      docker_bindings=docker_bindings)
            self.cached = {
                "services": services,
                "updated_at": int(time.time()),
                "listener_error": ss_error,
                "tmux_error": tmux_error if tmux_error and tmux_error != "no server running on /tmp/tmux-1000/default" else None,
                "docker_error": docker_error,
            }
            self.cached_at = time.monotonic()
            return {**self.cached, "version": content_version()}


class LiveFeed:
    """Own the background scan and publish only changed snapshots."""

    def __init__(self, discovery: Discovery, actions: tmux_control.ActionManager):
        self.discovery = discovery
        self.actions = actions
        self.condition = threading.Condition()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.state: dict | None = None
        self.revision = 0
        self.scan_count = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        with self.condition:
            self.condition.notify_all()
        self.thread.join(timeout=2)

    def request_scan(self):
        self.wake.set()

    def refresh(self, timeout: float = 15) -> dict | None:
        with self.condition:
            before = self.scan_count
            self.request_scan()
            ready = self.condition.wait_for(lambda: self.scan_count > before or self.stopped.is_set(), timeout)
            return dict(self.state) if ready and self.state else None

    def read(self, timeout: float = 10) -> dict | None:
        with self.condition:
            self.condition.wait_for(lambda: self.state is not None or self.stopped.is_set(), timeout)
            return dict(self.state) if self.state else None

    def wait_for_change(self, revision: int, timeout: float = 20) -> tuple[int, dict | None]:
        with self.condition:
            self.condition.wait_for(
                lambda: (self.state is not None and self.revision != revision) or self.stopped.is_set(),
                timeout,
            )
            if self.state is None or self.revision == revision:
                return revision, None
            return self.revision, dict(self.state)

    def collect(self) -> dict:
        data = self.discovery.snapshot(force=True)
        sessions = {service["tmux"]["session"] for service in data["services"] if service["tmux"]}
        output, _ = run_command(["tmux", "list-sessions", "-F", "#{session_name}"])
        sessions.update(output.splitlines())
        projects = tmux_control.discover(self.actions.directory, sessions, data["services"])
        return {**data, "projects": projects,
                "other_sessions": sorted(sessions - {p["session"] for p in projects}),
                "jobs": self.actions.public_jobs(), "token": self.actions.token}

    def run(self):
        while not self.stopped.is_set():
            self.wake.clear()
            try:
                incoming = self.collect()
                with self.condition:
                    old = {key: value for key, value in self.state.items() if key != "updated_at"} if self.state else None
                    new = {key: value for key, value in incoming.items() if key != "updated_at"}
                    if old != new:
                        self.state = incoming
                        self.revision += 1
                    else:
                        self.state["updated_at"] = incoming["updated_at"]
                    self.scan_count += 1
                    self.condition.notify_all()
            except Exception as exc:
                print(f"Scansione Launchpad fallita: {exc}", file=sys.stderr, flush=True)
            self.wake.wait(REFRESH_SECONDS)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    feed: LiveFeed
    actions: tmux_control.ActionManager

    def send_json(self, status: int, value: dict):
        content = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        if self.path not in ("/api/tmux/action", "/api/refresh"):
            self.send_error(404)
            return
        host = self.headers.get("Host", "")
        if (not allowed_local_host(host, self.server.server_address[1])
                or self.headers.get("Origin") != f"http://{host}"
                or self.headers.get("X-Launchpad-Token") != self.actions.token):
            self.send_json(403, {"error": "Richiesta non autorizzata"})
            return
        if self.path == "/api/refresh":
            state = self.feed.refresh()
            if state is None:
                self.send_json(503, {"error": "Scansione non disponibile"})
            else:
                self.send_json(200, {"updated_at": state["updated_at"]})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self.send_json(415, {"error": "Formato non supportato"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2048:
                raise ValueError("Dimensione della richiesta non valida")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or not isinstance(body.get("project"), str) or not isinstance(body.get("action"), str):
                raise ValueError("Richiesta non valida")
            state = self.feed.read()
            if state is None:
                raise ValueError("Scansione non disponibile")
            job = self.actions.submit(body["project"], body["action"], state["projects"])
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc)})
            return
        self.send_json(202, {"job": job})

    def stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        revision = -1
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while not self.feed.stopped.is_set():
                revision, state = self.feed.wait_for_change(revision)
                if state is None:
                    self.wfile.write(b": keepalive\n\n")
                else:
                    payload = json.dumps(state, separators=(",", ":"))
                    self.wfile.write(f"id: {revision}\nevent: snapshot\ndata: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self):
        if not allowed_local_host(self.headers.get("Host", ""), self.server.server_address[1]):
            self.send_error(403, "Host non locale")
            return
        request_url = urlsplit(self.path)
        path = request_url.path
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/style.css": ("style.css", "text/css; charset=utf-8"),
                 "/favicon.svg": ("favicon.svg", "image/svg+xml")}
        if path == "/api/events":
            self.stream_events()
            return
        if path in ("/api/services", "/api/tmux"):
            state = self.feed.read()
            if state is None:
                self.send_json(503, {"error": "Scansione non disponibile"})
                return
        if path == "/api/services":
            content = json.dumps({key: state[key] for key in
                                  ("services", "updated_at", "listener_error", "tmux_error", "docker_error", "version")}).encode()
            content_type = "application/json; charset=utf-8"
        elif path == "/api/tmux":
            content = json.dumps({key: state[key] for key in
                                  ("projects", "other_sessions", "jobs", "token")}).encode()
            content_type = "application/json; charset=utf-8"
        elif path in files:
            name, content_type = files[path]
            content = (ROOT / "static" / name).read_bytes()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' http: https: data:; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)


def main():
    parser = argparse.ArgumentParser(description="Dashboard locale delle porte TCP in ascolto")
    parser.add_argument("--host", default="127.0.0.1", help="indirizzo del dashboard (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7777, help="porta del dashboard (default: 7777)")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("la porta deve essere compresa tra 0 e 65535")
    try:
        local_bind = args.host == "localhost" or ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        local_bind = False
    if not local_bind:
        parser.error("Launchpad può ascoltare solo su un indirizzo locale")
    actions = tmux_control.ActionManager(Path.home() / "tmux")
    discovery = Discovery(args.port)
    feed = LiveFeed(discovery, actions)
    actions.on_change = feed.request_scan
    handler = type("DiscoveryHandler", (Handler,), {"feed": feed, "actions": actions})
    source_changed = threading.Event()
    class Server(ThreadingHTTPServer):
        daemon_threads = True

    with Server((args.host, args.port), handler) as server:
        actual_port = server.server_address[1]
        discovery.own_port = actual_port
        print(f"Dashboard: http://{host_for_url(args.host)}:{actual_port}", flush=True)
        feed.start()
        threading.Thread(target=watch_source, args=(server, source_changed), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            feed.stop()
    if source_changed.is_set():
        os.execv(sys.executable, [sys.executable, "-B", str(SOURCE_FILE), *sys.argv[1:]])


if __name__ == "__main__":
    main()
