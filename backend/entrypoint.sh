#!/bin/sh
set -e

echo "Waiting for Postgres at ${DB_HOST}:${DB_PORT}..."
until pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${POSTGRES_USER}" >/dev/null 2>&1; do
  sleep 1
done
echo "Postgres is up."

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "Ensuring the non-superuser app role exists (as owner)..."
  PGPASSWORD="${POSTGRES_PASSWORD}" psql -h "${DB_HOST}" -p "${DB_PORT}" \
    -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_APP_USER}') THEN
    CREATE ROLE ${DB_APP_USER} LOGIN PASSWORD '${DB_APP_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ${DB_APP_USER};
GRANT USAGE ON SCHEMA public TO ${DB_APP_USER};
SQL

  echo "Applying migrations (admin role, bypasses RLS)..."
  python manage.py migrate --database=admin --noinput

  echo "Granting runtime privileges to the app role..."
  python manage.py grant_app_privileges

  if [ "${SEED_ON_START:-0}" = "1" ]; then
    echo "Seeding (idempotent)..."
    python manage.py seed
  fi
fi

exec "$@"
