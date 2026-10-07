"""Landing page: cached packages and how to point PEAR/PECL clients at the proxy."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import __version__
from ..rewrite import mirror_host

_templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
    autoescape=select_autoescape(["html"]),
)

router = APIRouter()


@router.get("/", include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    service = request.app.state.service
    files, size = service.store.stats()
    html = _templates.get_template("index.html").render(
        version=__version__,
        public_base=request.state.public_base,
        mirror=mirror_host(request.state.public_base),
        upstream=service.upstream.status(),
        packages=list(service.store.packages().values()),
        files=files,
        size=human_size(size),
    )
    return HTMLResponse(html)


def human_size(size: int) -> str:
    for unit, factor in (("ГБ", 1024**3), ("МБ", 1024**2)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}"
    return f"{size / 1024:.1f} КБ"
