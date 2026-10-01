"""Read published TCP ports and start times from the local Docker Engine socket."""

from __future__ import annotations

from datetime import datetime
import http.client
import json
import os
from pathlib import Path
import re
import socket


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")


class DockerConnection(http.client.HTTPConnection):
    def __init__(self, path: Path):
        super().__init__("localhost", timeout=2)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


def socket_path() -> Path | None:
    configured = os.environ.get("DOCKER_HOST", "")
    if configured.startswith("unix://"):
        path = Path(configured[7:])
        return path if path.exists() else None
    candidates = [Path("/var/run/docker.sock")]
    if os.environ.get("XDG_RUNTIME_DIR"):
        candidates.append(Path(os.environ["XDG_RUNTIME_DIR"]) / "docker.sock")
    return next((path for path in candidates if path.exists()), None)


def get_json(path: Path, endpoint: str):
    connection = DockerConnection(path)
    try:
        connection.request("GET", endpoint)
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 200 or len(body) > MAX_RESPONSE_BYTES:
            raise ValueError(f"Docker API HTTP {response.status}")
        return json.loads(body)
    finally:
        connection.close()


def parse_started_at(value: str | None) -> int | None:
    if not value:
        return None
    try:
        timestamp = int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        return timestamp if timestamp > 0 else None
    except (ValueError, OverflowError):
        return None


def container_bindings(containers: list[dict], details: dict[str, dict]) -> list[dict]:
    bindings = []
    for container in containers:
        container_id = container.get("Id", "")
        if not CONTAINER_ID.fullmatch(container_id):
            continue
        state = details.get(container_id, {}).get("State") or {}
        info = {
            "id": container_id[:12],
            "name": (container.get("Names") or [container_id[:12]])[0].lstrip("/"),
            "image": container.get("Image"),
            "compose_project": (container.get("Labels") or {}).get("com.docker.compose.project"),
            "started_at": parse_started_at(state.get("StartedAt")),
        }
        for port in container.get("Ports") or []:
            if port.get("Type") != "tcp" or not isinstance(port.get("PublicPort"), int):
                continue
            bindings.append({"address": port.get("IP") or "0.0.0.0", "port": port["PublicPort"],
                             "container": {**info, "internal_port": port.get("PrivatePort")}})
    return bindings


def read_bindings() -> tuple[list[dict], str | None]:
    path = socket_path()
    if path is None:
        return [], None
    try:
        containers = get_json(path, "/containers/json")
        details = {}
        for container in containers:
            container_id = container.get("Id", "")
            if CONTAINER_ID.fullmatch(container_id) and any(
                    port.get("Type") == "tcp" and port.get("PublicPort") for port in container.get("Ports") or []):
                try:
                    details[container_id] = get_json(path, f"/containers/{container_id}/json")
                except (OSError, ValueError, http.client.HTTPException):
                    pass  # A container can stop between list and inspect.
        return container_bindings(containers, details), None
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return [], str(exc)


def match_binding(address: str, port: int, bindings: list[dict]) -> dict | None:
    same_port = [item for item in bindings if item["port"] == port]
    address = address.split("%", 1)[0]
    exact = [item for item in same_port if item["address"] == address]
    if exact:
        return exact[0]["container"]
    wildcard = [item for item in same_port if item["address"] in ("0.0.0.0", "::")]
    if address in ("*", "0.0.0.0", "::"):
        ids = {item["container"]["id"] for item in wildcard}
        return wildcard[0]["container"] if len(ids) == 1 else None
    family = ":" in address
    matches = [item for item in wildcard if (item["address"] == "::") == family]
    ids = {item["container"]["id"] for item in matches}
    return matches[0]["container"] if len(ids) == 1 else None
