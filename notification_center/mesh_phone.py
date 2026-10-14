"""Real TLS transport for the stock AgentCall request, with pre-send failover."""
from __future__ import annotations
import json
import socket
import ssl
import time

from .mesh_core import normalize


def read_frame(connection, deadline=None):
    data = bytearray()
    while b'\n' not in data and len(data) <= 65536:
        if deadline is not None:
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise OSError('phone mesh response deadline elapsed')
            connection.settimeout(remaining)
        chunk = connection.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    if b'\n' not in data or len(data) > 65536:
        raise OSError('incomplete phone mesh response')
    return bytes(data).split(b'\n', 1)[0]


class PhoneMeshRequester:
    def __init__(self, peers, core_socket, local_host, ca, cert, key):
        self.peers, self.core_socket, self.local_host = peers, core_socket, local_host
        self.tls = ssl.create_default_context(cafile=ca)
        self.tls.load_cert_chain(cert, key)

    def __call__(self, _local_socket, payload, timeout):
        deadline = time.monotonic() + timeout
        readiness_deadline = min(deadline, time.monotonic() + 15)
        route = normalize(self.core_socket, self.local_host, list(self.peers))
        while time.monotonic() < readiness_deadline:
            for host in route['hosts']:
                remaining = readiness_deadline - time.monotonic()
                if remaining <= 0:
                    break
                peer = self.peers[host]
                connection = None
                raw = None
                try:
                    raw = socket.create_connection((peer['address'], int(peer['port'])), timeout=min(3, remaining))
                    remaining = readiness_deadline - time.monotonic()
                    if remaining <= 0:
                        raw.close()
                        continue
                    raw.settimeout(min(3, remaining))
                    connection = self.tls.wrap_socket(raw, server_hostname='noticeplace-mesh')
                    remaining = readiness_deadline - time.monotonic()
                    if remaining <= 0:
                        connection.close()
                        continue
                    connection.settimeout(min(3, remaining))
                    connection.sendall(b'{"op":"status"}\n')
                    status = json.loads(read_frame(connection, min(readiness_deadline, time.monotonic()+3)))
                    if not isinstance(status, dict) or status.get('ready') is not True:
                        connection.close()
                        continue
                except (OSError, ValueError):
                    if connection is not None:
                        connection.close()
                    elif raw is not None:
                        raw.close()
                    continue
                # Only status checks repeat. Once any payload byte may have
                # crossed this boundary, no retry or peer fallback is allowed.
                try:
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise OSError('phone request deadline elapsed')
                    connection.settimeout(remaining)
                    connection.sendall(payload+b'\n')
                    result = read_frame(connection, deadline)
                    decoded = json.loads(result)
                    if not isinstance(decoded, dict):
                        raise ValueError('invalid phone receipt')
                    if decoded.get('ok') is not True and decoded.get('admitted') is not False:
                        raise ValueError('native failure does not prove non-admission')
                    if decoded.get('ok') is True and not decoded.get('call_id'):
                        raise ValueError('native success has no call identity')
                    return result
                except (OSError, ValueError) as error:
                    raise TimeoutError('Исход звонка неизвестен; повторный набор запрещён.') from error
                finally:
                    connection.close()
            remaining = readiness_deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(.25, remaining))
        raise RuntimeError('Нет доступного телефонного шлюза с активной регистрацией.')
