"""Command line entry point: ``pecl-proxy serve`` (default)."""

from __future__ import annotations

import argparse
import sys

from .config import Settings
from .logs import setup_logging


def cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app

    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,  # logging is configured by setup_logging()
        access_log=False,  # written by our middleware as JSON
        proxy_headers=False,  # X-Forwarded-* is handled by RequestContextMiddleware
        server_header=False,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pecl-proxy",
        description="Caching proxy for the pecl.php.net PEAR channel. "
        "Configuration is read from PECL_PROXY_* environment variables.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the HTTP server (default)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    setup_logging(settings.log_level, settings.log_file)
    handlers = {None: cmd_serve, "serve": cmd_serve}
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
