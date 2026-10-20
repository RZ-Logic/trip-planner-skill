"""Network guard for the test run: with TRIP_PLANNER_NO_NETWORK=1, every socket connection to a host that is not
the machine itself (127.0.0.0/8, ::1, localhost) is refused before any DNS lookup or packet leaves.

Python imports a module named sitecustomize at startup when it is on sys.path, so put this folder there:

    PYTHONPATH=tools/ci TRIP_PLANNER_NO_NETWORK=1 python -m unittest discover -s tests

A refused connection raises ConnectionRefusedError, which the scripts read as "network blocked", the same as a
sandbox with no outbound access. Tests must never try the network, so when any connection was refused the run
lists them on stderr and exits with status 1, even if every test passed. Proxy settings (HTTP(S)_PROXY, ALL_PROXY,
system proxies) are switched off, so a proxy on the machine cannot carry a request out. Standard library only,
Python 3.8+.
"""
import atexit
import ipaddress
import os
import socket
import sys

_ENV = "TRIP_PLANNER_NO_NETWORK"
_REFUSED = []


def _loopback(host):
    """True for localhost names and loopback addresses."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host or "").strip().strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


def _refuse(host, port):
    _REFUSED.append("%s:%s" % (host, port))
    raise ConnectionRefusedError("network blocked by %s=1 (only loopback connections are allowed): %s:%s"
                                 % (_ENV, host, port))


def install():
    """Patch socket.socket.connect, connect_ex and socket.create_connection (once)."""
    if getattr(socket, "_trip_planner_guard", False):
        return
    for name in list(os.environ):   # a proxy on the machine itself is loopback, and would carry requests out
        if name.lower() in ("http_proxy", "https_proxy", "all_proxy", "ftp_proxy"):
            del os.environ[name]
    # urllib reads the Windows registry or macOS proxy only when the environment names none: 'no' stops that
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_create = socket.create_connection
    inet = {socket.AF_INET, getattr(socket, "AF_INET6", socket.AF_INET)}

    def check(sock, address):
        if sock.family in inet and isinstance(address, tuple) and address and not _loopback(address[0]):
            _refuse(address[0], address[1] if len(address) > 1 else "")

    def connect(self, address):
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(self, address)
        return real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        if not _loopback(address[0]):     # refused before the name is looked up
            _refuse(address[0], address[1])
        return real_create(address, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.create_connection = create_connection
    socket._trip_planner_guard = True
    atexit.register(_report)


def _report():
    """At exit: a run that tried the network fails, so a test that forgot a mock cannot pass by luck."""
    if _REFUSED:
        sys.stdout.flush()
        sys.stderr.write("network guard: %d connection attempt(s) refused: %s\n"
                         % (len(_REFUSED), ", ".join(sorted(set(_REFUSED)))))
        sys.stderr.flush()
        os._exit(1)


if os.environ.get(_ENV, "").strip() == "1":
    install()
