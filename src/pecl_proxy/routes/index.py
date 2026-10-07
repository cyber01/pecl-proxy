"""Landing page: cached packages and how to point PEAR/PECL clients at the proxy.

The language is set with ``PECL_PROXY_INDEX_LANGUAGE`` (``en`` by default, or ``ru``).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .. import __version__
from ..rewrite import mirror_host

_templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
    autoescape=select_autoescape(["html"]),
)

# Constant strings; the ones containing markup are wrapped in Markup.
TEXTS = {
    "en": {
        "subtitle": "Caching proxy for the {url} channel · version {version}",
        "offline": "forced offline",
        "available": "available",
        "unavailable": "unavailable",
        "retry_in": "retry in {seconds} s",
        "summary": "cached packages: {packages}, files: {files} ({size})",
        "connect": "Connecting a client",
        "connect_once": "Once per machine or image, point the pecl.php.net channel at this "
                        "server:",
        "mirror_note": Markup(
            "<code>preferred_mirror</code> is required for offline use: before every install "
            "pecl checks the channel.xml of its channel, and without this setting it asks "
            "pecl.php.net and fails when that is unreachable."),
        "no_mirror": Markup(
            "The service is not published on port 80/443, so it cannot be used as "
            "<code>preferred_mirror</code>: without access to pecl.php.net "
            "<code>pecl install</code> fails on the channel.xml check. Publish the service "
            "on 80/443 (for example behind nginx)."),
        "install": "After that install as usual; every request goes through the proxy:",
        "dockerfile": "In a Dockerfile:",
        "update_channels": Markup(
            "While the internet is reachable, <code>pecl update-channels</code> points the "
            "channel back to the original pecl.php.net; run the connection commands again "
            "after it."),
        "packages": "Cached packages ({count})",
        "col_package": "Package",
        "col_versions": "Versions (archives)",
        "col_files": "Files",
        "col_size": "Size",
        "metadata_only": "metadata only",
        "empty": "Nothing yet: packages get cached on their first request.",
        "units": ("KB", "MB", "GB"),
    },
    "ru": {
        "subtitle": "Кеширующий прокси канала {url} · версия {version}",
        "offline": "принудительный офлайн",
        "available": "доступен",
        "unavailable": "недоступен",
        "retry_in": "повтор через {seconds} с",
        "summary": "в кеше пакетов: {packages}, файлов {files} ({size})",
        "connect": "Подключение клиента",
        "connect_once": "Один раз на машине или в образе направить канал pecl.php.net "
                        "на этот сервер:",
        "mirror_note": Markup(
            "<code>preferred_mirror</code> нужен для работы без интернета: перед каждой "
            "установкой pecl проверяет channel.xml своего канала и без этой настройки "
            "обращается к pecl.php.net, а при его недоступности падает."),
        "no_mirror": Markup(
            "Сервис опубликован не на порту 80/443, поэтому его нельзя указать как "
            "<code>preferred_mirror</code>: без доступа к pecl.php.net команда "
            "<code>pecl install</code> упадёт на проверке channel.xml. Опубликуйте сервис "
            "на 80/443 (например, за nginx)."),
        "install": "Дальше установка как обычно, все запросы идут через прокси:",
        "dockerfile": "В Dockerfile:",
        "update_channels": Markup(
            "Команда <code>pecl update-channels</code> при доступном интернете вернёт канал "
            "на оригинальный pecl.php.net; после неё повторите команды подключения."),
        "packages": "Пакеты в кеше ({count})",
        "col_package": "Пакет",
        "col_versions": "Версии (архивы)",
        "col_files": "Файлов",
        "col_size": "Размер",
        "metadata_only": "только метаданные",
        "empty": "Пока пусто: пакеты попадают в кеш при первом запросе.",
        "units": ("КБ", "МБ", "ГБ"),
    },
}

router = APIRouter()


@router.get("/", include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    language = request.app.state.settings.index_language
    texts = TEXTS[language]
    service = request.app.state.service
    files, size = service.store.stats()
    html = _templates.get_template("index.html").render(
        lang=language,
        t=texts,
        size_text=lambda value: human_size(value, texts["units"]),
        version=__version__,
        public_base=request.state.public_base,
        mirror=mirror_host(request.state.public_base),
        upstream=service.upstream.status(),
        packages=list(service.store.packages().values()),
        files=files,
        size=human_size(size, texts["units"]),
    )
    return HTMLResponse(html)


def human_size(size: int, units: tuple[str, str, str] = ("KB", "MB", "GB")) -> str:
    kb, mb, gb = units
    for unit, factor in ((gb, 1024**3), (mb, 1024**2)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{size / 1024:.1f} {kb}"
