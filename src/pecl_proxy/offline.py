"""Channel listings built from the cache, used while the upstream is unavailable.

``p/packages.xml``, the category files (``c/categories.xml``, ``c/<cat>/info.xml``,
``packages.xml``, ``packagesinfo.xml``) and each package's release list
(``r/<pkg>/allreleases.xml``, ``latest.txt``, ``stable.txt``, ...) describe what exists
upstream. Served from an old snapshot they would send ``pecl remote-list`` / ``search`` /
``install <pkg>`` after packages and versions that were never cached. Offline they are
therefore generated from what the cache actually holds: packages with at least one cached
archive, listing only those versions, newest first (as pecl.php.net orders them). The
stability of a version comes from the cached release list or ``r/<pkg>/<version>.xml``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote, quote_plus, unquote_plus
from xml.sax.saxutils import escape

from .resources import Kind, Resource
from .storage import CacheStore, PackageSummary

_HEAD = b'<?xml version="1.0" encoding="UTF-8" ?>\n'
_NS = (' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
       ' xmlns:xlink="http://www.w3.org/1999/xlink"')
_INFO_BODY = re.compile(rb"<p\b[^>]*>(.*)</p>\s*$", re.S)
_NAME = re.compile(rb"<n>\s*([^<]+?)\s*</n>")
_CATEGORY = re.compile(rb"<ca\b[^>]*>\s*([^<]+?)\s*</ca>")
_CATEGORY_FILE = re.compile(r"rest/c/([^/]+)/(info|packages|packagesinfo)\.xml")
_STABILITY_FILE = re.compile(r"rest/r/[^/]+/(latest|stable|beta|alpha|devel)\.txt")


@dataclass
class CachedPackage:
    name: str
    category: str
    info: bytes  # inner XML of p/<pkg>/info.xml
    releases: list[tuple[str, str]] = field(default_factory=list)  # (version, stability)
    deps: dict[str, bytes] = field(default_factory=dict)


def _root(tag: str, namespace: str) -> str:
    return (f'<{tag} xmlns="http://pear.php.net/dtd/rest.{namespace}"{_NS}'
            f' xsi:schemaLocation="http://pear.php.net/dtd/rest.{namespace}'
            f' http://pear.php.net/dtd/rest.{namespace}.xsd">\n')


class OfflineListings:
    def __init__(self, store: CacheStore, channel: str):
        self.store = store
        self.channel = channel

    def build(self, resource: Resource) -> bytes | None:
        """Listing for ``resource`` from the cache, or ``None`` if there is nothing to list."""
        if resource.kind in (Kind.ALLRELEASES, Kind.STABILITY):
            package = self._package(resource.package.lower()) if resource.package else None
            if package is None or not package.releases:
                return None
            if resource.kind is Kind.ALLRELEASES:
                # allreleases2.xml (REST 1.3) is not served by pecl.php.net
                if not resource.key.endswith("/allreleases.xml"):
                    return None
                return self._allreleases(package)
            return self._stability(package, resource.key)
        packages = self._packages()
        if resource.kind is Kind.PACKAGES:
            return self._all_packages(packages)
        if resource.kind is Kind.CATEGORIES:
            return self._categories(packages)
        match = _CATEGORY_FILE.fullmatch(resource.key)
        if resource.kind is not Kind.CATEGORY or match is None:
            return None
        category = unquote_plus(match.group(1))
        members = [p for p in packages if p.category == category]
        if not members:
            return None
        if match.group(2) == "info":
            return self._category_info(category)
        if match.group(2) == "packages":
            return self._category_packages(members)
        return self._category_packagesinfo(members)

    # -- collecting ------------------------------------------------------------------------

    def _read(self, key: str) -> bytes | None:
        return self.store.read_bytes(key) if self.store.read_meta(key) else None

    def _packages(self) -> list[CachedPackage]:
        packages = (self._build_package(lname, summary)
                    for lname, summary in self.store.packages().items())
        return sorted((p for p in packages if p is not None), key=lambda p: p.name.lower())

    def _package(self, lname: str) -> CachedPackage | None:
        summary = self.store.packages().get(lname)
        return self._build_package(lname, summary) if summary is not None else None

    def _build_package(self, lname: str, summary: PackageSummary) -> CachedPackage | None:
        if not summary.versions:  # metadata only: cannot be installed offline
            return None
        info = self._read(f"rest/p/{lname}/info.xml") or b""
        body = _INFO_BODY.search(info)
        name = _NAME.search(info)
        category = _CATEGORY.search(info)
        package = CachedPackage(
            name=name.group(1).decode() if name else summary.name,
            category=category.group(1).decode() if category else "Default",
            info=body.group(1).strip() if body else self._minimal_info(summary.name, lname),
        )
        # newest first, as pecl.php.net lists them: the installer takes the first suitable one
        for version in summary.versions:
            package.releases.append((version, summary.stability.get(version) or "stable"))
            deps = self._read(f"rest/r/{lname}/deps.{version}.txt")
            if deps is not None:
                package.deps[version] = deps
        return package

    def _minimal_info(self, name: str, lname: str) -> bytes:
        return (f"<n>{escape(name)}</n>\n <c>{escape(self.channel)}</c>\n"
                f' <ca xlink:href="/rest/c/Default">Default</ca>\n'
                f' <r xlink:href="/rest/r/{escape(lname)}"/>').encode()

    # -- documents -------------------------------------------------------------------------

    def _allreleases(self, package: CachedPackage) -> bytes:
        lines = [_root("a", "allreleases"), f" <p>{escape(package.name)}</p>\n",
                 f" <c>{escape(self.channel)}</c>\n"]
        lines += [f" <r><v>{escape(v)}</v><s>{escape(s)}</s></r>\n" for v, s in package.releases]
        lines.append("</a>\n")
        return _HEAD + "".join(lines).encode()

    @staticmethod
    def _stability(package: CachedPackage, key: str) -> bytes | None:
        match = _STABILITY_FILE.fullmatch(key)
        if match is None:
            return None
        wanted = match.group(1)
        for version, stability in package.releases:
            if wanted == "latest" or stability == wanted:
                return version.encode()
        return None

    def _all_packages(self, packages: list[CachedPackage]) -> bytes:
        lines = [_root("a", "allpackages"), f"<c>{escape(self.channel)}</c>\n"]
        lines += [f" <p>{escape(p.name)}</p>\n" for p in packages]
        lines.append("</a>\n")
        return _HEAD + "".join(lines).encode()

    def _categories(self, packages: list[CachedPackage]) -> bytes:
        lines = [_root("a", "allcategories"), f"<ch>{escape(self.channel)}</ch>\n"]
        for category in sorted({p.category for p in packages}, key=str.lower):
            href = quote(quote_plus(category), safe="")
            lines.append(f' <c xlink:href="/rest/c/{href}/info.xml">{escape(category)}</c>\n')
        lines.append("</a>\n")
        return _HEAD + "".join(lines).encode()

    def _category_info(self, category: str) -> bytes:
        name = escape(category)
        return _HEAD + (_root("c", "category") + f" <n>{name}</n>\n"
                        f" <c>{escape(self.channel)}</c>\n <a>{name}</a>\n <d>none</d>\n</c>\n"
                        ).encode()

    def _category_packages(self, members: list[CachedPackage]) -> bytes:
        lines = [_root("l", "categorypackages")]
        lines += [f' <p xlink:href="/rest/p/{escape(p.name.lower())}">{escape(p.name)}</p>\n'
                  for p in members]
        lines.append("</l>\n")
        return _HEAD + "".join(lines).encode()

    def _category_packagesinfo(self, members: list[CachedPackage]) -> bytes:
        parts = [_root("f", "categorypackageinfo").encode()]
        for package in members:
            parts.append(b"<pi>\n<p>" + package.info + b"\n</p>\n<a>\n")
            for version, stability in package.releases:
                parts.append(f" <r><v>{escape(version)}</v><s>{escape(stability)}</s></r>\n"
                             .encode())
            parts.append(b"</a>\n")
            for version, deps in package.deps.items():
                parts.append(f"<deps>\n <v>{escape(version)}</v>\n <d>".encode()
                             + escape(deps.decode("latin-1"), {'"': "&quot;"}).encode("latin-1")
                             + b"</d>\n</deps>\n")
            parts.append(b"</pi>\n")
        parts.append(b"</f>\n")
        return _HEAD + b"".join(parts)
