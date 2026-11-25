#!/bin/sh
# Same switch pattern as vds-sternwarte: one image, the role is chosen per service.
#   RUNWEB=true      web server (waitress, port 5080)
#   RUNINDEXER=true  background indexer (scans the read-only archive every SCAN_INTERVAL seconds)
set -e
echo "Running docker-startup.sh"

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
