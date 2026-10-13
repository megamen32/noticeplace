"""Call the installed shared Rust core; authorization stays in NoticePlace."""
from __future__ import annotations

import http.client
import json
import socket


class PrivateHTTP(http.client.HTTPConnection):
    def __init__(self, socket_path: str):
        super().__init__("mesh-core", timeout=3)
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


def normalize(socket_path: str, local_host: str, known_hosts: list[str]) -> dict:
    body = json.dumps({"local_host_id": local_host, "known_hosts": known_hosts,
                       "hosts": "*", "origin_host": local_host, "hop_count": 0}).encode()
    connection = PrivateHTTP(socket_path)
    try:
        connection.request("POST", "/v1/normalize", body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read(1024 * 1024 + 1)
        if response.status != 200 or len(data) > 1024 * 1024:
            raise RuntimeError("shared mesh core did not accept the authorized host set")
        result = json.loads(data)
        if not isinstance(result, dict) or not isinstance(result.get("hosts"), list):
            raise RuntimeError("shared mesh core returned an invalid route")
        return result
    finally:
        connection.close()
