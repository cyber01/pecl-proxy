# Admin API and CLI

**English** | [Русский](../ru/admin-api.md) · [Contents](README.md)

## Admin API

Enabled by setting `PECL_PROXY_ADMIN_TOKEN`; without a token the admin paths answer 404. The
prefix is `PECL_PROXY_ADMIN_PATH` (default `/_admin`). Every request needs an
`Authorization: Bearer <token>` header, otherwise it gets `401`. Every action and every
rejected request is logged with `type: admin` ([logging.md](logging.md)); the token itself
is never logged.

Do not expose the admin API publicly: restrict it on the reverse proxy (see
[`deploy/nginx.conf.example`](../../deploy/nginx.conf.example)).

```sh
export TOKEN=...
export ADMIN=http://pecl-proxy.example.local/_admin
```

| Method and path | Action |
|---|---|
| `GET /status` | Version, upstream state, cache size, main settings. |
| `GET /packages` | Cached packages: versions with archives, number of files, size. |
| `GET /packages/{name}` | One package and the list of its cached files. |
| `DELETE /packages/{name}` | Remove a package from the cache completely. |
| `DELETE /packages/{name}/{version}` | Remove one version (archive and its REST files). |
| `POST /refresh` | Mark mutable metadata as stale (`{"package": "redis"}` limits it to one package). The next request re-checks it against the upstream. |
| `POST /warm` | Download packages into the cache: `{"packages": ["redis-6.3.0", "pecl_http"], "dependencies": true}`. |

Examples:

```sh
curl -s -H "Authorization: Bearer $TOKEN" $ADMIN/status
curl -s -H "Authorization: Bearer $TOKEN" $ADMIN/packages
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" $ADMIN/packages/redis/6.3.0
curl -s -X POST -H "Authorization: Bearer $TOKEN" $ADMIN/refresh -d '{"package": "redis"}' \
     -H "Content-Type: application/json"
curl -s -X POST -H "Authorization: Bearer $TOKEN" $ADMIN/warm \
     -H "Content-Type: application/json" -d '{"packages": ["pecl_http-4.3.1"]}'
```

`warm` response:

```json
{
  "ok": true,
  "results": [
    {"spec": "pecl_http-4.3.1", "package": "pecl_http", "version": "4.3.1", "ok": true,
     "error": null, "files": ["rest/r/pecl_http/allreleases.xml", "..."],
     "dependencies": [{"spec": "raphf", "package": "raphf", "version": "2.0.2", "ok": true, "...": "..."}]}
  ]
}
```

## CLI

The commands work on the same data directory and read the same `PECL_PROXY_*` variables as the
server. In Docker: `docker compose exec pecl-proxy pecl-proxy <command>`.

| Command | Action |
|---|---|
| `pecl-proxy serve` | Run the server (default command). |
| `pecl-proxy warm SPEC... [--no-deps]` | Download packages into the cache (`redis`, `redis-6.3.0`, `apcu-beta`). |
| `pecl-proxy list [--json]` | Cached packages. |
| `pecl-proxy purge NAME [VERSION]` | Remove a package or one version. |
| `pecl-proxy refresh [NAME]` | Re-check metadata on the next request. |
| `pecl-proxy verify` | Compare the sha256 of every cached file; exit code `1` on mismatches. |

`warm`, `purge` and `refresh` write `type: admin` log records with `client_ip: "cli"`.
