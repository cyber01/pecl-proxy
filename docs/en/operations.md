# Operations

**English** | [Русский](../ru/operations.md) · [Contents](README.md)

## What is cached and how

| Resource | Examples | Policy |
|---|---|---|
| Release files | `rest/r/redis/6.3.0.xml`, `package.6.3.0.xml`, `deps.6.3.0.txt`, `get/redis-6.3.0.tgz` | Kept forever. Heartbeat: every N-th request (`IMMUTABLE_RECHECK_EVERY`) triggers a background re-check against the upstream. |
| Mutable metadata | `channel.xml`, `rest/p/packages.xml`, `rest/p/<pkg>/info.xml`, `rest/r/<pkg>/allreleases.xml`, `stable.txt`, categories, maintainers, `feeds/*.rss` | Fresh for `METADATA_TTL` seconds, then a conditional request (`If-None-Match`/`If-Modified-Since`) to the upstream. |

- Only what clients request is cached. When an archive is downloaded the proxy fetches the
  metadata of that version in the background (`info.xml`, `allreleases.xml`, `<ver>.xml`,
  `deps`, `package.xml`) so the version is guaranteed to install offline.
- Bodies are stored exactly as the upstream sent them. When text resources are served,
  `https://pecl.php.net/{rest,get,feeds}/...` and `channel.xml` URLs are replaced with
  `PUBLIC_URL`. Links to pecl.php.net web pages in RSS feeds stay unchanged: the web site is
  not proxied.
- An archive is verified before it is stored (size, gzip integrity, readable tar). A broken or
  truncated upstream response never gets into the cache.
- A resource is downloaded once at a time; concurrent requests wait for that download.
- The proxy answers clients with `ETag`/`Last-Modified` and `304` for conditional requests.

The `X-Cache` response header tells what happened:

| `X-Cache` | Meaning |
|---|---|
| `HIT` | Served from the cache without contacting the upstream. |
| `MISS` | Downloaded from the upstream and stored. |
| `REVALIDATED` | The upstream confirmed the copy is current (304 or the same content). |
| `UPDATED` | The upstream returned new content, the cache was updated. |
| `STALE` | The upstream is unavailable (or the resource disappeared there); the last stored copy was served. |

### Heartbeat and `history/`

Release files on pecl.php.net can change: `<ver>.xml` contains the package description and is
regenerated when it is edited, and a release can be deleted and uploaded again. Every N-th
request of such a file triggers a background conditional request to the upstream (the client
does not wait for it). If the content changed:

- the cache is updated;
- the previous copy is kept in `DATA_DIR/history/<path>.<timestamp>` together with its
  metadata;
- a `WARNING` with `event: immutable_changed` is logged and the
  `pecl_proxy_immutable_changed_total` metric grows.

If the file disappeared upstream, the copy is kept (`event: immutable_gone`). Request counters
live in memory and start over after a restart. Disable the heartbeat with
`IMMUTABLE_RECHECK_EVERY=0`.

## Upstream outages

1. An upstream request is retried up to `UPSTREAM_RETRIES` times with a growing pause
   (`UPSTREAM_RETRY_DELAY`) on network errors, timeouts, 5xx and 429.
2. When all attempts fail, the upstream is considered unavailable for
   `UPSTREAM_DOWN_COOLDOWN` seconds (`event: upstream_down`). During that time the upstream is
   not contacted and responses come from the cache right away, without waiting for timeouts.
3. Whatever is cached is served as is (`X-Cache: STALE`) — the last upstream snapshot.
4. Anything not cached gets a `504` with an explanation.
5. An upstream 404 is remembered for `NEGATIVE_TTL` seconds.
6. When the upstream answers again: `event: upstream_up`.

`PECL_PROXY_OFFLINE=true` switches to cache-only mode: the upstream is never contacted. Useful
to check that the cache is enough for your builds, or for a copy of the cache in a closed
network.

## Cache warm-up

Fill the cache in advance, without a pecl client:

```sh
docker compose exec pecl-proxy pecl-proxy warm redis-6.3.0 apcu-5.1.28 pecl_http
# without Docker:
pecl-proxy warm redis-6.3.0
```

Specs are the same as for `pecl install`: `name` (newest stable), `name-1.2.3`, `name-beta`.
Required PECL dependencies are downloaded recursively, choosing versions from the constraints
in `deps.*.txt` the way PEAR does. `--no-deps` skips dependencies. The exit code is `1` if
anything failed; a detailed result is printed as JSON. The same is available through the admin
API.

## Storage

```
DATA_DIR/
  cache/<upstream path>             body exactly as sent by the upstream
  cache/<upstream path>.meta.json   ETag, Last-Modified, Content-Type, sha256, fetch and check times
  history/                          previous copies of changed release files
  tmp/                              unfinished downloads (cleaned at startup)
```

- Writes are atomic (temporary file + rename), so the directory can be copied while the
  service runs.
- **Backup:** `docker run --rm -v pecl-proxy_pecl-cache:/data -v "$PWD":/backup alpine tar czf /backup/pecl-cache.tgz -C /data .`
  (see `docker volume ls` for the volume name).
- **Moving into a closed network:** restore a copy of the directory as `DATA_DIR` of another
  instance and optionally set `PECL_PROXY_OFFLINE=true`.
- **Integrity check:** `pecl-proxy verify` compares the sha256 of every file with the stored
  one.
- **Removing a package:** `pecl-proxy purge redis` or `pecl-proxy purge redis 6.3.0`.
- **Forcing a metadata refresh:** `pecl-proxy refresh [package]`.

Run one service process per data directory: the locks that prevent parallel downloads of the
same file work within a process. CLI commands can run alongside the server.

## Running without Docker

Requires Python 3.12+.

```sh
python3 -m venv /opt/pecl-proxy/venv
/opt/pecl-proxy/venv/bin/pip install /path/to/pecl-proxy   # repository checkout or wheel
export PECL_PROXY_DATA_DIR=/var/lib/pecl-proxy
export PECL_PROXY_PUBLIC_URL=http://pecl-proxy.example.local
/opt/pecl-proxy/venv/bin/pecl-proxy serve
```

Variables are described in [configuration.md](configuration.md). A systemd unit (the service
binds port 80 itself without root):

```ini
# /etc/systemd/system/pecl-proxy.service
[Unit]
Description=PECL caching proxy
After=network-online.target
Wants=network-online.target

[Service]
User=pecl-proxy
EnvironmentFile=/etc/pecl-proxy.env
ExecStart=/opt/pecl-proxy/venv/bin/pecl-proxy serve
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```sh
# /etc/pecl-proxy.env
PECL_PROXY_PORT=80
PECL_PROXY_DATA_DIR=/var/lib/pecl-proxy
PECL_PROXY_PUBLIC_URL=http://pecl-proxy.example.local
```

Logs go to stdout (journald) or to a file (`PECL_PROXY_LOG_FILE`).

## Reverse proxy and HTTPS

The service speaks plain HTTP. For HTTPS put nginx or a load balancer in front of it
([`deploy/nginx.conf.example`](../../deploy/nginx.conf.example)) and:

- set `PECL_PROXY_PUBLIC_URL=https://pecl.example.local`;
- or let the service trust the proxy headers: `PECL_PROXY_TRUSTED_PROXIES=<proxy IP or
  network>`. The scheme, host and path prefix then come from `X-Forwarded-Proto`,
  `X-Forwarded-Host` and `X-Forwarded-Prefix`, and the client IP for logs from
  `X-Forwarded-For` or `X-Real-IP`.

### nginx on the host, service in Docker

Inside the container nginx's connection does not come from the server's external IP or from
`127.0.0.1` but from the Docker network gateway (`172.17.0.1`, `172.18.0.1`, …). If that
address is not in `TRUSTED_PROXIES`, the service does not trust `X-Forwarded-For` and logs the
gateway address instead of the user's IP (plus a single `untrusted_proxy_headers` warning
naming that address).

Setup:

```sh
# .env
PECL_PROXY_PUBLIC_URL=https://pecl.example.local
PECL_PROXY_TRUSTED_PROXIES=private      # or narrower: the Docker network, e.g. 172.18.0.0/16
```

```yaml
# docker-compose.yml: the port is reachable only by nginx on this host
    ports:
      - "127.0.0.1:8080:8080"
```

Publishing the port on `127.0.0.1` only matters: otherwise clients could reach the service
directly, bypassing nginx. The gateway address is in the log warning, or run
`docker network inspect <project>_default -f '{{range .IPAM.Config}}{{.Gateway}} {{.Subnet}}{{end}}'`.

nginx has to pass the client address: `proxy_set_header X-Forwarded-For
$proxy_add_x_forwarded_for;` or `proxy_set_header X-Real-IP $remote_addr;`. The client IP is
the right-most `X-Forwarded-For` address that is not in `TRUSTED_PROXIES`, so a header sent by
the client cannot forge it.

If the service is published under a path prefix (`https://example.local/pecl`), put it into
`PUBLIC_URL` or send `X-Forwarded-Prefix`. The proxy adds the prefix to links inside REST files
(`xlink:href="/rest/..."`) as well.

Keep port 80 answering (a redirect to https is fine): before every install pecl requests
`http://<preferred_mirror>/channel.xml` and follows redirects.

## Metrics

`GET /metrics` (or the separate `METRICS_PORT`); the prefix is configurable
(`METRICS_PREFIX`):

| Metric | Type | Labels |
|---|---|---|
| `pecl_proxy_requests_total` | counter | `resource` (resource type), `cache` (`X-Cache`, `UNAVAILABLE`, `BAD_UPSTREAM`, `NEGATIVE`), `status` |
| `pecl_proxy_request_duration_seconds` | histogram | `resource` |
| `pecl_proxy_response_bytes_total` | counter | `resource` |
| `pecl_proxy_upstream_requests_total` | counter | `result` (HTTP status, `error`, `too_large`) |
| `pecl_proxy_upstream_request_duration_seconds` | histogram | — |
| `pecl_proxy_upstream_available` | gauge | 1/0 |
| `pecl_proxy_heartbeat_checks_total` | counter | `result` (`unchanged`, `changed`, `gone`, `error`) |
| `pecl_proxy_immutable_changed_total` | counter | — |
| `pecl_proxy_cache_files`, `pecl_proxy_cache_bytes` | gauge | recomputed at most once a minute |

## Health check

`GET /healthz` → `200 {"status": "ok", "upstream": {...}}`. It answers 200 even when the
upstream is unavailable — the service keeps working from the cache — and the upstream state is
in the `upstream` field. Used by the image `HEALTHCHECK`.

## Building the image in a closed network

```sh
docker build \
  --build-arg PYTHON_IMAGE=registry.local/library/python:3.13-slim \
  --build-arg PIP_INDEX_URL=https://nexus.local/repository/pypi/simple \
  --secret id=ca,src=/etc/ssl/certs/corporate-ca.pem \
  -t pecl-proxy .
```

- `PYTHON_IMAGE` — base image from a registry mirror.
- `PIP_INDEX_URL` — PyPI mirror.
- the `ca` secret — certificate of a TLS-intercepting proxy. It is used only during the build
  and does not end up in the image.
