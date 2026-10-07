"""HTTP client for the upstream channel: conditional requests, retries, circuit breaker."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import __version__
from .config import Settings
from .logs import app_log, log_event
from .metrics import Metrics


class UpstreamUnavailable(Exception):
    """Upstream could not be reached (offline mode, cooldown, or all attempts failed)."""


class UpstreamTooLarge(Exception):
    """The upstream file exceeds ``MAX_DOWNLOAD_SIZE``."""


@dataclass
class UpstreamResponse:
    status: int
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    body: bytes | None = None  # in-memory download
    file: Path | None = None  # streamed download
    size: int = 0
    sha256: str | None = None


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


class UpstreamClient:
    def __init__(
        self,
        settings: Settings,
        metrics: Metrics,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.settings = settings
        self.metrics = metrics
        self._sleep = sleep
        self._clock = clock
        self._down_until = 0.0
        self._down = False
        self._last_error: str | None = None
        timeout = httpx.Timeout(
            connect=settings.upstream_connect_timeout,
            read=settings.upstream_read_timeout,
            write=settings.upstream_read_timeout,
            pool=settings.upstream_connect_timeout,
        )
        self.client = httpx.AsyncClient(
            base_url=settings.upstream_url + "/",
            timeout=timeout,
            follow_redirects=True,
            # bodies are cached byte-for-byte, so ask for them without transfer compression
            headers={"User-Agent": f"pecl-proxy/{__version__}", "Accept-Encoding": "identity"},
            transport=transport,
            trust_env=transport is None,  # honour HTTPS_PROXY / NO_PROXY in production
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    # -- state -----------------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return not self.settings.offline and self._clock() >= self._down_until

    def status(self) -> dict:
        return {
            "url": self.settings.upstream_url,
            "offline_mode": self.settings.offline,
            "available": self.available,
            "down": self._down,
            "retry_in_seconds": max(0.0, round(self._down_until - self._clock(), 1)),
            "last_error": self._last_error,
        }

    def _mark_down(self, error: str) -> None:
        self._down_until = self._clock() + self.settings.upstream_down_cooldown
        self._last_error = error
        self.metrics.upstream_up.set(0)
        if not self._down:
            self._down = True
            log_event(app_log, logging.WARNING, "upstream is unavailable", "upstream_down",
                      upstream=self.settings.upstream_url, error=error,
                      cooldown=self.settings.upstream_down_cooldown)

    def _mark_up(self) -> None:
        self._down_until = 0.0
        self.metrics.upstream_up.set(1)
        if self._down:
            self._down = False
            log_event(app_log, logging.INFO, "upstream is reachable again", "upstream_up",
                      upstream=self.settings.upstream_url)

    # -- requests --------------------------------------------------------------------------

    async def fetch(
        self,
        key: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        to_file: Path | None = None,
    ) -> UpstreamResponse:
        """GET ``key`` from upstream.

        Returns 200 (body in memory or in ``to_file``), 304 for a matching conditional
        request, or any other non-retryable status (404, 403, ...) with an empty body.
        Raises :class:`UpstreamUnavailable` when the upstream cannot be used.
        """
        if self.settings.offline:
            raise UpstreamUnavailable("offline mode is enabled")
        if not self.available:
            raise UpstreamUnavailable(f"upstream is down: {self._last_error}")

        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        attempts = 1 + self.settings.upstream_retries
        error = "unknown error"
        for attempt in range(1, attempts + 1):
            started = self._clock()
            try:
                response = await self._request(key, headers, to_file)
            except UpstreamTooLarge:
                self.metrics.upstream_requests.labels("too_large").inc()
                raise
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}".rstrip(": ")
                self.metrics.upstream_requests.labels("error").inc()
            else:
                self.metrics.upstream_duration.observe(self._clock() - started)
                if not _retryable(response.status):
                    self.metrics.upstream_requests.labels(str(response.status)).inc()
                    log_event(app_log, logging.DEBUG, "upstream response", "upstream_fetch",
                              key=key, status=response.status, attempt=attempt)
                    self._mark_up()
                    return response
                error = f"HTTP {response.status}"
                self.metrics.upstream_requests.labels(str(response.status)).inc()

            if attempt < attempts:
                delay = self.settings.upstream_retry_delay * 2 ** (attempt - 1)
                log_event(app_log, logging.INFO, "upstream request failed, retrying",
                          "upstream_retry", key=key, attempt=attempt, error=error, delay=delay)
                await self._sleep(delay)

        self._mark_down(error)
        raise UpstreamUnavailable(error)

    async def _request(
        self, key: str, headers: dict[str, str], to_file: Path | None
    ) -> UpstreamResponse:
        async with self.client.stream("GET", key, headers=headers) as response:
            result = UpstreamResponse(
                status=response.status_code,
                content_type=response.headers.get("content-type"),
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
            if response.status_code != 200:
                return result

            declared = response.headers.get("content-length")
            limit = self.settings.max_download_size
            if declared and declared.isdigit() and int(declared) > limit:
                raise UpstreamTooLarge(f"{key}: {declared} bytes > {limit}")

            # Content-Length describes the encoded body; it can only be checked without decoding
            encoded = response.headers.get("content-encoding", "identity").lower() != "identity"
            stream = response.aiter_bytes() if encoded else response.aiter_raw()
            digest = hashlib.sha256()
            size = 0
            chunks: list[bytes] = []
            sink = to_file.open("wb") if to_file is not None else None
            try:
                async for chunk in stream:
                    size += len(chunk)
                    if size > limit:
                        raise UpstreamTooLarge(f"{key}: more than {limit} bytes")
                    digest.update(chunk)
                    if sink is not None:
                        sink.write(chunk)
                    else:
                        chunks.append(chunk)
            finally:
                if sink is not None:
                    sink.close()

            if not encoded and declared and declared.isdigit() and int(declared) != size:
                raise httpx.RemoteProtocolError(
                    f"incomplete body: got {size} of {declared} bytes"
                )
            result.size = size
            result.sha256 = digest.hexdigest()
            if to_file is None:
                result.body = b"".join(chunks)
            else:
                result.file = to_file
            return result
