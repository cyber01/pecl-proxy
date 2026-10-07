"""Service settings, read from ``PECL_PROXY_*`` environment variables."""

from __future__ import annotations

import ipaddress
import re
from functools import cached_property
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BeforeValidator, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SIZE_UNITS = {"": 1, "B": 1, "K": 1024, "KB": 1024, "M": 1024**2, "MB": 1024**2,
               "G": 1024**3, "GB": 1024**3}


def parse_size(value: object) -> int:
    """Parse sizes like ``200MB``, ``1.5G`` or ``1048576`` into bytes."""
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*", str(value))
    if not match or match.group(2).upper() not in _SIZE_UNITS:
        raise ValueError(f"invalid size: {value!r}")
    return int(float(match.group(1)) * _SIZE_UNITS[match.group(2).upper()])


ByteSize = Annotated[int, BeforeValidator(parse_size)]

# TRUSTED_PROXIES=private: loopback and private ranges, e.g. the Docker network gateway that
# a reverse proxy on the host connects through
PRIVATE_NETWORKS = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                    "::1/128", "fc00::/7")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PECL_PROXY_", env_ignore_empty=True, extra="ignore"
    )

    # network
    host: str = "0.0.0.0"
    port: int = 8080
    public_url: str | None = None
    trusted_proxies: str = "127.0.0.1"

    # upstream
    upstream_url: str = "https://pecl.php.net"
    upstream_connect_timeout: float = Field(5.0, gt=0)
    upstream_read_timeout: float = Field(60.0, gt=0)
    upstream_retries: int = Field(2, ge=0)
    upstream_retry_delay: float = Field(1.0, ge=0)
    upstream_down_cooldown: float = Field(30.0, ge=0)
    offline: bool = False
    max_download_size: ByteSize = 200 * 1024**2

    # channel
    channel_summary: str | None = None

    # cache
    data_dir: Path = Path("/data")
    metadata_ttl: float = Field(300.0, ge=0)
    immutable_recheck_every: int = Field(50, ge=0)
    negative_ttl: float = Field(60.0, ge=0)

    # index page
    index_enabled: bool = True

    # metrics
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"
    metrics_port: int | None = None
    metrics_prefix: str = "pecl_proxy"

    # admin API
    admin_token: SecretStr | None = None
    admin_path: str = "/_admin"

    # logging
    log_level: str = "INFO"
    log_file: Path | None = None
    access_log: bool = True

    @field_validator("public_url", "upstream_url")
    @classmethod
    def _strip_slash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"must be an absolute http(s) URL: {value!r}")
        return value.rstrip("/")

    @field_validator("metrics_path", "admin_path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        value = "/" + value.strip("/")
        if value == "/":
            raise ValueError("path must not be the site root")
        return value

    @field_validator("metrics_prefix")
    @classmethod
    def _check_prefix(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", value):
            raise ValueError(f"invalid Prometheus metric prefix: {value!r}")
        return value

    @field_validator("log_level")
    @classmethod
    def _check_level(cls, value: str) -> str:
        value = value.upper()
        if value not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(f"invalid log level: {value!r}")
        return value

    @field_validator("trusted_proxies")
    @classmethod
    def _check_networks(cls, value: str) -> str:
        for item in _split(value):
            if item not in ("*", "private"):
                ipaddress.ip_network(item, strict=False)
        return value

    @cached_property
    def trusted_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network] | None:
        """Networks allowed to set X-Forwarded-* headers; ``None`` means any client."""
        items = _split(self.trusted_proxies)
        if "*" in items:
            return None
        networks = []
        for item in items:
            for network in PRIVATE_NETWORKS if item == "private" else (item,):
                networks.append(ipaddress.ip_network(network, strict=False))
        return networks

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_token and self.admin_token.get_secret_value())

    @property
    def upstream_host(self) -> str:
        return urlsplit(self.upstream_url).netloc


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
