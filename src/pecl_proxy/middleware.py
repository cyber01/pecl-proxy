"""ASGI middleware: client/proxy detection, request id, access log, request metrics."""

from __future__ import annotations

import ipaddress
import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings
from .logs import access_log, app_log, log_event
from .metrics import Metrics

_REQUEST_ID = re.compile(r"[A-Za-z0-9._\-]{1,64}")


class RequestContextMiddleware:
    """Works out how the client sees the service and logs every request.

    Behind a reverse proxy listed in ``TRUSTED_PROXIES`` the ``X-Forwarded-Proto``,
    ``X-Forwarded-Host``, ``X-Forwarded-Prefix``, ``X-Forwarded-For`` and ``X-Real-IP`` headers
    are honoured, so URLs in responses use the external scheme/host and logs show the real
    client IP.
    """

    def __init__(self, app: ASGIApp, settings: Settings, metrics: Metrics):
        self.app = app
        self.settings = settings
        self.metrics = metrics
        self._warned: set[str] = set()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        state = scope.setdefault("state", {})
        request_id = headers.get("x-request-id", "")
        if not _REQUEST_ID.fullmatch(request_id):
            request_id = uuid.uuid4().hex[:16]
        client_ip, public_base = self._resolve(scope, headers)
        state.update(request_id=request_id, client_ip=client_ip, public_base=public_base)

        status = 500
        sent = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"],
                                      (b"x-request-id", request_id.encode())]
            elif message["type"] == "http.response.body":
                sent += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - started
            resource = state.get("resource")
            cache = state.get("cache")
            if resource is not None:
                self.metrics.requests.labels(resource, cache or "NONE", str(status)).inc()
                self.metrics.request_duration.labels(resource).observe(duration)
                self.metrics.response_bytes.labels(resource).inc(sent)
            if self.settings.access_log:
                path = scope.get("raw_path") or scope["path"].encode()
                query = scope.get("query_string", b"")
                if query:
                    path += b"?" + query
                log_event(
                    access_log, logging.INFO,
                    f'{scope["method"]} {path.decode("latin-1")} {status}',
                    method=scope["method"],
                    path=path.decode("latin-1"),
                    status=status,
                    duration_ms=round(duration * 1000, 2),
                    bytes=sent,
                    client_ip=client_ip,
                    user_agent=headers.get("user-agent"),
                    request_id=request_id,
                    resource=resource,
                    cache=cache,
                )

    def _resolve(self, scope: Scope, headers: dict[str, str]) -> tuple[str | None, str]:
        client = scope.get("client")
        peer = _normalize_ip(client[0]) if client else None
        scheme = scope.get("scheme", "http")
        host = headers.get("host")
        if not host:
            server = scope.get("server")
            host = f"{server[0]}:{server[1]}" if server else "localhost"
        prefix = ""
        client_ip = peer

        if self._trusted(peer):
            proto = _first(headers.get("x-forwarded-proto"))
            if proto in ("http", "https"):
                scheme = proto
            forwarded_host = _first(headers.get("x-forwarded-host"))
            if forwarded_host:
                host = forwarded_host
            forwarded_prefix = _first(headers.get("x-forwarded-prefix"))
            if forwarded_prefix:
                prefix = "/" + forwarded_prefix.strip("/") if forwarded_prefix.strip("/") else ""
            client_ip = self._client_ip(peer, headers)
        elif any(name in headers for name in _FORWARDED_HEADERS):
            self._warn_untrusted(peer)

        public_base = self.settings.public_url or f"{scheme}://{host}{prefix}"
        return client_ip, public_base

    def _client_ip(self, peer: str | None, headers: dict[str, str]) -> str | None:
        chain = [_normalize_ip(item) for item in headers.get("x-forwarded-for", "").split(",")
                 if item.strip()]
        if chain:
            # Proxies append the address they received the request from, so the client is
            # the right-most address that is not one of our trusted proxies. Anything to the
            # left of it was sent by the client and may be forged.
            for address in reversed(chain):
                if not self._trusted(address):
                    return address
            return chain[0]
        real_ip = headers.get("x-real-ip", "").strip()
        return _normalize_ip(real_ip) if real_ip else peer

    def _trusted(self, address: str | None) -> bool:
        networks = self.settings.trusted_networks
        if networks is None:
            return True
        if address is None:
            return False
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in network for network in networks)

    def _warn_untrusted(self, peer: str | None) -> None:
        key = peer or "unknown"
        if key in self._warned or len(self._warned) >= 100:
            return
        self._warned.add(key)
        log_event(
            app_log, logging.WARNING,
            f"X-Forwarded-* headers from {key} are ignored because it is not in "
            "PECL_PROXY_TRUSTED_PROXIES; add it there if it is your reverse proxy",
            "untrusted_proxy_headers", peer=key, trusted_proxies=self.settings.trusted_proxies,
        )


_FORWARDED_HEADERS = ("x-forwarded-for", "x-real-ip", "x-forwarded-proto", "x-forwarded-host",
                      "x-forwarded-prefix")


def _normalize_ip(value: str) -> str:
    """``::ffff:10.0.0.1`` -> ``10.0.0.1``; ``1.2.3.4:5678`` / ``[::1]:80`` -> address only."""
    value = value.strip()
    if value.startswith("[") and "]" in value:
        value = value[1:value.index("]")]
    elif value.count(":") == 1:
        value = value.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def _first(value: str | None) -> str | None:
    if not value:
        return None
    first = value.split(",")[0].strip()
    return first or None
