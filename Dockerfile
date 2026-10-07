# Build options for closed networks:
#   --build-arg PYTHON_IMAGE=registry.local/python:3.13-slim   base image from a registry mirror
#   --build-arg PIP_INDEX_URL=https://nexus.local/pypi/simple  PyPI mirror
#   --secret id=ca,src=/path/to/corporate-ca.pem               CA of a TLS-intercepting proxy
ARG PYTHON_IMAGE=python:3.13-slim

FROM ${PYTHON_IMAGE} AS build
ARG PIP_INDEX_URL=https://pypi.org/simple
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip wheel --no-cache-dir --wheel-dir /wheels .

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PECL_PROXY_DATA_DIR=/data \
    PECL_PROXY_PORT=8080
RUN useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin pecl \
 && mkdir -p /data && chown pecl /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels pecl-proxy && rm -rf /wheels
USER pecl
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PECL_PROXY_PORT', '8080'), timeout=3)"]
ENTRYPOINT ["pecl-proxy"]
CMD ["serve"]
