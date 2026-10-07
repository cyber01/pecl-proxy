"""PEAR channel protocol: channel.xml, REST files, archives and feeds."""

from __future__ import annotations

import hashlib
from email.utils import formatdate, parsedate_to_datetime

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from ..cache import BadUpstreamData, CacheService, CacheStatus, Entry, NotFound, Unavailable
from ..resources import classify

router = APIRouter()


@router.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def channel_resource(request: Request, path: str) -> Response:
    raw_path = request.scope.get("raw_path") or request.url.path.encode()
    resource = classify(raw_path.decode("latin-1"))
    if resource is None:
        return PlainTextResponse("Not found\n", status_code=404)

    state = request.state
    state.resource = resource.kind.value
    service: CacheService = request.app.state.service
    try:
        entry = await service.get(resource)
    except NotFound as exc:
        state.cache = exc.cache.value
        status = exc.status if 400 <= exc.status < 500 else 502
        reason = ("upstream is unavailable and nothing is cached to list"
                  if exc.cache is CacheStatus.GENERATED else "not found upstream")
        return PlainTextResponse(f"{resource.key}: {reason}\n", status_code=status,
                                 headers={"x-cache": exc.cache.value})
    except Unavailable as exc:
        state.cache = "UNAVAILABLE"
        return PlainTextResponse(
            f"{resource.key}: upstream is unavailable and the resource is not cached ({exc})\n",
            status_code=504,
        )
    except BadUpstreamData as exc:
        state.cache = "BAD_UPSTREAM"
        return PlainTextResponse(f"{resource.key}: {exc}\n", status_code=502)

    state.cache = entry.cache.value
    if resource.is_archive:
        return _archive_response(request, service, entry)
    return await _text_response(request, service, entry)


def _archive_response(request: Request, service: CacheService, entry: Entry) -> Response:
    meta = entry.meta
    headers = {
        "content-type": meta.content_type,
        "etag": f'"{meta.sha256}"',
        "last-modified": meta.last_modified or formatdate(meta.fetched_at, usegmt=True),
        "content-disposition": f"attachment; filename={entry.resource.key.rsplit('/', 1)[-1]}",
        "x-cache": entry.cache.value,
    }
    if _not_modified(request, headers):
        return _304(headers)
    return FileResponse(service.store.path(entry.resource.key), headers=headers)


async def _text_response(request: Request, service: CacheService, entry: Entry) -> Response:
    meta = entry.meta
    body = await service.render(entry, request.state.public_base)
    headers = {
        "content-type": meta.content_type,
        "etag": f'"{hashlib.sha256(body).hexdigest()[:32]}"',
        "last-modified": meta.last_modified or formatdate(meta.fetched_at, usegmt=True),
        "x-cache": entry.cache.value,
    }
    if _not_modified(request, headers):
        return _304(headers)
    if request.method == "HEAD":
        headers["content-length"] = str(len(body))
        body = b""
    return Response(content=body, headers=headers)


def _not_modified(request: Request, headers: dict[str, str]) -> bool:
    if_none_match = request.headers.get("if-none-match")
    if if_none_match is not None:
        tags = {tag.strip().removeprefix("W/") for tag in if_none_match.split(",")}
        return "*" in tags or headers["etag"] in tags
    if_modified_since = request.headers.get("if-modified-since")
    if if_modified_since:
        try:
            return parsedate_to_datetime(headers["last-modified"]) <= parsedate_to_datetime(
                if_modified_since
            )
        except (TypeError, ValueError):
            return False
    return False


def _304(headers: dict[str, str]) -> Response:
    keep = {k: v for k, v in headers.items() if k in ("etag", "last-modified", "x-cache")}
    return Response(status_code=304, headers=keep)
