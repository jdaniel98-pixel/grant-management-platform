"""Keeps the test suite offline. Import this module first in every test file.

It (1) removes OpenAI settings from the process environment so a developer's real key can never reach a test, and
(2) refuses every network connection except to this computer (loopback), so a test that forgets to mock the AI
gateway, the web fetcher or SMTP fails loudly instead of calling a real service.
"""
import os
import socket

for _name in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
    os.environ.pop(_name, None)

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}


class ExternalNetworkBlocked(AssertionError):
    """A test tried to reach a host outside this computer."""


def _host_of(address):
    if isinstance(address, (tuple, list)) and address:
        host = address[0]
        return host.decode() if isinstance(host, bytes) else str(host)
    return None


def _check(host):
    if host is not None and host not in _LOOPBACK and not host.startswith("127."):
        raise ExternalNetworkBlocked(f"tests must stay offline: blocked connection to {host!r}")


if not getattr(socket, "_offline_guard_installed", False):
    _connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex
    _getaddrinfo, _create = socket.getaddrinfo, socket.create_connection

    def connect(self, address):
        _check(_host_of(address))
        return _connect(self, address)

    def connect_ex(self, address):
        _check(_host_of(address))
        return _connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        _check(host.decode() if isinstance(host, bytes) else host)
        return _getaddrinfo(host, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        _check(_host_of(address))
        return _create(address, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    socket.getaddrinfo, socket.create_connection = getaddrinfo, create_connection
    socket._offline_guard_installed = True
