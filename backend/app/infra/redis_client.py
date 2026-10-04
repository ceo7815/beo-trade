from __future__ import annotations

import socket
from urllib.parse import urlparse


DEFAULT_URL = "redis://127.0.0.1:6379"
EVENTS = (
    "scan.requested",
    "scan.completed",
    "recommendation.created",
    "trade.approved",
    "risk.validated",
    "order.submitted",
    "order.updated",
    "order.filled",
    "position.opened",
    "exit.triggered",
    "exit.submitted",
    "position.closed",
)


class RedisUnavailable(RuntimeError):
    pass


def _encode(parts: list[str]) -> bytes:
    payload = f"*{len(parts)}\r\n".encode()
    for part in parts:
        raw = part.encode()
        payload += f"${len(raw)}\r\n".encode() + raw + b"\r\n"
    return payload


def _read_line(sock: socket.socket) -> bytes:
    data = b""
    while not data.endswith(b"\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            raise RedisUnavailable("Redis סגר את החיבור")
        data += chunk
    return data[:-2]


def _read(sock: socket.socket) -> object:
    prefix = sock.recv(1)
    if not prefix:
        raise RedisUnavailable("Redis לא ענה")
    if prefix == b"+":
        return _read_line(sock).decode()
    if prefix == b"-":
        raise RedisUnavailable(_read_line(sock).decode())
    if prefix == b":":
        return int(_read_line(sock))
    if prefix == b"$":
        size = int(_read_line(sock))
        if size < 0:
            return None
        body = b""
        while len(body) < size + 2:
            body += sock.recv(size + 2 - len(body))
        return body[:-2].decode()
    raise RedisUnavailable("תשובת Redis לא מוכרת")


class RedisClient:
    def __init__(self, url: str = "", timeout: float = 1.5, retries: int = 2) -> None:
        parsed = urlparse(url or DEFAULT_URL)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 6379
        self.timeout = timeout
        self.retries = retries
        self._pool: list[socket.socket] = []

    def close(self) -> None:
        while self._pool:
            try:
                self._pool.pop().close()
            except OSError:
                pass

    def _connect(self) -> socket.socket:
        if self._pool:
            return self._pool.pop()
        sock = socket.create_connection((self.host, self.port), self.timeout)
        sock.settimeout(self.timeout)
        return sock

    def command(self, *parts: str) -> object:
        last: Exception | None = None
        for _ in range(self.retries + 1):
            sock = None
            try:
                sock = self._connect()
                sock.sendall(_encode(list(parts)))
                value = _read(sock)
                self._pool.append(sock)
                return value
            except (OSError, RedisUnavailable) as exc:
                last = exc
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        raise RedisUnavailable(str(last or "Redis לא מחובר"))

    def ping(self) -> bool:
        return self.command("PING") == "PONG"

    def get(self, key: str) -> str | None:
        value = self.command("GET", key)
        return None if value is None else str(value)

    def set(self, key: str, value: str, ex_seconds: int | None = None) -> bool:
        if ex_seconds:
            return self.command("SET", key, value, "EX", str(ex_seconds)) == "OK"
        return self.command("SET", key, value) == "OK"

    def publish(self, event: str, payload: str) -> None:
        if event not in EVENTS:
            return
        self.command("LPUSH", "beo:events", f"{event}|{payload}")
        self.command("LTRIM", "beo:events", "0", "999")


def client_for(url: str = "") -> RedisClient:
    return RedisClient(url or DEFAULT_URL)


def publish_quiet(event: str, payload: str, url: str = "") -> None:
    client = client_for(url)
    try:
        if client.ping():
            client.publish(event, payload)
    except RedisUnavailable:
        return
    finally:
        client.close()
