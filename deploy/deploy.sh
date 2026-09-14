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

"${COMPOSE[@]}" build backend frontend
"${COMPOSE[@]}" up -d postgresql redis minio kc_postgresql keycloak
"${COMPOSE[@]}" run --rm createbuckets
"${COMPOSE[@]}" run --rm migrate
"${COMPOSE[@]}" up -d --remove-orphans backend celery frontend nginx
# Pick up nginx config changes shipped by this deploy.
"${COMPOSE[@]}" exec -T nginx nginx -t
"${COMPOSE[@]}" exec -T nginx nginx -s reload

docker image prune -f >/dev/null
"${COMPOSE[@]}" ps
