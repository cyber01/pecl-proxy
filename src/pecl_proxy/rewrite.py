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
_PRIMARY_REST = re.compile(rb"<primary\b[^>]*>\s*(<rest>.*?</rest>)", re.S)
_SERVERS_END = re.compile(rb"\s*</servers>")
_SUMMARY = re.compile(rb"<summary>.*?</summary>", re.S)
_NAME = re.compile(rb"<name>\s*([^<]+?)\s*</name>")
# PEAR's _PEAR_CHANNELS_SERVER_PREG: host name with an optional path, no port
_CHANNEL_SERVER = re.compile(r"[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)*(?:/[a-zA-Z0-9\-]+)*")


def mirror_host(public_base: str) -> str | None:
    """Server name under which the proxy can be listed as a channel mirror.

    PEAR checks ``http://<preferred_mirror>/channel.xml`` before every install and builds
    that URL without a port, so this only works on the default port of the scheme.
    """
    parts = urlsplit(public_base)
    if not parts.hostname:
        return None
    default_port = 443 if parts.scheme == "https" else 80
    if parts.port not in (None, default_port):
        return None
    server = parts.hostname + parts.path.rstrip("/")
    return server if _CHANNEL_SERVER.fullmatch(server) else None


def build_channel_xml(upstream: bytes, public_base: str, summary: str | None = None) -> bytes:
    """channel.xml of the upstream with every REST base URL pointing to the proxy.

    Upstream mirrors are dropped so clients never bypass the proxy. When possible the proxy
    adds itself as a mirror, so that ``pecl config-set preferred_mirror <host>`` sends the
    installer's channel.xml check to the proxy instead of pecl.php.net.
    """
    rest = (public_base + "/rest/").encode()
    body = _BASEURL.sub(lambda m: m.group(1) + rest + m.group(2), upstream)
    body = _MIRROR.sub(b"", body)
    if summary:
        replacement = b"<summary>" + escape(summary).encode() + b"</summary>"
        body = _SUMMARY.sub(lambda _: replacement, body, count=1)

    host = mirror_host(public_base)
    primary = _PRIMARY_REST.search(body)
    if host and primary and host != channel_name(body):
        ssl = b' ssl="yes"' if public_base.startswith("https:") else b""
        mirror = (b'  <mirror host="' + host.encode() + b'"' + ssl + b">\n   "
                  + primary.group(1) + b"\n  </mirror>\n")
        # Listed twice on purpose: PEAR_Registry::_mirrorExists() iterates the parsed
        # <mirror> element as a list, which a single mirror is not, so `pecl config-set
        # preferred_mirror <host>` would claim the mirror does not exist.
        body = _SERVERS_END.sub(lambda m: b"\n" + mirror * 2 + b" </servers>", body, count=1)
    return body


def channel_name(channel_xml: bytes) -> str | None:
    match = _NAME.search(channel_xml)
    return match.group(1).decode("utf-8", "replace") if match else None
