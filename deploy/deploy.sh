#!/usr/bin/env bash
# Build and (re)start the production stack from the current checkout.
# Called by the GitHub Actions deploy workflow after `git reset` to origin/main.
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "deploy/.env is missing: run deploy/setup.sh first." >&2
  exit 1
fi

COMPOSE=(docker compose -f compose.prod.yaml)

# Refresh generated config (idempotent: secrets are only created once, and the
# domains are read from .env, which is where they are changed).
./setup.sh

# The build fetches base images: retry a few times so a DNS or registry hiccup
# on the server does not fail the whole deploy.
for attempt in 1 2 3; do
  if "${COMPOSE[@]}" build backend frontend; then
    break
  elif [[ $attempt -eq 3 ]]; then
    echo "Build failed 3 times, giving up." >&2
    exit 1
  fi
  echo "Build attempt $attempt failed, retrying in 30s..." >&2
  sleep 30
done
"${COMPOSE[@]}" up -d postgresql redis minio kc_postgresql keycloak
# OnlyOffice only reads its config at boot: restart it when the file changed.
sum=$(sha256sum onlyoffice/local-production-linux.json | cut -d' ' -f1)
if [[ "$(cat .onlyoffice.sum 2>/dev/null)" != "$sum" ]]; then
  "${COMPOSE[@]}" stop onlyoffice
  echo "$sum" > .onlyoffice.sum
fi
# OnlyOffice takes a few minutes on first boot; the WOPI discovery below needs it up.
"${COMPOSE[@]}" up -d --wait --wait-timeout 900 onlyoffice
"${COMPOSE[@]}" run --rm createbuckets
"${COMPOSE[@]}" run --rm migrate
"${COMPOSE[@]}" up -d --remove-orphans backend celery frontend nginx
# Pick up nginx config changes shipped by this deploy.
"${COMPOSE[@]}" exec -T nginx nginx -t
"${COMPOSE[@]}" exec -T nginx nginx -s reload
# Load the editors' discovery (supported extensions, proof keys) into the cache.
"${COMPOSE[@]}" exec -T backend python manage.py trigger_wopi_configuration

docker image prune -f >/dev/null
"${COMPOSE[@]}" ps
