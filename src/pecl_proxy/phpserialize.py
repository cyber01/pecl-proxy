"""Minimal reader for PHP ``serialize()`` output (the format of REST ``deps.*.txt``)."""

from __future__ import annotations

from typing import Any


class UnserializeError(ValueError):
    pass


def unserialize(data: bytes) -> Any:
    reader = _Reader(data)
    value = reader.value()
    return value


def as_list(value: Any) -> list:
    """PHP arrays come back as dicts; a 0..n-1 keyed array is a list, anything else one item."""
    if isinstance(value, dict) and value and list(value) == list(range(len(value))):
        return [value[i] for i in range(len(value))]
    if value is None or value is False:
        return []
    return [value]


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _until(self, terminator: bytes) -> bytes:
        end = self.data.find(terminator, self.pos)
        if end < 0:
            raise UnserializeError(f"expected {terminator!r} after offset {self.pos}")
        chunk = self.data[self.pos:end]
        self.pos = end + len(terminator)
        return chunk

    def _expect(self, token: bytes) -> None:
        if self.data[self.pos:self.pos + len(token)] != token:
            raise UnserializeError(f"expected {token!r} at offset {self.pos}")
        self.pos += len(token)

    def value(self) -> Any:
        kind = self.data[self.pos:self.pos + 1]
        if kind == b"N":
            self._expect(b"N;")
            return None
        self._expect(kind + b":")
        if kind == b"b":
            return self._until(b";") == b"1"
        if kind == b"i":
            return int(self._until(b";"))
        if kind == b"d":
            return float(self._until(b";"))
        if kind == b"s":
            length = int(self._until(b":"))
            self._expect(b'"')
            raw = self.data[self.pos:self.pos + length]
            self.pos += length
            self._expect(b'";')
            return raw.decode("utf-8", "replace")
        if kind == b"a":
            count = int(self._until(b":"))
            self._expect(b"{")
            result: dict[Any, Any] = {}
            for _ in range(count):
                key = self.value()
                result[key] = self.value()
            self._expect(b"}")
            return result
        raise UnserializeError(f"unsupported type {kind!r} at offset {self.pos}")
