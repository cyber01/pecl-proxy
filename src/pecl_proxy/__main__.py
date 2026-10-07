"""Command line interface.

    pecl-proxy [serve]                 run the HTTP server
    pecl-proxy warm SPEC [SPEC ...]    pre-fill the cache (redis, redis-6.3.0, apcu-beta)
    pecl-proxy list [--json]           show cached packages
    pecl-proxy purge NAME [VERSION]    remove a package (or one version) from the cache
    pecl-proxy refresh [NAME]          make metadata revalidate on the next request
    pecl-proxy verify                  check cached files against their checksums

Configuration comes from the same ``PECL_PROXY_*`` environment variables as the server,
so the commands work on the server's cache (e.g. ``docker compose exec pecl-proxy ...``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .config import Settings
from .logs import admin_log, log_event, setup_logging


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


def _audit(action: str, target: str | None, result: str, **fields) -> None:
    log_event(admin_log, logging.INFO, f"cli {action}: {result}", action=action, target=target,
              result=result, client_ip="cli", **fields)


def cmd_warm(settings: Settings, args: argparse.Namespace) -> int:
    from .app import build_service
    from .warm import Warmer

    async def run() -> list:
        service = build_service(settings)
        try:
            warmer = Warmer(service)
            results = [await warmer.warm(spec, dependencies=not args.no_deps)
                       for spec in args.specs]
            await service.drain()
            return results
        finally:
            await service.close()
            await service.upstream.aclose()

    results = asyncio.run(run())
    ok = all(result.all_ok for result in results)
    _audit("warm", ",".join(args.specs), "ok" if ok else "partial")
    print(json.dumps([result.as_dict() for result in results], indent=2, ensure_ascii=False))
    return 0 if ok else 1


def cmd_list(settings: Settings, args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from .storage import CacheStore

    packages = CacheStore(settings.data_dir).packages()
    if args.json:
        print(json.dumps([asdict(p) for p in packages.values()], indent=2, ensure_ascii=False))
        return 0
    if not packages:
        print("cache is empty")
    for summary in packages.values():
        versions = ", ".join(summary.versions) or "(metadata only)"
        print(f"{summary.name:<30} {versions}")
    return 0


def cmd_purge(settings: Settings, args: argparse.Namespace) -> int:
    from .storage import CacheStore

    keys = CacheStore(settings.data_dir).purge(args.name, args.version)
    for key in keys:
        print(f"deleted {key}")
    target = f"{args.name}-{args.version}" if args.version else args.name
    _audit("purge", target, "ok" if keys else "not_found", deleted=len(keys))
    if not keys:
        print(f"{target} is not cached", file=sys.stderr)
        return 1
    return 0


def cmd_refresh(settings: Settings, args: argparse.Namespace) -> int:
    from .storage import CacheStore

    count = CacheStore(settings.data_dir).mark_stale(args.name)
    _audit("refresh", args.name or "*", "ok", marked=count)
    print(f"{count} metadata files will be revalidated on the next request")
    return 0


def cmd_verify(settings: Settings, args: argparse.Namespace) -> int:
    from .storage import CacheStore, sha256_file

    store = CacheStore(settings.data_dir)
    problems = 0
    keys = store.keys()
    for key in keys:
        meta = store.read_meta(key)
        if meta is None:
            continue
        digest = sha256_file(store.path(key))
        if digest != meta.sha256:
            problems += 1
            print(f"MISMATCH {key}: sha256 {digest} != {meta.sha256}")
    print(f"checked {len(keys)} files, {problems} problems")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pecl-proxy",
        description="Caching proxy for the pecl.php.net PEAR channel. "
        "Configuration is read from PECL_PROXY_* environment variables.",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="run the HTTP server (default)")

    warm = commands.add_parser("warm", help="download packages into the cache")
    warm.add_argument("specs", nargs="+", metavar="SPEC",
                      help="package, package-version or package-state (e.g. apcu-beta)")
    warm.add_argument("--no-deps", action="store_true",
                      help="do not download required PECL dependencies")

    listing = commands.add_parser("list", help="show cached packages")
    listing.add_argument("--json", action="store_true", help="machine-readable output")

    purge = commands.add_parser("purge", help="remove a package or one version from the cache")
    purge.add_argument("name")
    purge.add_argument("version", nargs="?")

    refresh = commands.add_parser("refresh", help="revalidate metadata on the next request")
    refresh.add_argument("name", nargs="?")

    commands.add_parser("verify", help="check cached files against stored checksums")
    return parser


COMMANDS = {
    None: cmd_serve, "serve": cmd_serve, "warm": cmd_warm, "list": cmd_list,
    "purge": cmd_purge, "refresh": cmd_refresh, "verify": cmd_verify,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if args.command not in (None, "serve"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(settings.log_level, settings.log_file)
    return COMMANDS[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
