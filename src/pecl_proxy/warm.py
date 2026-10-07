"""Pre-fill the cache with packages (and their required PECL dependencies).

Version selection follows the PEAR installer: a version, a stability (``redis-beta``) or the
newest release with the preferred stability (``stable``) is chosen from allreleases.xml.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field

from .cache import BadUpstreamData, CacheService, NotFound, Unavailable
from .phpserialize import UnserializeError, as_list, unserialize
from .resources import classify
from .version import version_compare

STATES = ["snapshot", "devel", "alpha", "beta", "stable"]
_RELEASE = re.compile(rb"<r>\s*<v>([^<]+)</v>\s*<s>([^<]+)</s>")
_DOWNLOAD = re.compile(rb"<g>([^<]+)</g>")


class WarmError(Exception):
    pass


@dataclass
class WarmResult:
    spec: str
    package: str | None = None
    version: str | None = None
    ok: bool = True
    error: str | None = None
    files: list[str] = field(default_factory=list)
    dependencies: list[WarmResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "spec": self.spec, "package": self.package, "version": self.version,
            "ok": self.ok, "error": self.error, "files": self.files,
            "dependencies": [dep.as_dict() for dep in self.dependencies],
        }

    @property
    def all_ok(self) -> bool:
        return self.ok and all(dep.all_ok for dep in self.dependencies)


@dataclass
class _Constraint:
    min: str | None = None
    max: str | None = None
    recommended: str | None = None
    exclude: tuple[str, ...] = ()


def better_states(state: str) -> list[str]:
    """``state`` and every more stable one (PEAR's betterStates($state, true))."""
    return STATES[STATES.index(state):]


def parse_spec(spec: str) -> tuple[str, str | None, str | None]:
    """Split ``redis``, ``redis-6.3.0``, ``redis-beta`` or ``pecl.php.net/redis``.

    Returns ``(name, version, state)``.
    """
    name = spec.strip().rsplit("/", 1)[-1]
    if "-" in name:
        base, suffix = name.rsplit("-", 1)
        if suffix in STATES:
            return base, None, suffix
        if suffix[:1].isdigit():
            return base, suffix, None
    return name, None, None


class Warmer:
    def __init__(self, service: CacheService):
        self.service = service

    async def warm(self, spec: str, *, dependencies: bool = True) -> WarmResult:
        name, version, state = parse_spec(spec)
        return await self._warm(spec, name, version, state, None, dependencies, set())

    async def _warm(self, spec, name, version, state, constraint, dependencies, seen) -> WarmResult:
        result = WarmResult(spec, package=name)
        seen.add(name.lower())
        try:
            release = await self._choose(result, name, version, state or "stable", constraint)
            result.version = release
            deps = await self._fetch_release(result, name, release)
        except (WarmError, NotFound, Unavailable, BadUpstreamData) as exc:
            result.ok = False
            result.error = str(exc) or type(exc).__name__
            return result

        if dependencies:
            for dep_name, dep_constraint in deps:
                if dep_name.lower() in seen:
                    continue
                result.dependencies.append(await self._warm(
                    dep_name, dep_name, None, None, dep_constraint, dependencies, seen))
        return result

    async def _get(self, result: WarmResult, path: str) -> bytes:
        resource = classify(path)
        if resource is None:
            raise WarmError(f"invalid path {path}")
        try:
            entry = await self.service.get(resource)
        except NotFound as exc:
            raise WarmError(f"{path}: not found upstream") from exc
        result.files.append(resource.key)
        if resource.is_archive:
            return b""
        return self.service.store.read_bytes(entry.resource.key)

    async def _choose(self, result, name, version, state, constraint) -> str:
        body = await self._get(result, f"rest/r/{name.lower()}/allreleases.xml")
        releases = [(v.decode(), s.decode()) for v, s in _RELEASE.findall(body)]
        if not releases:
            raise WarmError(f"{name}: no releases")
        if version is not None:
            if version not in {v for v, _ in releases}:
                raise WarmError(f"{name}: version {version} does not exist")
            return version
        states = better_states(state)
        if constraint is not None:
            return self._choose_dependency(name, releases, states, constraint)
        for release_version, stability in releases:
            if stability in states:
                return release_version
        raise WarmError(f"{name}: no release with stability {state} or better")

    @staticmethod
    def _choose_dependency(name, releases, states, constraint: _Constraint) -> str:
        for release_version, stability in releases:
            if release_version in constraint.exclude:
                continue
            if constraint.recommended:
                if release_version == constraint.recommended:
                    return release_version
                continue
            if constraint.min and version_compare(release_version, constraint.min) < 0:
                continue
            if constraint.max and version_compare(release_version, constraint.max) > 0:
                continue
            if stability in states:
                return release_version
        raise WarmError(f"{name}: no release matches the dependency constraints")

    async def _fetch_release(self, result, name, version) -> list[tuple[str, _Constraint]]:
        lname = name.lower()
        await self._get(result, f"rest/p/{lname}/info.xml")
        release_xml = await self._get(result, f"rest/r/{lname}/{version}.xml")
        deps_raw = await self._get(result, f"rest/r/{lname}/deps.{version}.txt")
        with contextlib.suppress(WarmError):  # package.xml is not needed by the installer
            await self._get(result, f"rest/r/{lname}/package.{version}.xml")

        match = _DOWNLOAD.search(release_xml)
        if match is None:
            raise WarmError(f"{name}-{version}: no download URL in release info")
        archive = match.group(1).decode().rstrip("/").rsplit("/", 1)[-1]
        await self._get(result, f"get/{archive}.tgz")
        channel = (await self.service.upstream_channel_name()
                   or self.service.settings.upstream_host)
        return self._dependencies(deps_raw, channel)

    @staticmethod
    def _dependencies(raw: bytes, channel: str) -> list[tuple[str, _Constraint]]:
        try:
            deps = unserialize(raw)
        except (UnserializeError, ValueError):
            return []
        if not isinstance(deps, dict):
            return []
        required = deps.get("required") or {}
        if not isinstance(required, dict):
            return []
        found = []
        for kind in ("package", "subpackage"):
            for dep in as_list(required.get(kind)):
                if not isinstance(dep, dict) or "conflicts" in dep or "uri" in dep:
                    continue
                if dep.get("channel", channel).lower() != channel.lower() or not dep.get("name"):
                    continue
                found.append((dep["name"], _Constraint(
                    min=dep.get("min"), max=dep.get("max"), recommended=dep.get("recommended"),
                    exclude=tuple(as_list(dep.get("exclude"))),
                )))
        return found
