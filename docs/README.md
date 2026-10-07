# pecl-proxy documentation

**English** | [Русский](ru/README.md) · [Project overview](../README.md)

- [Client setup](en/client-setup.md) — connecting clients, Dockerfile usage, what works
  offline, which requests pecl commands make, PEAR client quirks.
- [Configuration](en/configuration.md) — every `PECL_PROXY_*` environment variable.
- [Operations](en/operations.md) — caching policy, heartbeat, upstream outages, cache warm-up,
  storage and backups, running without Docker, reverse proxy and client IPs, metrics,
  health check, building the image in a closed network.
- [Admin API and CLI](en/admin-api.md) — managing the cache over HTTP and from the command line.
- [Logging](en/logging.md) — JSON log format, `access`/`admin`/`app` records, events.
