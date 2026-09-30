#!/bin/bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v role_password="$QF_PG_PASSWORD" <<'SQL'
CREATE ROLE challenge LOGIN CREATEDB PASSWORD :'role_password';
SQL
