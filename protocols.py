"""Small, bounded probes for common local TCP services."""

from __future__ import annotations

import socket
import struct


TIMEOUT = 0.35
PORTS = {5432: "PostgreSQL", 27017: "MongoDB", 27018: "MongoDB",
         27019: "MongoDB", 6379: "Redis", 3306: "MySQL/MariaDB", 11211: "Memcached"}
COMMANDS = {"postgres": "PostgreSQL", "postmaster": "PostgreSQL",
            "mongod": "MongoDB", "mongos": "MongoDB", "redis-server": "Redis",
            "mysqld": "MySQL/MariaDB", "mariadbd": "MySQL/MariaDB",
            "memcached": "Memcached", "sshd": "SSH"}
IMAGES = {"postgres": "PostgreSQL", "postgis": "PostgreSQL", "mongo": "MongoDB",
          "redis": "Redis", "mysql": "MySQL/MariaDB", "mariadb": "MySQL/MariaDB",
          "memcached": "Memcached"}


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def probe_postgres(host: str, port: int) -> bool:
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        sock.settimeout(TIMEOUT)
        sock.sendall(struct.pack("!II", 8, 80877103))  # SSLRequest
        return recv_exact(sock, 1) in (b"S", b"N")


def probe_mongodb(host: str, port: int) -> bool:
    # OP_MSG with a read-only hello command against the admin database.
    elements = b"\x10hello\x00" + struct.pack("<i", 1) + b"\x02$db\x00" + struct.pack("<i", 6) + b"admin\x00"
    bson = struct.pack("<i", len(elements) + 5) + elements + b"\x00"
    body = struct.pack("<I", 0) + b"\x00" + bson
    message = struct.pack("<iiii", len(body) + 16, 1, 0, 2013) + body
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        sock.settimeout(TIMEOUT)
        sock.sendall(message)
        header = recv_exact(sock, 16)
    if len(header) != 16:
        return False
    length, _, response_to, opcode = struct.unpack("<iiii", header)
    return 21 <= length <= 1024 * 1024 and response_to == 1 and opcode == 2013


def probe_redis(host: str, port: int) -> bool:
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        sock.settimeout(TIMEOUT)
        sock.sendall(b"*1\r\n$4\r\nPING\r\n")
        response = sock.recv(128)
    return response.startswith((b"+PONG\r\n", b"-NOAUTH ", b"-DENIED "))


def probe_mysql(host: str, port: int) -> bool:
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        sock.settimeout(TIMEOUT)
        header = recv_exact(sock, 5)
    return len(header) == 5 and header[3] == 0 and header[4] == 10 and 10 <= int.from_bytes(header[:3], "little") <= 65536


def probe_memcached(host: str, port: int) -> bool:
    with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
        sock.settimeout(TIMEOUT)
        sock.sendall(b"version\r\n")
        return sock.recv(128).startswith(b"VERSION ")


PROBES = {"PostgreSQL": probe_postgres, "MongoDB": probe_mongodb,
          "Redis": probe_redis, "MySQL/MariaDB": probe_mysql,
          "Memcached": probe_memcached}


def identify_tcp(host: str, port: int, command: str | None, image: str | None = None) -> dict:
    process_type = COMMANDS.get((command or "").lower())
    image_name = (image or "").rsplit("/", 1)[-1].split(":", 1)[0].lower()
    image_type = IMAGES.get(image_name)
    label = process_type or image_type or PORTS.get(port)
    if not label:
        return {"protocol": None, "protocol_evidence": None}
    probe = PROBES.get(label)
    if probe:
        try:
            if probe(host, port):
                return {"protocol": label, "protocol_evidence": "handshake"}
        except OSError:
            pass
    return {"protocol": label, "protocol_evidence": "process" if process_type else "image" if image_type else "port"}
