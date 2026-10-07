# Admin API и CLI

[English](../en/admin-api.md) | **Русский** · [Оглавление](README.md)

## Admin API

Включается заданием `PECL_PROXY_ADMIN_TOKEN`; без токена пути Admin API отвечают 404.
Префикс — `PECL_PROXY_ADMIN_PATH` (по умолчанию `/_admin`). Каждый запрос требует заголовок
`Authorization: Bearer <токен>`, иначе `401`. Каждое действие и каждый отказ в доступе
пишутся в лог с `type: admin` ([logging.md](logging.md)); токен в лог не попадает.

Admin API лучше не публиковать наружу: ограничьте доступ на reverse proxy (пример в
[`deploy/nginx.conf.example`](../../deploy/nginx.conf.example)).

```sh
export TOKEN=...
export ADMIN=http://pecl-proxy.example.local/_admin
```

| Метод и путь | Действие |
|---|---|
| `GET /status` | Версия, состояние upstream, размер кеша, основные настройки. |
| `GET /packages` | Пакеты в кеше: версии с архивами, число файлов, размер. |
| `GET /packages/{name}` | Один пакет и список его файлов в кеше. |
| `DELETE /packages/{name}` | Удалить пакет из кеша целиком. |
| `DELETE /packages/{name}/{version}` | Удалить одну версию (архив и её REST-файлы). |
| `POST /refresh` | Пометить изменяемые метаданные устаревшими (`{"package": "redis"}` — только одного пакета). Следующий запрос перепроверит их в upstream. |
| `POST /warm` | Скачать пакеты в кеш: `{"packages": ["redis-6.3.0", "pecl_http"], "dependencies": true}`. |

Примеры:

```sh
curl -s -H "Authorization: Bearer $TOKEN" $ADMIN/status
curl -s -H "Authorization: Bearer $TOKEN" $ADMIN/packages
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" $ADMIN/packages/redis/6.3.0
curl -s -X POST -H "Authorization: Bearer $TOKEN" $ADMIN/refresh -d '{"package": "redis"}' \
     -H "Content-Type: application/json"
curl -s -X POST -H "Authorization: Bearer $TOKEN" $ADMIN/warm \
     -H "Content-Type: application/json" -d '{"packages": ["pecl_http-4.3.1"]}'
```

Ответ `warm`:

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

Команды работают с тем же каталогом данных и теми же переменными `PECL_PROXY_*`, что и сервер.
В Docker: `docker compose exec pecl-proxy pecl-proxy <команда>`.

| Команда | Действие |
|---|---|
| `pecl-proxy serve` | Запустить сервер (команда по умолчанию). |
| `pecl-proxy warm SPEC... [--no-deps]` | Скачать пакеты в кеш (`redis`, `redis-6.3.0`, `apcu-beta`). |
| `pecl-proxy list [--json]` | Пакеты в кеше. |
| `pecl-proxy purge NAME [VERSION]` | Удалить пакет или версию. |
| `pecl-proxy refresh [NAME]` | Перепроверить метаданные при следующем запросе. |
| `pecl-proxy verify` | Сверить sha256 всех файлов кеша; код выхода `1` при расхождениях. |

`warm`, `purge` и `refresh` пишут в лог записи `type: admin` с `client_ip: "cli"`.
