# Эксплуатация

## Что и как кешируется

| Ресурс | Примеры | Политика |
|---|---|---|
| Файлы конкретной версии | `rest/r/redis/6.3.0.xml`, `package.6.3.0.xml`, `deps.6.3.0.txt`, `get/redis-6.3.0.tgz` | Хранятся бессрочно. Heartbeat: на каждый N-й запрос (`IMMUTABLE_RECHECK_EVERY`) фоновая перепроверка в upstream. |
| Изменяемые метаданные | `channel.xml`, `rest/p/packages.xml`, `rest/p/<pkg>/info.xml`, `rest/r/<pkg>/allreleases.xml`, `stable.txt`, категории, мейнтейнеры, `feeds/*.rss` | Свежие `METADATA_TTL` секунд, затем условный запрос (`If-None-Match`/`If-Modified-Since`) к upstream. |

- В кеш попадает только то, что запросили клиенты. При скачивании архива прокси в фоне
  докачивает метаданные этой версии (`info.xml`, `allreleases.xml`, `<ver>.xml`, `deps`,
  `package.xml`), чтобы она гарантированно ставилась офлайн.
- Тела хранятся ровно так, как их отдал upstream. При отдаче в текстовых ресурсах адреса
  `https://pecl.php.net/{rest,get,feeds}/...` и `channel.xml` заменяются на `PUBLIC_URL`.
  Ссылки на веб-страницы pecl.php.net в RSS не меняются: сайт не проксируется.
- Архив перед сохранением проверяется (размер, целостность gzip, читаемость tar). Битый или
  обрезанный ответ upstream в кеш не попадает.
- Один ресурс одновременно скачивается один раз, параллельные запросы ждут его.
- Клиентам прокси сам отвечает `ETag`/`Last-Modified` и `304` на условные запросы.

Заголовок ответа `X-Cache` показывает, что произошло:

| `X-Cache` | Значение |
|---|---|
| `HIT` | Из кеша, без обращения к upstream. |
| `MISS` | Скачано из upstream и сохранено. |
| `REVALIDATED` | Upstream подтвердил, что копия актуальна (304 или то же содержимое). |
| `UPDATED` | Upstream отдал новое содержимое, кеш обновлён. |
| `STALE` | Upstream недоступен (или ресурс у него пропал) — отдана последняя сохранённая копия. |

### Heartbeat и `history/`

Файлы версии на pecl.php.net могут измениться: `<ver>.xml` содержит описание пакета и
перегенерируется при его правке, а релиз могут удалить и перезалить. Каждый N-й запрос такого
файла вызывает фоновый условный запрос к upstream (клиент ответ не ждёт). Если содержимое
изменилось:

- кеш обновляется;
- прежняя копия сохраняется в `DATA_DIR/history/<путь>.<время>` вместе с метаданными;
- пишется `WARNING` с `event: immutable_changed` и увеличивается метрика
  `pecl_proxy_immutable_changed_total`.

Если файл в upstream пропал, копия сохраняется (`event: immutable_gone`). Счётчики запросов
хранятся в памяти и после рестарта начинаются заново. Выключить heartbeat:
`IMMUTABLE_RECHECK_EVERY=0`.

## Недоступность upstream

1. Запрос к upstream повторяется до `UPSTREAM_RETRIES` раз с растущей паузой
   (`UPSTREAM_RETRY_DELAY`) при сетевой ошибке, таймауте, 5xx и 429.
2. Если все попытки неудачны, upstream считается недоступным на `UPSTREAM_DOWN_COOLDOWN`
   секунд (`event: upstream_down`). В это время в upstream не ходим, ответы идут сразу из
   кеша, без ожидания таймаутов.
3. То, что есть в кеше, отдаётся как есть (`X-Cache: STALE`) — последний снимок upstream.
4. Чего нет в кеше — `504` с пояснением.
5. Ответ 404 от upstream запоминается на `NEGATIVE_TTL` секунд.
6. Когда upstream снова отвечает — `event: upstream_up`.

`PECL_PROXY_OFFLINE=true` включает режим «только кеш»: в upstream не ходим вообще. Удобно,
чтобы проверить, что кеша достаточно для сборок, или для копии кеша в закрытом контуре.

## Прогрев кеша

Заполнить кеш заранее, без pecl-клиента:

```sh
docker compose exec pecl-proxy pecl-proxy warm redis-6.3.0 apcu-5.1.28 pecl_http
# без Docker:
pecl-proxy warm redis-6.3.0
```

Спецификации — как у `pecl install`: `name` (свежий stable), `name-1.2.3`, `name-beta`.
Обязательные PECL-зависимости скачиваются рекурсивно; версия подбирается по ограничениям
из `deps.*.txt`, как это делает PEAR. `--no-deps` — без зависимостей. Код выхода `1`, если
что-то не скачалось; подробный результат печатается в JSON. То же доступно через Admin API.

## Хранилище

```
DATA_DIR/
  cache/<путь upstream>             тело ровно как у upstream
  cache/<путь upstream>.meta.json   ETag, Last-Modified, Content-Type, sha256, время загрузки и проверки
  history/                          прежние версии изменившихся файлов версий
  tmp/                              незавершённые загрузки (чистятся при старте)
```

- Запись атомарная (временный файл + rename), поэтому каталог можно копировать на ходу.
- **Бэкап:** `docker run --rm -v pecl-proxy_pecl-cache:/data -v "$PWD":/backup alpine tar czf /backup/pecl-cache.tgz -C /data .`
  (имя тома см. `docker volume ls`).
- **Перенос в закрытый контур:** разверните копию каталога в `DATA_DIR` другого экземпляра и
  при желании включите `PECL_PROXY_OFFLINE=true`.
- **Проверка целостности:** `pecl-proxy verify` сверяет sha256 всех файлов с сохранёнными.
- **Удаление пакета:** `pecl-proxy purge redis` или `pecl-proxy purge redis 6.3.0`.
- **Принудительное обновление метаданных:** `pecl-proxy refresh [пакет]`.

На один каталог данных рекомендуется один процесс сервиса: блокировки от параллельной загрузки
работают внутри процесса. CLI-команды можно запускать параллельно с сервером.

## Запуск без Docker

Нужен Python 3.12+.

```sh
python3 -m venv /opt/pecl-proxy/venv
/opt/pecl-proxy/venv/bin/pip install /path/to/pecl-proxy   # каталог репозитория или wheel
export PECL_PROXY_DATA_DIR=/var/lib/pecl-proxy
export PECL_PROXY_PUBLIC_URL=http://pecl-proxy.example.local
/opt/pecl-proxy/venv/bin/pecl-proxy serve
```

Все переменные — как в [configuration.md](configuration.md). Пример unit-файла systemd
(сервис сам слушает порт 80 без root):

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

Логи идут в stdout (journald) или в файл (`PECL_PROXY_LOG_FILE`).

## Reverse proxy и HTTPS

Сервис говорит только по HTTP. Для HTTPS поставьте перед ним nginx или балансировщик
([`deploy/nginx.conf.example`](../deploy/nginx.conf.example)) и:

- задайте `PECL_PROXY_PUBLIC_URL=https://pecl.example.local`;
- либо доверьте прокси заголовки: `PECL_PROXY_TRUSTED_PROXIES=<IP или подсеть прокси>`.
  Сервис будет брать схему, хост и префикс пути из `X-Forwarded-Proto`, `X-Forwarded-Host`,
  `X-Forwarded-Prefix`, а IP клиента для логов — из `X-Forwarded-For`.

Если сервис опубликован под префиксом (`https://example.local/pecl`), укажите его в
`PUBLIC_URL` или передавайте `X-Forwarded-Prefix`. Ссылки внутри REST-файлов
(`xlink:href="/rest/..."`) прокси тоже дополняет префиксом.

Оставьте порт 80 отвечающим (редирект на https допустим): перед каждой установкой pecl
запрашивает `http://<preferred_mirror>/channel.xml` и ходит по редиректам.

## Метрики

`GET /metrics` (или отдельный порт `METRICS_PORT`), префикс настраивается
(`METRICS_PREFIX`):

| Метрика | Тип | Метки |
|---|---|---|
| `pecl_proxy_requests_total` | counter | `resource` (тип ресурса), `cache` (`X-Cache`, `UNAVAILABLE`, `BAD_UPSTREAM`, `NEGATIVE`), `status` |
| `pecl_proxy_request_duration_seconds` | histogram | `resource` |
| `pecl_proxy_response_bytes_total` | counter | `resource` |
| `pecl_proxy_upstream_requests_total` | counter | `result` (HTTP-код, `error`, `too_large`) |
| `pecl_proxy_upstream_request_duration_seconds` | histogram | — |
| `pecl_proxy_upstream_available` | gauge | 1/0 |
| `pecl_proxy_heartbeat_checks_total` | counter | `result` (`unchanged`, `changed`, `gone`, `error`) |
| `pecl_proxy_immutable_changed_total` | counter | — |
| `pecl_proxy_cache_files`, `pecl_proxy_cache_bytes` | gauge | пересчитываются не чаще раза в минуту |

## Healthcheck

`GET /healthz` → `200 {"status": "ok", "upstream": {...}}`. Отвечает 200 и при недоступном
upstream: сервис при этом работает из кеша, а состояние upstream видно в поле `upstream`.
Используется в `HEALTHCHECK` образа.

## Сборка образа в закрытой сети

```sh
docker build \
  --build-arg PYTHON_IMAGE=registry.local/library/python:3.13-slim \
  --build-arg PIP_INDEX_URL=https://nexus.local/repository/pypi/simple \
  --secret id=ca,src=/etc/ssl/certs/corporate-ca.pem \
  -t pecl-proxy .
```

- `PYTHON_IMAGE` — базовый образ из зеркала реестра.
- `PIP_INDEX_URL` — зеркало PyPI.
- секрет `ca` — сертификат TLS-перехватывающего прокси. Он используется только при сборке и в
  образ не попадает.
