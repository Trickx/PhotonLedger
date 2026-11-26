# syntax=docker/dockerfile:1
ARG GITCOMMIT=unknown
ARG APPVERSION=
# 1 = install Airgus (ASIAIR/NINA night logs), 0 = leave it out; everything else works without it
ARG WITH_AIRGUS=1

# Debian slim instead of alpine: numpy/astropy install as ready-made wheels
FROM python:3.14-slim

ARG GITCOMMIT
ARG APPVERSION
ARG WITH_AIRGUS

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PHOTONLEDGER_CONFIG=/data/config.json \
    FLASK_ARCHIVE_PATH=/archive \
    FLASK_DATA_PATH=/data

WORKDIR /app
COPY requirements.txt requirements-airgus.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$WITH_AIRGUS" = "1" ]; then pip install --no-cache-dir --no-deps -r requirements-airgus.txt; \
    else echo "Airgus wird nicht installiert (WITH_AIRGUS=$WITH_AIRGUS)"; fi

COPY webapp webapp/
COPY migrations migrations/
COPY app.py prod.py docker-startup.sh LICENSE README.md VERSION ./
RUN chmod +x docker-startup.sh

LABEL org.opencontainers.image.licenses="GPL-3.0-or-later"
EXPOSE 5080/tcp
# runs as the user given in docker-compose.yml (PUID/PGID), needs only write access to /data
CMD ["./docker-startup.sh"]
ENV GITCOMMIT=${GITCOMMIT} APPVERSION=${APPVERSION}
