#!/bin/sh
# Wait for PostgreSQL, ensure the schema exists, then hand over to the command.
set -eu

# compose already gates startup on the database healthcheck, but `docker compose
# run` bypasses that, and a restarted database can briefly refuse connections.
# Waiting here keeps both paths working.
if [ -n "${DATABASE_URL:-}" ]; then
    attempt=0
    until pg_isready -d "$DATABASE_URL" >/dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 60 ]; then
            echo "Database not ready after 60 attempts, giving up." >&2
            exit 1
        fi
        echo "Waiting for the database... ($attempt)"
        sleep 1
    done

    # Idempotent: init-db uses CREATE TABLE IF NOT EXISTS, so a container
    # restart against a populated database is a no-op rather than an error.
    shazam init-db
fi

# exec so the server becomes PID 1 and receives SIGTERM directly. Without it the
# shell holds PID 1, never forwards the signal, and `docker compose down` waits
# out the full ten-second kill timeout on every shutdown.
exec "$@"
