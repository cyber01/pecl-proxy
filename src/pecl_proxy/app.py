"""Application factory."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Callable

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_client import start_http_server as start_metrics_server

from . import __version__
from .cache import CacheService
from .config import Settings
from .logs import app_log, log_event
from .metrics import Metrics
from .middleware import RequestContextMiddleware
from .rewrite import mirror_host
from .routes import admin, index, protocol
from .storage import CacheStore
from .upstream import UpstreamClient


def build_service(
    settings: Settings,
    metrics: Metrics | None = None,
    *,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] | None = None,
) -> CacheService:
    """Cache service without the web app (used by the CLI as well)."""
    metrics = metrics or Metrics(settings.metrics_prefix)
    store = CacheStore(settings.data_dir)
    extra = {"clock": clock} if clock else {}
    upstream = UpstreamClient(settings, metrics, transport=upstream_transport, **extra)
    return CacheService(settings, store, upstream, metrics, **extra)


def create_app(
    settings: Settings | None = None,
    *,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    metrics = Metrics(settings.metrics_prefix)
    service = build_service(settings, metrics, upstream_transport=upstream_transport, clock=clock)
    metrics.watch_cache(service.store.stats)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service.store.cleanup_tmp()
        metrics_server = None
        if settings.metrics_enabled and settings.metrics_port:
            metrics_server, _ = start_metrics_server(
                settings.metrics_port, addr=settings.host, registry=metrics.registry
            )
        log_event(app_log, logging.INFO, "pecl-proxy started", "startup",
                  version=__version__, upstream=settings.upstream_url,
                  data_dir=str(settings.data_dir), offline=settings.offline,
                  public_url=settings.public_url, admin_enabled=settings.admin_enabled,
                  index_enabled=settings.index_enabled, index_language=settings.index_language,
                  metrics=_metrics_location(settings))
        _warn_about_public_url(settings)
        try:
            yield
        finally:
            if metrics_server is not None:
                metrics_server.shutdown()
                metrics_server.server_close()
            await service.close()
            await service.upstream.aclose()
            log_event(app_log, logging.INFO, "pecl-proxy stopped", "shutdown")

    app = FastAPI(title="pecl-proxy", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.metrics = metrics
    app.state.service = service
    app.add_middleware(RequestContextMiddleware, settings=settings, metrics=metrics)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__,
                             "upstream": service.upstream.status()})

    if settings.metrics_enabled and not settings.metrics_port:
        @app.get(settings.metrics_path, include_in_schema=False)
        async def prometheus_metrics() -> Response:
            return Response(generate_latest(metrics.registry),
                            headers={"content-type": CONTENT_TYPE_LATEST})

    if settings.admin_enabled:
        app.include_router(admin.build_router(settings))
    if settings.index_enabled:
        app.include_router(index.router)
    app.include_router(protocol.router)  # catch-all, must stay last
    return app


def _warn_about_public_url(settings: Settings) -> None:
    if settings.public_url is None:
        log_event(app_log, logging.WARNING,
                  "PUBLIC_URL is not set: URLs in responses are built from the Host header, "
                  "which PEAR clients send without the port; set PUBLIC_URL unless clients "
                  "reach the service on port 80/443", "config_warning", setting="PUBLIC_URL")
    elif mirror_host(settings.public_url) is None:
        log_event(app_log, logging.WARNING,
                  "PUBLIC_URL is not on port 80/443: the proxy cannot be offered as a channel "
                  "mirror, so `pecl install` checks pecl.php.net/channel.xml before every "
                  "install and fails when it is unreachable; publish the service on 80/443",
                  "config_warning", setting="PUBLIC_URL", public_url=settings.public_url)


def _metrics_location(settings: Settings) -> str | None:
    if not settings.metrics_enabled:
        return None
    if settings.metrics_port:
        return f"{settings.host}:{settings.metrics_port}"
    return settings.metrics_path
