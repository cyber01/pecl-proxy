"""ASGI middleware: client/proxy detection, request id, access log, request metrics."""

from __future__ import annotations

import ipaddress
import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import Settings
from .logs import access_log, log_event
from .metrics import Metrics

_REQUEST_ID = re.compile(r"[A-Za-z0-9._\-]{1,64}")


class RequestContextMiddleware:
    """Works out how the client sees the service and logs every request.

    Behind a reverse proxy listed in ``TRUSTED_PROXIES`` the ``X-Forwarded-Proto``,
    ``X-Forwarded-Host``, ``X-Forwarded-Prefix`` and ``X-Forwarded-For`` headers are honoured,
    so URLs in responses use the external scheme/host and logs show the real client IP.
    """

    def __init__(self, app: ASGIApp, settings: Settings, metrics: Metrics):
        self.app = app
        self.settings = settings
        self.metrics = metrics

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
        peer = client[0] if client else None
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
            forwarded_for = _first(headers.get("x-forwarded-for"))
            if forwarded_for:
                client_ip = forwarded_for
            forwarded_prefix = _first(headers.get("x-forwarded-prefix"))
            if forwarded_prefix:
                prefix = "/" + forwarded_prefix.strip("/") if forwarded_prefix.strip("/") else ""

        public_base = self.settings.public_url or f"{scheme}://{host}{prefix}"
        return client_ip, public_base

    def _trusted(self, peer: str | None) -> bool:
        networks = self.settings.trusted_networks
        if networks is None:
            return True
        if peer is None:
            return False
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        return any(address in network for network in networks)


def _first(value: str | None) -> str | None:
    if not value:
        return None
    first = value.split(",")[0].strip()
    return first or None
