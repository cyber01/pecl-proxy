"""On-disk cache.

Layout below ``DATA_DIR``::

    cache/<key>             raw upstream bytes (``key`` mirrors the upstream path)
    cache/<key>.meta.json   metadata: upstream ETag/Last-Modified, content type, sha256, ...
    history/<key>.<stamp>   previous copies of release files that changed upstream
    tmp/                    downloads in progress (same filesystem -> atomic rename)

Bodies are stored exactly as received from upstream; rewriting happens when serving.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path

from .version import version_key

META_SUFFIX = ".meta.json"


@dataclass
class Meta:
    content_type: str
    size: int
    sha256: str
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: float = field(default_factory=time.time)
    validated_at: float = field(default_factory=time.time)

    @classmethod
    def from_dict(cls, data: dict) -> Meta:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class PackageSummary:
    name: str
    versions: list[str] = field(default_factory=list)  # versions with a cached archive
    files: int = 0
    size: int = 0


class CacheStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        self.cache_dir = self.root / "cache"
        self.history_dir = self.root / "history"
        self.tmp_dir = self.root / "tmp"
        for directory in (self.cache_dir, self.history_dir, self.tmp_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._cache_root = self.cache_dir.resolve()

    # -- paths -----------------------------------------------------------------------------

    def path(self, key: str) -> Path:
        path = (self.cache_dir / key).resolve()
        if not path.is_relative_to(self._cache_root) or path == self._cache_root:
            raise ValueError(f"cache key escapes the cache directory: {key!r}")
        return path

    def _meta_path(self, key: str) -> Path:
        path = self.path(key)
        return path.with_name(path.name + META_SUFFIX)

    def tmp_file(self) -> Path:
        fd, name = tempfile.mkstemp(dir=self.tmp_dir, prefix="dl-")
        os.close(fd)
        return Path(name)

    # -- read ------------------------------------------------------------------------------

    def read_meta(self, key: str) -> Meta | None:
        try:
            data = json.loads(self._meta_path(key).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        if not self.path(key).is_file():
            return None
        return Meta.from_dict(data)

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    # -- write -----------------------------------------------------------------------------

    def put_bytes(self, key: str, body: bytes, meta: Meta) -> None:
        tmp = self.tmp_file()
        tmp.write_bytes(body)
        self.put_file(key, tmp, meta)

    def put_file(self, key: str, tmp: Path, meta: Meta) -> None:
        """Move a completely downloaded temporary file into the cache."""
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
        self._write_meta(key, meta)

    def update_meta(self, key: str, **changes) -> Meta | None:
        meta = self.read_meta(key)
        if meta is None:
            return None
        for name, value in changes.items():
            setattr(meta, name, value)
        self._write_meta(key, meta)
        return meta

    def _write_meta(self, key: str, meta: Meta) -> None:
        target = self._meta_path(key)
        tmp = self.tmp_file()
        tmp.write_text(json.dumps(asdict(meta), indent=1), encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)

    def save_history(self, key: str) -> Path | None:
        """Keep a copy of the current version of ``key`` before it is replaced."""
        source = self.path(key)
        if not source.is_file():
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.history_dir / f"{key}.{stamp}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        meta_path = self._meta_path(key)
        if meta_path.is_file():
            shutil.copy2(meta_path, target.with_name(target.name + META_SUFFIX))
        return target

    def delete(self, key: str) -> bool:
        removed = False
        for path in (self.path(key), self._meta_path(key)):
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                pass
        return removed

    def cleanup_tmp(self, max_age: float = 3600) -> None:
        """Remove leftovers of interrupted downloads."""
        now = time.time()
        for path in self.tmp_dir.iterdir():
            try:
                if now - path.stat().st_mtime > max_age:
                    path.unlink()
            except OSError:
                pass

    # -- listing ---------------------------------------------------------------------------

    def keys(self) -> list[str]:
        result = []
        for meta_path in self.cache_dir.rglob("*" + META_SUFFIX):
            body = meta_path.with_name(meta_path.name[: -len(META_SUFFIX)])
            if body.is_file():
                result.append(body.relative_to(self.cache_dir).as_posix())
        return sorted(result)

    def stats(self) -> tuple[int, int]:
        count = size = 0
        for key in self.keys():
            count += 1
            size += self.path(key).stat().st_size
        return count, size

    def packages(self) -> dict[str, PackageSummary]:
        """Cached packages keyed by lowercase name."""
        from .resources import Kind, classify

        result: dict[str, PackageSummary] = {}
        for key in self.keys():
            resource = classify(key)
            if resource is None or resource.package is None:
                continue
            name = resource.package.lower()
            summary = result.setdefault(name, PackageSummary(resource.package))
            summary.files += 1
            summary.size += self.path(key).stat().st_size
            if resource.kind is Kind.ARCHIVE:
                summary.name = resource.package  # archive names keep the original case
                if resource.version not in summary.versions:
                    summary.versions.append(resource.version)
        for summary in result.values():
            summary.versions.sort(key=version_key, reverse=True)
        return dict(sorted(result.items()))

    def package_keys(self, package: str, version: str | None = None) -> list[str]:
        """All cached keys belonging to a package (optionally to one of its versions)."""
        from .resources import classify

        name = package.lower()
        selected = []
        for key in self.keys():
            resource = classify(key)
            if resource is None or resource.package is None or resource.package.lower() != name:
                continue
            if version is not None and resource.version != version:
                continue
            selected.append(key)
        return selected


    def purge(self, package: str, version: str | None = None) -> list[str]:
        """Delete a package (or one of its versions); returns the removed keys."""
        keys = self.package_keys(package, version)
        for key in keys:
            self.delete(key)
        return keys

    def mark_stale(self, package: str | None = None) -> int:
        """Make mutable metadata (of one package, or all) revalidate on the next request."""
        from .resources import classify

        count = 0
        for key in self.package_keys(package) if package else self.keys():
            resource = classify(key)
            if resource is not None and not resource.versioned:
                self.update_meta(key, validated_at=0.0)
                count += 1
        return count


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
