"""Real TLS transport for the stock AgentCall request, with pre-send failover."""
from __future__ import annotations
import json
import socket
import ssl
import time

from .mesh_core import normalize


def read_frame(connection):
    data = bytearray()
    while b'\n' not in data and len(data) <= 65536:
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
        route = normalize(self.core_socket, self.local_host, list(self.peers))
        deadline = time.monotonic() + timeout
        for host in route['hosts']:
            peer = self.peers[host]
            connection = None
            raw = None
            try:
                raw = socket.create_connection((peer['address'], int(peer['port'])), timeout=min(3, max(.1, deadline-time.monotonic())))
                connection = self.tls.wrap_socket(raw, server_hostname='noticeplace-mesh')
                connection.settimeout(min(3, max(.1, deadline-time.monotonic())))
                connection.sendall(b'{"op":"status"}\n')
                status = json.loads(read_frame(connection))
                if status.get('ready') is not True:
                    connection.close()
                    continue
            except (OSError, ValueError):
                if connection is not None:
                    connection.close()
                elif raw is not None:
                    raw.close()
                continue
            # No peer fallback follows this boundary, even if the connection
            # disappears before a receipt arrives. The dispatcher persists
            # UNCERTAIN and never redials this reservation automatically.
            try:
                connection.settimeout(max(.1, deadline-time.monotonic()))
                connection.sendall(payload+b'\n')
                result = read_frame(connection)
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
        raise RuntimeError('Нет доступного телефонного шлюза с активной регистрацией.')
