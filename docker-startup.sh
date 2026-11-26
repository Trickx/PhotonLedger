#!/bin/sh
# Same switch pattern as vds-sternwarte: one image, the role is chosen per service.
#   RUNWEB=true      web server (waitress, port 5080)
#   RUNINDEXER=true  background indexer (scans the read-only archive every SCAN_INTERVAL seconds)
set -e
echo "Running docker-startup.sh as user $(id -u):$(id -g)"

# /data (./data on the host) must be writable for PUID:PGID - otherwise SQLite fails with
# "unable to open database file", which does not say what is wrong
DATA="${FLASK_DATA_PATH:-/data}"
probe="$DATA/.write-test-$$"
if ! ( : > "$probe" ) 2>/dev/null; then
  owner=$(stat -c '%u:%g' "$DATA" 2>/dev/null || echo "?")
  echo "FEHLER: $DATA ist für den Container-Benutzer $(id -u):$(id -g) nicht beschreibbar (Besitzer: $owner)."
  echo "  Auf dem Host: PUID/PGID in .env auf die Ausgabe von 'id -u' / 'id -g' setzen und"
  echo "  sudo chown -R <PUID>:<PGID> ./data   (Docker legt einen fehlenden ./data als root an)"
  exit 1
fi
rm -f "$probe"
if [ -z "$FLASK_ARCHIVES" ] && [ ! -r "${FLASK_ARCHIVE_PATH:-/archive}" ]; then
  echo "WARNUNG: Archiv ${FLASK_ARCHIVE_PATH:-/archive} ist für $(id -u):$(id -g) nicht lesbar - ARCHIVE_PATH und Rechte prüfen."
fi

if [ "$RUNWEB" = "true" ]; then
  # only the web service migrates; the indexer service waits for it (depends_on: service_healthy)
  echo "Upgrade database if needed"
  flask --app app.py db upgrade
fi

if [ "$RUNINDEXER" = "true" ]; then
  if [ "$RUNWEB" = "true" ]; then
    echo "Run indexer in background"
    python -m webapp.indexer &
  else
    echo "Run indexer"
    exec python -m webapp.indexer
  fi
fi

if [ "$RUNWEB" = "true" ]; then
  echo "Run web server"
  exec python prod.py
fi

echo "Neither RUNWEB nor RUNINDEXER is true - nothing to do"
exit 1
