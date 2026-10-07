"""Rewriting of upstream URLs in served text resources.

Only URLs of resources the proxy serves (``/rest/``, ``/get/``, ``/feeds/``, ``/channel.xml``)
are rewritten; links to upstream web pages stay as they are. Channel name, package.xml and
deps files are never touched, and archives are always served byte-for-byte.
Bodies are handled as bytes because upstream files are not always valid UTF-8.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit
from xml.sax.saxutils import escape


class Rewriter:
    def __init__(self, hosts: Iterable[str]):
        self.hosts = sorted({host.lower() for host in hosts if host})
        alternatives = b"|".join(re.escape(host.encode()) for host in self.hosts)
        self._url = re.compile(
            rb"https?://(?:" + alternatives + rb")(?=/(?:rest|get|feeds)/|/channel\.xml)",
            re.IGNORECASE,
        )

    def rewrite(self, body: bytes, public_base: str) -> bytes:
        base = public_base.encode()
        body = self._url.sub(lambda _: base, body)
        prefix = urlsplit(public_base).path.rstrip("/").encode()
        if prefix:
            # REST files link to each other with root-relative xlink:href="/rest/..."
            body = body.replace(b'xlink:href="/rest/', b'xlink:href="' + prefix + b"/rest/")
        return body


_BASEURL = re.compile(rb"(<baseurl\b[^>]*>)[^<]*(</baseurl>)")
_MIRROR = re.compile(rb"\s*<mirror\b(?:[^>]*/>|.*?</mirror>)", re.S)
_SUMMARY = re.compile(rb"<summary>.*?</summary>", re.S)
_NAME = re.compile(rb"<name>\s*([^<]+?)\s*</name>")


def build_channel_xml(upstream: bytes, public_base: str, summary: str | None = None) -> bytes:
    """channel.xml of the upstream with every REST base URL pointing to the proxy.

    Upstream mirrors are dropped so clients never bypass the proxy.
    """
    rest = (public_base + "/rest/").encode()
    body = _BASEURL.sub(lambda m: m.group(1) + rest + m.group(2), upstream)
    body = _MIRROR.sub(b"", body)
    if summary:
        replacement = b"<summary>" + escape(summary).encode() + b"</summary>"
        body = _SUMMARY.sub(lambda _: replacement, body, count=1)
    return body


def channel_name(channel_xml: bytes) -> str | None:
    match = _NAME.search(channel_xml)
    return match.group(1).decode("utf-8", "replace") if match else None
