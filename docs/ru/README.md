# Документация pecl-proxy

[English](../README.md) | **Русский** · [Обзор проекта](../../README_ru.md)

- [Подключение клиентов](client-setup.md) — команды подключения, Dockerfile, что работает без
  интернета, какие запросы делают команды pecl, найденные особенности PEAR-клиента.
- [Конфигурация](configuration.md) — все переменные окружения `PECL_PROXY_*`.
- [Эксплуатация](operations.md) — политика кеширования, heartbeat, недоступность upstream,
  прогрев, хранилище и бэкап, запуск без Docker, reverse proxy и IP клиентов, метрики,
  healthcheck, сборка образа в закрытой сети.
- [Admin API и CLI](admin-api.md) — управление кешем по HTTP и из командной строки.
- [Логи](logging.md) — формат JSON-логов, типы записей `access`/`admin`/`app`, события.
