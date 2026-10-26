FROM python:3.14.3-slim-bookworm@sha256:f21c0d5a44c56805654c15abccc1b2fd576c8d93aca0a3f74b4aba2dc92510e2 AS build
WORKDIR /build
RUN pip install --no-cache-dir uv==0.13.1
COPY pyproject.toml uv.lock ./
COPY client/ client/
COPY server/ server/
COPY assets/manifest.json assets/manifest.json
RUN uv sync --locked --no-dev --no-editable --package assist-live-server
COPY tools/check_release.py tools/asset_rights.py tools/check_assets.py tools/
RUN /build/.venv/bin/python tools/check_release.py --write-provenance /build/wheel-provenance.json

FROM python:3.14.3-slim-bookworm@sha256:f21c0d5a44c56805654c15abccc1b2fd576c8d93aca0a3f74b4aba2dc92510e2 AS runtime
LABEL org.opencontainers.image.title="assist-live-server" \
      org.opencontainers.image.source="https://github.com/legion-works/assist-live-server" \
      org.opencontainers.image.licenses="Project code: Apache-2.0; third-party components: see /app/THIRD_PARTY_NOTICES.md"
ENV PATH="/build/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ASSIST_LIVE_DATA=/data
RUN groupadd --gid 10001 assist-live && useradd --uid 10001 --gid 10001 --no-create-home assist-live \
    && mkdir /data && chown 10001:10001 /data
COPY --from=build /build/.venv /build/.venv
COPY --from=build /build/wheel-provenance.json /app/wheel-provenance.json
WORKDIR /app
COPY LICENSE THIRD_PARTY_NOTICES.md ./
USER 10001:10001
EXPOSE 8099/tcp
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10m CMD ["assist-live-server", "--healthcheck"]
CMD ["assist-live-server"]

FROM runtime AS app
USER root
COPY ha-app/run.py /app/run.py
LABEL io.hass.type="addon" io.hass.name="assist-live-server" \
      io.hass.description="Experimental realtime voice server" \
      io.hass.url="https://github.com/legion-works/assist-live-server"
ENTRYPOINT ["python", "/app/run.py"]

FROM runtime AS service
USER 10001:10001
