# pecl-proxy

**English** | [Русский](README_ru.md)

> [!IMPORTANT]
> This service was written for my own specific needs. The code is partially AI-generated.

A caching proxy for the **pecl.php.net** PEAR channel. `pecl`/`pear` clients talk to it
instead of pecl.php.net. Whatever has been requested before is served from the proxy's cache;
everything else is fetched from pecl.php.net, stored and served. The goal is to keep builds
(Dockerfiles, CI) working when the external network is unreliable or gone.

```
pecl install redis ──► pecl-proxy ──(cache misses only)──► pecl.php.net
                           │
                           └── on-disk cache: archives + metadata
```

## How it works

- A client points the pecl.php.net channel at the proxy once (`channel-update` +
  `preferred_mirror`). The channel is still called `pecl.php.net`, so `pecl install redis`
  and existing Dockerfiles stay unchanged.
- The proxy implements the PEAR channel REST protocol as pecl.php.net provides it
  (REST 1.0/1.1): `channel.xml`, `rest/p|r|c|m/...`, archives `get/...`, RSS `feeds/...`.
  Everything else is a 404 — the service is not an open proxy.
- Only what clients request is cached, plus the metadata needed to install a downloaded
  version. pecl.php.net is not mirrored as a whole.
- Archives are served **byte-for-byte** as on pecl.php.net. In metadata only the pecl.php.net
  addresses are replaced with the proxy address.
- Mutable metadata (release lists, `stable.txt` and so on) is refreshed after a TTL with
  conditional requests (ETag/Last-Modified). Release files are kept forever and occasionally
  re-checked in the background (heartbeat).
- When pecl.php.net is unavailable, the last stored snapshot is served; anything not cached
  gets a 504.

## Quick start (Docker)

```sh
cp .env.example .env            # set PECL_PROXY_PUBLIC_URL — the address clients use
docker compose up -d --build
curl http://pecl-proxy.example.local/healthz
```

The service speaks plain HTTP only (port 8080 in the container, 80 outside). HTTPS is handled
by an external reverse proxy, see [`deploy/nginx.conf.example`](deploy/nginx.conf.example).
Running without Docker: [docs/en/operations.md](docs/en/operations.md#running-without-docker).

## Connecting a client

```sh
pecl channel-update http://pecl-proxy.example.local/channel.xml
pecl config-set preferred_mirror pecl-proxy.example.local
pecl install redis
```

In a Dockerfile:

```dockerfile
FROM php:8.3-cli
RUN pecl channel-update http://pecl-proxy.example.local/channel.xml \
 && pecl config-set preferred_mirror pecl-proxy.example.local \
 && pecl install redis-6.3.0 \
 && docker-php-ext-enable redis
```

Three rules without which offline installs break (details in
[docs/en/client-setup.md](docs/en/client-setup.md)):

1. **Set `PECL_PROXY_PUBLIC_URL`.** The PEAR client sends the `Host` header without the port.
2. **Publish the service on port 80/443 and run `config-set preferred_mirror`.** Before every
   install pecl checks `http://<preferred_mirror>/channel.xml` (pecl.php.net by default) and
   aborts if it is unreachable; the port is ignored in that check.
3. **Do not run `pecl update-channels` while the internet is reachable** — it points the
   channel back to pecl.php.net (run the two connection commands again afterwards).

## Documentation

Contents: [docs/en](docs/en/README.md) (Русский: [docs/ru](docs/ru/README.md)).

- [Client setup](docs/en/client-setup.md) — connecting clients, what works offline, which
  requests pecl commands make (`install`, `remote-info`, `remote-list`, …), PEAR client quirks
- [Configuration](docs/en/configuration.md) — every environment variable
- [Operations](docs/en/operations.md) — cache, offline mode, warm-up, backups, running without
  Docker, reverse proxy and client IPs, metrics, building the image in a closed network
- [Admin API and CLI](docs/en/admin-api.md) — managing the cache
- [Logging](docs/en/logging.md) — JSON log format

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                    # unit and integration tests (fake pecl.php.net)
.venv/bin/ruff check src tests

# end-to-end with the real PEAR client (needs php and permission to listen on port 80)
tests/e2e/setup_pear.sh
.venv/bin/pytest -m e2e

# check a running proxy against the real pecl.php.net
scripts/smoke.sh http://pecl-proxy.example.local raphf-2.0.2
```

The tests use recorded pecl.php.net responses (`tests/fixtures/upstream`).
