"""Classification of request paths into known PEAR channel resources.

Only paths that belong to the PEAR REST 1.0/1.1 protocol (plus archives and RSS feeds)
are served; anything else is rejected, so the service cannot be used as an open proxy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_NAME = r"[A-Za-z0-9_][A-Za-z0-9_.\-]*"
_VERSION = r"[0-9][A-Za-z0-9_.\-+]*"
_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.\-+]*"


class Kind(StrEnum):
    CHANNEL = "channel"
    PACKAGES = "packages"
    PACKAGE_INFO = "package_info"
    PACKAGE_MAINTAINERS = "package_maintainers"
    ALLRELEASES = "allreleases"
    STABILITY = "stability"
    RELEASE = "release"
    PACKAGE_XML = "package_xml"
    DEPS = "deps"
    CATEGORIES = "categories"
    CATEGORY = "category"
    MAINTAINERS = "maintainers"
    MAINTAINER = "maintainer"
    ARCHIVE = "archive"
    FEED = "feed"


XML = "application/xml"
TEXT = "text/plain"
BINARY = "application/octet-stream"
RSS = "text/xml; charset=utf-8"


@dataclass(frozen=True)
class Resource:
    """A known channel resource.

    ``key`` is both the path below the channel root on the upstream server and the
    location of the file in the cache.
    """

    key: str
    kind: Kind
    versioned: bool  # bound to one release: kept forever (with heartbeat re-checks)
    rewrite: bool  # text containing upstream URLs that must point to the proxy
    content_type: str  # fallback when upstream sends none
    package: str | None = None
    version: str | None = None

    @property
    def is_archive(self) -> bool:
        return self.kind is Kind.ARCHIVE


def _rule(pattern: str, kind: Kind, versioned: bool, rewrite: bool, content_type: str):
    return re.compile(pattern + r"\Z"), kind, versioned, rewrite, content_type


_RULES = [
    _rule(r"channel\.xml", Kind.CHANNEL, False, True, XML),
    _rule(r"rest/p/packages\.xml", Kind.PACKAGES, False, True, XML),
    _rule(rf"rest/p/(?P<package>{_NAME})/info\.xml", Kind.PACKAGE_INFO, False, True, XML),
    _rule(rf"rest/p/(?P<package>{_NAME})/maintainers2?\.xml",
          Kind.PACKAGE_MAINTAINERS, False, True, XML),
    _rule(rf"rest/r/(?P<package>{_NAME})/allreleases2?\.xml", Kind.ALLRELEASES, False, True, XML),
    _rule(rf"rest/r/(?P<package>{_NAME})/(?:latest|stable|beta|alpha|devel)\.txt",
          Kind.STABILITY, False, False, TEXT),
    _rule(rf"rest/r/(?P<package>{_NAME})/package\.(?P<version>{_VERSION})\.xml",
          Kind.PACKAGE_XML, True, True, XML),
    _rule(rf"rest/r/(?P<package>{_NAME})/deps\.(?P<version>{_VERSION})\.txt",
          Kind.DEPS, True, False, TEXT),
    _rule(rf"rest/r/(?P<package>{_NAME})/(?:v2\.)?(?P<version>{_VERSION})\.xml",
          Kind.RELEASE, True, True, XML),
    _rule(r"rest/c/categories\.xml", Kind.CATEGORIES, False, True, XML),
    _rule(rf"rest/c/{_SEGMENT}/(?:info|packages|packagesinfo)\.xml",
          Kind.CATEGORY, False, True, XML),
    _rule(r"rest/m/allmaintainers\.xml", Kind.MAINTAINERS, False, True, XML),
    _rule(rf"rest/m/{_SEGMENT}/info\.xml", Kind.MAINTAINER, False, True, XML),
    _rule(r"feeds/[A-Za-z0-9_][A-Za-z0-9_.\-]*\.rss", Kind.FEED, False, True, RSS),
]

_ARCHIVE = re.compile(
    rf"get/(?P<package>{_NAME})-(?P<version>{_VERSION}?)(?P<ext>\.tgz|\.tar)?\Z"
)


def classify(path: str) -> Resource | None:
    """Map a raw (still percent-encoded) request path to a resource, or ``None``."""
    path = path.lstrip("/")
    if not path or any(part in ("", ".", "..") for part in path.split("/")):
        return None

    match = _ARCHIVE.match(path)
    if match:
        package, version, ext = match.group("package", "version", "ext")
        # ``/get/name-1.0`` serves the same file as ``/get/name-1.0.tgz`` upstream
        key = f"get/{package}-{version}{ext or '.tgz'}"
        return Resource(key, Kind.ARCHIVE, True, False, BINARY, package, version)

    for pattern, kind, versioned, rewrite, content_type in _RULES:
        match = pattern.match(path)
        if match:
            groups = match.groupdict()
            return Resource(path, kind, versioned, rewrite, content_type,
                            groups.get("package"), groups.get("version"))
    return None


def release_metadata(package: str, version: str) -> list[Resource]:
    """REST resources a client needs to install ``package-version``."""
    name = package.lower()
    paths = [
        f"rest/p/{name}/info.xml",
        f"rest/r/{name}/allreleases.xml",
        f"rest/r/{name}/{version}.xml",
        f"rest/r/{name}/deps.{version}.txt",
        f"rest/r/{name}/package.{version}.xml",
    ]
    return [res for res in map(classify, paths) if res is not None]
