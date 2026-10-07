# Подключение клиентов

## Требования к публикации сервиса

- **`PECL_PROXY_PUBLIC_URL`** — адрес, по которому клиенты ходят к прокси. Он попадает в
  `channel.xml` и в ссылки на архивы.
- **Порт 80 (HTTP) или 443 (HTTPS)**. Нестандартный порт (`http://host:8080`) работает только
  при доступном pecl.php.net. Причина — в разделе про особенности PEAR ниже.
- Имя хоста из букв, цифр, точек и дефисов (так PEAR проверяет имена серверов канала).

`docker-compose.yml` из репозитория публикует контейнер на порту 80. Пример с HTTPS —
[`deploy/nginx.conf.example`](../deploy/nginx.conf.example).

## Подключение

Один раз на машине или в образе:

```sh
pecl channel-update http://pecl-proxy.example.local/channel.xml
pecl config-set preferred_mirror pecl-proxy.example.local
```

- `channel-update` скачивает `channel.xml` с прокси и записывает в реестр канала pecl.php.net
  адреса REST прокси. Все последующие запросы к каналу, включая зависимости вида
  `pecl_http → raphf`, идут через прокси.
- `preferred_mirror` направляет на прокси проверку `channel.xml`, которую pecl делает перед
  каждой установкой (подробности ниже).

Готовые команды с правильным адресом есть на главной странице прокси.

Проверка:

```sh
pecl channel-info pecl.php.net
# REST BASE и MIRROR должны указывать на http://pecl-proxy.example.local/rest/
pecl config-get preferred_mirror
```

Дальше всё как обычно:

```sh
pecl install redis
pecl install redis-6.3.0
pecl install apcu-beta
```

### Dockerfile

```dockerfile
FROM php:8.3-cli
RUN pecl channel-update http://pecl-proxy.example.local/channel.xml \
 && pecl config-set preferred_mirror pecl-proxy.example.local \
 && pecl install redis-6.3.0 apcu-5.1.28 \
 && docker-php-ext-enable redis apcu
```

Для сборок, которые должны работать без интернета, **указывайте версии явно**. Без версии
клиент выбирает свежий релиз по последнему снимку `allreleases.xml`. Если этот релиз через
прокси ещё никто не скачивал, установка без интернета не пройдёт.

### Отключение

```sh
pecl config-set preferred_mirror pecl.php.net
pecl channel-update pecl.php.net        # нужен доступ к pecl.php.net
```

## Что работает без интернета

- Любая версия, которую хоть раз скачивали через прокси, вместе с её PECL-зависимостями.
  При скачивании архива прокси сам докачивает метаданные этой версии.
- `pecl install <пакет>` без версии — если свежий по сохранённому снимку релиз есть в кеше.
- Пакеты и версии, которые никогда не запрашивались, недоступны (ошибка «No releases
  available» / 504). Заполнить кеш заранее можно командой `pecl-proxy warm`
  ([operations.md](operations.md#прогрев-кеша)).

## Особенности PEAR-клиента

Найдены при разработке по исходникам [pear/pear-core](https://github.com/pear/pear-core)
(PEAR 1.10.x — та же версия, что в официальных образах `php:*`). Проверены end-to-end тестом
с настоящим клиентом.

### `Host` без порта

`PEAR/REST.php` и `PEAR/Downloader.php` отправляют `Host: <хост>` без порта. Если прокси
доступен на нестандартном порту и `PUBLIC_URL` не задан, адреса в `channel.xml` получатся без
порта, и клиент пойдёт на порт 80. Поэтому `PUBLIC_URL` нужно задавать; без него сервис
пишет предупреждение при старте.

### Проверка channel.xml перед каждой установкой

Перед `install`/`download` клиент запрашивает `http://<preferred_mirror>/channel.xml`, а при
ошибке — `https://...` (`PEAR/Downloader.php`, метод `download`). По умолчанию
`preferred_mirror` равен имени канала, то есть запрос идёт на **pecl.php.net** напрямую, мимо
прокси. Из-за ошибки в обработке ошибок PEAR неудача обеих попыток не игнорируется, а
**завершает команду**. Без интернета `pecl install` падает, даже если всё нужное лежит в
кеше прокси.

Решение: прокси указывает себя зеркалом канала в отдаваемом `channel.xml`
(`<mirror host="pecl-proxy.example.local">`), а клиент выбирает это зеркало через
`config-set preferred_mirror`. Тогда проверка идёт на прокси и получает 304.

URL этой проверки PEAR собирает **без порта**, поэтому сервис должен быть доступен на 80/443.
Если `PUBLIC_URL` указывает на другой порт, прокси не добавляет себя зеркалом и пишет
предупреждение при старте.

### Зеркало указано дважды

`PEAR_Registry::_mirrorExists()`, которую вызывает `config-set preferred_mirror`, перебирает
элементы `<mirror>` как список. Единственное зеркало парсер возвращает не списком, поэтому
команда отвечает «Channel Mirror ... does not exist». Прокси указывает себя зеркалом дважды;
на работу клиента это не влияет.

### `update-channels` возвращает оригинальный канал

`pecl update-channels` и `pecl channel-update pecl.php.net` скачивают `channel.xml` с
настоящего pecl.php.net (если он доступен) и затирают адреса прокси. После них повторите
команды подключения. Без интернета эти команды ничего не меняют.

### Почему канал называется pecl.php.net

Скачав архив, клиент сверяет канал из `package.xml` внутри архива с каналом, из которого
ставит пакет, и при расхождении прерывает установку: `CRITICAL ERROR: We are
<канал>/redis-6.3.0, but the file downloaded claims to be pecl.php.net/redis-6.3.0`.
Отдельное имя канала потребовало бы перепаковывать каждый архив. Поэтому канал остаётся
`pecl.php.net`, а на прокси указывают только его адреса — архивы при этом совпадают с
оригиналом байт-в-байт.
