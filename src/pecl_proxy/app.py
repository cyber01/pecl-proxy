"""Application factory."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Callable

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__
from .cache import CacheService
from .config import Settings
from .logs import app_log, log_event
from .metrics import Metrics
from .middleware import RequestContextMiddleware
from .routes import protocol
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
        log_event(app_log, logging.INFO, "pecl-proxy started", "startup",
                  version=__version__, upstream=settings.upstream_url,
                  data_dir=str(settings.data_dir), offline=settings.offline,
                  public_url=settings.public_url)
        try:
            yield
        finally:
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

    app.include_router(protocol.router)  # catch-all, must stay last
    return app
