"""Prometheus metrics."""

from __future__ import annotations

import time
from collections.abc import Callable

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily

_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


class _CacheSizeCollector:
    """Reports cache size, recomputed at most once per ``ttl`` seconds (it walks the disk)."""

    def __init__(self, prefix: str, stats: Callable[[], tuple[int, int]], ttl: float = 60):
        self._prefix = prefix
        self._stats = stats
        self._ttl = ttl
        self._cached: tuple[float, tuple[int, int]] | None = None

    def collect(self):
        now = time.monotonic()
        if self._cached is None or now - self._cached[0] > self._ttl:
            self._cached = (now, self._stats())
        files, size = self._cached[1]
        yield GaugeMetricFamily(f"{self._prefix}_cache_files", "Files in the cache", value=files)
        yield GaugeMetricFamily(f"{self._prefix}_cache_bytes", "Size of the cache", value=size)


class Metrics:
    def __init__(self, prefix: str = "pecl_proxy", registry: CollectorRegistry | None = None):
        self.prefix = prefix
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.requests = Counter(
            f"{prefix}_requests", "Client requests to channel resources",
            ["resource", "cache", "status"], registry=r,
        )
        self.request_duration = Histogram(
            f"{prefix}_request_duration_seconds", "Time to serve channel resources",
            ["resource"], buckets=_DURATION_BUCKETS, registry=r,
        )
        self.response_bytes = Counter(
            f"{prefix}_response_bytes", "Bytes served to clients", ["resource"], registry=r,
        )
        self.upstream_requests = Counter(
            f"{prefix}_upstream_requests", "Requests to the upstream channel",
            ["result"], registry=r,
        )
        self.upstream_duration = Histogram(
            f"{prefix}_upstream_request_duration_seconds", "Duration of upstream requests",
            buckets=_DURATION_BUCKETS, registry=r,
        )
        self.upstream_up = Gauge(
            f"{prefix}_upstream_available", "1 if the upstream is considered reachable",
            registry=r,
        )
        self.upstream_up.set(1)
        self.immutable_changed = Counter(
            f"{prefix}_immutable_changed", "Release files whose content changed upstream",
            registry=r,
        )
        self.heartbeats = Counter(
            f"{prefix}_heartbeat_checks", "Background re-checks of release files",
            ["result"], registry=r,
        )

    def watch_cache(self, stats: Callable[[], tuple[int, int]]) -> None:
        self.registry.register(_CacheSizeCollector(self.prefix, stats))
