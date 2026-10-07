# Client setup

**English** | [Русский](../ru/client-setup.md) · [Contents](README.md)

## Publishing requirements

- **`PECL_PROXY_PUBLIC_URL`** — the address clients use to reach the proxy. It ends up in
  `channel.xml` and in archive download URLs.
- **Port 80 (HTTP) or 443 (HTTPS).** A non-standard port (`http://host:8080`) only works while
  pecl.php.net is reachable; see the PEAR client quirks below.
- A host name made of letters, digits, dots and dashes (PEAR validates channel server names
  this way).

The `docker-compose.yml` in the repository publishes the container on port 80. An HTTPS
example: [`deploy/nginx.conf.example`](../../deploy/nginx.conf.example).

## Connecting a client

Once per machine or image:

```sh
pecl channel-update http://pecl-proxy.example.local/channel.xml
pecl config-set preferred_mirror pecl-proxy.example.local
```

- `channel-update` downloads `channel.xml` from the proxy and stores the proxy's REST
  addresses in the registry entry of the pecl.php.net channel. All further channel requests,
  including dependencies such as `pecl_http → raphf`, go through the proxy.
- `preferred_mirror` sends the `channel.xml` check that pecl performs before every install to
  the proxy as well (details below).

The landing page of the proxy shows these commands with the right address.

Check:

```sh
pecl channel-info pecl.php.net
# REST BASE and MIRROR must point to http://pecl-proxy.example.local/rest/
pecl config-get preferred_mirror
```

After that everything works as usual:

```sh
pecl install redis
pecl install redis-6.3.0
pecl install apcu-beta
```

### Dockerfile

```dockerfile
FROM php:8.3-cli
RUN pecl channel-update http://pecl-proxy.example.local/channel.xml \
 && pecl config-set preferred_mirror pecl-proxy.example.local \
 && pecl install redis-6.3.0 apcu-5.1.28 \
 && docker-php-ext-enable redis apcu
```

**Pin versions** in builds that must be reproducible. Without a version the client picks the
newest suitable release: while pecl.php.net is reachable, the newest one there (it gets
cached); without it, the newest one in the proxy cache. The same Dockerfile may therefore
install different versions online and offline.

### Disconnecting

```sh
pecl config-set preferred_mirror pecl.php.net
pecl channel-update pecl.php.net        # needs access to pecl.php.net
```

## Stable and non-stable releases

Every release on pecl.php.net has a stability: `stable`, `beta`, `alpha` or `devel`. The proxy
caches and serves all of them in the same way; which release to take is decided by the client:

- `pecl install <package>` takes the newest release that is at least as stable as
  `preferred_state` (`stable` by default, `pecl config-set preferred_state beta` changes it);
- `<package>-beta`, `-alpha`, `-devel` relax this for one command;
- `<package>-<version>` installs exactly that version, whatever its stability.

The landing page marks non-stable cached versions, `GET /_admin/packages` and
`pecl-proxy list` show the stability of every cached version ([admin-api.md](admin-api.md)).

## What works offline

- Every version that has been downloaded through the proxy at least once, together with its
  PECL dependencies. When an archive is downloaded the proxy also fetches the metadata of that
  version.
- `pecl install <package>` without a version — the newest cached release that fits
  `preferred_state`. If only non-stable versions are cached, pecl says so ("latest release is
  version 0.1.0, stability beta") and installs them with `<package>-beta` or an explicit
  version.
- Packages and versions that were never requested are unavailable ("No releases available" /
  504). The cache can be filled in advance with `pecl-proxy warm`
  ([operations.md](operations.md#cache-warm-up)).

## pecl commands and the requests they make

Every channel command goes through the proxy, including the `channel.xml` check (thanks to
`preferred_mirror`). The proxy caches exactly what the client requests. For informational
commands that is metadata without archives — sometimes a lot of files:

| Command | Client requests | Offline |
|---|---|---|
| `install`, `download`, `upgrade` | `r/<pkg>/allreleases.xml`, `p/<pkg>/info.xml`, `r/<pkg>/<v>.xml`, `r/<pkg>/deps.<v>.txt`, archive `get/<pkg>-<v>.tgz`; the same for required dependencies | Versions already downloaded through the proxy; without a version, the newest of them. |
| `remote-info <pkg>` | `p/<pkg>/info.xml`, `r/<pkg>/allreleases.xml`, then `deps.<v>.txt` and `<v>.xml` of **every** release of the package. `pecl_http` has 160 releases, so the first call makes 322 requests, and every cache miss goes to pecl.php.net one after another. Later calls are served from the cache. | Only cached versions; `Latest` is the newest cached one. |
| `remote-list`, `list-all` | `c/categories.xml`, then `c/<category>/packagesinfo.xml` for each of the ~50 pecl categories (several MB describing every package of the channel) | Generated from the cache: only packages with cached archives and only their cached versions. |
| `search <text>` | `p/packages.xml`, then `p/<pkg>/info.xml` and `r/<pkg>/allreleases.xml` of every package whose name contains the text. A description search (`search <text> <description>`) reads `info.xml` of every package of the channel. | Searches only packages with cached archives (`p/packages.xml` is generated from the cache). |
| `list-upgrades`, `upgrade-all` | `p/packages.xml`, `r/<pkg>/allreleases.xml` of installed packages and `<v>.xml` of the newer version found | Packages with cached archives; the newest cached version counts as the latest one. |

While the upstream is reachable all of these go through the proxy and are cached like
everything else; offline answers are described in
[operations.md](operations.md#upstream-outages). Lists and `allreleases.xml` are refreshed
after `METADATA_TTL`. Release files are kept forever
and re-checked by the heartbeat ([operations.md](operations.md#what-is-cached-and-how)).
The PEAR client also keeps its own cache of REST responses (`cache_dir`, `cache_ttl`
defaults to 3600 s), so for an hour repeated calls on the same machine may not reach the proxy
at all.

## PEAR client quirks

Found in the [pear/pear-core](https://github.com/pear/pear-core) sources (PEAR 1.10.x, the
version shipped in the official `php:*` images) while building the proxy, and covered by an
end-to-end test with the real client.

### `Host` without the port

`PEAR/REST.php` and `PEAR/Downloader.php` send `Host: <host>` without the port. If the proxy
listens on a non-standard port and `PUBLIC_URL` is not set, URLs in `channel.xml` lose the port
and the client goes to port 80. Set `PUBLIC_URL`; without it the service logs a warning at
startup.

### channel.xml check before every install

Before `install`/`download` the client fetches `http://<preferred_mirror>/channel.xml` and,
on failure, `https://...` (`PEAR/Downloader.php`, method `download`). By default
`preferred_mirror` is the channel name, so the request goes **directly to pecl.php.net**,
bypassing the proxy. Because of a bug in PEAR's error handling a failure of both attempts is
not ignored but **aborts the command**: without internet access `pecl install` fails even when
everything it needs is in the proxy cache.

Fix: the proxy lists itself as a mirror of the channel in the served `channel.xml`
(`<mirror host="pecl-proxy.example.local">`) and the client selects that mirror with
`config-set preferred_mirror`. The check then goes to the proxy and gets a 304.

PEAR builds the URL of this check **without a port**, so the service has to be reachable on
80/443. If `PUBLIC_URL` uses another port, the proxy does not add itself as a mirror and logs a
warning at startup.

### Direct channel check in remote-info, remote-list, search

`remote-info`, `remote-list`, `list-all`, `search` and `list-upgrades` start with
`_checkChannelForStatus()` (`PEAR/Command/Remote.php`), which requests
`http://pecl.php.net/channel.xml` **directly** — it ignores `preferred_mirror` (the variable is
read but never used) and the proxy. Errors are ignored, so the commands still work, but in a
closed network where packets to pecl.php.net are dropped the client waits for PHP's
`default_socket_timeout` (60 s) every time. The proxy cannot see this request.

Fix in isolated networks: make `pecl.php.net` resolve to the proxy, which answers
`/channel.xml` for any host name. The proxy must listen on port 80 of that address.

```sh
docker build --add-host pecl.php.net:10.0.0.5 .        # 10.0.0.5 — the proxy
docker run --add-host pecl.php.net:10.0.0.5 ...
# docker-compose: extra_hosts: ["pecl.php.net:10.0.0.5"]
# a host or VM: "10.0.0.5 pecl.php.net" in /etc/hosts, or a DNS record
```

`install` and `download` do not make this request; for them `preferred_mirror` is enough.

### The mirror is listed twice

`PEAR_Registry::_mirrorExists()`, used by `config-set preferred_mirror`, iterates over the
`<mirror>` elements as a list. A single mirror is not parsed into a list, so the command
answers "Channel Mirror ... does not exist". The proxy lists itself twice; this does not affect
how the client works.

### `update-channels` restores the original channel

`pecl update-channels` and `pecl channel-update pecl.php.net` download `channel.xml` from the
real pecl.php.net (when it is reachable) and overwrite the proxy addresses. Run the connection
commands again afterwards. Without internet access these commands change nothing.

### Why the channel is called pecl.php.net

After downloading an archive the client compares the channel in the `package.xml` inside the
archive with the channel it installs from and aborts on a mismatch: `CRITICAL ERROR: We are
<channel>/redis-6.3.0, but the file downloaded claims to be pecl.php.net/redis-6.3.0`.
A channel with its own name would require repackaging every archive. So the channel stays
`pecl.php.net` and only its addresses point to the proxy — and archives stay byte-for-byte
identical to the originals.
