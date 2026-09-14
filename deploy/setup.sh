#!/usr/bin/env bash
# One-time server setup: generates deploy/.env, deploy/env/*.env and the
# Keycloak realm with fresh secrets. Safe to re-run: existing files are kept.
# Usage: deploy/setup.sh <public-ip-or-base-domain>
#   deploy/setup.sh 37.187.129.149      -> *.37-187-129-149.sslip.io
set -euo pipefail

cd "$(dirname "$0")"

BASE="${1:?usage: setup.sh <public-ip-or-base-domain>}"
if [[ "$BASE" =~ ^[0-9.]+$ ]]; then
  BASE="${BASE//./-}.sslip.io"
fi

DRIVE_DOMAIN="drive.${BASE}"
AUTH_DOMAIN="auth.drive.${BASE}"
S3_DOMAIN="s3.drive.${BASE}"

secret() { openssl rand -hex "${1:-32}"; }

OFFICE_DOMAIN="office.drive.${BASE}"

if [[ -f .env ]]; then
  echo "deploy/.env already exists, keeping existing secrets."
else

mkdir -p env
DB_PASSWORD="$(secret 24)"
KC_DB_PASSWORD="$(secret 24)"
MINIO_PASSWORD="$(secret 24)"
OIDC_SECRET="$(secret 32)"
KC_ADMIN_PASSWORD="$(secret 16)"

cat > .env <<EOF
DRIVE_DOMAIN=${DRIVE_DOMAIN}
AUTH_DOMAIN=${AUTH_DOMAIN}
S3_DOMAIN=${S3_DOMAIN}
EOF

cat > env/postgresql.env <<EOF
POSTGRES_DB=drive
POSTGRES_USER=drive
POSTGRES_PASSWORD=${DB_PASSWORD}
EOF

cat > env/kc_postgresql.env <<EOF
POSTGRES_DB=keycloak
POSTGRES_USER=keycloak
POSTGRES_PASSWORD=${KC_DB_PASSWORD}
EOF

cat > env/minio.env <<EOF
MINIO_ROOT_USER=drive
MINIO_ROOT_PASSWORD=${MINIO_PASSWORD}
EOF

cat > env/keycloak.env <<EOF
KC_BOOTSTRAP_ADMIN_USERNAME=admin
KC_BOOTSTRAP_ADMIN_PASSWORD=${KC_ADMIN_PASSWORD}
KC_DB=postgres
KC_DB_URL_HOST=kc_postgresql
KC_DB_URL_DATABASE=keycloak
KC_DB_USERNAME=keycloak
KC_DB_PASSWORD=${KC_DB_PASSWORD}
EOF

KC_INTERNAL="http://keycloak:8080/realms/drive/protocol/openid-connect"
cat > env/backend.env <<EOF
DJANGO_CONFIGURATION=Production
DJANGO_SETTINGS_MODULE=drive.settings
DJANGO_SECRET_KEY=$(secret 50)
DJANGO_ALLOWED_HOSTS=${DRIVE_DOMAIN},backend,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://${DRIVE_DOMAIN}
DJANGO_EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DJANGO_EMAIL_URL_APP=https://${DRIVE_DOMAIN}
DJANGO_EMAIL_BRAND_NAME=Drive
PYTHONPATH=/app
LOGGING_LEVEL_HANDLERS_CONSOLE=INFO
LOGGING_LEVEL_LOGGERS_ROOT=INFO
LOGGING_LEVEL_LOGGERS_APP=INFO

DB_HOST=postgresql
DB_PORT=5432
DB_NAME=drive
DB_USER=drive
DB_PASSWORD=${DB_PASSWORD}
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0

AWS_S3_ENDPOINT_URL=http://minio:9000
AWS_S3_DOMAIN_REPLACE=https://${S3_DOMAIN}
AWS_S3_ACCESS_KEY_ID=drive
AWS_S3_SECRET_ACCESS_KEY=${MINIO_PASSWORD}
AWS_S3_REGION_NAME=eu-east-1
AWS_S3_SIGNATURE_VERSION=s3v4
MEDIA_BASE_URL=https://${DRIVE_DOMAIN}

OIDC_OP_URL=https://${AUTH_DOMAIN}/realms/drive
OIDC_OP_AUTHORIZATION_ENDPOINT=https://${AUTH_DOMAIN}/realms/drive/protocol/openid-connect/auth
OIDC_OP_TOKEN_ENDPOINT=${KC_INTERNAL}/token
OIDC_OP_USER_ENDPOINT=${KC_INTERNAL}/userinfo
OIDC_OP_JWKS_ENDPOINT=${KC_INTERNAL}/certs
OIDC_OP_LOGOUT_ENDPOINT=https://${AUTH_DOMAIN}/realms/drive/protocol/openid-connect/logout
OIDC_RP_CLIENT_ID=drive
OIDC_RP_CLIENT_SECRET=${OIDC_SECRET}
OIDC_RP_SIGN_ALGO=RS256
OIDC_RP_SCOPES=openid email
OIDC_REDIRECT_ALLOWED_HOSTS=${DRIVE_DOMAIN}
LOGIN_REDIRECT_URL=https://${DRIVE_DOMAIN}
LOGIN_REDIRECT_URL_FAILURE=https://${DRIVE_DOMAIN}
LOGOUT_REDIRECT_URL=https://${DRIVE_DOMAIN}

FRONTEND_THEME=dsfr-light
FEATURES_INDEXED_SEARCH=False
MALWARE_DETECTION_BACKEND=core.malware_detection.SleepyDummyBackend
MALWARE_DETECTION_DUMMY_SLEEP=1
EOF

mkdir -p keycloak
sed \
  -e "s#ThisIsAnExampleKeyForDevPurposeOnly#${OIDC_SECRET}#g" \
  -e "s#http://localhost:3000#https://${DRIVE_DOMAIN}#g" \
  ../docker/auth/realm.json > keycloak/realm.json

chmod 600 .env env/*.env keycloak/realm.json

cat <<EOF
Generated configuration for:
  app:      https://${DRIVE_DOMAIN}
  keycloak: https://${AUTH_DOMAIN}  (admin / ${KC_ADMIN_PASSWORD})
  s3:       https://${S3_DOMAIN}
EOF
fi

# --- OnlyOffice (added after the first install: appended only once) ---
if ! grep -q '^OFFICE_DOMAIN=' .env; then
  ONLYOFFICE_JWT_SECRET="$(secret 32)"

  echo "OFFICE_DOMAIN=${OFFICE_DOMAIN}" >> .env

  cat > env/onlyoffice.env <<EOF
JWT_ENABLED=true
JWT_SECRET=${ONLYOFFICE_JWT_SECRET}
USE_UNAUTHORIZED_STORAGE=true
TZ=Europe/Paris
EOF

  # OnlyOffice calls WOPI back through the public URL: Drive checks the WOPI
  # proof signature against the absolute URL it receives.
  cat >> env/backend.env <<EOF

WOPI_CLIENTS=onlyoffice
WOPI_ONLYOFFICE_DISCOVERY_URL=http://onlyoffice/hosting/discovery
WOPI_SRC_BASE_URL=https://${DRIVE_DOMAIN}
WOPI_ONLYOFFICE_OPTIONS={"ForceConvertExtensions": ["doc", "xls", "ppt"], "ConvertServiceUrl": "http://onlyoffice/converter"}
WOPI_ONLYOFFICE_CONVERT_JWT_SECRET=${ONLYOFFICE_JWT_SECRET}
EOF

  mkdir -p onlyoffice
  sed "s#http://localhost:9981#https://${OFFICE_DOMAIN}#" \
    ../docker/onlyoffice/local-development.json > onlyoffice/local-production-linux.json

  chmod 600 env/onlyoffice.env
  echo "OnlyOffice configured on https://${OFFICE_DOMAIN}"
fi
