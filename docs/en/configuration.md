# Configuration

**English** | [Русский](../ru/configuration.md) · [Contents](README.md)

All settings are environment variables prefixed with `PECL_PROXY_`. Every variable has a
default; an empty value is the same as an unset one. Booleans: `true`/`false` (also `1`/`0`,
`yes`/`no`). Durations are in seconds. Sizes are bytes or a number with a `KB`, `MB` or `GB`
suffix.

A template with every variable: [`.env.example`](../../.env.example).

## Network

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `0.0.0.0` | Address the HTTP server listens on. |
| `PORT` | `8080` | Port of the HTTP server. The service speaks plain HTTP only; HTTPS is terminated by an external reverse proxy. |
| `PUBLIC_URL` | empty | External address of the proxy, e.g. `https://pecl.mycorp.local`. Used for every URL in responses (`channel.xml`, archive links). When empty the address is taken from the request (`Host` + `X-Forwarded-*`). **Always set it**: the PEAR client sends `Host` without the port. |
| `TRUSTED_PROXIES` | `127.0.0.1` | Comma-separated IPs or networks of reverse proxies whose `X-Forwarded-Proto`, `X-Forwarded-Host`, `X-Forwarded-Prefix`, `X-Forwarded-For` and `X-Real-IP` headers are trusted. This is how the service learns the external scheme (https), host, path prefix and the real client IP. `private` means every loopback and private network (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, ::1, fc00::/7); `*` trusts everyone. From other addresses these headers are ignored and an `untrusted_proxy_headers` warning naming the address to add is logged once. **nginx on the host, service in Docker:** nginx connects to the container from the Docker network gateway (`172.17.0.1`, `172.18.0.1`, …), not from the server's external IP — use `private` or the Docker subnet. See [operations.md](operations.md#nginx-on-the-host-service-in-docker). |

## Upstream

| Variable | Default | Purpose |
|---|---|---|
| `UPSTREAM_URL` | `https://pecl.php.net` | The upstream channel. |
| `UPSTREAM_CONNECT_TIMEOUT` | `5` | Timeout for connecting to the upstream. |
| `UPSTREAM_READ_TIMEOUT` | `60` | Timeout for reading the upstream response. |
| `UPSTREAM_RETRIES` | `2` | How many times to retry an upstream request after a failed attempt: network error, timeout, 5xx or 429. A 404 is not retried. `0` disables retries. |
| `UPSTREAM_RETRY_DELAY` | `1` | Pause before the first retry; every next one is twice as long (1 → 2 → 4…). |
| `UPSTREAM_DOWN_COOLDOWN` | `30` | When all attempts fail, the upstream is considered unavailable for this many seconds: requests are served from the cache right away, without attempts or waiting for timeouts. |
| `OFFLINE` | `false` | Forced offline mode: never contact the upstream, serve only from the cache. |
| `MAX_DOWNLOAD_SIZE` | `200MB` | Maximum size of an upstream file; larger files fail with 502 and are not cached. |

An outgoing proxy for reaching pecl.php.net is configured with the standard `HTTPS_PROXY`,
`HTTP_PROXY` and `NO_PROXY` variables (without the prefix).

## Channel

| Variable | Default | Purpose |
|---|---|---|
| `CHANNEL_SUMMARY` | empty | Channel description (`<summary>` in `channel.xml`), shown by `pecl channel-info pecl.php.net`. Empty means the upstream description ("PHP Extension Community Library"). |

The channel name (`pecl.php.net`) and its alias (`pecl`) always come from the upstream and
cannot be changed: the PEAR client compares the channel in the `package.xml` of a downloaded
archive with the channel it installs from, and archives are served unmodified. Details:
[client-setup.md](client-setup.md#why-the-channel-is-called-peclphpnet).

## Cache

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `/data` | Cache directory. A volume in Docker. |
| `METADATA_TTL` | `300` | How long mutable metadata (release lists, `stable.txt`, `info.xml`, categories, `channel.xml`, RSS) is fresh without asking the upstream. After that a conditional request is made (usually answered with 304). Matches pecl.php.net's `max-age`. |
| `IMMUTABLE_RECHECK_EVERY` | `50` | Heartbeat for release files (`<ver>.xml`, `package.<ver>.xml`, `deps.<ver>.txt`, archives): every N-th request of a file triggers a background conditional request to the upstream. The client does not wait for it. If the content changed, the cache is updated and the previous copy is kept in `history/`. `0` disables it. |
| `NEGATIVE_TTL` | `60` | How many seconds an upstream 404 is remembered, so it is not requested again. |

## Landing page

| Variable | Default | Purpose |
|---|---|---|
| `INDEX_ENABLED` | `true` | Page `/` with cached packages, upstream state and client setup commands. `false` makes `/` answer 404. |
| `INDEX_LANGUAGE` | `en` | Language of the page: `en` or `ru`. |

## Metrics

| Variable | Default | Purpose |
|---|---|---|
| `METRICS_ENABLED` | `true` | Prometheus metrics. |
| `METRICS_PATH` | `/metrics` | Metrics path on the main port. |
| `METRICS_PORT` | empty | Separate port for metrics (on `HOST`). When set, metrics are not served on the main port. |
| `METRICS_PREFIX` | `pecl_proxy` | Prefix of metric names. |

## Admin API

| Variable | Default | Purpose |
|---|---|---|
| `ADMIN_TOKEN` | empty | Bearer token of the admin API. Empty disables the admin API (its paths answer 404). |
| `ADMIN_PATH` | `/_admin` | Path prefix of the admin API. |

## Logging

| Variable | Default | Purpose |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LOG_FILE` | empty | Empty logs to stdout; a path writes to that file (works with logrotate). |
| `ACCESS_LOG` | `true` | Write a `type: access` record for every HTTP request. |

Format: [logging.md](logging.md).
