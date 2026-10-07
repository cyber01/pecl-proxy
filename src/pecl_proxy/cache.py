"""Cache logic: decides when to serve from disk and when to ask the upstream."""

from __future__ import annotations

import asyncio
import gzip
import logging
import tarfile
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config import Settings
from .logs import app_log, log_event
from .metrics import Metrics
from .resources import Kind, Resource, classify, release_metadata
from .rewrite import Rewriter, build_channel_xml, channel_name
from .storage import CacheStore, Meta
from .upstream import UpstreamClient, UpstreamResponse, UpstreamTooLarge, UpstreamUnavailable


class CacheStatus(StrEnum):
    HIT = "HIT"  # served from cache without asking upstream
    MISS = "MISS"  # fetched from upstream and stored
    REVALIDATED = "REVALIDATED"  # upstream confirmed the cached copy (304 / same content)
    UPDATED = "UPDATED"  # upstream had newer content, cache replaced
    STALE = "STALE"  # upstream unusable, served the last cached copy
    NEGATIVE = "NEGATIVE"  # upstream recently answered 404


class NotFound(Exception):
    def __init__(self, status: int = 404, cache: CacheStatus = CacheStatus.MISS):
        super().__init__(f"upstream answered {status}")
        self.status = status
        self.cache = cache


class Unavailable(Exception):
    """Not cached and the upstream cannot be reached."""


class BadUpstreamData(Exception):
    """Upstream returned something that must not be cached (corrupt or too large)."""


@dataclass
class Entry:
    resource: Resource
    meta: Meta
    cache: CacheStatus


class CacheService:
    def __init__(
        self,
        settings: Settings,
        store: CacheStore,
        upstream: UpstreamClient,
        metrics: Metrics,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self.store = store
        self.upstream = upstream
        self.metrics = metrics
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._negative: dict[str, tuple[float, int]] = {}
        self._hits: dict[str, int] = {}
        self._tasks: set[asyncio.Task] = set()
        self._channel_name: str | None = None
        self._rewriters: dict[frozenset[str], Rewriter] = {}

    # -- public API ------------------------------------------------------------------------

    async def get(self, resource: Resource) -> Entry:
        """Return a cache entry for ``resource``, fetching or revalidating as needed.

        Raises :class:`NotFound`, :class:`Unavailable` or :class:`BadUpstreamData`.
        """
        return await self._get(resource, count_hit=True)

    async def render(self, entry: Entry, public_base: str) -> bytes:
        """Body of a text resource as served to clients (URLs point to the proxy)."""
        body = self.store.read_bytes(entry.resource.key)
        if entry.resource.kind is Kind.CHANNEL:
            body = build_channel_xml(body, public_base, self.settings.channel_summary)
        if entry.resource.rewrite:
            body = (await self.rewriter()).rewrite(body, public_base)
        return body

    async def rewriter(self) -> Rewriter:
        hosts = {self.settings.upstream_host}
        name = await self.upstream_channel_name()
        if name:
            hosts.add(name)
        key = frozenset(hosts)
        if key not in self._rewriters:
            self._rewriters[key] = Rewriter(hosts)
        return self._rewriters[key]

    async def upstream_channel_name(self) -> str | None:
        """Channel name of the upstream (``pecl.php.net``); download URLs use it as host."""
        if self._channel_name is None:
            resource = classify("channel.xml")
            assert resource is not None
            try:
                await self._get(resource, count_hit=False)
                self._channel_name = channel_name(self.store.read_bytes(resource.key))
            except (NotFound, Unavailable, BadUpstreamData):
                return None
        return self._channel_name

    def mark_stale(self, package: str | None = None) -> int:
        """Force revalidation of mutable metadata (all of it, or of one package)."""
        keys = self.store.package_keys(package) if package else self.store.keys()
        count = 0
        for key in keys:
            resource = classify(key)
            if resource is not None and not resource.versioned:
                self.store.update_meta(key, validated_at=0.0)
                count += 1
        self._negative.clear()
        return count

    def forget(self, keys: list[str]) -> None:
        for key in keys:
            self._negative.pop(key, None)
            self._hits.pop(key, None)

    async def drain(self) -> None:
        """Wait for background tasks (heartbeats, release completion)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # -- core ------------------------------------------------------------------------------

    async def _get(self, resource: Resource, *, count_hit: bool) -> Entry:
        meta = self.store.read_meta(resource.key)
        if meta is not None:
            if resource.versioned:
                if count_hit:
                    self._count_hit(resource)
                return Entry(resource, meta, CacheStatus.HIT)
            if self._fresh(meta):
                return Entry(resource, meta, CacheStatus.HIT)
            return await self._revalidate(resource)
        self._check_negative(resource)
        return await self._fetch_new(resource)

    def _fresh(self, meta: Meta) -> bool:
        return self._clock() - meta.validated_at < self.settings.metadata_ttl

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def _check_negative(self, resource: Resource) -> None:
        negative = self._negative.get(resource.key)
        if negative is None:
            return
        expires, status = negative
        if self._clock() < expires:
            raise NotFound(status, CacheStatus.NEGATIVE)
        del self._negative[resource.key]

    async def _fetch_new(self, resource: Resource) -> Entry:
        async with self._lock(resource.key):
            # another request may have fetched it while we were waiting
            meta = self.store.read_meta(resource.key)
            if meta is not None:
                return Entry(resource, meta, CacheStatus.HIT)
            self._check_negative(resource)
            try:
                response = await self._download(resource)
            except UpstreamUnavailable as exc:
                raise Unavailable(str(exc)) from exc
            if response.status != 200:
                self._negative[resource.key] = (
                    self._clock() + self.settings.negative_ttl, response.status
                )
                raise NotFound(response.status)
            meta = self._save(resource, response)

        if resource.is_archive and resource.package and resource.version:
            self._spawn(self._complete_release(resource.package, resource.version))
        return Entry(resource, meta, CacheStatus.MISS)

    async def _revalidate(self, resource: Resource) -> Entry:
        async with self._lock(resource.key):
            meta = self.store.read_meta(resource.key)
            if meta is not None:
                return await self._revalidate_locked(resource, meta)
        # purged while we were waiting for the lock
        return await self._fetch_new(resource)

    async def _revalidate_locked(self, resource: Resource, meta: Meta) -> Entry:
        if self._fresh(meta):
            return Entry(resource, meta, CacheStatus.HIT)
        if not self.upstream.available:
            return Entry(resource, meta, CacheStatus.STALE)
        try:
            response = await self._download(resource, meta)
        except UpstreamUnavailable:
            return Entry(resource, meta, CacheStatus.STALE)
        except BadUpstreamData as exc:
            log_event(app_log, logging.WARNING, "bad upstream data, serving cached copy",
                      "upstream_bad_data", key=resource.key, error=str(exc))
            return Entry(resource, meta, CacheStatus.STALE)

        now = self._clock()
        if response.status == 304 or (
            response.status == 200 and response.sha256 == meta.sha256
        ):
            self._discard(response)
            meta = self.store.update_meta(
                resource.key, validated_at=now,
                etag=response.etag or meta.etag,
                last_modified=response.last_modified or meta.last_modified,
            ) or meta
            return Entry(resource, meta, CacheStatus.REVALIDATED)
        if response.status == 200:
            return Entry(resource, self._save(resource, response), CacheStatus.UPDATED)

        # The resource disappeared upstream. Keep serving what we have: the point of
        # the proxy is that builds keep working.
        log_event(app_log, logging.WARNING, "resource is gone upstream, serving cached copy",
                  "upstream_gone", key=resource.key, status=response.status)
        meta = self.store.update_meta(resource.key, validated_at=now) or meta
        return Entry(resource, meta, CacheStatus.STALE)

    # -- heartbeat & release completion ----------------------------------------------------

    def _count_hit(self, resource: Resource) -> None:
        every = self.settings.immutable_recheck_every
        if every <= 0:
            return
        count = self._hits.get(resource.key, 0) + 1
        self._hits[resource.key] = count
        if count % every == 0 and self.upstream.available:
            self._spawn(self._heartbeat(resource))

    async def _heartbeat(self, resource: Resource) -> None:
        async with self._lock(resource.key):
            meta = self.store.read_meta(resource.key)
            if meta is None or not self.upstream.available:
                return
            try:
                response = await self._download(resource, meta)
            except (UpstreamUnavailable, BadUpstreamData):
                self.metrics.heartbeats.labels("error").inc()
                return
            now = self._clock()
            if response.status == 304 or (
                response.status == 200 and response.sha256 == meta.sha256
            ):
                self._discard(response)
                self.store.update_meta(resource.key, validated_at=now,
                                       etag=response.etag or meta.etag,
                                       last_modified=response.last_modified or meta.last_modified)
                self.metrics.heartbeats.labels("unchanged").inc()
            elif response.status == 200:
                history = self.store.save_history(resource.key)
                new = self._save(resource, response)
                self.metrics.heartbeats.labels("changed").inc()
                self.metrics.immutable_changed.inc()
                log_event(app_log, logging.WARNING, "release file changed upstream",
                          "immutable_changed", key=resource.key, old_sha256=meta.sha256,
                          new_sha256=new.sha256, history=str(history) if history else None)
            else:
                self.metrics.heartbeats.labels("gone").inc()
                log_event(app_log, logging.WARNING, "release file is gone upstream, keeping it",
                          "immutable_gone", key=resource.key, status=response.status)

    async def _complete_release(self, package: str, version: str) -> None:
        """Make sure everything needed to install ``package-version`` offline is cached."""
        for resource in release_metadata(package, version):
            try:
                await self._get(resource, count_hit=False)
            except (NotFound, Unavailable, BadUpstreamData) as exc:
                log_event(app_log, logging.INFO, "could not cache release metadata",
                          "release_completion", key=resource.key, error=str(exc))

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            app_log.error("background task failed", exc_info=task.exception(),
                          extra={"fields": {"event": "background_error"}})

    # -- upstream I/O ----------------------------------------------------------------------

    async def _download(self, resource: Resource, meta: Meta | None = None) -> UpstreamResponse:
        etag = meta.etag if meta else None
        last_modified = meta.last_modified if meta else None
        tmp = self.store.tmp_file() if resource.is_archive else None
        try:
            response = await self.upstream.fetch(
                resource.key, etag=etag, last_modified=last_modified, to_file=tmp
            )
            if response.status == 200 and response.file is not None:
                await asyncio.to_thread(_verify_archive, response.file, resource.key)
            elif tmp is not None:
                tmp.unlink(missing_ok=True)
            return response
        except UpstreamTooLarge as exc:
            raise BadUpstreamData(str(exc)) from exc
        except BaseException:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            raise

    def _save(self, resource: Resource, response: UpstreamResponse) -> Meta:
        now = self._clock()
        meta = Meta(
            content_type=response.content_type or resource.content_type,
            size=response.size,
            sha256=response.sha256 or "",
            etag=response.etag,
            last_modified=response.last_modified,
            fetched_at=now,
            validated_at=now,
        )
        if response.file is not None:
            self.store.put_file(resource.key, response.file, meta)
        else:
            self.store.put_bytes(resource.key, response.body or b"", meta)
        log_event(app_log, logging.INFO, "stored upstream resource", "cache_store",
                  key=resource.key, size=meta.size, sha256=meta.sha256)
        return meta

    @staticmethod
    def _discard(response: UpstreamResponse) -> None:
        if response.file is not None:
            response.file.unlink(missing_ok=True)


def _verify_archive(path: Path, key: str) -> None:
    """Reject truncated or corrupt archives before they reach the cache."""
    try:
        if key.endswith(".tgz"):
            with gzip.open(path, "rb") as fh:  # reading to EOF checks the gzip CRC
                while fh.read(1024 * 1024):
                    pass
        with tarfile.open(path, "r:*") as tar:
            members = tar.getmembers()
    except (OSError, EOFError, tarfile.TarError) as exc:
        path.unlink(missing_ok=True)
        raise BadUpstreamData(f"{key}: not a valid archive ({exc})") from exc
    if not members:
        path.unlink(missing_ok=True)
        raise BadUpstreamData(f"{key}: empty archive")
