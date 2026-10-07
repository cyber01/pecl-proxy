# pecl-proxy

[English](README.md) | **Русский**

> [!IMPORTANT]
> Сервис писался под конкретные собственные задачи. Код частично сгенерирован ИИ.

Кеширующий прокси для PEAR-канала **pecl.php.net**. Клиенты `pecl`/`pear` работают с ним
вместо pecl.php.net. Что уже запрашивалось, прокси отдаёт из своего кеша. Остальное забирает
с pecl.php.net, сохраняет и отдаёт. Цель — чтобы сборки (Dockerfile, CI) продолжали работать
при нестабильной или отсутствующей внешней сети.

```
pecl install redis ──► pecl-proxy ──(только при промахе кеша)──► pecl.php.net
                           │
                           └── кеш на диске: архивы + метаданные
```

## Как это работает

- Клиент один раз направляет канал pecl.php.net на прокси (`channel-update` +
  `preferred_mirror`). Имя канала остаётся `pecl.php.net`, поэтому `pecl install redis`
  и существующие Dockerfile'ы не меняются.
- Прокси реализует REST-протокол PEAR-канала в объёме pecl.php.net (REST 1.0/1.1):
  `channel.xml`, `rest/p|r|c|m/...`, архивы `get/...`, RSS `feeds/...`. Всё остальное — 404,
  открытым прокси сервис не является.
- В кеш попадает только то, что запросили клиенты, плюс метаданные, нужные для установки
  скачанной версии. Весь pecl.php.net не зеркалируется.
- Архивы отдаются **байт-в-байт** как на pecl.php.net. В метаданных заменяются только адреса
  pecl.php.net на адрес прокси.
- Изменяемые метаданные (списки релизов, `stable.txt` и т.п.) обновляются по TTL
  условными запросами (ETag/Last-Modified). Файлы конкретных версий хранятся бессрочно и
  изредка перепроверяются в фоне (heartbeat).
- Доступность upstream проверяется в фоне. Когда pecl.php.net недоступен (или
  `PECL_PROXY_OFFLINE=true`), закешированные файлы отдаются из последнего снимка; списки канала
  (`remote-list`, `search`) и списки релизов (`allreleases.xml`, `stable.txt`, …) собираются из
  того, что реально есть в кеше, поэтому `pecl install redis` берёт самую свежую закешированную
  версию; то, чего нет в кеше, — ошибка 504, сразу, без ожидания таймаутов.
- Поддерживаются релизы любой стабильности (`stable`, `beta`, `alpha`, `devel`); главная
  страница и админка показывают, какие закешированные версии нестабильные.

## Быстрый старт (Docker)

```sh
cp .env.example .env            # укажите PECL_PROXY_PUBLIC_URL — адрес, по которому ходят клиенты
docker compose up -d --build
curl http://pecl-proxy.example.local/healthz
```

Сервис слушает только HTTP (порт 8080 в контейнере, наружу — 80). HTTPS обеспечивает внешний
reverse proxy, пример — [`deploy/nginx.conf.example`](deploy/nginx.conf.example).
Запуск без Docker описан в [docs/ru/operations.md](docs/ru/operations.md#запуск-без-docker).

## Подключение клиента

```sh
pecl channel-update http://pecl-proxy.example.local/channel.xml
pecl config-set preferred_mirror pecl-proxy.example.local
pecl install redis
```

В Dockerfile:

```dockerfile
FROM php:8.3-cli
RUN pecl channel-update http://pecl-proxy.example.local/channel.xml \
 && pecl config-set preferred_mirror pecl-proxy.example.local \
 && pecl install redis-6.3.0 \
 && docker-php-ext-enable redis
```

Три правила, без которых работа без интернета ломается (подробности —
[docs/ru/client-setup.md](docs/ru/client-setup.md)):

1. **Задайте `PECL_PROXY_PUBLIC_URL`.** PEAR-клиент отправляет заголовок `Host` без порта.
2. **Публикуйте сервис на порту 80/443 и выполняйте `config-set preferred_mirror`.** Перед
   каждой установкой pecl проверяет `http://<preferred_mirror>/channel.xml` (по умолчанию
   pecl.php.net) и падает, если адрес недоступен; порт в этой проверке не учитывается.
3. **Не запускайте `pecl update-channels` при живой сети** — он вернёт канал на pecl.php.net
   (после него повторите две команды подключения).

В закрытой сети также направьте имя `pecl.php.net` на прокси (`--add-host`, `extra_hosts` или
DNS): `remote-info`, `remote-list` и `search` проверяют `http://pecl.php.net/channel.xml`
напрямую и иначе ждут его по 60 с.

## Документация

Оглавление: [docs/ru](docs/ru/README.md) (English: [docs/en](docs/en/README.md)).

- [Подключение клиентов](docs/ru/client-setup.md) — команды подключения, что работает без
  интернета, какие запросы делают команды pecl (`install`, `remote-info`, `remote-list`, …),
  найденные особенности PEAR-клиента
- [Конфигурация](docs/ru/configuration.md) — все переменные окружения
- [Эксплуатация](docs/ru/operations.md) — кеш, офлайн, прогрев, бэкап, запуск без Docker,
  reverse proxy и IP клиентов, метрики, сборка образа в закрытой сети
- [Admin API и CLI](docs/ru/admin-api.md) — управление кешем
- [Логи](docs/ru/logging.md) — формат JSON-логов

## Разработка

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                    # unit- и интеграционные тесты (фейковый pecl.php.net)
.venv/bin/ruff check src tests

# end-to-end с настоящим PEAR-клиентом (нужны php и право слушать порт 80)
tests/e2e/setup_pear.sh
.venv/bin/pytest -m e2e

# проверка запущенного прокси против настоящего pecl.php.net
scripts/smoke.sh http://pecl-proxy.example.local raphf-2.0.2
```

Тесты работают на записанных ответах pecl.php.net (`tests/fixtures/upstream`).
