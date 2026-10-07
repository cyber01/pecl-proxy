"""Port of PHP's ``version_compare()`` (the ordering PEAR uses for releases)."""

from __future__ import annotations

import functools
import re

_SPECIAL_FORMS = [
    ("dev", 0), ("alpha", 1), ("a", 1), ("beta", 2), ("b", 2),
    ("RC", 3), ("rc", 3), ("#", 4), ("pl", 5), ("p", 5),
]


def _canonicalize(version: str) -> list[str]:
    version = re.sub(r"[-_+]", ".", version)
    version = re.sub(r"(?<=\d)(?=[^\d.])|(?<=[^\d.])(?=\d)", ".", version)
    return [part for part in version.split(".") if part != ""]


def _special(form: str) -> int:
    for name, order in _SPECIAL_FORMS:
        if form.startswith(name):
            return order
    return -1


def _compare_special(left: str, right: str) -> int:
    a, b = _special(left), _special(right)
    return (a > b) - (a < b)


def version_compare(left: str, right: str) -> int:
    """Return -1, 0 or 1 like PHP's ``version_compare($left, $right)``."""
    parts1, parts2 = _canonicalize(left), _canonicalize(right)
    for p1, p2 in zip(parts1, parts2, strict=False):
        if p1.isdigit() and p2.isdigit():
            result = (int(p1) > int(p2)) - (int(p1) < int(p2))
        elif not p1.isdigit() and not p2.isdigit():
            result = _compare_special(p1, p2)
        elif p1.isdigit():
            result = _compare_special("#N", p2)
        else:
            result = _compare_special(p1, "#N")
        if result:
            return result
    if len(parts1) > len(parts2):
        rest = parts1[len(parts2)]
        return 1 if rest.isdigit() else _compare_special(rest, "#N")
    if len(parts2) > len(parts1):
        rest = parts2[len(parts1)]
        return -1 if rest.isdigit() else -_compare_special(rest, "#N")
    return 0


version_key = functools.cmp_to_key(version_compare)
