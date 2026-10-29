#!/usr/bin/env python3
"""Expose a loopback-only HTTP CONNECT proxy backed by the runner's SOCKS proxy.

This is intentionally small and purpose-specific: GitHub's Node artifact uploader
accepts HTTP(S) proxy URLs, while the self-hosted runner may only have SOCKS
configured. No upstream proxy URL or credentials are printed.
"""
from __future__ import annotations

import argparse
import os
import select
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


_PROXY_ENV_NAMES = (
    "ALL_PROXY", "all_proxy",
    "HTTPS_PROXY", "https_proxy",
    "HTTP_PROXY", "http_proxy",
)


def parse_socks_upstream(environ: dict[str, str] | None = None) -> dict[str, object] | None:
    """Return the first configured SOCKS5 upstream without exposing credentials."""
    env = os.environ if environ is None else environ
    for name in _PROXY_ENV_NAMES:
        raw = str(env.get(name, "") or "").strip()
        if not raw:
            continue
        try:
            parsed = urlsplit(raw)
            scheme = parsed.scheme.lower()
            if scheme not in {"socks5", "socks5h"}:
                continue
            host = parsed.hostname
            port = parsed.port or 1080
        except ValueError:
            continue
        if not host or not 1 <= port <= 65535:
            continue
        return {
            "scheme": scheme,
            "host": host,
            "port": port,
            "rdns": scheme == "socks5h",
            "username": unquote(parsed.username) if parsed.username else None,
            "password": unquote(parsed.password) if parsed.password else None,
        }
    return None


def parse_connect_target(authority: str) -> tuple[str, int]:
    """Parse a CONNECT authority including bracketed IPv6 hosts."""
    value = str(authority or "").strip()
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0 or value[closing + 1:closing + 2] != ":":
            raise ValueError("invalid CONNECT authority")
        host = value[1:closing]
        port_text = value[closing + 2:]
    else:
        host, separator, port_text = value.rpartition(":")
        if not separator:
            raise ValueError("invalid CONNECT authority")
    if not host or not port_text.isdigit():
        raise ValueError("invalid CONNECT authority")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError("invalid CONNECT port")
    return host, port


class _LoopbackProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, upstream):
        self.upstream = upstream
        super().__init__(address, handler)


class _ConnectHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        # Default access logs include the requested host and are unnecessary here.
        return

    def do_CONNECT(self) -> None:
        upstream_socket = None
        connected = False
        try:
            destination_host, destination_port = parse_connect_target(self.path)
            import socks

            cfg = self.server.upstream  # type: ignore[attr-defined]
            upstream_socket = socks.socksocket()
            upstream_socket.set_proxy(
                socks.SOCKS5,
                addr=str(cfg["host"]),
                port=int(cfg["port"]),
                rdns=bool(cfg["rdns"]),
                username=cfg["username"],
                password=cfg["password"],
            )
            upstream_socket.settimeout(30)
            upstream_socket.connect((destination_host, destination_port))
            self.send_response(200, "Connection Established")
            self.send_header("Proxy-Agent", "OpenFilm loopback SOCKS bridge")
            self.end_headers()
            self.wfile.flush()
            connected = True
            client_socket = self.connection
            client_socket.settimeout(None)
            upstream_socket.settimeout(None)
            while True:
                readable, _, exceptional = select.select(
                    [client_socket, upstream_socket], [], [client_socket, upstream_socket], 300
                )
                if exceptional or not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = upstream_socket if source is client_socket else client_socket
                    target.sendall(data)
        except Exception as exc:
            if not connected:
                try:
                    self.send_error(502, explain=f"upstream {type(exc).__name__}")
                except OSError:
                    pass
        finally:
            if upstream_socket is not None:
                try:
                    upstream_socket.close()
                except OSError:
                    pass
            try:
                self.connection.close()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port-file", required=True, help="file receiving the loopback listener port")
    args = parser.parse_args(argv)
    upstream = parse_socks_upstream()
    if upstream is None:
        print("ACTION_ARTIFACT_PROXY=SKIP reason=no_socks_upstream")
        return 0
    server = _LoopbackProxyServer(("127.0.0.1", 0), _ConnectHandler, upstream)
    port_path = Path(args.port_file)
    port_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = port_path.with_suffix(port_path.suffix + ".tmp")
    temporary.write_text(str(server.server_address[1]), encoding="ascii")
    os.replace(temporary, port_path)
    print(f"ACTION_ARTIFACT_PROXY=READY upstream_scheme={upstream['scheme']} bind=127.0.0.1", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ACTION_ARTIFACT_PROXY=REJECT error_type={type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
