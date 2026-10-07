"""Admin API (enabled only when ``PECL_PROXY_ADMIN_TOKEN`` is set).

Every call is audited with a ``type: admin`` log record; the token itself is never logged.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import __version__
from ..cache import CacheService
from ..config import Settings
from ..logs import admin_log, log_event
from ..warm import Warmer


class RefreshRequest(BaseModel):
    package: str | None = None


class WarmRequest(BaseModel):
    packages: list[str] = Field(min_length=1, max_length=100)
    dependencies: bool = True


def audit(request: Request, action: str, result: str, target: str | None = None,
          level: int = logging.INFO, **fields) -> None:
    log_event(
        admin_log, level, f"admin {action}: {result}",
        action=action, target=target, result=result,
        client_ip=getattr(request.state, "client_ip", None),
        request_id=getattr(request.state, "request_id", None),
        **fields,
    )


def _action(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "name", None) or request.url.path


def build_router(settings: Settings) -> APIRouter:
    token = settings.admin_token.get_secret_value() if settings.admin_token else ""

    async def require_token(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            supplied.strip().encode(), token.encode()
        ):
            audit(request, _action(request), "denied", level=logging.WARNING, auth="denied")
            raise HTTPException(401, "invalid or missing admin token",
                                headers={"WWW-Authenticate": "Bearer"})

    router = APIRouter(prefix=settings.admin_path, dependencies=[Depends(require_token)])

    def service_of(request: Request) -> CacheService:
        return request.app.state.service

    @router.get("/status", name="status")
    async def status(request: Request) -> dict:
        service = service_of(request)
        files, size = service.store.stats()
        audit(request, "status", "ok")
        return {
            "version": __version__,
            "upstream": service.upstream.status(),
            "cache": {"files": files, "bytes": size,
                      "packages": len(service.store.packages())},
            "settings": {
                "public_url": settings.public_url,
                "metadata_ttl": settings.metadata_ttl,
                "immutable_recheck_every": settings.immutable_recheck_every,
                "negative_ttl": settings.negative_ttl,
                "upstream_retries": settings.upstream_retries,
                "upstream_down_cooldown": settings.upstream_down_cooldown,
            },
        }

    @router.get("/packages", name="list")
    async def list_packages(request: Request) -> dict:
        packages = service_of(request).store.packages()
        audit(request, "list", "ok", count=len(packages))
        return {"packages": [asdict(summary) for summary in packages.values()]}

    @router.get("/packages/{name}", name="show")
    async def show_package(request: Request, name: str) -> dict:
        service = service_of(request)
        summary = service.store.packages().get(name.lower())
        if summary is None:
            audit(request, "show", "not_found", name)
            raise HTTPException(404, f"package {name} is not cached")
        audit(request, "show", "ok", name)
        return {**asdict(summary), "keys": service.store.package_keys(name)}

    @router.delete("/packages/{name}", name="purge")
    async def purge_package(request: Request, name: str) -> dict:
        return _purge(request, name, None)

    @router.delete("/packages/{name}/{version}", name="purge")
    async def purge_version(request: Request, name: str, version: str) -> dict:
        return _purge(request, name, version)

    def _purge(request: Request, name: str, version: str | None) -> dict:
        keys = service_of(request).purge(name, version)
        target = f"{name}-{version}" if version else name
        audit(request, "purge", "ok" if keys else "not_found", target, deleted=len(keys))
        if not keys:
            raise HTTPException(404, f"{target} is not cached")
        return {"deleted": keys}

    @router.post("/refresh", name="refresh")
    async def refresh(request: Request, body: RefreshRequest | None = None) -> dict:
        package = body.package if body else None
        count = service_of(request).mark_stale(package)
        audit(request, "refresh", "ok", package or "*", marked=count)
        return {"marked_stale": count}

    @router.post("/warm", name="warm")
    async def warm(request: Request, body: WarmRequest) -> dict:
        warmer = Warmer(service_of(request))
        results = [await warmer.warm(spec, dependencies=body.dependencies)
                   for spec in body.packages]
        ok = all(result.all_ok for result in results)
        audit(request, "warm", "ok" if ok else "partial", ",".join(body.packages),
              level=logging.INFO if ok else logging.WARNING)
        return {"ok": ok, "results": [result.as_dict() for result in results]}

    return router
