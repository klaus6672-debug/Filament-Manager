# Läuft sowohl als Home-Assistant-Add-on (BUILD_FROM kommt vom Supervisor) als auch eigenständig mit Docker.
ARG BUILD_FROM=alpine:3.20
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip

WORKDIR /opt/filament
COPY app/requirements.txt ./
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY app/ ./
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENV DATA_DIR=/data PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD wget -qO- "http://127.0.0.1:${PORT}/api/health" >/dev/null || exit 1

CMD ["/run.sh"]
