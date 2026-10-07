# Логи

[English](../en/logging.md) | **Русский** · [Оглавление](README.md)

Все логи — JSON, одна запись на строку. По умолчанию — stdout (`docker compose logs`,
journald). `PECL_PROXY_LOG_FILE=/path/pecl-proxy.log` пишет в файл; файл открывается заново
после ротации, так что подходит обычный logrotate.

Записи всех видов идут в общий поток и различаются полем `type`:

| `type` | Что пишется |
|---|---|
| `access` | Каждый HTTP-запрос (кроме случая `ACCESS_LOG=false`). |
| `admin` | Действия Admin API и CLI (`warm`, `purge`, `refresh`), отказы в доступе. |
| `app` | События сервиса: старт, обращения к upstream, сохранение в кеш, недоступность upstream, изменения в upstream, ошибки, сообщения uvicorn. |

Общие поля: `ts` (ISO-8601, UTC), `level`, `type`, `logger`, `msg`.

## access

```json
{"ts": "2026-10-06T14:45:43.021+00:00", "level": "INFO", "type": "access",
 "logger": "pecl_proxy.access", "msg": "GET /get/raphf-2.0.2.tgz 200",
 "method": "GET", "path": "/get/raphf-2.0.2.tgz", "status": 200, "duration_ms": 12.4,
 "bytes": 16262, "client_ip": "10.1.2.3", "user_agent": "PEAR/1.10.18/PHP/8.3.35",
 "request_id": "5a3d65033d5f4670", "resource": "archive", "cache": "MISS"}
```

- `resource` — тип ресурса канала (`channel`, `allreleases`, `release`, `deps`, `archive`, …),
  `null` для служебных путей.
- `cache` — как в заголовке `X-Cache` (`HIT`, `MISS`, `REVALIDATED`, `UPDATED`, `STALE`, `GENERATED`),
  а также `NEGATIVE` (недавний 404 upstream), `UNAVAILABLE` (upstream недоступен, в кеше нет)
  и `BAD_UPSTREAM` (битый или слишком большой ответ upstream).
- `client_ip` — с учётом `X-Forwarded-For`/`X-Real-IP` от доверенных прокси
  (`TRUSTED_PROXIES`); иначе — адрес, с которого пришло соединение (за nginx в Docker это
  шлюз Docker-сети, см. [operations.md](operations.md#nginx-на-хосте-сервис-в-docker)).
- `request_id` — из заголовка `X-Request-ID` или сгенерированный; возвращается в ответе.

## admin

```json
{"ts": "...", "level": "INFO", "type": "admin", "logger": "pecl_proxy.admin",
 "msg": "admin purge: ok", "action": "purge", "target": "redis-6.3.0", "result": "ok",
 "client_ip": "10.1.2.3", "request_id": "0fe2c67a93204d3c", "deleted": 4}
{"ts": "...", "level": "WARNING", "type": "admin", "logger": "pecl_proxy.admin",
 "msg": "admin status: denied", "action": "status", "target": null, "result": "denied",
 "client_ip": "10.9.9.9", "request_id": "...", "auth": "denied"}
```

Запрос к Admin API даёт две записи: `access` (сам HTTP-запрос) и `admin` (действие).

## app

Поле `event` описывает событие:

| `event` | Уровень | Когда |
|---|---|---|
| `startup` / `shutdown` | INFO | Запуск и остановка, с основными настройками. |
| `config_warning` | WARNING | Не задан `PUBLIC_URL` или он не на порту 80/443. |
| `untrusted_proxy_headers` | WARNING | Заголовки `X-Forwarded-*`/`X-Real-IP` пришли с адреса не из `TRUSTED_PROXIES` и проигнорированы (`peer` — этот адрес). Пишется один раз на адрес. |
| `cache_store` | INFO | Ресурс сохранён в кеш (`key`, `size`, `sha256`). |
| `upstream_retry` | INFO | Неудачная попытка, будет повтор (`attempt`, `error`, `delay`). |
| `upstream_down` / `upstream_up` | WARNING / INFO | Upstream стал недоступен / снова доступен. |
| `upstream_gone` | WARNING | Метаданные пропали в upstream, отдаётся кеш. |
| `upstream_bad_data` | WARNING | Upstream отдал некорректные данные, отдаётся кеш. |
| `immutable_changed` | WARNING | Heartbeat: файл версии изменился в upstream (`old_sha256`, `new_sha256`, `history`). |
| `immutable_gone` | WARNING | Heartbeat: файл версии пропал в upstream, копия сохранена. |
| `release_completion` | INFO | Не удалось докачать метаданные версии. |
| `background_error` | ERROR | Ошибка фоновой задачи (с `exc`). |

## Примеры фильтрации

```sh
docker compose logs -f --no-log-prefix pecl-proxy | jq -c 'select(.type == "admin")'
jq -c 'select(.type == "access" and .cache == "MISS") | {path, status}' pecl-proxy.log
jq -c 'select(.event == "immutable_changed")' pecl-proxy.log
```
