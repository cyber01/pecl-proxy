# Logging

**English** | [Русский](../ru/logging.md) · [Contents](README.md)

All logs are JSON, one record per line. By default they go to stdout (`docker compose logs`,
journald). `PECL_PROXY_LOG_FILE=/path/pecl-proxy.log` writes to a file instead; the file is
reopened after rotation, so plain logrotate works.

All records share one stream and are told apart by the `type` field:

| `type` | What is logged |
|---|---|
| `access` | Every HTTP request (unless `ACCESS_LOG=false`). |
| `admin` | Admin API and CLI actions (`warm`, `purge`, `refresh`) and rejected requests. |
| `app` | Service events: startup, upstream requests, cache writes, upstream outages, upstream changes, errors, uvicorn messages. |

Common fields: `ts` (ISO-8601, UTC), `level`, `type`, `logger`, `msg`.

## access

```json
{"ts": "2026-10-06T14:45:43.021+00:00", "level": "INFO", "type": "access",
 "logger": "pecl_proxy.access", "msg": "GET /get/raphf-2.0.2.tgz 200",
 "method": "GET", "path": "/get/raphf-2.0.2.tgz", "status": 200, "duration_ms": 12.4,
 "bytes": 16262, "client_ip": "10.1.2.3", "user_agent": "PEAR/1.10.18/PHP/8.3.35",
 "request_id": "5a3d65033d5f4670", "resource": "archive", "cache": "MISS"}
```

- `resource` — channel resource type (`channel`, `allreleases`, `release`, `deps`, `archive`,
  …), `null` for service paths.
- `cache` — as in the `X-Cache` header (`HIT`, `MISS`, `REVALIDATED`, `UPDATED`, `STALE`),
  plus `NEGATIVE` (recent upstream 404), `UNAVAILABLE` (upstream unavailable, not cached) and
  `BAD_UPSTREAM` (broken or too large upstream response).
- `client_ip` — taken from `X-Forwarded-For`/`X-Real-IP` sent by trusted proxies
  (`TRUSTED_PROXIES`); otherwise the address the connection came from (behind nginx in front
  of Docker that is the Docker network gateway, see
  [operations.md](operations.md#nginx-on-the-host-service-in-docker)).
- `request_id` — from the `X-Request-ID` header or generated; returned in the response.

## admin

```json
{"ts": "...", "level": "INFO", "type": "admin", "logger": "pecl_proxy.admin",
 "msg": "admin purge: ok", "action": "purge", "target": "redis-6.3.0", "result": "ok",
 "client_ip": "10.1.2.3", "request_id": "0fe2c67a93204d3c", "deleted": 4}
{"ts": "...", "level": "WARNING", "type": "admin", "logger": "pecl_proxy.admin",
 "msg": "admin status: denied", "action": "status", "target": null, "result": "denied",
 "client_ip": "10.9.9.9", "request_id": "...", "auth": "denied"}
```

An admin API request produces two records: `access` (the HTTP request) and `admin` (the
action).

## app

The `event` field names the event:

| `event` | Level | When |
|---|---|---|
| `startup` / `shutdown` | INFO | Start and stop, with the main settings. |
| `config_warning` | WARNING | `PUBLIC_URL` is not set or not on port 80/443. |
| `untrusted_proxy_headers` | WARNING | `X-Forwarded-*`/`X-Real-IP` headers came from an address outside `TRUSTED_PROXIES` and were ignored (`peer` is that address). Logged once per address. |
| `cache_store` | INFO | A resource was stored in the cache (`key`, `size`, `sha256`). |
| `upstream_retry` | INFO | An attempt failed and will be retried (`attempt`, `error`, `delay`). |
| `upstream_down` / `upstream_up` | WARNING / INFO | The upstream became unavailable / is reachable again. |
| `upstream_gone` | WARNING | Metadata disappeared upstream; the cached copy is served. |
| `upstream_bad_data` | WARNING | The upstream returned invalid data; the cached copy is served. |
| `immutable_changed` | WARNING | Heartbeat: a release file changed upstream (`old_sha256`, `new_sha256`, `history`). |
| `immutable_gone` | WARNING | Heartbeat: a release file disappeared upstream; the copy is kept. |
| `release_completion` | INFO | Could not fetch metadata of a downloaded version. |
| `background_error` | ERROR | A background task failed (with `exc`). |

## Filtering examples

```sh
docker compose logs -f --no-log-prefix pecl-proxy | jq -c 'select(.type == "admin")'
jq -c 'select(.type == "access" and .cache == "MISS") | {path, status}' pecl-proxy.log
jq -c 'select(.event == "immutable_changed")' pecl-proxy.log
```
